PORTAL_API_VERSION = 1
"""콜핑 공식몰(ekolping.co.kr 콜핑몰, Cafe24) — 수동 색상 매핑(URL 방식).

2026-11-27 실사이트 확인(KVP9610M 틸로(남) · KUP0812M 국민바지250(남) · KSP9555M 레바(남) · KUT0477M 오아스(남)):
- 품번 = 상품 정보 표의 '모델명'(예: KVP9610M). 상품명(틸로(남))에는 품번이 없음.
  사이트 검색 /product/search.html?keyword=모델명 이 모델명을 찾아 줌(앞부분만 넣으면 안 나옴)
  → 검색 결과 상품마다 상세의 모델명이 입력과 정확히 같은지 다시 확인. 상품번호·상품 URL 직접 입력도 가능.
- 색상·사이즈: option_stock_data 색상#$%사이즈 (BLACK-076). 공식 색상코드는 없음 → 색상명만.
  품절·숨김 사이즈도 그 색상 사이즈로 유지(재고는 '색상별 사이즈 재고' 열).
- 사진: 갤러리 big·extra(모든 색상 공통, 대표는 여러 색 나열컷인 경우가 많음) + 상세 본문의
  모델명 파일(KUP0812M_1.jpg 등)을 전부 매핑 후보로 보냄. 색상 배정은 사람이 색상 매핑 화면에서.
- 상세: #prdDetail 본문 이미지(ktcdn 이미지 서버). 브랜드 공통 배너(shop_top·shop_footer·
  apparel_top_sp 등 '이미지 무단도용·CCTV 안내' 배너)는 제외, 스펙 통이미지(모델명_spec.jpg)·
  핏 가이드(pants_style 등)는 유지 + 상품정보제공고시(#desctable) 표.
- 가격: 판매가 #span_product_price_text, 정가 #span_product_price_custom(regular_price cafe24, 판매가로 대체 안 함).
검증: 오프라인 fixture 테스트(tests/test_kolping_official.py) + 사용자 PC 브라우저로 실사이트 구조 확인.
"""

import hashlib
import html
import io
import json
import re
import time
from collections import OrderedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image

from crawler_rules import check_public_image, clean_product_name
from detail_store import excel_detail
from regular_price import collect_regular_price

REVISION = '2026-11-27-kolping-manual-1'
SITE = 'https://ekolping.co.kr'
SITE_HOSTS = ('ekolping.co.kr', 'www.ekolping.co.kr', 'm.ekolping.co.kr')
GALLERY_HOSTS = SITE_HOSTS + ('ecimg.cafe24img.com',)
DETAIL_HOSTS = ('sno525570-sno525570.ktcdn.co.kr',) + SITE_HOSTS
BRAND_ALIASES = ['콜핑', 'KOLPING', '콜핑몰', 'BTR', '비티알']
REQUEST_GAP = .8
RETRY_HTTP = (5, 20)
RETRY_CONN = (5, 60)
POLICY = dict(checked='2026-11-09', source='https://marketplace.coupang.com/mba/listing', min_side=500,
              max_bytes=10_000_000, max_aspect=1.8, formats=('JPEG', 'PNG', 'WEBP'),
              note='정지 WebP·비율 1.8은 프로젝트 정책. 등록 승인 전체는 미검증.')
SIZE_DIR = re.compile(r'/web/product/(extra/)?(big|medium|small|tiny)/')
MODEL = re.compile(r'[A-Z]{2,}[0-9][A-Z0-9]{2,}')
PNO = re.compile(r'(?:[?&]product_no=|/product/[^/?#]+/)(\d+)(?=[/&#?]|$)', re.I)
NEGATIVE = re.compile(r'size|guide|chart|measure|banner|notice|promotion|intro|사이즈|실측|측정|고시', re.I)
# 브랜드 공통 홍보·안내 배너(상품 정보 아님)
DETAIL_SKIP = re.compile(r'shop_top|shop_footer|_top_sp|top_sp\.|/banner|/event|/promotion/|/skin/|btn_|icon|ico_|logo|blank|noimg|\.svg|\.gif$', re.I)


class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


class Blocked(CrawlError):
    """사이트가 연결을 계속 끊음."""


