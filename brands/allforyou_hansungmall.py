PORTAL_API_VERSION = 1
"""올포유 등 한성 브랜드 — 한성몰(hansungmall.net, Cafe24) 품번 크롤러 (자동 색상 매핑 / URL 방식).

사용자 단독 한성몰 크롤러(2026-11-24 전달)의 구조를 허브 SDK로 옮김:
- 검색: /product/search.html?keyword=품번 의 .xans-search-result li[id^=anchorBoxId_] (페이지 이동 포함).
  상품명 끝 '품번-색상코드'(… 패딩 점퍼 AWJU67301-403) 또는 상품정보 '품번코드'로 기본 품번을 정확히 대조.
- 상세: /product/detail.html?product_no= . 상품명 var product_name, 판매가 #span_product_price_sale→text,
  정상가 #span_product_price_custom(regular_price cafe24 프로필, 판매가로 대체 안 함).
- 색상·사이즈: var option_stock_data (색상#사이즈). 숨김(is_display=F)·판매중지·자동품절 사이즈도 품절로 포함.
- 사진: 갤러리 /web/product/big·extra/big (www.hansungmall.net, cafe24img.poxo.com/hansungimall). 1장뿐이면
  image_zoom2 확대보기의 추가이미지 확인. 한 페이지 = 한 색상(품번코드 기본품번-색상코드)일 때만 그 색상에 연결.
  한 페이지에 여러 색상이면 색상칩 link_image 컷만 그 색상에 연결하고, 연결 근거 없는 공용 갤러리는 쓰지 않음.
- 상세: #prdDetail2 의 /<브랜드>/detail/ 상품 본문 이미지 + #desctable 상품정보고시 2열 표.
  공통 머리·꼬리(/img/top·bottom), 행사(/web/upload/) 이미지는 요청하지 않음.
기존 '올포유(공식 홈페이지)' 브랜드(allforyou_official)는 그대로 두고 별도 카드로 등록.
검증: 오프라인 fixture 테스트만 수행(tests/test_allforyou_hansungmall.py). 실사이트 실행 확인은 아직 없음.
"""

import hashlib
import html
import io
import json
import re
import time
from collections import OrderedDict
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image

from crawler_rules import check_public_image, clean_product_name, gallery_slots, validate_gallery_image
from regular_price import collect_regular_price

REVISION = '2026-11-24-allforyou-hansungmall-1'
SITE = 'https://www.hansungmall.net'
SITE_HOSTS = ('hansungmall.net', 'www.hansungmall.net')
GALLERY_PREFIX = {'hansungmall.net': '/web/product/', 'www.hansungmall.net': '/web/product/',
                  'cafe24img.poxo.com': '/hansungimall/web/product/'}
DETAIL_HOST = 'hansungimall.openhost.cafe24.com'
POLICY = dict(checked='2026-11-09', source='https://marketplace.coupang.com/mba/listing', route='WING 웹/엑셀',
              min_side=500, max_bytes=10_000_000, max_aspect=1.8,
              note='정지 WebP·비율 1.8은 프로젝트 정책. 전체 등록 승인은 미검증.')
MAX_PAGES = 5
MAX_UNNAMED = 20               # 목록 이름에 품번이 없을 때 상세까지 열어 볼 최대 상품 수
REQUEST_GAP = 1.0                 # 사용자 크롤러: 한성몰은 빠른 연속 요청 시 연결을 끊음
RETRY_HTTP = (5, 20)              # 403·429·5xx
RETRY_CONN = (5, 60)              # 연결끊김·응답없음: 5초 후, 60초 쉬고 1번 더, 그래도 안 되면 중단
EXCEL_CELL = 32767
# 브랜드별 별칭(상품명 정리용 출력 규칙. 상품 소속 판정에는 쓰지 않는다)
ALIASES = {'올포유': ['올포유', '올 포 유', 'ALL FOR YOU', 'ALLFORYOU']}
GALLERY_NEGATIVE = re.compile(r'size|chart|measure|banner|notice|collage|사이즈|고시|측정', re.I)
DETAIL_SKIP = re.compile(r'/img/|/web/upload/|/skin/|btn_|icon|ico_|logo|banner|blank|noimg|\.svg', re.I)
SIZE_DIR = re.compile(r'/web/product/(extra/)?(big|medium|small|tiny)/')
CODE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,}[0-9][A-Za-z0-9]{3,})(?:-([A-Za-z0-9]{2,4}))?(?![A-Za-z0-9])')


