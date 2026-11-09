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

SITE = "https://volvikapparel.com"
ADAPTER_REVISION = "2026-11-08-r3"
POLICY = {"checked": "2026-11-08", "source": "https://marketplace.coupang.com/mba/listing",
          "min_side": 500, "max_bytes": 10000000, "max_aspect": 1.8,
          "formats": ("JPEG", "PNG", "WEBP"),
          "note": "WING 웹/엑셀 공식 안내 + 사용자 정지 WebP 허용. 비율은 프로젝트 정책."}
# 실사이트: VMBSQ991 OW/BK, VMMSP991 MG의 HTML 및 공개 사진 확인.
# 사이트 JS는 #wivis_detail .prodSize/.procEssInfo를 하단 탭으로 이동한다.
# JSON-LD의 image는 전 색상 공통이므로 색상 매핑에 쓰지 않는다.
# Hub 최종 정제기/재실행 교체와 실제 WING 업로드는 별도 환경에서 확인 필요.

class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


def fetch(ctx, url, limit=6000000):
    url = quote(url, safe=':/?&=%+#')
    if urlsplit(url).scheme not in ('http', 'https'):
        raise CrawlError('HTTP 이미지/페이지 주소가 아님: ' + url)
    for attempt in range(3):
        ctx.checkpoint()
        ctx.log('HTTP 요청 ' + str(attempt + 1) + '/3: ' + url, 'INFO')
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


def page(ctx, url):
    data, final, _ = fetch(ctx, url)
    if urlsplit(final).hostname not in ('volvikapparel.com', 'www.volvikapparel.com'):
        raise CrawlError('공식몰 외부 이동: ' + final)
    return BeautifulSoup(data.decode('utf-8-sig'), 'html.parser'), final


def identity(doc):
    node = doc.select_one('#detail_ma_product_code')
    value = node.get_text(strip=True).upper() if node else ''
    match = re.fullmatch(r'([A-Z0-9]+(?:-[A-Z0-9]+)*)_([A-Z0-9]+)', value)
    if not match:
        raise CrawlError('공식 자체상품코드 해석 불가: ' + value)
    return match[1], match[2], value


def search(ctx, code):
    queue = [SITE + '/product/search.html?' + urlencode({'keyword': code})]
    seen, links = set(), []
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        if len(seen) >= 30:
            raise CrawlError('검색 페이지 한도 초과')
        seen.add(url)
        doc, final = page(ctx, url)
        for a in doc.select('.prdList .name a[href]'):
            link = urljoin(final, a['href'])
            if urlsplit(link).hostname == 'volvikapparel.com' and link not in links:
                links.append(link)
        for a in doc.select('.xans-search-paging a[href], .xans-product-searchpaging a[href]'):
            link = urljoin(final, a['href'])
            p = urlsplit(link)
            if p.hostname == 'volvikapparel.com' and p.path == '/product/search.html' and parse_qs(p.query).get('keyword') == [code] and link not in seen and link not in queue:
                queue.append(link)
    return links


def color_labels(doc):
    labels = [li['option_value'] for li in doc.select('ul[option_title="Color"] li[option_value]')]
    labels.extend(o['value'] for o in doc.select('select[option_title="Color"] option[value]') if o['value'] not in ('', '*', '**'))
    return list(dict.fromkeys(labels))


def color_name(doc, color):
    for label in color_labels(doc):
        if label.partition('/')[0].strip().upper() == color:
            return label.partition('/')[2].strip()
    raise CrawlError('자체상품코드와 Color 옵션 불일치: ' + color)


def clean_name(value, codes):
    for token in sorted(codes + ['VOLVIK APPAREL', '볼빅 어패럴', 'VOLVIK', '볼빅'], key=len, reverse=True):
        value = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])', '', value, flags=re.I)
    value = re.sub(r'\s+', ' ', value).strip(' _-')
    if not value:
        raise CrawlError('정리된 상품명 없음')
    return value


def option_rows(doc, color):
    for script in doc.find_all('script', src=False):
        m = re.search(r"var\s+option_stock_data\s*=\s*'([^']*)'", script.get_text())
        if m:
            data = json.loads(json.loads('"' + m[1] + '"'))
            rows = [v for v in data.values() if v.get('option_value_orginal', [''])[0].partition('/')[0].upper() == color]
            if rows:
                return rows
    raise CrawlError('색상별 공식 옵션 정보 없음: ' + color)


def photo_url(img, page_url):
    raw = next((img.get(k) for k in ('data-replace', 'ec-data-src', 'data-src', 'src') if img.get(k)), '')
    return quote(urljoin(page_url, raw), safe=':/?&=%+#') if raw else ''