# ───────────────────────── 통신·검색 ─────────────────────────
def pause(ctx, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.checkpoint()
        time.sleep(min(.5, max(0, end - time.monotonic())))


def fetch_text(ctx, url, limit=6_000_000):
    """403·429·5xx는 5초·20초 후, 연결끊김은 5초·60초 후 재시도. 그래도 끊기면 Blocked."""
    http_waits, conn_waits = list(RETRY_HTTP), list(RETRY_CONN)
    opener = build_opener()
    while True:
        ctx.checkpoint()
        request = Request(quote(url, safe=':/?&=%+#'), headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8', 'Referer': SITE + '/'})
        try:
            with opener.open(request, timeout=40) as response:
                final = response.geturl()
                raw = response.read(limit + 1)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code not in (403, 429, 500, 502, 503, 504) or not http_waits:
                raise CrawlError(f'HTTP {code}: {url}') from exc
            wait = http_waits.pop(0)
            ctx.log(f'HTTP {code} → {wait}초 후 다시 시도: {url}', 'WARNING')
        except (URLError, TimeoutError, OSError) as exc:
            if not conn_waits:
                raise Blocked('콜핑몰이 연결을 계속 끊음(' + type(exc).__name__ + '): ' + url) from exc
            wait = conn_waits.pop(0)
            ctx.log(f'{type(exc).__name__} → {wait}초 후 다시 시도: {url}', 'WARNING')
        else:
            ctx.checkpoint()
            if len(raw) > limit:
                raise CrawlError('응답 용량 초과: ' + url)
            if urlsplit(final).hostname not in SITE_HOSTS:
                raise CrawlError('콜핑몰 밖으로 이동: ' + final)
            for encoding in ('utf-8-sig', 'cp949'):
                try:
                    return raw.decode(encoding), final
                except UnicodeDecodeError:
                    continue
            return raw.decode('utf-8', errors='ignore'), final
        pause(ctx, wait)


def search(ctx, model):
    """사이트 검색(모델명) → 상품번호 목록(검색 결과 순)."""
    text, _ = fetch_text(ctx, SITE + '/product/search.html?keyword=' + quote(model))
    pause(ctx, REQUEST_GAP)
    numbers = []
    for match in re.finditer(r'href="([^"]*)"', text):
        href = html.unescape(match[1])
        if '/product/' not in href or 'search.html' in href:
            continue
        found = PNO.search(href)
        if found and found[1] not in numbers:
            numbers.append(found[1])
    return numbers


def direct_number(value):
    """'18982' / 콜핑몰 상품 URL → 상품번호."""
    text = value.strip()
    if '/' in text or 'product_no' in text.lower():
        host = urlsplit(text if '://' in text else 'https://' + text).hostname
        match = PNO.search(text)
        return match[1] if match and host in SITE_HOSTS else ''
    return text if re.fullmatch(r'\d{1,7}', text) else ''


# ───────────────────────── 상세 ─────────────────────────
def js_string(text, name):
    match = re.search(r'var\s+' + name + r"\s*=\s*'((?:[^'\\]|\\.)*)'", text)
    if not match:
        return ''
    value = match[1].replace("\\'", "'")
    if '\\' in value:
        try:
            value = json.loads('"' + re.sub(r'(?<!\\)"', '\\\\"', value) + '"')
        except ValueError:
            pass
    return BeautifulSoup(value, 'html.parser').get_text()


def option_stock(text):
    match = re.search(r"var option_stock_data\s*=\s*'((?:[^'\\]|\\.)*)'", text)
    if not match:
        return {}
    raw = match[1].replace("\\'", "'")
    try:
        data = json.loads(json.loads('"' + raw + '"'))
    except ValueError:
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def notice_rows(doc):
    rows = OrderedDict()
    for tr in doc.select('#desctable tr'):
        cells = tr.find_all(['th', 'td'])
        if len(cells) == 2:
            key = cells[0].get_text(' ', strip=True)
            value = re.sub(r'\s+', ' ', cells[1].get_text(' ', strip=True))
            if key and value and key not in rows:
                rows[key] = value
    return rows


def info_rows(doc):
    """상품 정보 표(.infoArea): 상품요약정보·정가·판매가·브랜드·모델명 …"""
    rows = OrderedDict()
    for tr in doc.select('.infoArea tr, .xans-product-detaildesign tr'):
        th, td = tr.find('th'), tr.find('td')
        if th and td:
            key = re.sub(r'\s+', '', th.get_text())
            value = re.sub(r'\s+', ' ', td.get_text(' ', strip=True))
            if key and value and key not in rows:
                rows[key] = value
    return rows


def model_of(doc, notice):
    value = (info_rows(doc).get('모델명') or notice.get('모델명') or '').strip().upper()
    return value if MODEL.fullmatch(value) else ''


def product_soldout(doc):
    for node in doc.select('.xans-product-action [class*=soldout], .xans-product-action .sub_sold, .btnArea [class*=soldout], #btnSoldout'):
        label = re.sub(r'\s+', '', node.get_text()).upper()
        hidden = any('displaynone' in (p.get('class') or []) or 'display:none' in (p.get('style') or '').replace(' ', '')
                     for p in [node] + [q for q in node.parents if hasattr(q, 'get')])
        if label in ('품절', 'SOLDOUT') and not hidden:
            return True
    return False


def colors_and_sizes(text, doc, default_color):
    """색상 원문 → {'sizes': [...], 'stock': {사이즈: 수량|'있음'|0}}. 품절·숨김 사이즈 포함."""
    titles = [s.get('option_title') or '' for s in doc.select('select[option_title]') if s.get('product_type') != 'addproduct_option']
    joined = len(titles) >= 2 or any('-' in t and ('색' in t or '컬러' in t) for t in titles)
    size_only = len(titles) == 1 and not joined and re.search(r'사이즈|SIZE', titles[0], re.I)
    colors = OrderedDict()
    for item in option_stock(text).values():
        value = str(item.get('option_value') or '').strip()
        if not value:
            continue
        names = item.get('option_name_original') or []
        values = item.get('option_value_orginal') or item.get('option_value_original') or []
        if (len(values) == 1 and names and '/' in values[0] and re.search(r'색|컬러|COLOR', names[0], re.I)
                and re.search(r'사이즈|SIZE', names[0], re.I)):
            color, size = values[0].rsplit('/', 1)
        elif len(names) == 2 and len(values) == 2 and re.search(r'사이즈|SIZE', names[1], re.I):
            color, size = values
            size = re.sub(r'\(\d+\)$', '', size.strip()) or size
        elif len(values) == 1 and names and re.search(r'사이즈|SIZE', names[0], re.I):
            color, size = default_color, values[0]
        elif joined and '-' in value:
            color, size = value.rsplit('-', 1)
        elif size_only:
            color, size = default_color, value
        else:
            color, size = value, ''
        color, size = color.strip(), size.strip()
        sold = (item.get('is_selling') == 'F' or item.get('is_display') == 'F'
                or (item.get('use_stock') and item.get('is_auto_soldout') == 'T' and str(item.get('stock_number')) in ('0', '0.0')))
        entry = colors.setdefault(color, {'sizes': [], 'stock': OrderedDict()})
        if size and size not in entry['sizes']:
            entry['sizes'].append(size)
            quantity = item.get('stock_number')
            entry['stock'][size] = 0 if sold else (int(quantity) if str(quantity or '').isdigit() else '있음')
    if not colors:
        color_values, size_values = [], []
        for select in doc.select('select[option_title]'):
            if select.get('product_type') == 'addproduct_option':
                continue
            values = [o.get('value', '').strip() for o in select.select('option[value]') if o.get('value', '').strip() not in ('', '*', '**')]
            target = size_values if re.search(r'사이즈|SIZE', select.get('option_title') or '', re.I) else color_values
            target.extend(v for v in values if v not in target)
        for color in color_values or ([default_color] if default_color else []):
            colors[color] = {'sizes': list(size_values), 'stock': OrderedDict((s, '확인불가') for s in size_values)}
    if product_soldout(doc):
        for entry in colors.values():
            entry['stock'] = OrderedDict((size, 0) for size in entry['stock'])
    return colors


def price(ctx, doc):
    node = doc.select_one('#span_product_price_sale') or doc.select_one('#span_product_price_text')
    match = re.search(r'([\d,]+)', node.get_text(' ', strip=True)) if node else None
    if not match or not match[1].replace(',', ''):
        ctx.log('공개 KRW 판매가를 확인할 수 없어 판매가는 빈칸입니다.', 'WARNING')
        return None
    return int(match[1].replace(',', ''))


def category(doc):
    trail = []
    for li in doc.select('.xans-product-headcategory li'):
        label = li.get_text(' ', strip=True)
        if label and label not in ('홈', 'HOME', 'Home', '현재 위치', '>') and label not in trail:
            trail.append(label)
    return ' > '.join(trail)


def source_of(image):
    for key in ('ec-data-src', 'data-src', 'data-original', 'src'):
        value = (image.get(key) or '').strip()
        if value and not value.startswith('data:'):
            return value
    return ''


def absolute(raw, final):
    if not raw:
        return None
    url = urljoin(final, raw)
    if url.startswith('//'):
        url = 'https:' + url
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or parts.username or parts.password or '..' in unquote(parts.path).split('/'):
        return None
    return parts


def gallery_url(raw, final):
    parts = absolute(raw, final)
    if not parts or parts.hostname not in GALLERY_HOSTS:
        return ''
    path = unquote(parts.path)
    if not SIZE_DIR.search(path) or NEGATIVE.search(path) or not re.search(r'\.(jpe?g|png|webp)$', path, re.I):
        return ''
    return urlunsplit(('https', parts.netloc, parts.path, '', ''))


def big_first(url):
    """Cafe24 갤러리: 같은 사진의 big 원본 → 원래 크기 순서로 확인."""
    match = SIZE_DIR.search(url)
    if not match or match[2] == 'big':
        return [url]
    return [url[:match.start()] + '/web/product/' + (match[1] or '') + 'big/' + url[match.end():], url]


def gallery_groups(ctx, doc, final, number):
    groups, seen = [], set()
    for image in doc.select('.xans-product-image img, img.BigImage, .keyImg img, .xans-product-addimage img'):
        if image.find_parent(class_=re.compile(r'recent|relation|listitem', re.I)):
            continue
        url = gallery_url(source_of(image), final)
        if url and url not in seen:
            seen.add(url)
            group = big_first(url)
            if group not in groups:
                groups.append(group)
    if len(groups) < 2:
        try:
            text, zoom_final = fetch_text(ctx, SITE + '/product/image_zoom2.html?product_no=' + number)
            pause(ctx, REQUEST_GAP)
            for image in BeautifulSoup(text, 'html.parser').select('img'):
                url = gallery_url(source_of(image), zoom_final)
                group = big_first(url) if url else None
                if group and group not in groups and not any(u in g for g in groups for u in group):
                    groups.append(group)
            ctx.log(f'갤러리 1장 이하 → 확대보기(image_zoom2) 확인: 후보 {len(groups)}개', 'INFO')
        except Blocked:
            raise
        except CrawlError as exc:
            ctx.log('확대보기(image_zoom2) 확인 실패: ' + str(exc), 'WARNING')
    return groups


def detail_urls(doc, final, model):
    """#prdDetail 본문 이미지(공통 배너 제외). (전체, 모델명 파일 = 매핑 후보 사진)."""
    urls, shots = [], []
    for image in doc.select('#prdDetail img'):
        if image.find_parent(class_=re.compile(r'review|board', re.I)):
            continue
        parts = absolute(source_of(image), final)
        if not parts or parts.hostname not in DETAIL_HOSTS or DETAIL_SKIP.search(parts.path):
            continue
        url = urlunsplit(('https', parts.netloc, parts.path, parts.query, ''))
        if url in urls:
            continue
        urls.append(url)
        name = unquote(parts.path.rsplit('/', 1)[-1]).upper()
        if model and name.startswith(model) and not re.search(r'SPEC|SIZE|DETAIL', name):
            shots.append(url)
    return urls, shots


def parse_product(ctx, number, model, direct):
    url = SITE + '/product/detail.html?product_no=' + number
    text, final = fetch_text(ctx, url)
    pause(ctx, REQUEST_GAP)
    doc = BeautifulSoup(text, 'html.parser')
    full = re.sub(r'\s+', ' ', js_string(text, 'product_name')).strip()
    if not full:
        node = doc.select_one('.infoArea .name, .headingArea h2, .infoArea h2')
        full = node.get_text(' ', strip=True) if node else ''
    if not full:
        raise CrawlError('상품명 없음')
    notice = notice_rows(doc)
    found = model_of(doc, notice)
    if not direct and found != model:
        ctx.log(f'상품 {number}: 모델명 {found or "없음"} ≠ {model} → 제외', 'INFO')
        return None
    model = found or model
    raw_colors = colors_and_sizes(text, doc, notice.get('색상') or '')
    palette = [dict(name=raw, code='', raw=raw, sizes=entry['sizes'], stock=entry['stock'])
               for raw, entry in raw_colors.items() if raw]
    if not palette:
        raise CrawlError('색상 옵션·고시 색상 모두 없음: 색상 후보를 만들 수 없음')
    info = info_rows(doc)
    cleaned = clean_product_name(full, BRAND_ALIASES, [model]).strip(' _-/') or full
    product = dict(number=number, url=final, style=model, full=full, name=cleaned, palette=palette, notice=notice,
                   brand=info.get('브랜드', ''), summary=info.get('상품요약정보', ''))
    if ctx.enabled('price'):
        product['price'] = price(ctx, doc)
    if ctx.enabled('category'):
        product['category'] = category(doc)
    if ctx.enabled('regular_price'):
        holder = {}
        collect_regular_price(ctx, holder, doc, 'cafe24')
        product['regular_price'] = holder.get('regular_price')
    product['details'], product['shots'] = detail_urls(doc, final, model)
    product['groups'] = gallery_groups(ctx, doc, final, number)
    return product


# ───────────────────────── 사진 후보 검사 ─────────────────────────
def pixels(im):
    flat = getattr(im, 'get_flattened_data', None)
    return list(flat() if flat else im.getdata())


def content_signal(im):
    """규칙 기반 보조 검사(완벽한 의미 인식 아님): 글자 위주 라벨/고시, 전폭 흰 구분선 콜라주."""
    im = im.convert('RGB')
    im.thumbnail((480, 480))
    w, h = im.size
    dark = {i for i, rgb in enumerate(pixels(im)) if max(rgb) < 170}
    total_dark = len(dark)
    small_count = small_area = 0
    while dark:
        stack, area = [dark.pop()], 0
        x0 = y0 = 10 ** 9
        x1 = y1 = -1
        while stack:
            i = stack.pop()
            x, y = i % w, i // w
            area += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                j = ny * w + nx
                if 0 <= nx < w and 0 <= ny < h and j in dark:
                    dark.remove(j)
                    stack.append(j)
        if 2 <= area <= 120 and x1 - x0 < 32 and y1 - y0 < 20:
            small_count += 1
            small_area += area
    if small_count >= 150 and 0 < total_dark < w * h * .16 and small_area > total_dark * .35:
        return '작은 글자 성분이 지배적인 라벨/고시/측정 안내 의심'
    gray = im.convert('L')
    gray.thumbnail((120, 120))
    for g in (gray, gray.transpose(Image.Transpose.ROTATE_90)):
        a, b = g.size
        values = pixels(g)
        for y in range(b // 3, b * 2 // 3):
            if sum(v >= 252 for v in values[y * a:(y + 1) * a]) < a * .99:
                continue
            upper, lower = values[max(0, y - b // 4) * a:y * a], values[(y + 1) * a:min(b, y + b // 4) * a]
            if upper and lower and sum(v < 220 for v in upper) / len(upper) > .18 and sum(v < 220 for v in lower) / len(lower) > .18:
                return '전폭 흰 구분선 양쪽에 독립 사진 영역: 콜라주 의심'
    return ''


def candidate(ctx, url, cache):
    """공개 GET + Pillow 해독 + 형식·크기·비율·글자/콜라주 신호. → (통과 여부, 내용 해시)."""
    if url in cache:
        return cache[url]
    check = check_public_image(url, timeout=10, max_bytes=POLICY['max_bytes'], retries=1, checkpoint=ctx.checkpoint)
    result = (False, '')
    if check['status'] != 'accessible':
        ctx.log(f"후보 제외({check['status']}, 쿠키·Referer 없는 공개 GET): {url}: " + '; '.join(check['reasons']), 'WARNING')
    else:
        data = check['data']
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt, frames = im.format, getattr(im, 'n_frames', 1)
                im.load()
                w, h = im.size
                reasons = []
                if fmt not in POLICY['formats']:
                    reasons.append('형식 ' + str(fmt))
                if frames > 1:
                    reasons.append('애니메이션')
                if min(w, h) < POLICY['min_side']:
                    reasons.append(f'{w}x{h} 500px 미만')
                elif max(w, h) / min(w, h) > POLICY['max_aspect']:
                    reasons.append(f'{w}x{h} 비율 1.8 초과(통짜·배너 의심)')
                if len(data) > POLICY['max_bytes']:
                    reasons.append('10MB 초과')
                if not reasons:
                    signal = content_signal(im)
                    if signal:
                        reasons.append(signal)
        except (OSError, ValueError) as exc:
            reasons, w, h, fmt = ['이미지 해독 실패 ' + type(exc).__name__], 0, 0, ''
        if reasons:
            ctx.log('후보 제외: ' + ', '.join(reasons) + ': ' + url, 'WARNING')
        else:
            result = (True, hashlib.sha256(data).hexdigest())
            ctx.log(f'후보 채택: {w}x{h} {fmt}: {url}', 'INFO')
    cache[url] = result
    return result


def collect_photos(ctx, products):
    """모든 상품 링크의 갤러리(공통) → 상세 모델컷 순서. 같은 내용 해시는 한 번만."""
    photos, hashes, cache = [], set(), {}
    groups = []
    for product in products:
        groups.extend(g for g in product['groups'] if g not in groups)
        groups.extend([s] for s in product['shots'] if [s] not in groups)
    for group in groups:
        ctx.checkpoint()
        for url in group:
            passed, digest = candidate(ctx, url, cache)
            if not passed:
                continue
            if digest in hashes:
                ctx.log('같은 사진(내용 해시 동일) 중복 제외: ' + url, 'INFO')
            else:
                hashes.add(digest)
                photos.append(url)
                if len(group) > 1 and url == group[0]:
                    ctx.log('썸네일 대신 같은 사진의 Cafe24 big 원본 사용: ' + url, 'INFO')
            break
    return photos


# ───────────────────────── 공통 상세 HTML ─────────────────────────
def notice_table(products):
    rows = OrderedDict()
    for product in products:
        for key, value in product['notice'].items():
            rows.setdefault(key, value)
    if not rows:
        return ''
    body = ''.join('<tr><th>' + html.escape(k) + '</th><td>' + html.escape(v) + '</td></tr>' for k, v in rows.items())
    return '<h3>상품정보제공고시</h3><table border="1" cellpadding="6" style="border-collapse:collapse;width:100%"><tbody>' + body + '</tbody></table>'


def shared_detail(ctx, products):
    urls, cache = [], {}
    for product in products:
        urls.extend(u for u in product['details'] if u not in urls)
    parts = []
    for url in urls:
        ctx.checkpoint()
        check = cache.get(url) or check_public_image(url, timeout=12, max_bytes=40_000_000, retries=1, checkpoint=ctx.checkpoint)
        cache[url] = check
        if check['status'] == 'accessible':
            parts.append('<img src="' + html.escape(url, quote=True) + '" alt="상품 상세" style="max-width:100%;height:auto;">')
        elif check['status'] == 'unknown':
            raise CrawlError('상세 이미지 확인 중 일시 오류: ' + url + ': ' + '; '.join(check['reasons']))
        else:
            ctx.log(f"상세 이미지 제외(inaccessible): {url}: " + '; '.join(check['reasons']), 'WARNING')
    if not parts:
        ctx.log('상세 본문 이미지가 없거나 공개 접근 불가: 상세는 고시 표만', 'WARNING')
    table = notice_table(products)
    if table:
        parts.append(table)
    else:
        ctx.log('상품정보제공고시(#desctable) 없음: 표 생략', 'WARNING')
    if not parts:
        raise CrawlError('상세에 넣을 본문 이미지·고시가 모두 없음')
    compact, overflow = excel_detail(''.join(parts))
    if overflow:
        raise CrawlError('공통 상세 HTML Excel 셀 한도(32,767자) 초과. 원본을 임의 절단하지 않습니다.')
    return compact


def common(values):
    present = [v for v in values if v is not None]
    return present[0] if present and all(v == present[0] for v in present) else None


# ───────────────────────── 실행 ─────────────────────────
def crawl(ctx):
    ctx.log('콜핑 수동 매핑 크롤러 ' + REVISION, 'INFO')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        message = '수동 색상 매핑은 대표이미지 또는 추가이미지 선택이 필요합니다.'
        ctx.fail_item(message)
        raise CrawlError(message)
    raw = re.sub(r'\s+', '', str(ctx.product_code or ''))
    number = direct_number(raw)
    model = raw.upper()
    if not number and not MODEL.fullmatch(model):
        message = '품번 형식 오류: 모델명(예: KVP9610M), 상품번호 또는 콜핑몰 상품 URL을 입력하세요.'
        ctx.fail_item(message)
        raise CrawlError(message + ' / ' + raw)
    try:
        if number:
            hits = [number]
        else:
            ctx.stage('콜핑몰 모델명 검색: ' + model)
            hits = search(ctx, model)
    except CrawlError as exc:
        ctx.fail_item('검색 실패: ' + str(exc))
        raise
    if not hits:
        message = '콜핑몰 검색에서 모델명을 찾지 못함(판매 종료·비공개 가능): ' + model
        ctx.fail_item(message)
        raise CrawlError(message)
    products, errors = [], []
    for no in hits[:20]:
        ctx.checkpoint()
        ctx.stage(f'상품 {no} 수집')
        try:
            product = parse_product(ctx, no, model, bool(number))
        except Blocked as exc:
            ctx.fail_item(str(exc))
            raise
        except CrawlError as exc:
            errors.append(f'상품 {no}: {exc}')
            ctx.fail_item(errors[-1])
            continue
        if product is not None:
            products.append(product)
    if not products:
        message = '모델명이 정확히 일치하는 상품 없음: ' + model + (' / ' + ' / '.join(errors) if errors else '')
        if not errors:
            ctx.fail_item(message)
        raise CrawlError(message)
    style = products[0]['style']
    if number and not MODEL.fullmatch(style):
        ctx.log('상품 정보에 모델명이 없어 입력값을 품번으로 사용: ' + style, 'WARNING')
    palette, by_key = [], {}
    for product in products:
        for color in product['palette']:
            key = color['name'].upper()
            if key not in by_key:
                by_key[key] = dict(color, sizes=list(color['sizes']), stock=OrderedDict(color['stock']))
                palette.append(by_key[key])
            else:
                for size in color['sizes']:
                    if size not in by_key[key]['sizes']:
                        by_key[key]['sizes'].append(size)
                    by_key[key]['stock'].setdefault(size, color['stock'].get(size))
    ctx.stage('매핑 후보 사진 검사')
    photos = collect_photos(ctx, products)
    if not photos:
        message = '수동 매핑에 쓸 유효한 상품 사진 없음(공개 접근·500px·비율 1.8·글자/콜라주 검사 탈락): ' + style
        ctx.fail_item(message)
        raise CrawlError(message)
    record = {'product_code': style, 'product_name': products[0]['name'], 'product_url': products[0]['url'],
              '브랜드': next((p['brand'] for p in products if p['brand']), ''),
              '원본 상품명': '\n'.join(dict.fromkeys(p['full'] for p in products))}
    colors = []
    for color in palette:
        value = {'color_name': color['name'], 'color_code': ''}
        if ctx.enabled('size'):
            value['size'] = ','.join(color['sizes'])
        colors.append(value)
    if ctx.enabled('size'):
        lists = {tuple(c['sizes']) for c in palette}
        record['size'] = ','.join(next(iter(lists))) if len(lists) == 1 else None
        if len(lists) > 1:
            ctx.log('색상마다 사이즈가 달라 공통 사이즈는 빈칸, 색상별 사이즈는 매핑 후 각 색상 행에 반영', 'INFO')
        record['색상별 사이즈 재고'] = ' | '.join(
            (c['raw'] + ': ' + ','.join(f'{k}:{v}' for k, v in c['stock'].items())) for c in palette)
    for field in ('price', 'regular_price', 'category'):
        if ctx.enabled(field):
            record[field] = common([p.get(field) for p in products])
            if record[field] is None and len(products) > 1 and any(p.get(field) is not None for p in products):
                ctx.log(f'상품 링크마다 {field} 값이 달라 빈칸', 'WARNING')
    if ctx.enabled('detail_page'):
        ctx.stage('공통 상세페이지 만들기')
        try:
            record['detail_page'] = shared_detail(ctx, products)
        except CrawlError as exc:
            ctx.fail_item(str(exc))
            raise
    for url in photos:
        ctx.checkpoint()
        ctx.emit_unmapped(dict(record), [dict(c) for c in colors], url, shared_detail_html=record.get('detail_page', ''))
    ctx.log(f'{style}: 상품 링크 {len(products)}개, 색상 {len(colors)}개, 매핑 후보 사진 {len(photos)}장', 'SUCCESS')
    if errors:
        raise CrawlError('일부 상품 링크 실패. 성공 후보 보존, 입력 행 미완료: ' + ' / '.join(errors))