class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


class Blocked(CrawlError):
    """한성몰이 연결을 계속 끊음."""


class NoCode(CrawlError):
    """상품 페이지에 품번 근거가 없음(검색에 섞인 다른 상품일 수 있음)."""


def pause(ctx, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.checkpoint()
        time.sleep(min(.5, max(0, end - time.monotonic())))


def fetch_text(ctx, url, limit=6_000_000):
    """한성몰 HTML. 403·429·5xx는 5초·20초 후 재시도, 연결끊김은 5초·60초 후 재시도."""
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
                raise Blocked('한성몰이 연결을 계속 끊음(' + type(exc).__name__ + '): ' + url) from exc
            wait = conn_waits.pop(0)
            ctx.log(f'{type(exc).__name__} → {wait}초 후 다시 시도'
                    + (' (사이트가 잠시 막은 듯)' if not conn_waits else '') + ': ' + url, 'WARNING')
        else:
            ctx.checkpoint()
            if len(raw) > limit:
                raise CrawlError('응답 용량 초과: ' + url)
            if urlsplit(final).hostname not in SITE_HOSTS:
                raise CrawlError('한성몰 밖으로 이동: ' + final)
            for encoding in ('utf-8-sig', 'cp949'):
                try:
                    return raw.decode(encoding), final
                except UnicodeDecodeError:
                    continue
            return raw.decode('utf-8', errors='ignore'), final
        pause(ctx, wait)


def page(ctx, url):
    text, final = fetch_text(ctx, url)
    pause(ctx, REQUEST_GAP)
    return text, BeautifulSoup(text, 'html.parser'), final


def has_code(text, code):
    """AWJU67301 → 'AWJU67301-403'에 맞음 / 'AWJU673012'에는 안 맞음."""
    return bool(re.search(r'(?<![A-Za-z0-9])' + re.escape(code) + r'(?![A-Za-z0-9])', text or '', re.I))


# ───────────────────────── 검색 ─────────────────────────
def search(ctx, keyword):
    """→ [(product_no, 목록 상품명)] 검색 결과 영역만(추천·최근 본 상품 제외)."""
    pending = [SITE + '/product/search.html?keyword=' + quote(keyword)]
    visited, hits, seen = set(), [], set()
    while pending and len(visited) < MAX_PAGES:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        _, doc, final = page(ctx, url)
        area = doc.select_one('.xans-search-result')
        if area is None:
            for junk in doc.select('[class*="listmain"], [class*="recent"], [class*="recommend"], #header, #footer'):
                junk.decompose()
            area = doc
        for li in area.select('li[id^="anchorBoxId_"]'):
            number = li.get('id', '').rsplit('_', 1)[-1]
            label = li.select_one('.name a, .name, .df-prl-name') or li.select_one('a[href*="/product/"]')
            name = re.sub(r'^상품명\s*:\s*', '', label.get_text(' ', strip=True) if label else '').strip()
            if number.isdigit() and number not in seen:
                seen.add(number)
                hits.append((number, name))
        for anchor in doc.select('.xans-search-paging a[href]'):
            link = urljoin(final, anchor['href'])
            parts = urlsplit(link)
            if parts.hostname in SITE_HOSTS and parse_qs(parts.query).get('keyword') == [keyword] and link not in visited:
                pending.append(link)
    # 목록 이름에 품번이 있는 상품만(상세에서 다시 품번 확인). 없으면 전체를 상세에서 확인.
    mine = [hit for hit in hits if has_code(hit[1], keyword)]
    return mine or hits[:MAX_UNNAMED]


# ───────────────────────── 상세: 품번·상품명 ─────────────────────────
def info_field(doc, label):
    node = doc.select_one('li[rel="' + label + '"]')
    if node is not None:
        return re.sub(r'^' + re.escape(label) + r'\s*:?\s*', '', node.get_text(' ', strip=True)).strip()
    for row in doc.select('.infoArea tr, .xans-product-detaildesign tr'):
        th, td = row.select_one('th'), row.select_one('td')
        if th and td and th.get_text(' ', strip=True) == label:
            return re.sub(r'\s+', ' ', td.get_text(' ', strip=True))
    return ''


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


def full_name(text, doc):
    name = js_string(text, 'product_name') or info_field(doc, '상품명')
    if not name:
        node = doc.select_one('h1.efd-prd-name, .infoArea .name, .headingArea h2')
        name = node.get_text(' ', strip=True) if node else ''
    return re.sub(r'\s+', ' ', name).strip()


def identity(text, doc, base):
    """(기본품번, 색상코드, 근거) 또는 None(다른 품번). 품번 근거가 없으면 CrawlError."""
    field = info_field(doc, '품번코드').upper()
    match = re.fullmatch(r'([A-Z0-9]+)-([A-Z0-9]+)', field)
    if match:
        return (match[1], match[2], '상품정보 품번코드 ' + field) if match[1] == base else None
    if field and field == base:
        return base, '', '상품정보 품번코드 ' + field
    name = full_name(text, doc)
    for found in CODE.finditer(name):
        if found[1].upper() == base:
            return base, (found[2] or '').upper(), '상품명 품번 ' + found[0]
    if CODE.search(name) or field:
        return None
    raise NoCode('상품 페이지에서 품번을 확인할 수 없음(품번코드·상품명 품번 없음)')


def brand_of(doc, name):
    brand = info_field(doc, '브랜드')
    if not brand:
        bracket = re.search(r'\[([^\]]+)\]', name)
        brand = bracket[1].strip() if bracket else ''
    if not brand:
        trail = [li.get_text(' ', strip=True) for li in doc.select('.xans-product-headcategory li')]
        if '브랜드' in trail and trail.index('브랜드') + 1 < len(trail):
            brand = trail[trail.index('브랜드') + 1]
    return brand


def clean_name(name, brand, codes, colors):
    aliases = list(ALIASES.get(re.sub(r'\s+', '', brand), [])) + ([brand] if brand else [])
    cleaned = clean_product_name(name, aliases, [c for c in codes if c])
    if len(colors) == 1 and colors[0]:
        cleaned = re.sub(r'(?<![\w])' + re.escape(colors[0]) + r'\s*$', '', cleaned, flags=re.I).strip(' -_/')
    return cleaned or name


# ───────────────────────── 상세: 색상·사이즈 ─────────────────────────
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


def product_soldout(doc):
    for node in doc.select('.xans-product-action [class*=soldout], .xans-product-action .sub_sold, .btnArea [class*=soldout], #btnSoldout'):
        label = re.sub(r'\s+', '', node.get_text()).upper()
        hidden = any('displaynone' in (p.get('class') or []) or 'display:none' in (p.get('style') or '').replace(' ', '')
                     for p in [node] + [q for q in node.parents if hasattr(q, 'get')])
        if label in ('품절', 'SOLDOUT') and not hidden:
            return True
    return False


def colors_and_sizes(text, doc, default_color):
    """색상 → {'sizes': [표시 순서], 'stock': {사이즈: 수량|0|'있음'}}. 품절·숨김 사이즈도 포함."""
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
            color, size = values[0].rsplit('/', 1)              # '색상/사이즈' 한 칸: '블랙/125'
        elif len(names) == 2 and len(values) == 2 and re.search(r'사이즈|SIZE', names[1], re.I):
            color, size = values
            size = re.sub(r'\(\d+\)$', '', size.strip()) or size     # 'FREE(999)' → 'FREE'
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
        # option_stock_data가 없으면 화면 옵션 목록(품절 표시 여부는 알 수 없음)
        color_values, size_values = [], []
        for select in doc.select('select[option_title]'):
            if select.get('product_type') == 'addproduct_option':
                continue
            values = [o.get('value', '').strip() for o in select.select('option[value]') if o.get('value', '').strip() not in ('', '*', '**')]
            target = size_values if re.search(r'사이즈|SIZE', select.get('option_title') or '', re.I) else color_values
            target.extend(v for v in values if v not in target)
        for color in color_values or [default_color]:
            colors[color] = {'sizes': list(size_values), 'stock': OrderedDict((s, '확인불가') for s in size_values)}
    if product_soldout(doc):
        for entry in colors.values():
            entry['stock'] = OrderedDict((size, 0) for size in entry['stock'])
    return colors


def price(ctx, doc):
    node = doc.select_one('#span_product_price_sale') or doc.select_one('#span_product_price_text')
    match = re.search(r'([\d,]+)\s*원?', node.get_text(' ', strip=True)) if node else None
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


# ───────────────────────── 사진 ─────────────────────────
def source_of(image):
    for key in ('ec-data-src', 'data-src', 'data-original', 'src'):
        value = (image.get(key) or '').strip()
        if value and not value.startswith('data:'):
            return value
    return ''


def gallery_url(raw, final):
    if not raw:
        return ''
    url = urljoin(final, raw)
    if url.startswith('//'):
        url = 'https:' + url
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or parts.username or parts.password:
        return ''
    prefix = GALLERY_PREFIX.get(parts.hostname or '')
    path = unquote(parts.path)
    if not prefix or '..' in path.split('/') or not path.startswith(prefix) or not SIZE_DIR.search(path):
        return ''
    if GALLERY_NEGATIVE.search(path) or not re.search(r'\.(jpe?g|png|webp)$', path, re.I):
        return ''
    return urlunsplit(('https', parts.netloc, parts.path, '', ''))


def big_first(url):
    """Cafe24 갤러리: 같은 사진의 big → 원래 크기 순서로 후보(작은 썸네일만 있을 때 big 원본 확인)."""
    match = SIZE_DIR.search(url)
    if not match or match[2] == 'big':
        return [url]
    return [url[:match.start()] + '/web/product/' + (match[1] or '') + 'big/' + url[match.end():], url]


def gallery_groups(ctx, doc, final, number):
    groups, seen = [], set()
    nodes = doc.select('.imgArea img, .xans-product-image img, img.BigImage, .keyImg img, .xans-product-addimage img')
    for image in nodes:
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
            _, zoom, zoom_final = page(ctx, SITE + '/product/image_zoom2.html?product_no=' + number)
            for image in zoom.select('img'):
                url = gallery_url(source_of(image), zoom_final)
                group = big_first(url) if url else None
                if group and group not in groups and not any(u in g for g in groups for u in group):
                    groups.append(group)
            ctx.log(f'갤러리 1장 이하 → 확대보기(image_zoom2) 확인: 후보 {len(groups)}개', 'INFO')
        except CrawlError as exc:
            ctx.log('확대보기(image_zoom2) 확인 실패: ' + str(exc), 'WARNING')
    return groups


def link_images(doc, final):
    """색상칩 link_image = 사이트가 지정한 그 색상 대표컷."""
    found = {}
    for li in doc.select('ul[option_title] li[link_image]'):
        title = li.find_parent('ul').get('option_title') or ''
        if re.search(r'color|색|컬러', title, re.I):
            url = gallery_url(li.get('link_image'), final)
            if url:
                found[(li.get('option_value') or li.get('title') or '').strip()] = url
    return found


def screen(ctx, url, cache, mapping_evidence):
    if url in cache:
        return cache[url]
    check = check_public_image(url, max_bytes=POLICY['max_bytes'], checkpoint=ctx.checkpoint)
    if check['status'] != 'accessible':
        ctx.log(f"갤러리 제외({check['status']}, 쿠키·Referer 없는 공개 GET): {url}: " + '; '.join(check['reasons']), 'WARNING')
        cache[url] = None
        return None
    data = check['data']
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        signal = content_signal(im)
    result = validate_gallery_image(
        data, role='product_shot', mapping_verified=True, single_shot_verified=not signal,
        mapping_evidence=mapping_evidence,
        content_evidence=('Cafe24 상품 갤러리(/web/product/big·extra) 영역, 파일경로 사이즈·배너·고시 신호 없음, '
                          f"{check.get('width')}x{check.get('height')} {check.get('format')}, 글자·콜라주 보조검사 통과") if not signal else '',
        max_aspect=POLICY['max_aspect'], min_side=POLICY['min_side'], max_bytes=POLICY['max_bytes'])
    if signal:
        result['reasons'].append(signal)
    ctx.log(('채택: ' if result['passed'] else '갤러리 제외: ') + f"{check.get('width')}x{check.get('height')} "
            + (', '.join(result['reasons']) or mapping_evidence) + ': ' + url, 'INFO' if result['passed'] else 'WARNING')
    cache[url] = (result, hashlib.sha256(data).hexdigest())
    return cache[url]


def pick_images(ctx, groups, cache, mapping_evidence):
    candidates, hashes = [], set()
    for group in groups:
        ctx.checkpoint()
        for url in group:
            screened = screen(ctx, url, cache, mapping_evidence)
            if not screened:
                continue
            result, digest = screened
            if result['passed']:
                if digest in hashes:
                    ctx.log('같은 사진(내용 해시 동일) 중복 제외: ' + url, 'INFO')
                else:
                    hashes.add(digest)
                    candidates.append({'url': url, 'validation': result})
                    if url != group[-1]:
                        ctx.log('썸네일 대신 같은 사진의 Cafe24 big 원본 사용: ' + url, 'INFO')
                break
        if len(candidates) >= 6:
            break
    main, extras, warning = gallery_slots(candidates)
    if warning:
        ctx.log(warning, 'WARNING')
    return main or '', ','.join(extras)


def pixels(im):
    flat = getattr(im, 'get_flattened_data', None)     # Pillow 14부터 getdata 제거 예정
    return list(flat() if flat else im.getdata())


def content_signal(im):
    """규칙 기반 보조 검사(완벽한 의미 인식 아님): 글자 위주 라벨/고시, 전폭 흰 구분선 콜라주."""
    im = im.convert('RGB')
    im.thumbnail((480, 480))
    w, h = im.size
    px = pixels(im)
    dark = {i for i, rgb in enumerate(px) if max(rgb) < 170}
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


# ───────────────────────── 상세 HTML ─────────────────────────
def detail_html(ctx, doc, final, style, color_code, notice, cache):
    urls = []
    nodes = doc.select('#prdDetail2 img') or doc.select('#prdDetail img')
    for image in nodes:
        if image.find_parent(class_=re.compile(r'review|board', re.I)):
            continue
        raw = source_of(image)
        if not raw:
            continue
        url = urljoin(final, raw)
        if url.startswith('//'):
            url = 'https:' + url
        parts = urlsplit(url)
        # 상품 본문 /<브랜드>/detail/ 만. 공통 머리·꼬리(/img/), 행사(/web/upload/)는 요청하지 않음.
        if parts.hostname != DETAIL_HOST or '/detail/' not in parts.path or DETAIL_SKIP.search(parts.path):
            continue
        url = urlunsplit(('https', parts.netloc, parts.path, parts.query, ''))     # 레거시 http 링크 → https 원본
        if url not in urls:
            urls.append(url)
    own = [u for u in urls if has_code(unquote(urlsplit(u).path.rsplit('/', 1)[-1]).replace('_', '-'), style)]
    if own and len(own) < len(urls):
        ctx.log(f'상세 본문 중 다른 품번 파일 {len(urls) - len(own)}장 제외(파일명 품번 기준)', 'INFO')
        urls = own
    if color_code:
        mine = [u for u in urls if has_code(unquote(urlsplit(u).path).upper(), f'{style}-{color_code}'.upper())
                or not re.search(re.escape(style) + r'-[A-Z0-9]{2,4}', unquote(urlsplit(u).path), re.I)]
        if mine and len(mine) < len(urls):
            ctx.log(f'상세 본문 중 다른 색상 파일 {len(urls) - len(mine)}장 제외', 'INFO')
            urls = mine
    parts = []
    for url in urls:
        ctx.checkpoint()
        if url not in cache:
            cache[url] = check_public_image(url, max_bytes=20_000_000, checkpoint=ctx.checkpoint)
        check = cache[url]
        if check['status'] == 'accessible':
            parts.append('<img src="' + html.escape(url, quote=True) + '" style="max-width:100%;height:auto;">')
        else:
            ctx.log(f"상세 이미지 제외({check['status']}): {url}: " + '; '.join(check['reasons']), 'WARNING')
    if not parts:
        ctx.log('상품 본문 상세 이미지가 없거나 공개 접근 불가: 상세는 고시 표만', 'WARNING')
    if notice:
        rows = ''.join('<tr><th style="padding:6px 10px;border:1px solid #ddd;background:#f6f6f6;text-align:left;white-space:nowrap;">'
                       + html.escape(k) + '</th><td style="padding:6px 10px;border:1px solid #ddd;">' + html.escape(v) + '</td></tr>'
                       for k, v in notice.items())
        parts.append('<h3>상품정보고시</h3><table style="width:100%;border-collapse:collapse;font-size:13px;"><tbody>' + rows + '</tbody></table>')
    else:
        ctx.log('상품정보고시(#desctable) 없음: 고시 표 생략', 'WARNING')
    value = ''.join(parts)
    if len(value.encode('utf-16-le')) // 2 > EXCEL_CELL:
        raise CrawlError('상세 HTML Excel 셀 한도 초과. 원본을 임의 절단하지 않습니다.')
    return value


# ───────────────────────── 입력 해석·실행 ─────────────────────────
def interpretations(raw):
    """'AWJU67301-403'/'AWJU67301_403' → (기본, 색상)을 먼저, 그다음 입력 전체를 기본 품번으로."""
    out = []
    match = re.fullmatch(r'([A-Z0-9][A-Z0-9-]*?[0-9][A-Z0-9]*)[_-]([A-Z0-9]{2,4})', raw)
    if match:
        out.append((match[1], match[2]))
    if re.fullmatch(r'[A-Z0-9][A-Z0-9_-]*', raw) and '_' not in raw:
        out.append((raw, None))
    return out


def crawl_product(ctx, number, base, requested, emitted):
    url = SITE + '/product/detail.html?product_no=' + number
    text, doc, final = page(ctx, url)
    found = identity(text, doc, base)
    if found is None:
        ctx.log('다른 품번 상품 제외: ' + final, 'INFO')
        return 0
    style, code_color, evidence = found
    name = full_name(text, doc)
    if not name:
        raise CrawlError('상품명 없음')
    notice = notice_rows(doc)
    colors = colors_and_sizes(text, doc, notice.get('색상') or code_color)
    colors = OrderedDict((k, v) for k, v in colors.items() if k)
    if not colors:
        raise CrawlError('색상 옵션·고시 색상·품번 색상코드 모두 없음: 색상을 확인할 수 없음')
    single = len(colors) == 1
    if not single and code_color:
        ctx.log(f'품번코드 색상 {code_color}인데 색상 옵션이 {len(colors)}개: 색상코드는 비움', 'WARNING')
    brand = brand_of(doc, name)
    base_record = {'product_code': style, 'product_url': final, '원본 상품명': name}
    if brand:
        base_record['브랜드'] = brand
    base_record['product_name'] = clean_name(name, brand, [f'{style}-{code_color}' if code_color else '', style], list(colors))
    if ctx.enabled('price'):
        base_record['price'] = price(ctx, doc)
    if ctx.enabled('category'):
        base_record['category'] = category(doc)
    collect_regular_price(ctx, base_record, doc, 'cafe24')
    want_gallery = ctx.enabled('main_image') or ctx.enabled('additional_images')
    groups = gallery_groups(ctx, doc, final, number) if want_gallery else []
    chips = link_images(doc, final) if want_gallery and not single else {}
    cache, detail_cache, count = {}, {}, 0
    for color, entry in colors.items():
        ctx.checkpoint()
        color_code = code_color if single else ''
        if requested and requested not in (color_code, color.upper()):
            continue
        key = (style, color_code or color.casefold())
        if key in emitted:
            ctx.log(f'같은 색상 중복 등록 상품 제외: {color} ({final})', 'INFO')
            continue
        ctx.stage(f'색상 수집: {color}' + (f' ({color_code})' if color_code else ''))
        record = dict(base_record, color=color, color_name=color, color_code=color_code)
        if ctx.enabled('size'):
            record['size'] = ','.join(entry['sizes'])
            record['사이즈별 재고'] = ','.join(f'{k}:{v}' for k, v in entry['stock'].items())
        if want_gallery:
            if single:
                main, extras = pick_images(ctx, groups, cache, f'{evidence}, 한 상품 페이지 = 한 색상({color}) 공식 갤러리')
            elif color in chips:
                main, extras = pick_images(ctx, [[chips[color]]], cache, f'{evidence}, 공식 색상칩 link_image({color})')
                ctx.log(f'여러 색상 상품: {color}는 색상칩 지정 컷만 사용(공용 갤러리는 색상 연결 근거 없음)', 'WARNING')
            else:
                main, extras = '', ''
                ctx.log(f'여러 색상 상품인데 {color} 색상칩 사진 없음: 사진 빈칸, 검수 필요(공용 갤러리를 임의 배정하지 않음)', 'WARNING')
            if ctx.enabled('main_image'):
                record['main_image'] = main
            if ctx.enabled('additional_images'):
                record['additional_images'] = extras
        if ctx.enabled('detail_page'):
            record['detail_page'] = detail_html(ctx, doc, final, style, color_code, notice, detail_cache)
        ctx.emit_result(record)
        emitted.add(key)
        count += 1
    return count


def crawl(ctx):
    ctx.log('올포유(한성몰) 크롤러 ' + REVISION, 'INFO')
    raw = re.sub(r'\s+', '', str(ctx.product_code or '')).upper()
    options = interpretations(raw)
    if not options:
        ctx.fail_item('품번 형식 오류: 기본 품번 또는 기본품번-색상코드(_ 가능)로 입력하세요.')
        raise CrawlError('품번 형식 오류: ' + raw)
    errors, emitted, checked, unverified = [], set(), [], []
    for base, requested in options:
        ctx.stage('한성몰 품번 검색: ' + base)
        try:
            hits = search(ctx, base)
        except CrawlError as exc:
            ctx.fail_item('검색 실패: ' + str(exc))
            raise
        checked.append(base)
        matched = False
        for number, label in hits:
            ctx.checkpoint()
            try:
                found = crawl_product(ctx, number, base, requested, emitted)
                matched = matched or found > 0
            except Blocked as exc:
                ctx.fail_item(str(exc))
                raise
            except NoCode as exc:
                unverified.append(number)
                ctx.log(f'상품 {number}({label or "-"}) 제외: {exc}', 'WARNING')
            except CrawlError as exc:
                message = f'상품 {number}({label or "-"}): {exc}'
                errors.append(message)
                ctx.fail_item(message)
        if matched or errors:
            break
    if not emitted and not errors:
        message = '일치하는 한성몰 품번' + ('/지정 색상' if any(r for _, r in options) else '') + ' 상품 없음: ' + raw + ' (검색: ' + ', '.join(checked) + ')' + (
            f' · 품번 근거 없는 상품 {len(unverified)}개 제외' if unverified else '')
        ctx.fail_item(message)
        raise CrawlError(message)
    if errors:
        raise CrawlError(' / '.join(errors))