def gallery(ctx, doc, url, full_code, cache):
    accepted, hashes = [], set()
    if identity(doc)[2] != full_code:
        raise CrawlError('갤러리 페이지의 공식 품번/색상 불일치')
    candidates = []
    for img in doc.select('.xans-product-addimage.prdImg img'):
        ctx.checkpoint()
        link = photo_url(img, url)
        # 실제 공식 갤러리 + 공식 자체상품코드가 명시된 CDN 개별컷만 연결.
        # 파일명으로 색을 추측하지 않고, 별도 페이지의 자체상품코드를 대조한다.
        path = unquote(urlsplit(link).path)
        if not re.search(r'/' + re.escape(full_code) + r'_\d+\.(jpg|jpeg|png|webp)$', path, re.I):
            continue
        candidates.append((img, link))
    if not candidates:
        # 품번 없는 해시 파일명 상품은 해당 색상 페이지가 직접 지정한
        # .bigImage[data-src]의 공개 원본만 사용한다. URL을 추측하지 않는다.
        # JSON-LD 공통 image 배열이나 다른 색상 페이지 사진은 사용하지 않는다.
        for img in doc.select('.xans-product-image.imgArea .bigImage[data-src]'):
            ctx.checkpoint()
            link = photo_url(img, url)
            parsed = urlsplit(link)
            if parsed.hostname in ('volvikapparel.com', 'www.volvikapparel.com') and parsed.path.startswith('/web/product/big/'):
                candidates.append((img, link))
                ctx.log('색상별 공식 페이지 bigImage 원본 사용: ' + full_code + ': ' + link, 'INFO')
    for img, link in candidates:
        ctx.checkpoint()
        path = unquote(urlsplit(link).path)
        info = image_info(ctx, link, cache, gallery=True)
        if not info:
            continue
        fmt, w, h, animated, digest, signal = info
        reasons = []
        if fmt not in POLICY['formats'] or animated:
            reasons.append('미지원 형식/애니메이션')
        if min(w, h) < 500 or max(w, h) / min(w, h) > 1.8:
            reasons.append('크기/통짜 의심 비율')
        if re.search(r'size|measure|collage|banner|chart|notice|wash|logo', img.get('alt', '') + ' ' + path, re.I):
            reasons.append('측정/고시/합성/배너 역할 신호')
        if signal:
            reasons.append(signal)
        if reasons:
            ctx.log('갤러리 제외: ' + ', '.join(reasons) + ': ' + link, 'WARNING')
        elif digest not in hashes:
            hashes.add(digest)
            accepted.append(link)
            ctx.log('개별컷 채택: 공식 페이지/자체품번 일치, 규격 및 부정 신호 검사 통과: ' + link, 'INFO')
    if not accepted:
        ctx.log('유효한 갤러리 0장: 이미지 검수 필요', 'WARNING')
        return '', ''
    extra = accepted[1:] or accepted[:1]
    if len(accepted) == 1:
        ctx.log('유효한 컷 1장: 추가이미지에도 재사용', 'WARNING')
    return accepted[0], ','.join(extra[i % len(extra)] for i in range(5))


def text(node):
    return ' '.join(node.stripped_strings)


def table_html(table):
    rows = []
    for tr in table.find_all('tr'):
        cells = []
        for cell in tr.find_all(['th', 'td'], recursive=False):
            attrs = ''.join(' ' + k + '="' + str(int(cell[k])) + '"' for k in ('rowspan', 'colspan') if str(cell.get(k, '')).isdigit())
            cells.append('<' + cell.name + attrs + '>' + escape(text(cell)) + '</' + cell.name + '>')
        if cells:
            rows.append('<tr>' + ''.join(cells) + '</tr>')
    if not rows:
        raise CrawlError('원본 표가 있지만 추출 행이 없음')
    return '<table border="1" style="width:100%;border-collapse:collapse"><tbody>' + ''.join(rows) + '</tbody></table>'


def detail(ctx, doc, url, full_code, cache):
    parts, seen = [], set()
    def add_image(img):
        link = photo_url(img, url)
        if not link or link in seen:
            return
        seen.add(link)
        if image_info(ctx, link, cache):
            parts.append('<img src="' + escape(link, quote=True) + '" style="max-width:100%"><br>')
    root = doc.select_one('#wivis_detail')
    if root is None:
        raise CrawlError('공식 상품 상세 영역을 찾지 못함')
    # 확인한 두 상품별 사진 구조만 포함. 공통 배너/care 요청 금지.
    for img in root.select('img'):
        link = unquote(photo_url(img, url))
        product_photo = ('/SellerCanvas/' in link and '/' + full_code + '/' in link) or ('/goods/S004/' in link and re.search('/' + re.escape(full_code) + r'_\d+\.(jpg|jpeg|png|webp)(?:\?|$)', link, re.I))
        if product_photo and not img.find_parent(class_=['prodSize', 'procEssInfo']):
            add_image(img)
    sizes = root.select('.prodSize')
    if not sizes:
        ctx.log('공식 사이즈표 없음: 생략', 'WARNING')
    for size in sizes:
        parts.append('<h3>사이즈표</h3>')
        for img in size.select('.sizeInfo .img img'):
            add_image(img)
        for table in size.select('.sizeTbl table'):
            parts.append(table_html(table))
        for li in size.select('.sizeInfo .txt li'):
            value = text(li)
            if ('측정' in value or '단위' in value) and not re.search('교환|반품|환불', value):
                parts.append('<p>' + escape(value) + '</p>')
    notices = root.select('.procEssInfo')
    if not notices:
        ctx.log('공식 상품정보고시 없음: 생략', 'WARNING')
    for notice in notices:
        rows = []
        for tr in notice.select('table tr'):
            th, td = tr.find('th'), tr.find('td')
            if th is not None and td is not None:
                rows.append((text(th), text(td)))
        if not rows or not any(v for _, v in rows):
            raise CrawlError('상품고시 영역 존재하나 항목·값 추출 실패')
        parts.append('<h3>상품정보고시</h3><table border="1" style="width:100%;border-collapse:collapse"><tbody>' + ''.join('<tr><th>' + escape(k) + '</th><td>' + escape(v) + '</td></tr>' for k, v in rows) + '</tbody></table>')
    result = ''.join(parts)
    if len(result.encode('utf-16-le')) // 2 > 32767:
        raise CrawlError('상세 HTML 엑셀 셀 한도 초과')
    return result


