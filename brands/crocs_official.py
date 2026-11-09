PORTAL_API_VERSION = 1

import hashlib
import html
import json
import math
import re
import time
import uuid
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlencode, unquote

from bs4 import BeautifulSoup
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots, clean_product_name

ORIGIN = 'https://www.crocs.co.kr'
ADAPTER_REVISION = '2026-11-09-worker-compat-2'
POLICY = {'checked_on': '2026-11-09', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ['JPEG', 'PNG', 'WEBP'], 'max_aspect': 1.8}
# 2026-11-09: 공식 HTML의 RSC product/imageGroups/variationAttributes 및
# variants.price/retailPrice 확인. 실제 Chrome 조회와 사이즈 팝업 확인.
# 색상 매핑은 imageGroups.variationAttributes의 color 값으로 판정한다.
# 규칙 기반 이미지 필터이며 모든 콜라주를 시각적으로 판정하는 기능은 아니다.
NEGATIVE = re.compile(r'size|guide|chart|measur|collage|montage|banner|logo|swatch|notice|사이즈|실측|측정|고시|콜라주|배너|로고', re.I)
NON_CATALOG = re.compile(r'_ALT210(?:/|$)|review|ugc|customer|lifestyle|생활\s*양식|리뷰|고객\s*사진', re.I)
# 현재 공식 갤러리의 alt로 확인한 카탈로그 촬영 역할만 허용한다.
CATALOG_POSES = {'100':'옆면','110':'한쌍','120':'상단','130':'바닥면','140':'사선','160':'뒷면'}


def rsc_text(source):
    doc = BeautifulSoup(source, 'html.parser')
    parts = []
    for script in doc.find_all('script'):
        text = script.get_text()
        if 'self.__next_f.push([1,' in text:
            try:
                parts.append(json.loads(text.split('self.__next_f.push(', 1)[1].rsplit(')', 1)[0])[1])
            except (ValueError, IndexError, TypeError):
                continue
    return doc, ''.join(parts)


def objects(text, key):
    decoder = json.JSONDecoder()
    for match in re.finditer('"' + re.escape(key) + r'"\s*:', text):
        try:
            yield decoder.raw_decode(text[match.end():].lstrip())[0]
        except ValueError:
            continue


def parse_product(source, code):
    doc, text = rsc_text(source)
    for product in objects(text, 'product'):
        if isinstance(product, dict) and str(product.get('masterProductId', product.get('id'))) == code and product.get('imageGroups'):
            return doc, text, product
    raise ValueError('공식 품번/상품 데이터 확인 실패: ' + code)


def variations(product, kind):
    return next((a.get('values', []) for a in product.get('variationAttributes', []) if a.get('id') == kind), [])


def displayed_colors(doc, product, base):
    official = {str(c['value']): c for c in variations(product, 'color')}
    result = []
    for button in doc.select('button[data-testid^="pdp-colour-"]'):
        image = button.find('img', src=True)
        if not image:
            raise ValueError('화면 색상 스와치의 공식 이미지/색상 연결 누락')
        path = unquote(urlsplit(image['src']).path)
        match = re.search(r'/products/' + re.escape(base) + r'_([A-Za-z0-9]+)_ALT\d+(?:/|$)', path)
        if not match or match[1] not in official:
            raise ValueError('화면 색상과 공식 품번/색상코드 대조 실패')
        color = official[match[1]]
        if button.get('aria-label') != color.get('name'):
            raise ValueError('화면 색상명과 공식 색상명 불일치')
        # Disabled/sold-out swatches remain included when actually displayed.
        if color not in result:
            result.append(color)
    if not result:
        raise ValueError('현재 상품 화면의 색상 선택 목록을 확인하지 못했습니다.')
    return result


def verified_colors(product, text, base):
    result=[]
    for color in variations(product,'color'):
        cid=str(color.get('value',''))
        if not cid or not color.get('name'):
            raise ValueError('공식 색상명/코드 누락')
        # 화면 노출 여부가 아니라 실제 공식 SKU의 품번/색상 연결로 판정.
        variants=variants_for(text,base,cid)
        if not variants:
            raise ValueError('공식 SKU에서 품번/색상 연결 확인 불가: '+base+'-'+cid)
        if any(not str(v.get('productId','')).startswith(base+'-'+cid+'-') for v in variants):
            raise ValueError('공식 SKU 품번/색상 불일치: '+cid)
        result.append(color)
    return result


