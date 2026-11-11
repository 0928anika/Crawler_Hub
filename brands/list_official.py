PORTAL_API_VERSION = 1

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from crawler_rules import check_public_image, clean_product_name, gallery_slots, validate_gallery_image

SITE = 'https://idfmall.cafe24.com'
SHOP_HOSTS = {'idfmall.cafe24.com', 'idfmall.co.kr', 'www.idfmall.co.kr'}
POLICY = dict(checked_on='2026-11-11', source='https://marketplace.coupang.com/mba/listing',
              min_side=500, max_bytes=10000000, max_aspect=1.8,
              formats=['JPEG', 'PNG', 'WEBP'], note='정지 WebP는 사용자 환경 정책. 비율은 프로젝트 보조 규칙.')
IMAGE_WORKERS = 4
# 공식 detail.js의 /web/product/index/{품번}_index.json 사용.
# product.cafe24_no -> 실제 상세의 data-code/자체상품코드 대조.
# images.colors[공식코드].name/files로 옵션명과 상품컷을 연결한다.


def fetch(ctx, url):
    url = quote(url, safe=':/?&=%+#')
    if urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in SHOP_HOSTS:
        raise ValueError('IDFmall 공식 주소 확인 실패')
    for attempt in range(2):
        ctx.checkpoint()
        try:
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
                final = response.geturl()
                if urlsplit(final).hostname not in SHOP_HOSTS:
                    raise ValueError('지정한 공식몰 외부로 이동했습니다.')
                data = response.read(6000001)
                if len(data) > 6000000:
                    raise ValueError('공식 응답 크기 초과')
                return data.decode(response.headers.get_content_charset() or 'utf-8-sig'), final
        except HTTPError as exc:
            if exc.code in (400, 401, 403, 404, 410, 429) or attempt:
                raise
        except OSError:
            if attempt:
                raise
        finally:
            ctx.checkpoint()


def field(doc, selector):
    node = doc.select_one(selector)
    return node.get_text(' ', strip=True) if node else ''


def product_page(ctx, index, base):
    product = index.get('product') or {}
    if index.get('meta', {}).get('style_code') != base or (product.get('code') and product['code'] != base):
        raise ValueError('공식 인덱스 품번 불일치')
    number = str(product.get('cafe24_no') or '')
    if number.isdecimal():
        return fetch(ctx, SITE + '/product/detail.html?' + urlencode({'product_no': number}))
    # v1 인덱스에는 product/cafe24_no가 없다. 상품번호를 추측하지 않고
    # 공식 사이트맵의 실제 상품 링크를 찾은 뒤 crawl에서 자체상품코드를 대조한다.
    ctx.stage('구형 상품 인덱스: 공식 사이트맵에서 상품 링크 확인')
    source, _ = fetch(ctx, SITE + '/sitemap.xml')
    root = ET.fromstring(source)
    for node in root.iter():
        if node.tag.rsplit('}', 1)[-1] != 'loc' or not node.text:
            continue
        url = node.text.strip()
        parsed = urlsplit(url)
        path = unquote(parsed.path)
        if (parsed.scheme == 'https' and parsed.hostname in SHOP_HOSTS
                and re.fullmatch(r'/product/[^/]+/\d+/?', path)
                and re.search(r'(?<![A-Za-z0-9])' + re.escape(base) + r'(?![A-Za-z0-9])', path, re.I)):
            return fetch(ctx, url)
    raise ValueError('공식 사이트맵에서 해당 품번의 상품 링크를 확인할 수 없습니다: ' + base)


def normal(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).upper()


