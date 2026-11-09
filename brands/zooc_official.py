PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices
import re
import io
import json
import hashlib
from html import escape
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qs, unquote
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

SITE = "https://www.daehyuninside.com"
CRAWLER_REVISION = '2026-11-08-full-option-color-v2'
POLICY = {"checked": "2026-11-08", "source": "https://marketplace.coupang.com/mba/listing",
          "min_side": 500, "max_bytes": 10000000, "max_aspect": 1.8,
          "formats": ("JPEG", "PNG", "WEBP"),
          "note": "WING 웹/엑셀 공식 안내 + 사용자 정지 WebP 허용. 비율은 프로젝트 정책."}
# 실사이트 조사: Z264MSF501 MG/BK. 실측 표와 고시 h3/p를 별도로 재구성.
# 실측 표 원본 thead에 tr이 없으므로 직접 th도 보존한다.
# Hub 최종 정제/재실행 교체 및 실제 WING 등록은 미검증.

class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


def fetch(ctx, url, limit=6000000):
    url = quote(url, safe=':/?&=%+#')
    if urlsplit(url).scheme not in ('http', 'https'):
        raise CrawlError('HTTP 이미지/페이지 주소가 아님: ' + url)
    for attempt in range(3):
        ctx.checkpoint()
        failure = None
        try:
            with build_opener().open(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as r:
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


def image_info(ctx, url, cache, gallery=False):
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
            signal = content_signal(im) if gallery else ''
        ctx.log(f'이미지 {fmt} {w}x{h}, {len(data)}bytes, animated={animated}, HTTP={content_type}: {url}', 'INFO')
        info = (fmt, w, h, animated, hashlib.sha256(data).hexdigest(), signal)
    except (CrawlError, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        ctx.log(f'이미지 제외: {url}: {e}', 'WARNING')
        info = None
    cache[url] = info
    return info


def text(node):
    return ' '.join(node.stripped_strings)


def page(ctx, url):
    data, final, _ = fetch(ctx, url)
    if urlsplit(final).hostname != 'www.daehyuninside.com':
        raise CrawlError('공식몰 외부 이동: ' + final)
    return BeautifulSoup(data.decode('utf-8-sig'), 'html.parser'), final


def product_link(href, parent):
    link = urljoin(parent, href)
    p = urlsplit(link)
    return link if p.hostname == 'www.daehyuninside.com' and p.path in ('/shop/detail.php', '/zooc/shop/detail.php') and re.fullmatch(r'[A-Fa-f0-9]{32}', parse_qs(p.query).get('pno', [''])[0]) else ''


def search(ctx, value):
    queue = [SITE + '/shop/search_result.php?' + urlencode({'search_str': value})]
    seen, links = set(), []
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        if len(seen) >= 30:
            raise CrawlError('검색 30페이지 한도 초과')
        seen.add(url)
        doc, final = page(ctx, url)
        for a in doc.select('.prdimg a[href], .ref_color a[href]'):
            link = product_link(a['href'], final)
            if link and link not in links:
                links.append(link)
        for a in doc.select('a[href]'):
            link = urljoin(final, a['href'])
            p = urlsplit(link)
            q = parse_qs(p.query)
            if p.hostname == 'www.daehyuninside.com' and p.path == '/shop/search_result.php' and q.get('search_str') == [value] and 'page' in q and link not in seen and link not in queue:
                queue.append(link)
    return links


def identity(doc):
    # 일반 상품은 detail_brand=ZOOC. 아울렛은 OUTLET / outlet_brand=ZOOC.
    label = doc.select_one('.info_title .detail_brand')
    visible_brand = text(label).strip().upper() if label else ''
    categories = [n.get('value', '').strip().upper() for n in doc.select('input[name="category1"]')]
    outlet = doc.select_one('.info_title .outlet_brand')
    outlet_brand = text(outlet).strip().upper() if outlet else ''
    category2 = [n.get('value', '').strip().upper() for n in doc.select('input[name="category2"]')]
    if visible_brand:
        belongs = visible_brand == 'ZOOC' or (visible_brand == 'OUTLET' and outlet_brand == 'ZOOC')
    else:
        belongs = 'ZOOC' in categories or ('OUTLET' in categories and 'ZOOC' in category2)
    if not belongs:
        raise CrawlError('주크 상품 소속 확인 실패: detail_brand=' + repr(visible_brand) + ', outlet_brand=' + repr(outlet_brand) + ', category1=' + repr(categories) + ', category2=' + repr(category2))
    node = doc.select_one('.info_title p.prc')
    full = text(node).upper() if node else ''
    match = re.fullmatch(r'([A-Z0-9]+(?:-[A-Z0-9]+)*)_([A-Z0-9]+)', full)
    if not match:
        raise CrawlError('공식 품번·색상 식별 불가: ' + full)
    return match[1], match[2], full


def clean_name(name, codes):
    for token in sorted(codes + ['ZOOC', '주크'], key=len, reverse=True):
        name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip(' _-')
    if not name:
        raise CrawlError('상품명 없음')
    return name


def option_color(doc, displayed):
    """Preserve the complete official option code, including trailing digits."""
    options = []
    for option in doc.select('select[name="option1"] option[value]'):
        if not option.get('value', '').strip():
            continue
        match = re.fullmatch(r'\s*(.*?)\s*\(([A-Za-z0-9]+)\)\s*', text(option))
        if not match:
            raise CrawlError('공식 컬러 옵션 코드 해석 불가: ' + text(option))
        candidate = (re.sub(r'\s+', ' ', match[1]).strip(), match[2].upper())
        if candidate not in options:
            options.append(candidate)
    if len(options) == 1:
        return options[0]
    chip = doc.select_one('.info_title .ref_color li:first-child span')
    label = text(chip) if chip else ''
    normalized = lambda s: re.sub(r'\s+', '', s).casefold()
    matches = [c for c in options if c[1] == displayed or (label and normalized(c[0]) == normalized(label))]
    if len(matches) == 1:
        return matches[0]
    if not options:
        return label, displayed
    raise CrawlError('여러 공식 컬러 옵션 중 현재 상품 페이지의 색상 연결을 확인할 수 없습니다.')


def image_url(img, url):
    raw = img.get('src', '')
    return quote(urljoin(url, raw), safe=':/?&=%+#') if raw else ''


def gallery(ctx, doc, url, cache):
    accepted, hashes = [], set()
    for img in doc.select('#product_add_image2_list img'):
        ctx.checkpoint()
        link = image_url(img, url)
        if urlsplit(link).hostname != 'daehyuninside.wisacdn.com' or '/_data/attach/' not in urlsplit(link).path:
            ctx.log('갤러리 역할/공식 CDN 연결 미확인: ' + link, 'WARNING')
            continue
        info = image_info(ctx, link, cache, gallery=True)
        if not info:
            continue
        fmt, w, h, animated, digest, signal = info
        reasons = []
        if fmt not in POLICY['formats'] or animated:
            reasons.append('미지원 형식/애니메이션')
        if min(w, h) < 500 or max(w, h) / min(w, h) > 1.8:
            reasons.append('작은 이미지/통짜 의심 비율')
        if re.search('size|measure|collage|banner|chart|notice|wash|logo', img.get('alt', '') + ' ' + urlsplit(link).path, re.I):
            reasons.append('측정/고시/합성/공통안내 역할 신호')
        if signal:
            reasons.append(signal)
        if reasons:
            ctx.log('갤러리 제외: ' + ', '.join(reasons) + ': ' + link, 'WARNING')
        elif digest not in hashes:
            hashes.add(digest)
            accepted.append(link)
            ctx.log('채택: 색상별 공식 pno 갤러리, 파일 규격/역할/부정 신호 검사 통과: ' + link, 'INFO')
    if not accepted:
        ctx.log('유효 갤러리 0장: 이미지 검수 필요', 'WARNING')
        return '', ''
    extras = accepted[1:] or accepted[:1]
    if len(accepted) == 1:
        ctx.log('유효컷 1장: 추가에도 재사용', 'WARNING')
    return accepted[0], ','.join(extras[i % len(extras)] for i in range(5))


def cell_html(cell):
    attrs = ''.join(' ' + k + '="' + str(int(cell[k])) + '"' for k in ('rowspan', 'colspan') if str(cell.get(k, '')).isdigit())
    return '<' + cell.name + attrs + '>' + escape(text(cell)) + '</' + cell.name + '>'


def size_table(table):
    rows = []
    # 원본은 thead 바로 밑에 th가 있으며 tr이 없는 경우도 있다.
    for section in table.find_all(['thead', 'tbody', 'tfoot'], recursive=False):
        direct = section.find_all(['th', 'td'], recursive=False)
        if direct:
            rows.append('<tr>' + ''.join(cell_html(c) for c in direct) + '</tr>')
        for tr in section.find_all('tr', recursive=False):
            rows.append('<tr>' + ''.join(cell_html(c) for c in tr.find_all(['th', 'td'], recursive=False)) + '</tr>')
    for tr in table.find_all('tr', recursive=False):
        rows.append('<tr>' + ''.join(cell_html(c) for c in tr.find_all(['th', 'td'], recursive=False)) + '</tr>')
    if not rows:
        raise CrawlError('실측 표 존재하나 행 추출 실패')
    return '<table border="1" style="width:100%;border-collapse:collapse"><tbody>' + ''.join(rows) + '</tbody></table>'


def notice_rows(section):
    root = section.select_one('.prd_info')
    if root is None:
        content = section.select_one('.cnt')
        if content is None or not text(content):
            return []
        raise CrawlError('상품정보고시 내부 .prd_info 구조 변경')
    if not text(root) and not root.find('img'):
        return []
    rows = []
    for heading in root.find_all('h3', recursive=False):
        values, image_only = [], False
        for node in heading.next_siblings:
            if getattr(node, 'name', None) == 'h3':
                break
            if getattr(node, 'name', None):
                value = text(node)
                if value:
                    values.append(value)
                if node.find('img'):
                    image_only = True
            elif str(node).strip():
                values.append(str(node).strip())
        rows.append((text(heading), ' '.join(values), image_only))
    if not rows or not any(v for _, v, _ in rows):
        raise CrawlError('상품고시 항목·값 추출 실패')
    return rows


def detail(ctx, doc, url, cache):
    parts, seen = [], set()
    def add_image(img):
        ctx.checkpoint()
        link = image_url(img, url)
        if not link or link in seen:
            return
        seen.add(link)
        if image_info(ctx, link, cache):
            parts.append('<img src="' + escape(link, quote=True) + '" style="max-width:100%"><br>')
    for img in doc.select('#product_add_image2_list img'):
        if '/_data/attach/' in urlsplit(image_url(img, url)).path:
            add_image(img)
    sections = doc.select('.toggle_detail > .section')
    found_size = found_notice = False
    for section in sections:
        title = section.select_one('.title')
        label = re.sub(r'\s+', '', text(title)) if title else ''
        if label == '실측사이즈':
            found_size = True
            tables = section.select('table.tbl_col')
            drawings = section.select('.size_img img')
            if not tables and not drawings:
                ctx.log('실측사이즈 자료 없음: 생략', 'WARNING')
                continue
            parts.append('<h3>실측사이즈</h3>')
            for img in drawings:
                add_image(img)
            for table in tables:
                parts.append(size_table(table))
            for node in section.select('p.size_info, .size_info_box p'):
                value = text(node)
                if value and ('단위' in value or '측정' in value or '사이즈' in value):
                    parts.append('<p>' + escape(value) + '</p>')
        elif label == '상품정보고시':
            found_notice = True
            rows = notice_rows(section)
            if not rows:
                ctx.log('상품정보고시 실제 자료 없음: 생략', 'WARNING')
                continue
            parts.append('<h3>상품정보고시</h3><table border="1" style="width:100%;border-collapse:collapse"><tbody>')
            for key, value, image_only in rows:
                if image_only and not value:
                    ctx.log('고시 텍스트 없음(원본 이미지 전용): ' + key + '. 케어 아이콘은 요청/복사하지 않음', 'WARNING')
                parts.append('<tr><th>' + escape(key) + '</th><td>' + escape(value) + '</td></tr>')
            parts.append('</tbody></table>')
    if not found_size:
        ctx.log('실측사이즈 없음: 생략', 'WARNING')
    if not found_notice:
        ctx.log('상품정보고시 없음: 생략', 'WARNING')
    result = ''.join(parts)
    if len(result.encode('utf-16-le')) // 2 > 32767:
        raise CrawlError('상세 HTML 엑셀 셀 한도 초과')
    return result


def sizes(doc):
    values = []
    for node in doc.select('a.optChipSet2, select[name="option2"] option[value]'):
        value = text(node)
        if node.name == 'option' and not node.get('value'):
            continue
        value = re.sub(r'\s*\(?품절\)?\s*$', '', value).strip()
        if value and value not in values:
            values.append(value)
    if not values:
        raise CrawlError('공식 사이즈 옵션 없음/구조 변경')
    return ','.join(values)


def crawl(ctx):
    ctx.log('주크 크롤러 ' + CRAWLER_REVISION, 'INFO')
    value = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]+(?:[-_][A-Z0-9]+)*', value):
        ctx.fail_item('품번 입력 형식 오류')
        raise CrawlError('품번 입력 형식 오류')
    ctx.stage('주크 공식 품번 검색')
    queries = [value]
    if re.search('[-_]', value):
        queries.append(re.split('[-_](?=[^-_]+$)', value)[0])
    links = search(ctx, queries[0])
    if not links and len(queries) > 1:
        links = search(ctx, queries[1])
    queue, visited, records, failures = list(links), set(), [], []
    while queue:
        link = queue.pop(0)
        pno = parse_qs(urlsplit(link).query)['pno'][0]
        if pno in visited:
            continue
        if len(visited) >= 100:
            raise CrawlError('검색/색상 페이지 100개 한도 초과')
        visited.add(pno)
        try:
            doc, final = page(ctx, link)
            base, displayed, full = identity(doc)
            name, color = option_color(doc, displayed)
            if value not in (base, full, base + '-' + displayed, base + '_' + color, base + '-' + color):
                continue
            records.append((doc, final, base, color, full, name))
            if value == base:
                for a in doc.select('.info_title .ref_color a[href]'):
                    target = product_link(a['href'], final)
                    if target:
                        queue.append(target)
        except Exception as e:
            message = link + ': ' + str(e)
            ctx.fail_item(message)
            failures.append(message)
    if not records:
        message = '일치하는 주크 공식 품번 없음: ' + value
        ctx.fail_item(message)
        raise CrawlError(message)
    emitted = set()
    for doc, url, base, color, full, name in records:
        if color in emitted:
            continue
        ctx.checkpoint()
        ctx.stage(full + ' 수집')
        try:
            title = doc.select_one('.info_title .name')
            if title is None:
                raise CrawlError('상품명 없음')
            record = dict(product_code=base, product_name=clean_name(text(title), [base, full]), color=name or color, color_name=name, color_code=color, product_url=url)
            if ctx.enabled('size'):
                record['size'] = sizes(doc)
            if ctx.enabled('price'):
                price = doc.select_one('.info_price .prc_basic .dis_price')
                if price is None:
                    ctx.log('일반 판매가 없음: 가격 생략', 'WARNING')
                    record['price'] = None
                else:
                    amount = re.sub(r'[^0-9]', '', text(price))
                    if not amount or '원' not in text(price):
                        raise CrawlError('KRW 판매가 해석 불가')
                    record['price'] = int(amount)
            if ctx.enabled('category'):
                record['category'] = ' > '.join(n.get('value', '') for i in range(2, 5) for n in doc.select('form[name="prdFrm"] input[name="category' + str(i) + '"]') if n.get('value'))
            cache = {}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main, extra = gallery(ctx, doc, url, cache)
                if ctx.enabled('main_image'):
                    record['main_image'] = main
                if ctx.enabled('additional_images'):
                    record['additional_images'] = extra
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail(ctx, doc, url, cache)
            collect_regular_price(ctx, record, doc, 'daehyun')
            ctx.emit_result(record)
            emitted.add(color)
        except Exception as e:
            message = full + ': ' + str(e)
            ctx.fail_item(message)
            failures.append(message)
    if failures:
        raise CrawlError('\n'.join(failures))