def variants_for(text, base, color):
    for variants in objects(text, 'variants'):
        if isinstance(variants, list) and any(str(v.get('productId', '')).startswith(base + '-') for v in variants if isinstance(v, dict)):
            return [v for v in variants if isinstance(v, dict) and str(v.get('productId', '')).startswith(base + '-') and v.get('variationValues', {}).get('color') == color]
    raise ValueError('공식 변형 옵션 데이터 확인 실패')


def money(ctx, variants, key, currency):
    if currency != 'KRW':
        ctx.log('KRW 이외 통화: 가격 빈칸', 'WARNING')
        return None
    values = []
    for variant in variants:
        value = variant.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            ctx.log(key + ' 누락/0/잘못된 값: 빈칸', 'WARNING')
            return None
        values.append(value)
    if not values or len(set(values)) != 1:
        ctx.log(key + ' 없음 또는 사이즈별 가격 상이: 빈칸', 'WARNING')
        return None
    return values[0]


def visit(ctx, page, url):
    if urlsplit(url).hostname != 'www.crocs.co.kr':
        raise ValueError('크록스 공식 상품 URL이 아님')
    last = None
    for attempt in range(2):
        ctx.checkpoint()
        try:
            response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
            if response and response.status >= 400:
                raise ValueError('공식 페이지 HTTP ' + str(response.status))
            if urlsplit(page.url).hostname != 'www.crocs.co.kr':
                raise ValueError('공식몰 밖으로 이동')
            return
        except Exception as exc:
            last = exc
            ctx.log('페이지 조회 재시도: ' + str(exc), 'WARNING')
        finally:
            ctx.checkpoint()
    raise RuntimeError('공식 페이지 조회 실패: ' + str(last))


def wait_product(ctx, page, code):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ctx.checkpoint()
        try:
            return parse_product(page.content(), code)
        except ValueError:
            page.wait_for_timeout(400)
    raise ValueError('상품 데이터 로딩 시간 초과: ' + code)


def close_overlays(page):
    for name in ('쿠키 배너 닫기', '아니요, 괜찮습니다.'):
        button = page.get_by_role('button', name=name, exact=True)
        if button.count() and button.first.is_visible():
            button.first.click(timeout=3000)