def color_dictionary(ctx, doc, final):
    # 이미지 인덱스의 name은 LB=Light Blue처럼 실제 옵션과 다른 경우가 있다.
    # 화면이 실제 사용하는 최신 ColorMapping의 코드 후보로 연결한다.
    for script in doc.select('script[src]'):
        if '/ind-script/optimizer_user.php?' not in script['src']:
            continue
        source, _ = fetch(ctx, urljoin(final, script['src']))
        match = re.search(r'const\s+colorCodeMapAll\s*=\s*\{(.*?)\}\s*;', source, re.S)
        if not match:
            continue
        result = {}
        for row in re.finditer(r'^\s*(?:"([^"]+)"|([A-Z][A-Z0-9_]*))\s*:\s*(\[[^\]]*\])\s*,?\s*$', match[1], re.M):
            values = json.loads(row[3])
            if isinstance(values, list) and all(isinstance(v, str) for v in values):
                result[normal(row[1] or row[2])] = values
        if result:
            return result
    raise ValueError('공식 화면의 색상코드 연결 사전을 확인할 수 없습니다.')


def colors(doc, index, mapping):
    official = index.get('images', {}).get('colors', {})
    labels = list(dict.fromkeys(n['option_value'] for n in doc.select('[option_title="색상"] li[option_value]')))
    if not labels:
        raise ValueError('공식 색상 옵션을 확인할 수 없습니다.')
    result = []
    for label in labels:
        candidates = mapping.get(normal(label), [])
        matches = [(code, value) for code, value in official.items() if code in candidates]
        if not candidates:
            matches = [(code, value) for code, value in official.items()
                       if normal(label) in (normal(code), normal(value.get('name')))]
        if len(matches) != 1:
            raise ValueError('공식 옵션과 색상 이미지 연결 확인 실패: ' + label)
        code, value = matches[0]
        result.append(dict(code=code, label=label, data=value))
    return result


def stocks(doc):
    for node in doc.find_all('script', src=False):
        match = re.search(r"var\s+option_stock_data\s*=\s*'((?:\\.|[^'\\])*)'", node.get_text())
        if match:
            data = json.loads(json.loads('"' + match[1] + '"'))
            if isinstance(data, dict) and data:
                return list(data.values())
    raise ValueError('색상별 공식 옵션 조합 확인 실패')


def color_stocks(rows, label):
    result = []
    for row in rows:
        names, values = row.get('option_name_original'), row.get('option_value_orginal')
        if isinstance(names, list) and isinstance(values, list) and '색상' in names:
            pos = names.index('색상')
            if len(values) > pos and normal(values[pos]) == normal(label):
                result.append(row)
    if not result:
        raise ValueError('해당 색상의 공식 옵션 조합 없음: ' + label)
    return result


def sizes(rows):
    result = []
    for row in rows:
        names, values = row['option_name_original'], row['option_value_orginal']
        if '사이즈' in names and len(values) > names.index('사이즈'):
            value = str(values[names.index('사이즈')])
            if value and value not in result:
                result.append(value)
    return ','.join(result)


def money(value):
    if isinstance(value, bool):
        return None
    match = re.fullmatch(r'\s*(\d+(?:,\d{3})*)(?:\.0+)?\s*원?\s*', str(value or ''))
    return int(match[1].replace(',', '')) or None if match else None


def selling_price(ctx, rows):
    values = {money(row.get('option_price')) for row in rows}
    if len(values) == 1 and None not in values:
        return values.pop()
    ctx.log('색상 내 사이즈별 판매가가 다르거나 공식 가격이 불명확하여 빈칸으로 저장합니다.', 'WARNING')
    return None


def image_url(raw):
    if not raw:
        return ''
    url = urljoin(SITE, raw)
    parsed = urlsplit(url)
    allowed = (parsed.hostname in SHOP_HOSTS and parsed.path.startswith('/web/product/')) or (
        parsed.hostname == 'cafe24img.poxo.com' and parsed.path.startswith('/idfmall/web/product/'))
    return url if parsed.scheme == 'https' and allowed else ''