def crawl(ctx):
    value = str(ctx.product_code).strip().upper()
    ctx.log('볼빅 코드 버전 ' + ADAPTER_REVISION + ', 입력 품번=' + value, 'INFO')
    if not re.fullmatch(r'[A-Z0-9]+(?:[-_][A-Z0-9]+)*', value):
        ctx.fail_item('품번 입력 형식 오류')
        raise CrawlError('품번 입력 형식 오류')
    ctx.stage('공식 품번 검색')
    # 전체 입력을 먼저 검색하고, 결과 없을 때만 마지막 구분자 이전을 조회한다.
    links = search(ctx, value)
    if not links and re.search('[-_]', value):
        links = search(ctx, re.split('[-_](?=[^-_]+$)', value)[0])
    docs, failures = [], []
    for link in links:
        ctx.checkpoint()
        try:
            doc, final = page(ctx, link)
            base, color, full = identity(doc)
            if value in (base, full, base + '-' + color):
                docs.append((doc, final, base, color, full))
        except Exception as e:
            message = link + ': ' + str(e)
            ctx.fail_item(message)
            failures.append(message)
    if not docs:
        ctx.fail_item('일치하는 공식 자체상품코드 없음: ' + value)
        raise CrawlError('일치하는 공식 자체상품코드 없음: ' + value)
    # 특정 색상 검색 결과에서도 기본 검색으로 전체 색상의 실제 페이지를 확인.
    if value == docs[0][2]:
        expected = {label.partition('/')[0].upper() for d, _, _, _, _ in docs for label in color_labels(d)}
        missing = expected - {d[3] for d in docs}
        if missing:
            message = '공식 색상 옵션의 별도 페이지 누락: ' + ','.join(sorted(missing))
            ctx.fail_item(message)
            failures.append(message)
    emitted = set()
    for doc, url, base, color, full in docs:
        if color in emitted:
            continue
        ctx.checkpoint()
        ctx.stage(full + ' 수집')
        current_field = '필수 상품정보'
        try:
            title = doc.select_one('.headingArea h1')
            if title is None:
                raise CrawlError('상품명 영역 없음')
            name = color_name(doc, color)
            record = dict(product_code=base, product_name=clean_name(text(title), [base, full]), color=name or color, color_name=name, color_code=color, product_url=url)
            if ctx.enabled('size') or ctx.enabled('price'):
                current_field = '사이즈/가격'
                rows = option_rows(doc, color)
                if ctx.enabled('size'):
                    record['size'] = ','.join(dict.fromkeys(str(r['option_value_orginal'][1]) for r in rows))
                if ctx.enabled('price'):
                    prices = {float(r['option_price']) for r in rows}
                    record['price'] = prices.pop() if len(prices) == 1 else None
                    if record['price'] is None:
                        ctx.log('옵션별 가격 상이: 가격 생략', 'WARNING')
            if ctx.enabled('category'):
                current_field = '카테고리'
                record['category'] = ' > '.join(text(a) for a in doc.select('.xans-product-headcategory.path li a') if text(a) and text(a) != '홈')
                if not record['category']:
                    ctx.log('공식 카테고리 경로 없음', 'WARNING')
            cache = {}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                current_field = '대표/추가이미지'
                main, extra = gallery(ctx, doc, url, full, cache)
                if ctx.enabled('main_image'):
                    record['main_image'] = main
                if ctx.enabled('additional_images'):
                    record['additional_images'] = extra
            if ctx.enabled('detail_page'):
                current_field = '상세 HTML'
                record['detail_page'] = detail(ctx, doc, url, full, cache)
            current_field = '결과 전송'
            collect_regular_price(ctx, record, doc, 'cafe24')
            ctx.emit_result(record)
            ctx.log(full + ' 결과 전송 완료', 'SUCCESS')
            emitted.add(color)
        except Exception as e:
            message = full + ' [' + current_field + '] ' + type(e).__name__ + ': ' + str(e)
            ctx.fail_item(message)
            failures.append(message)
    if failures:
        raise CrawlError('\n'.join(failures))