def find_product(ctx, page, base):
    # 링크는 검색 결과 DOM에서 회수한 후 상품 데이터의 masterProductId로 대조.
    # 실제 검색 UI에서 Enter 후 /search?q=10126 이동을 확인했다.
    visit(ctx, page, ORIGIN + '/search?' + urlencode({'q': base}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ctx.checkpoint()
        links = page.locator('a[href]').evaluate_all('(els)=>els.map(e=>e.href)')
        matches = list(dict.fromkeys(u for u in links if urlsplit(u).hostname == 'www.crocs.co.kr' and urlsplit(u).path.endswith('/' + base + '.html')))
        if matches:
            visit(ctx, page, matches[0])
            wait_product(ctx, page, base)
            return page.url
        page.wait_for_timeout(500)
    # 검색이 상품상세로 직접 이동한 경우도 실제 상품정보로 대조한다.
    if urlsplit(page.url).path.endswith('/' + base + '.html'):
        wait_product(ctx, page, base)
        return page.url
    raise ValueError('공식 검색에서 해당 품번을 찾지 못했습니다: ' + base)


def size_table(ctx, page):
    close_overlays(page)
    button = page.get_by_role('button', name='사이즈 표 보기', exact=True)
    # 초기 로딩과 실제 부재를 구분한다.
    try:
        button.first.wait_for(state='visible', timeout=12000)
    except Exception:
        body = page.locator('body').inner_text()
        if 'Loading pricing' in body or 'Loading product details' in body:
            raise ValueError('상품 화면 로딩 미완료: 사이즈표 부재로 처리할 수 없음')
        ctx.log('공식 상품에 사이즈표 버튼 없음: 생략', 'WARNING')
        return ''
    ctx.checkpoint()
    button.first.click(timeout=10000)
    dialog = page.locator('[role="dialog"][aria-labelledby="size-guide-modal-title"]')
    dialog.wait_for(state='visible', timeout=15000)
    dialog.locator('table').first.wait_for(state='attached', timeout=15000)
    source = BeautifulSoup(dialog.inner_html(), 'html.parser')
    output = []
    for table in source.find_all('table'):
        rows = []
        for row in table.find_all('tr'):
            cells = []
            for cell in row.find_all(['th', 'td'], recursive=False):
                attrs = ''.join(' ' + a + '="' + str(int(cell[a])) + '"' for a in ('rowspan', 'colspan') if str(cell.get(a, '')).isdigit())
                cells.append('<' + cell.name + attrs + '>' + html.escape(cell.get_text(' ', strip=True)) + '</' + cell.name + '>')
            if cells:
                rows.append('<tr>' + ''.join(cells) + '</tr>')
        if not rows:
            raise ValueError('사이즈표는 있으나 추출된 행 없음')
        output.append('<table border="1"><tbody>' + ''.join(rows) + '</tbody></table>')
    for p in source.find_all('p'):
        value = p.get_text(' ', strip=True)
        if value and ('팁:' in value or 'mm' in value or '사이즈' in value):
            output.append('<p>' + html.escape(value) + '</p>')
    page.keyboard.press('Escape')
    ctx.checkpoint()
    return '<h3>사이즈표</h3>' + ''.join(output)


def gallery(ctx, product, color, cache, doc=None):
    high_resolution = {}
    renderer = ''
    if doc is not None:
        for node in doc.select('[data-slot="pdp-gallery"] img'):
            # The actual 900px src is sufficient; avoid downloading 1600px PNG
            # for every historical color just because srcset offers it.
            urls = [(900, node.get('src', ''))]
            # srcset 주소는 공홈이 실제 제공한 후보만 사용한다.
            for candidate in node.get('srcset', '').split(', '):
                match = re.fullmatch(r'(https?://\S+)\s+(\d+)w', candidate.strip())
                if match and 500 <= int(match[2]) <= 1000:
                    urls.append((int(match[2]), match[1]))
            for width, candidate in urls:
                path = unquote(urlsplit(candidate).path)
                pose=re.search(r'_ALT(\d+)(?:/|$)',path)
                if not pose or pose[1] not in CATALOG_POSES:
                    continue
                if urlsplit(candidate).hostname == 'media.crocs.com' and '/products/' in path:
                    if width==900 and 't_standard' not in candidate:
                        renderer=candidate.split('/products/',1)[0]+'/products/'
                    identity = path.split('/products/', 1)[1]
                    if identity not in high_resolution or width > high_resolution[identity][0]:
                        high_resolution[identity] = (width, candidate, str(node.get('alt',''))+' '+str(node.get('title','')))
    base=str(product.get('masterProductId') or product.get('id') or '')
    for group in product.get('imageGroups',[]):
        mapped=any(a.get('id')=='color' and any(v.get('value')==color for v in a.get('values',[])) for a in group.get('variationAttributes',[]))
        if not mapped or group.get('viewType')!='standard':
            continue
        for image in group.get('images',[]):
            original=image.get('link','')
            path=unquote(urlsplit(original).path)
            match=re.search(r'/products/('+re.escape(base)+'_'+re.escape(color)+r'_ALT(\d+)/crocs)$',path)
            if not match or match[2] not in CATALOG_POSES or urlsplit(original).hostname!='media.crocs.com':
                continue
            identity=match[1]
            if identity not in high_resolution and renderer:
                high_resolution[identity]=(900,renderer+identity,'공식 카탈로그 촬영 역할: '+CATALOG_POSES[match[2]])
    prepared = []
    for group in product.get('imageGroups', []):
        mapped = any(a.get('id') == 'color' and any(v.get('value') == color for v in a.get('values', [])) for a in group.get('variationAttributes', []))
        if mapped and group.get('viewType') == 'standard':
            for image in group.get('images', []):
                original = image.get('link', '')
                identity = unquote(urlsplit(original).path).split('/products/', 1)[-1]
                if not identity.startswith(base+'_'+color+'_ALT'):
                    continue
                url = high_resolution.get(identity, (0, original))[1]
                signal = ' '.join(str(image.get(k, '')) for k in ('link','alt','title')) + ' ' + high_resolution.get(identity,(0,'',''))[2]
                if identity not in high_resolution:
                    continue
                if not NEGATIVE.search(signal) and not NON_CATALOG.search(signal) and '/products/' in urlsplit(url).path and url not in prepared:
                    prepared.append(url)
    # Bounded parallel public checks. Emit/checkpoint only on the main thread;
    # worker threads never call Hub's stream API or use browser session cookies.
    missing = [url for url in prepared if url not in cache]
    # A pasted adapter can run on an older Hub whose urllib hook prints events
    # without a lock. Never start background network calls on that runtime.
    worker = sys.modules.get('__main__')
    if getattr(worker,'EMIT_LOCK',None) is None:
        for url in missing:
            cache[url]=check_public_image(url,timeout=6,retries=1,checkpoint=ctx.checkpoint)
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {url: pool.submit(check_public_image, url, timeout=6, retries=1) for url in missing}
            for url, future in pending.items():
                while not future.done():
                    ctx.checkpoint()
                    time.sleep(.1)
                cache[url] = future.result()
    candidates, seen = [], set()
    for group in product.get('imageGroups', []):
        mapped = any(a.get('id') == 'color' and any(v.get('value') == color for v in a.get('values', [])) for a in group.get('variationAttributes', []))
        if not mapped or group.get('viewType') != 'standard':
            continue
        for image in group.get('images', []):
            ctx.checkpoint()
            url = image.get('link', '')
            identity = unquote(urlsplit(url).path).split('/products/', 1)[-1]
            if not identity.startswith(base+'_'+color+'_ALT'):
                continue
            if identity not in high_resolution:
                ctx.log('현재 화면 갤러리에 없는 사진 제외: ' + url, 'INFO')
                continue
            url = high_resolution[identity][1]
            signal = ' '.join(str(image.get(k, '')) for k in ('link', 'alt', 'title')) + ' ' + high_resolution[identity][2]
            if NEGATIVE.search(signal) or NON_CATALOG.search(signal) or '/products/' not in urlsplit(url).path:
                ctx.log('갤러리 제외: 상품컷 외 신호 ' + url, 'WARNING')
                continue
            if url not in cache:
                cache[url] = check_public_image(url, checkpoint=ctx.checkpoint)
            result = cache[url]
            if result['status'] != 'accessible':
                ctx.log('이미지 공개 접근 확인 불가: ' + url + ' / ' + ','.join(result['reasons']), 'WARNING')
                continue
            digest = hashlib.sha256(result['data']).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            evidence = f'standard imageGroups 상품컷; 부정 신호 없음; {result["width"]}x{result["height"]}; 실제 파일 규격 검사'
            validation = validate_gallery_image(result['data'], role='product_shot', mapping_verified=True,
                single_shot_verified=True, mapping_evidence='imageGroups.variationAttributes.color=' + color, content_evidence=evidence)
            if not validation['passed']:
                ctx.log('이미지 제외: ' + url + ' / ' + ','.join(validation['reasons']), 'WARNING')
            else:
                ctx.log(f'상품컷 통과 {result["format"]} {result["width"]}x{result["height"]}: ' + url, 'INFO')
            candidates.append({'url': url, 'validation': validation})
    return gallery_slots(candidates)


def detail(ctx, product, images, chart):
    parts = ['<div>']
    parts.extend('<p><img src="' + html.escape(u, quote=True) + '" alt="상품이미지"></p>' for u in dict.fromkeys(images) if u)
    notice = [(label, product.get(key)) for key, label in (('outerMaterial', '겉감'), ('innerMaterial', '안감'), ('importer', '수입자')) if product.get(key)]
    if notice:
        parts.append('<h3>상품정보고시</h3><p>' + '<br>'.join(html.escape(label + ' : ' + str(value)) for label, value in notice) + '</p>')
    else:
        ctx.log('공식 상품상세설명의 고시 텍스트 없음: 생략', 'WARNING')
    parts.extend([chart, '</div>'])
    result = ''.join(parts)
    if len(result) > 32767:
        raise ValueError('상세 HTML 엑셀 셀 한도 초과')
    return result


def crawl(ctx):
    from playwright.sync_api import sync_playwright
    locked=getattr(sys.modules.get('__main__'),'EMIT_LOCK',None) is not None
    ctx.log('크록스 코드 버전: '+ADAPTER_REVISION+' / 이미지 요청: '+('병렬(실행부 잠금 확인)' if locked else '순차(구버전 실행부 호환)'), 'INFO')
    match = re.fullmatch(r'(\d{5,8})(?:[_-]([A-Za-z0-9]{3}))?', str(ctx.product_code).strip())
    if not match:
        ctx.fail_item('품번 형식: 10126 또는 10126_100 / 10126-100')
        raise ValueError('품번 형식 오류')
    base, selected = match[1], (match[2] or '').upper()
    failures = []
    try:
        with sync_playwright() as playwright:
            profile = Path(ctx.output_dir) / 'crocs-browser' / uuid.uuid4().hex
            profile.mkdir(parents=True, exist_ok=True)
            browser = playwright.chromium.launch_persistent_context(str(profile), channel='chrome', headless=True, locale='ko-KR')
            try:
                page = browser.new_page()
                ctx.stage('크록스 공식 상품 조회')
                url = find_product(ctx, page, base)
                _, text, product = wait_product(ctx, page, base)
                colors = verified_colors(product,text,base)
                ctx.log(f'공식 SKU 품번/색상 연결 {len(colors)}개 확인: 화면 미노출·품절 포함', 'INFO')
                if selected:
                    colors = [c for c in colors if c.get('value') == selected]
                if not colors:
                    raise ValueError('공식 색상 없음: ' + selected)
                chart = size_table(ctx, page) if ctx.enabled('detail_page') else ''
                doc=BeautifulSoup('', 'html.parser')
                if any(ctx.enabled(f) for f in ('main_image','additional_images','detail_page')):
                    page.locator('[data-slot="pdp-gallery"] img').first.wait_for(state='visible',timeout=20000)
                    doc=BeautifulSoup(page.content(),'html.parser')
                cache = {}
                for number, color in enumerate(colors, 1):
                    ctx.checkpoint()
                    cid = color.get('value')
                    try:
                        ctx.stage(f'크록스 색상 {number}/{len(colors)}: {color.get("name",cid)}')
                        if not cid or not color.get('name'):
                            raise ValueError('공식 색상명/코드 누락')
                        color_text,current=text,product
                        name = clean_product_name(current['name'], ['Crocs', '크록스'], [base, base + '-' + cid])
                        if not name:
                            raise ValueError('상품명 누락')
                        record = dict(product_code=base, product_name=name, color=color['name'], color_name=color['name'], color_code=cid,
                                      product_url=page.url)
                        variants = []
                        if ctx.enabled('size') or ctx.enabled('price'):
                            variants = variants_for(color_text, base, cid)
                        elif ctx.enabled('regular_price'):
                            try:
                                variants = variants_for(color_text, base, cid)
                            except Exception as exc:
                                ctx.log('정상가 옵션 확인 불가: ' + str(exc), 'WARNING')
                        if ctx.enabled('size'):
                            values = {v.get('variationValues', {}).get('size') for v in variants}
                            record['size'] = ','.join(str(v['name']) for v in variations(current, 'size') if v.get('value') in values)
                            if not record['size']:
                                raise ValueError('색상별 공식 사이즈 옵션 확인 실패')
                        if ctx.enabled('price'):
                            record['price'] = money(ctx, variants, 'price', current.get('currency'))
                        if ctx.enabled('regular_price'):
                            try:
                                record['regular_price'] = money(ctx, variants, 'retailPrice', current.get('currency'))
                            except Exception as exc:
                                record['regular_price'] = None
                                ctx.log('정상가 확인 불가: ' + str(exc), 'WARNING')
                        if ctx.enabled('category'):
                            record['category'] = str(current.get('primaryCategoryId') or '')
                        if any(ctx.enabled(f) for f in ('main_image', 'additional_images', 'detail_page')):
                            # 미노출 색상도 공식 asset ID와 확인된 카탈로그
                            # 촬영 역할만 사용. ALT210/리뷰·UGC는 요청부터 제외.
                            cache = {}
                            main, extras, warning = gallery(ctx, current, cid, cache, doc)
                            if warning:
                                ctx.log(warning, 'WARNING')
                            if ctx.enabled('main_image'):
                                record['main_image'] = main or ''
                            if ctx.enabled('additional_images'):
                                record['additional_images'] = ','.join(extras)
                            if ctx.enabled('detail_page'):
                                record['detail_page'] = detail(ctx, current, [main] + extras, chart)
                        ctx.emit_result(record)
                    except Exception as exc:
                        message = str(cid) + ': ' + str(exc)
                        failures.append(message)
                        ctx.fail_item(message)
            finally:
                browser.close()
    except Exception as exc:
        ctx.log('크록스 오류 위치:\n'+traceback.format_exc(),'ERROR')
        if not failures:
            ctx.fail_item(str(exc))
        raise
    if failures:
        raise RuntimeError('색상 수집 미완료: ' + ' / '.join(failures))