def photo_urls(color):
    urls = []
    for raw in sorted(color['data'].get('files', []), key=lambda s: [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', s)]):
        url = image_url(raw)
        if url and url not in urls:
            urls.append(url)
    return urls


def public_image(ctx, url, cache, detail=False):
    key = (url, detail)
    if key not in cache:
        shared = cache.get((url, False))
        if detail and shared and shared['status'] == 'accessible':
            cache[key] = shared
        else:
            cache[key] = check_public_image(url, timeout=10, retries=1, checkpoint=ctx.checkpoint,
                                            max_bytes=64*1024*1024 if detail else 10000000)
    result = cache[key]
    if result['status'] != 'accessible':
        message = url + ' / ' + ', '.join(result['reasons'])
        if result['status'] == 'unknown':
            raise ValueError('원본 이미지 조회 미완료: ' + message)
        ctx.log('원본 이미지 제외: ' + message, 'WARNING')
        return None
    return result


def gallery_excluded(url):
    return re.search(r'size|guide|chart|measurement|banner|coupon|logo|swatch|collage|실측|측정|고시', urlsplit(url).path, re.I)


def prefetch_images(ctx, urls, index, cache, wants_gallery, wants_detail):
    # 최대 네 요청만 병렬 실행한다. 결과 저장/로그/색상 출력은 기존 순서를 유지한다.
    jobs = {}
    for url in urls:
        if wants_gallery and not gallery_excluded(url):
            jobs[(url, False)] = 10000000
        elif wants_detail:
            jobs[(url, True)] = 64*1024*1024
    if wants_detail:
        size = index.get('images', {}).get('size_guide', {})
        diagram = image_url(size.get('path')) if size.get('exists') else ''
        if diagram:
            jobs[(diagram, True)] = 64*1024*1024
    pending = [(key, limit) for key, limit in jobs.items() if key not in cache]
    if not pending:
        return

    def download(job):
        key, limit = job
        result = check_public_image(key[0], timeout=10, retries=1, max_bytes=limit, checkpoint=ctx.checkpoint)
        return key, result

    with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
        for key, result in pool.map(download, pending):
            ctx.checkpoint()
            cache[key] = result


def gallery(ctx, urls, base, color, cache):
    accepted, digests = [], set()
    for url in urls:
        ctx.checkpoint()
        if gallery_excluded(url):
            ctx.log('갤러리 제외: 상품컷 이외 경로 ' + url, 'WARNING')
            continue
        result = public_image(ctx, url, cache)
        if not result:
            continue
        validation = validate_gallery_image(
            result['data'], role='product_shot', mapping_verified=True, single_shot_verified=True,
            mapping_evidence=base + ' 공식 images.colors[' + color['code'] + '].files / 옵션 ' + color['label'],
            content_evidence='공식 색상별 개별 상품사진 필드. SIZE/MAIN/후기 분리, 경로 부정 신호 없음. 실제 픽셀·형식·용량·비율 검사.')
        digest = hashlib.sha256(result['data']).hexdigest()
        if validation['passed'] and digest not in digests:
            accepted.append(dict(url=url, validation=validation))
            digests.add(digest)
        elif not validation['passed']:
            ctx.log('갤러리 규격 제외: ' + url + ' / ' + ', '.join(validation['reasons']), 'WARNING')
    return gallery_slots(accepted)


def notice(doc):
    rows = []
    for number in (6, 7, 8, 9, 10, 11, 12, 14):
        row = doc.select_one('.price-info-area .custom_option' + str(number) + '_css')
        if row and row.find('th') and row.find('td'):
            label, value = row.th.get_text(' ', strip=True), row.td.get_text(' ', strip=True)
            if label and value:
                rows.append('<tr><th>' + html.escape(label) + '</th><td>' + html.escape(value) + '</td></tr>')
    return '<h3>상품정보고시</h3><table border="1"><tbody>' + ''.join(rows) + '</tbody></table>' if rows else ''


def detail(ctx, doc, index, urls, cache):
    parts = []
    size = index.get('images', {}).get('size_guide', {})
    diagram = image_url(size.get('path')) if size.get('exists') else ''
    for url in dict.fromkeys(urls + ([diagram] if diagram else [])):
        if public_image(ctx, url, cache, detail=True):
            parts.append('<p><img src="' + html.escape(url, quote=True) + '" alt="상품 상세" style="max-width:100%;height:auto"></p>')
    if not diagram:
        ctx.log('공식 실측사이즈 이미지가 없어 생략합니다.', 'WARNING')
    info = notice(doc)
    if not info:
        ctx.log('공식 상품정보고시가 없어 생략합니다.', 'WARNING')
    parts.append(info)
    result = '<div>' + ''.join(parts) + '</div>' if any(parts) else ''
    if len(result.encode('utf-16-le')) // 2 > 32767:
        raise ValueError('상세 HTML Excel 셀 한도 초과')
    return result


def crawl(ctx):
    raw = normal(ctx.product_code)
    match = re.fullmatch(r'([A-Z0-9]{11})(?:[_-]([A-Z0-9]{2,3}))?', raw)
    if not match:
        raise ValueError('리스트 기본 품번 11자리 또는 기본품번_공식색상코드를 입력하세요.')
    base, requested = match.groups()
    ctx.stage('리스트 공식 품번 조회: ' + base)
    try:
        source, _ = fetch(ctx, SITE + '/web/product/index/' + base + '_index.json')
        index = json.loads(source)
    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError('해당 품번의 공식 상품 인덱스가 없습니다: ' + base) from exc
        raise
    source, final = product_page(ctx, index, base)
    doc = BeautifulSoup(source, 'html.parser')
    identity = doc.select_one('.detailArea[data-code]')
    if not identity or normal(identity['data-code']) != base or normal(field(doc, '.ma_product_code_css td')) != base:
        raise ValueError('실제 상품 상세의 공식 품번 불일치')
    title = field(doc, '.headingArea h1')
    title = re.sub(r'[_\s-]+' + re.escape(base) + r'\s*$', '', title, flags=re.I)
    title = clean_product_name(title, ['리스트', 'LIST'], [base])
    if not title:
        raise ValueError('공식 상품명 확인 실패')
    all_colors = colors(doc, index, color_dictionary(ctx, doc, final))
    chosen = [color for color in all_colors if not requested or color['code'] == requested]
    if not chosen:
        raise ValueError('해당 공식 색상코드가 없습니다: ' + str(requested))
    option_rows = stocks(doc) if ctx.enabled('size') or ctx.enabled('price') else []
    common = dict(product_code=base, product_name=title, product_url=final)
    if ctx.enabled('regular_price'):
        node = doc.select_one('.infoArea[data-custom]')
        common['regular_price'] = money(node.get('data-custom')) if node else None
        if common['regular_price'] is None:
            ctx.log('공식 독립 정상가가 없어 빈칸으로 저장합니다.', 'WARNING')
    if ctx.enabled('category'):
        common['category'] = ' > '.join(n.get_text(' ', strip=True) for n in doc.select('.detail-navigation li:not(.displaynone) a') if n.get_text(strip=True))
    failures, cache = [], {}
    for color in chosen:
        try:
            ctx.stage('리스트 색상 수집: ' + color['label'])
            record = dict(common, color=color['label'], color_name=color['label'], color_code=color['code'])
            rows = color_stocks(option_rows, color['label']) if option_rows else []
            if ctx.enabled('size'):
                record['size'] = sizes(rows)
            if ctx.enabled('price'):
                record['price'] = selling_price(ctx, rows)
            wants_gallery = ctx.enabled('main_image') or ctx.enabled('additional_images')
            urls = photo_urls(color) if wants_gallery or ctx.enabled('detail_page') else []
            if wants_gallery or ctx.enabled('detail_page'):
                prefetch_images(ctx, urls, index, cache, wants_gallery, ctx.enabled('detail_page'))
            if wants_gallery:
                main, extras, warning = gallery(ctx, urls, base, color, cache)
                if warning:
                    ctx.log(warning, 'WARNING')
                if ctx.enabled('main_image'):
                    record['main_image'] = main or ''
                if ctx.enabled('additional_images'):
                    record['additional_images'] = ','.join(extras)
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail(ctx, doc, index, urls, cache)
            ctx.emit_result(record)
        except (OSError, ValueError, KeyError) as exc:
            message = color['label'] + ' / ' + str(exc)
            ctx.fail_item(message)
            failures.append(message)
    if failures:
        raise RuntimeError('일부 색상 수집 미완료: ' + ' / '.join(failures))
