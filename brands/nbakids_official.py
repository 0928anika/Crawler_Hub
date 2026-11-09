PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

import hashlib
import html
import io
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, quote
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

SITE = 'https://www.style24.com'
BRAND_GROUP = '2143'
CRAWLER_REVISION = '2026-11-09-english-color-v3'
POLICY = {'checked': '2026-11-08', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP·비율 1.8은 프로젝트 정책. 전체 등록 승인은 미검증.'}
# 필수 품번은 공개 ProductGroup.productGroupID로 대조한다.
# 색상별 공식 상품 링크와 그 페이지의 goodsDetailImageWeb 이미지로 매핑한다.
# 사이즈 팝업은 사이트 UI와 동일한 공개 POST이며 상세 선택 시만 요청한다.


class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


def fetch(ctx, url, limit=6000000, post=False):
    url = quote(url, safe=':/?&=%+#')
    if urlsplit(url).scheme not in ('http', 'https'):
        raise CrawlError('HTTP 이미지/페이지 주소가 아님: ' + url)
    for attempt in range(3):
        ctx.checkpoint()
        failure = None
        try:
            with build_opener().open(Request(url, data=b'' if post else None, headers={'User-Agent': 'Mozilla/5.0'}, method='POST' if post else 'GET'), timeout=20) as r:
                final = r.geturl()
                data = r.read(limit + 1)
                content_type = r.headers.get('Content-Type', '')
                if len(data) > limit:
                    raise CrawlError('응답 용량 초과: ' + url)
                return data, final, content_type
        except HTTPError as e:
            failure = f'HTTP {e.code}: {url}'
            if e.code in (400, 401, 403, 404, 410, 429):
                raise CrawlError(failure) from e
        except (URLError, OSError, TimeoutError) as e:
            failure = f'통신 오류: {url}: {e}'
        finally:
            ctx.checkpoint()
        if attempt == 2:
            raise CrawlError(failure)
        ctx.log(f'{attempt + 1}/2 재시도: ' + failure, 'WARNING')
    raise CrawlError('요청 실패: ' + url)


def content_signal(im):
    # OCR/의미 인식이 아닌 보수적 보조 검사. 작은 글자 성분이 지배적인
    # 라벨/고시와 전폭 흰 구분선으로 분리된 합성 사진을 제외한다.
    im = im.convert('RGB')
    im.thumbnail((480, 480))
    w, h = im.size
    px = list(im.getdata())
    dark = {i for i, rgb in enumerate(px) if max(rgb) < 170}
    total_dark = len(dark)
    small_count = small_area = 0
    while dark:
        start = dark.pop()
        stack, area = [start], 0
        x0 = x1 = start % w
        y0 = y1 = start // w
        while stack:
            i = stack.pop()
            x, y = i % w, i // w
            area += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                j = ny * w + nx
                if 0 <= nx < w and 0 <= ny < h and j in dark:
                    dark.remove(j)
                    stack.append(j)
        if 2 <= area <= 120 and x1-x0 < 32 and y1-y0 < 20:
            small_count += 1
            small_area += area
    if small_count >= 150 and 0 < total_dark < w*h*.16 and small_area > total_dark*.35:
        return '작은 글자 성분이 지배적인 라벨/고시/측정 안내 의심'
    # 밝은 배경 전체가 아닌 내부 구분선 양쪽에 독립 사진 영역이 있는지 확인.
    gray = im.convert('L')
    gray.thumbnail((120, 120))
    for g in (gray, gray.transpose(Image.Transpose.ROTATE_90)):
        a, b = g.size
        values = list(g.getdata())
        for y in range(b//3, b*2//3):
            if sum(v >= 252 for v in values[y*a:(y+1)*a]) < a*.99:
                continue
            upper, lower = values[max(0,y-b//4)*a:y*a], values[(y+1)*a:min(b,y+b//4)*a]
            if upper and lower and sum(v < 220 for v in upper)/len(upper) > .18 and sum(v < 220 for v in lower)/len(lower) > .18:
                return '전폭 흰 구분선 양쪽에 독립 사진 영역: 콜라주 의심'
    return ''


def image_info(ctx, url, cache):
    if url in cache:
        return cache[url]
    try:
        data, final, content_type = fetch(ctx, url, POLICY['max_bytes'])
        with Image.open(io.BytesIO(data)) as im:
            fmt, w, h = im.format, im.width, im.height
            animated = getattr(im, 'is_animated', False)
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            signal = content_signal(im)
        ctx.log(f'이미지 {fmt} {w}x{h}, {len(data)}bytes, animated={animated}, HTTP={content_type}: {url}', 'INFO')
        info = (fmt, w, h, animated, hashlib.sha256(data).hexdigest(), signal)
    except (CrawlError, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        ctx.log(f'이미지 제외: {url}: {e}', 'WARNING')
        info = None
    cache[url] = info
    return info


def page(ctx, url, post=False):
    data, final, _ = fetch(ctx, url, post=post)
    if urlsplit(final).hostname != 'www.style24.com':
        raise CrawlError('지정 공식몰 밖으로 이동: ' + final)
    return BeautifulSoup(data.decode('utf-8-sig'), 'html.parser'), final


def structured(doc, kind):
    for script in doc.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text())
        except ValueError as error:
            raise CrawlError('공식 구조화 데이터 JSON 오류') from error
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and item.get('@type') == kind:
                return item
    raise CrawlError('공식 ' + kind + ' 정보 없음')


def identity(doc, final):
    group = structured(doc, 'ProductGroup')
    brand_links = doc.select('.brand_box a[href*="brandGroupNo="]')
    if not any(parse_qs(urlsplit(a['href']).query).get('brandGroupNo') == [BRAND_GROUP] for a in brand_links):
        raise CrawlError('NBA KIDS 브랜드관 소속 확인 실패: ' + final)
    code = str(group.get('productGroupID') or '').strip().upper()
    color_match = re.search(r'params\.colorCd\s*=\s*"([A-Za-z0-9]+)"', str(doc))
    code_node = doc.select_one('.codenumber')
    if not code or code_node is None or code_node.get_text(strip=True).upper() != code or not color_match:
        raise CrawlError('공식 품번/현재 색상 식별 불일치')
    return group, code, color_match[1].upper()


def color_links(doc, final, base):
    result = []
    for anchor in doc.select('.opt_color a[href]'):
        url = urljoin(final, anchor['href'])
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        if parsed.hostname != 'www.style24.com' or parsed.path != '/goods/detail/form':
            continue
        if params.get('goodsCd') != [base] or not params.get('colorCd'):
            continue
        color = params['colorCd'][0].upper()
        if not any(existing_color == color for existing_color, _ in result):
            result.append((color, url))
    if not result:
        raise CrawlError('공식 색상별 상품 링크 없음')
    return result


def name(group, base):
    value = str(group.get('name') or '')
    for token in ('NBA KIDS', 'NBA키즈', 'NBA 키즈', '엔비에이 키즈', 'NBA', base):
        value = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])', '', value, flags=re.I)
    value = re.sub(r'\(\s*\)|\[\s*\]', '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    if not value:
        raise CrawlError('실제 상품명 없음')
    return value


def english_color(doc, final, base, color):
    # 컬러 안내의 이미지와 공식 옵션 링크의 이미지를 정확히 연결한다.
    # 고시의 색상 목록 순서나 약어의 임의 영문 확장으로 매핑하지 않는다.
    paths = set()
    for anchor in doc.select('.opt_color a[href]'):
        query = parse_qs(urlsplit(urljoin(final, anchor['href'])).query)
        if query.get('goodsCd') != [base] or query.get('colorCd') != [color]:
            continue
        for image in anchor.select('img[src]'):
            paths.add(urlsplit(urljoin(final, image['src'])).path.split('/gio/')[0])
    names = set()
    for item in doc.select('.pd-core__color-item'):
        label = item.select_one('.pd-core__color-name')
        if label is None:
            continue
        if any(urlsplit(urljoin(final, image['src'])).path.split('/gio/')[0] in paths for image in item.select('img[src]')):
            value = label.get_text(' ', strip=True)
            if value and re.search(r'[A-Za-z]', value) and not re.search(r'[가-힣]', value):
                names.add(value)
    if len(names) != 1:
        raise CrawlError('공식 컬러 안내에서 현재 색상의 영문 원문을 고유하게 확인할 수 없습니다: ' + color)
    return names.pop()


def image_urls(doc, selector, final):
    return list(dict.fromkeys(urljoin(final, image['src']) for image in doc.select(selector) if image.get('src')))


def gallery(ctx, doc, final, cache):
    accepted, hashes = [], set()
    for url in image_urls(doc, 'img[data-thumbnail="goodsDetailImageWeb"]', final):
        ctx.checkpoint()
        info = image_info(ctx, url, cache)
        if not info:
            continue
        fmt, width, height, animated, digest, signal = info
        if fmt not in POLICY['formats'] or animated or min(width,height) < 500 or max(width,height)/min(width,height) > 1.8 or signal:
            ctx.log('갤러리 제외: ' + (signal or '파일/픽셀/비율 기준') + ': ' + url, 'WARNING')
            continue
        if digest not in hashes:
            hashes.add(digest)
            accepted.append(url)
    if not accepted:
        ctx.log('통과한 갤러리 이미지 없음', 'WARNING')
        return '', ''
    extras = accepted[1:]
    if not extras:
        extras = accepted
        ctx.log('대표 한 장만 통과하여 추가 5슬롯에도 재사용', 'WARNING')
    return accepted[0], ','.join(extras[index % len(extras)] for index in range(5))


def cell_html(cell):
    attributes = ''.join(' ' + key + '="' + str(cell[key]) + '"' for key in ('rowspan','colspan') if str(cell.get(key,'')).isdigit() and 1 < int(cell[key]) <= 100)
    return '<' + cell.name + attributes + '>' + html.escape(cell.get_text(' ', strip=True)) + '</' + cell.name + '>'


def table_html(table):
    rows = []
    for row in table.select('tr'):
        cells = row.find_all(['th','td'], recursive=False)
        if cells:
            rows.append('<tr>' + ''.join(cell_html(cell) for cell in cells) + '</tr>')
    if not rows:
        raise CrawlError('원본 표에서 행을 읽지 못했습니다.')
    return '<table><tbody>' + ''.join(rows) + '</tbody></table>'


def notice(doc):
    section = doc.select_one('.required_box .area_infotbl')
    if section is None:
        return ''
    rows = []
    for row in section.select('table tr'):
        label, value = row.find('th', recursive=False), row.find('td', recursive=False)
        if label is not None and value is not None:
            rows.append((label.get_text(' ',strip=True), value.get_text(' ',strip=True)))
    if not rows or not any(value for _, value in rows):
        raise CrawlError('상품고시정보가 있지만 실제 항목/값을 추출하지 못했습니다.')
    return '<h3>상품고시정보</h3><table><tbody>' + ''.join('<tr><th>' + html.escape(key) + '</th><td>' + html.escape(value) + '</td></tr>' for key,value in rows) + '</tbody></table>'


def size_family(category):
    if re.search(r'우주복|바디수트|롬퍼', category):
        return None, '의류/언더'
    if re.search(r'팬츠|바지|스커트|쇼츠|레깅스', category):
        return '하의', '의류/언더'
    if re.search(r'티셔츠|맨투맨|상의|점퍼|자켓|재킷|코트|후드|니트|셔츠|베스트|원피스', category):
        return '상의', '의류/언더'
    if re.search(r'신발|운동화|샌들|슬리퍼|스니커즈', category):
        return '신발', '신발'
    if re.search(r'모자|비니|캡', category):
        return None, '모자'
    # 분류가 낯설어도 상품에 직접 연결된 실측 표는 보존한다.
    return None, None


def size_info(ctx, doc, final, base, color, cache):
    button = doc.select_one('#btn_infoSize_pop')
    if button is None:
        ctx.log('공식 사이즈정보 버튼 없음', 'WARNING')
        return ''
    breadcrumb = structured(doc, 'BreadcrumbList')
    categories = ' > '.join(str(item.get('name') or '') for item in breadcrumb.get('itemListElement', [])[:-1])
    drawing_label, table_label = size_family(categories)
    url = SITE + '/goods/sizeInfo/layer/' + quote(base) + '/' + quote(color) + '/null'
    popup, popup_final = page(ctx, url, post=True)
    parts = ['<h3>사이즈정보</h3>']
    tabs = popup.select('.tab_nav li')
    panels = popup.select('.tab_cont_wrap > .tab_cont')
    real_index = next((index for index, tab in enumerate(tabs) if '실측' in tab.get_text()), None)
    standard_index = next((index for index, tab in enumerate(tabs) if '표준' in tab.get_text()), None)
    selected_panel = popup
    if real_index is not None:
        if real_index >= len(panels):
            raise CrawlError('실측 사이즈 탭과 본문을 연결하지 못했습니다.')
        selected_panel = panels[real_index]
        parts.append('<p>실측 사이즈</p>')
    elif standard_index is not None:
        if standard_index >= len(panels):
            raise CrawlError('표준 사이즈 탭과 본문을 연결하지 못했습니다.')
        selected_panel = panels[standard_index]
        parts.append('<p>표준 사이즈</p>')
    # 공식 측정 가이드 중 해당 품목 그림만 선택하고, 표보다 앞에 배치한다.
    drawings = selected_panel.select('img[src]') if real_index is not None else []
    if not drawings:
        drawings = [image for image in popup.select('.img_sizeinfo img[src]') if drawing_label and drawing_label in str(image.get('alt') or '')]
    for image in drawings:
        image_url = urljoin(popup_final, image['src'])
        if not image_info(ctx, image_url, cache):
            raise CrawlError('사이즈정보 그림 공개 접근 실패: ' + image_url)
        parts.append('<img src="' + html.escape(image_url,quote=True) + '">')
    if not drawings:
        ctx.log('해당 품목 측정 그림 없음. 존재하는 공식 표만 유지합니다.', 'WARNING')
    selected_boxes = []
    for box in selected_panel.select('.size_tbl_box'):
        heading = box.find(['h6','h5','h4'])
        if real_index is not None or (heading and heading.get_text(strip=True) == table_label):
            selected_boxes.append(box)
    if not selected_boxes:
        if real_index is not None and selected_panel.select('table'):
            for table in selected_panel.select('table'):
                parts.append(table_html(table))
        elif selected_panel.select('table'):
            ctx.log('해당 품목의 표준 사이즈 표를 특정할 수 없어 공통 표는 생략합니다.', 'WARNING')
        else:
            ctx.log('공식 사이즈 표 없음', 'WARNING')
    for box in selected_boxes:
        heading = box.find(['h6','h5','h4'])
        unit = box.select_one('.size_unit')
        if heading:
            parts.append('<p>' + html.escape(heading.get_text(' ',strip=True)) + '</p>')
        if unit:
            parts.append('<p>' + html.escape(unit.get_text(' ',strip=True)) + '</p>')
        left, right = box.select_one('.head_t table'), box.select_one('.body_t table')
        if left is not None and right is not None:
            labels, values = left.select('tr'), right.select('tr')
            if len(labels) != len(values) or not labels:
                raise CrawlError('사이즈 표의 항목명/값 행수가 다릅니다.')
            rows = []
            for label_row, value_row in zip(labels, values):
                cells = label_row.find_all(['th','td'],recursive=False) + value_row.find_all(['th','td'],recursive=False)
                rows.append('<tr>' + ''.join(cell_html(cell) for cell in cells) + '</tr>')
            parts.append('<table><tbody>' + ''.join(rows) + '</tbody></table>')
        else:
            for table in box.select('table'):
                parts.append(table_html(table))
    for item in selected_panel.select('.size_footer .info_txt li'):
        parts.append('<p>' + html.escape(item.get_text(' ',strip=True)) + '</p>')
    return ''.join(parts)


def detail(ctx, doc, final, base, color, cache):
    parts = []
    selector = '.pd-core__product-view img, .pd-core__detail img'
    for url in image_urls(doc, selector, final):
        ctx.checkpoint()
        if not image_info(ctx, url, cache):
            raise CrawlError('상품 상세 이미지 공개 접근 실패: ' + url)
        parts.append('<img src="' + html.escape(url,quote=True) + '">')
    parts.append(size_info(ctx, doc, final, base, color, cache))
    information = notice(doc)
    if not information:
        ctx.log('공식 상품고시정보 없음', 'WARNING')
    parts.append(information)
    markup = ''.join(parts)
    if len(markup.encode('utf-16-le')) // 2 > 32767:
        raise CrawlError('상세 HTML Excel 셀 32767자 한도 초과')
    return markup


def crawl(ctx):
    ctx.log('NBA키즈 크롤러 ' + CRAWLER_REVISION, 'INFO')
    raw = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]+(?:[_-][A-Z0-9]+)*', raw):
        ctx.fail_item('품번 입력 형식을 확인하세요.')
        raise CrawlError('품번 형식 오류')
    candidates = [(raw, None)]
    split = re.fullmatch(r'(.+)[_-]([A-Z0-9]+)', raw)
    if split:
        candidates.append((split[1], split[2]))
    seed = None
    for proposed, requested in candidates:
        ctx.stage('공식 품번 조회: ' + proposed)
        try:
            doc, final = page(ctx, SITE + '/goods/detail/form?' + urlencode({'goodsCd':proposed}))
            group, base, current = identity(doc, final)
            if base != proposed:
                raise CrawlError('조회 품번과 공식 상품 ID 불일치')
            seed = doc, final, base, current, requested
            break
        except CrawlError as error:
            ctx.log(str(error), 'WARNING')
    if seed is None:
        ctx.fail_item('정확한 공식 품번을 조회할 수 없습니다: ' + raw)
        raise CrawlError('공식 품번 조회 실패')
    doc, final, base, current, requested = seed
    links = color_links(doc, final, base)
    errors, emitted, cache = [], set(), {}
    for color, url in links:
        ctx.checkpoint()
        if requested and requested != color:
            continue
        try:
            ctx.stage('색상 수집: ' + color)
            if color == current:
                color_doc, color_final = doc, final
            else:
                color_doc, color_final = page(ctx, url)
            group, found_base, found_color = identity(color_doc, color_final)
            if found_base != base or found_color != color:
                raise CrawlError('공식 품번/색상 응답 불일치')
            canonical = color_doc.select_one('link[rel="canonical"][href]')
            if canonical:
                canonical_url = urljoin(color_final, canonical['href'])
                parsed = urlsplit(canonical_url)
                query = parse_qs(parsed.query)
                if parsed.hostname == 'www.style24.com' and query.get('goodsCd') == [base] and query.get('colorCd') == [color]:
                    color_final = canonical_url
            label = color_doc.select_one('.opt_color .color')
            korean_name = label.get_text(' ',strip=True) if label else ''
            color_name = english_color(color_doc, color_final, base, color)
            record = {'product_code':base,'product_name':name(group,base),'color':color_name,'color_name':color_name,'color_code':color,'product_url':color_final,'한글명':korean_name}
            if ctx.enabled('size'):
                sizes = list(dict.fromkeys(item.get('optcd2','') for item in color_doc.select('input[name="opt"][optcd2]')))
                record['size'] = ','.join(size for size in sizes if size)
                if not record['size']:
                    ctx.log('공식 사이즈 옵션 없음', 'WARNING')
            if ctx.enabled('price'):
                price_node = color_doc.select_one('.price_blk .sale_price em')
                price_text = price_node.get_text(strip=True).replace(',','') if price_node else ''
                if not price_text.isdigit():
                    raise CrawlError('일반 KRW 판매가를 확인하지 못했습니다.')
                additions = {str(item.get('addprice','0')) for item in color_doc.select('input[name="opt"]')}
                record['price'] = int(price_text) if additions <= {'0','0.0','0.00'} else None
                if record['price'] is None:
                    ctx.log('옵션 추가금액이 달라 공통 가격은 빈 값', 'WARNING')
            if ctx.enabled('category'):
                path = structured(color_doc,'BreadcrumbList')
                record['category'] = ' > '.join(str(item.get('name') or '') for item in path.get('itemListElement',[])[:-1])
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main, extras = gallery(ctx,color_doc,color_final,cache)
                if ctx.enabled('main_image'):
                    record['main_image'] = main
                if ctx.enabled('additional_images'):
                    record['additional_images'] = extras
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail(ctx,color_doc,color_final,base,color,cache)
            collect_regular_price(ctx, record, color_doc, 'style24')
            ctx.emit_result(record)
            emitted.add(color)
        except CrawlError as error:
            message = base + '_' + color + ': ' + str(error)
            ctx.fail_item(message)
            errors.append(message)
    if not emitted:
        message = '수집 가능한 공식 상품/지정 색상 없음: ' + raw
        ctx.fail_item(message)
        errors.append(message)
    if errors:
        raise CrawlError(' / '.join(errors))


