PORTAL_API_VERSION = 1
"""올리비아로렌 공식몰(ovlr.kr, Cafe24) — 수동 색상 매핑(URL 방식).

사용자 단독 올리비아로렌 크롤러(2026-11-25 전달)의 구조를 허브 수동 매핑 SDK로 옮김:
- 사이트 검색(search.html)은 화면 JS(datarize AI 검색)로 그려 서버 HTML에 결과가 없음
  → 카테고리 목록(SHOP 104 · OUTLET 114 · SALE 530)을 훑어 '상품번호 → 상품명' 색인을 만들고
  상품명 끝 품번(…티셔츠_OLBAL26W9181)으로 찾음. 색인은 작업 폴더(ctx.output_dir)에 12시간 재사용,
  색인에 없으면 한 번 새로 만들어 다시 찾음. 상품번호(750)·상품 URL 직접 입력도 가능.
- 상세 /product/detail.html?product_no= : var product_name, 판매가 #span_product_price_sale→text,
  정상가 #span_product_price_custom(regular_price cafe24 프로필, 판매가로 대체 안 함).
- 색상·사이즈: option_stock_data '챠콜 그레이(05)-090' → 색상명 챠콜 그레이 / 색상코드 05.
  (05)가 실제 색상코드인 근거: 상세 모델컷 파일명 OLBAL26W918105_1.jpg 와 같은 코드.
  품절·숨김 사이즈도 그 색상의 사이즈로 유지(색상별 사이즈는 팔레트 size로 전달).
- 사진: 갤러리 big·extra(모든 색상 공통) + 상세 모델컷(ovlr.speedgabia.com/product/품번/…)을
  전부 매핑 후보로 보냄. 사진 색감으로 색상을 배정하지 않음(사람이 색상 매핑 화면에서 선택).
- 상세: #prdDetail 상품 이미지(공통 인트로 00_intro·프로모션 배너 제외)
  + 상품정보·실측 이미지 ovlr.speedgabia.com/details/품번_details.jpg(본문에 없어도 있으면 붙임.
    없는 파일은 speedgabia가 err_img/err_404.gif로 보내므로 그 응답은 '없음'으로 처리)
  + '상품 상세정보' 표(상품명·품번·색상·사이즈·상품요약정보·#desctable 고시가 있으면 고시 항목)
  + '사이즈 정보' 표(옵션 사이즈, 색상마다 다르면 색상별 행).
  2026-11-26 실사이트 확인: 상세 하단 '상품 상세정보·사이즈 정보'는 세정 iframe
  fapi.sejung.co.kr/size_spec.html?style_cd=품번 이 JSON API로 그림:
    /searchOnlineStyleInfo/getStyleInfo  (상품명·색상·사이즈·소재·성별·시즌·제조국·세탁·제조년월)
    /searchOnlineStyleInfo/getSizeSpec1  (측정 항목 헤더, input_seq_num 순)
    /searchOnlineStyleInfo/getSizeSpec2  (사이즈별 측정값)
  → 이 API로 화면과 같은 두 표를 만듦(헤더에 있는 항목만, 화면과 같게). API에 실측이 있으면
    details 이미지는 중복이라 생략. API가 비거나 실패하면 details 이미지 + 페이지 정보로 대체 표.
검증: 오프라인 fixture 테스트만(tests/test_olivialauren_official.py). 실사이트 실행 확인은 아직 없음.
"""

import hashlib
import html
import io
import json
import os
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

REVISION = '2026-11-26-olivialauren-manual-3'
SITE = 'https://ovlr.kr'
SITE_HOSTS = ('ovlr.kr', 'www.ovlr.kr')
GALLERY_HOSTS = ('ovlr.kr', 'www.ovlr.kr', 'ecimg.cafe24img.com')
DETAIL_HOST = 'ovlr.speedgabia.com'
SPEC_URL = 'https://ovlr.speedgabia.com/details/{}_details.jpg'
SEJUNG_API = 'https://fapi.sejung.co.kr/searchOnlineStyleInfo/'
BRAND_ALIASES = ['올리비아로렌', '올리비아 로렌', 'OLIVIA LAUREN', 'OLIVIALAUREN']
INDEX_CATEGORIES = OrderedDict([('SHOP', '104'), ('OUTLET', '114'), ('SALE', '530')])
INDEX_FILE = 'olivialauren_code_index.json'
INDEX_TTL = 12 * 3600
INDEX_REBUILD_AFTER = 600         # 10분 넘은 색인에서 못 찾으면 한 번 새로 만듦
INDEX_MAX_PAGES = 60
LIST_GAP = .8
REQUEST_GAP = 1.0
RETRY_HTTP = (5, 20)
RETRY_CONN = (5, 60)
POLICY = dict(checked='2026-11-09', source='https://marketplace.coupang.com/mba/listing', min_side=500,
              max_bytes=10_000_000, max_aspect=1.8, formats=('JPEG', 'PNG', 'WEBP'),
              note='정지 WebP·비율 1.8은 프로젝트 정책. 등록 승인 전체는 미검증.')
SIZE_DIR = re.compile(r'/web/product/(extra/)?(big|medium|small|tiny)/')
CODE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,}[0-9][A-Za-z0-9]{3,})(?![A-Za-z0-9])')
PNO = re.compile(r'(?:[?&]product_no=|/product/[^/?#]+/)(\d+)(?=[/&#?]|$)', re.I)
NEGATIVE = re.compile(r'size|guide|chart|measure|banner|notice|promotion|intro|사이즈|실측|측정|고시', re.I)
DETAIL_SKIP = re.compile(r'/00_intro|/promotion/|/banner|/web/upload/|/skin/|btn_|icon|ico_|logo|blank|noimg|\.svg', re.I)


class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


class Blocked(CrawlError):
    """사이트가 연결을 계속 끊음."""


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
                raise Blocked('올리비아로렌몰이 연결을 계속 끊음(' + type(exc).__name__ + '): ' + url) from exc
            wait = conn_waits.pop(0)
            ctx.log(f'{type(exc).__name__} → {wait}초 후 다시 시도: {url}', 'WARNING')
        else:
            ctx.checkpoint()
            if len(raw) > limit:
                raise CrawlError('응답 용량 초과: ' + url)
            if urlsplit(final).hostname not in SITE_HOSTS:
                raise CrawlError('올리비아로렌몰 밖으로 이동: ' + final)
            for encoding in ('utf-8-sig', 'cp949'):
                try:
                    return raw.decode(encoding), final
                except UnicodeDecodeError:
                    continue
            return raw.decode('utf-8', errors='ignore'), final
        pause(ctx, wait)


def has_code(text, code):
    """OLBAL26W9181 → '…티셔츠_OLBAL26W9181'에 맞음 / 'OLBAL26W91812'에는 안 맞음."""
    return bool(re.search(r'(?<![A-Za-z0-9])' + re.escape(code) + r'(?![A-Za-z0-9])', (text or '').replace('_', ' '), re.I))


# ───────────────────────── 품번 색인 ─────────────────────────
def list_items(text):
    """카테고리 목록 → {상품번호: 상품명 + 주소}. 머리·꼬리·최근 본 상품 제외."""
    doc = BeautifulSoup(text, 'html.parser')
    for node in doc.select('header, #header, footer, #footer, [class*=recent], #float-menu'):
        node.decompose()
    found = {}
    for anchor in doc.select('a[href*="/product/"]'):
        href = anchor.get('href', '')
        match = PNO.search(href)
        if not match or '/product/list' in href or '/product/search' in href:
            continue
        label = anchor.get_text(' ', strip=True) or ' '.join(i.get('alt', '') for i in anchor.select('img'))
        found[match[1]] = (found.get(match[1], '') + ' ' + label + ' ' + unquote(href)).strip()
    return found


def load_index(ctx):
    path = ctx.output_dir / INDEX_FILE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        age = time.time() - float(data.get('ts', 0))
        if 0 <= age < INDEX_TTL and isinstance(data.get('items'), dict) and data['items']:
            return data['items'], age
    except (OSError, ValueError, TypeError):
        pass
    return None, None


def build_index(ctx):
    items = OrderedDict()
    ctx.stage('품번 색인 만들기(카테고리 목록, 처음 한 번 1~2분)')
    for label, number in INDEX_CATEGORIES.items():
        before = len(items)
        for page_no in range(1, INDEX_MAX_PAGES + 1):
            text, _ = fetch_text(ctx, f'{SITE}/product/list.html?cate_no={number}&page={page_no}')
            got = list_items(text)
            new = [k for k in got if k not in items]
            for key in new:
                items[key] = got[key]
            if not new:
                break
            pause(ctx, LIST_GAP)
        ctx.log(f'색인 {label}: 상품 +{len(items) - before}', 'INFO')
    if not items:
        raise CrawlError('카테고리 목록에서 상품을 하나도 찾지 못함(색인 실패)')
    path = ctx.output_dir / INDEX_FILE
    temp = path.with_suffix('.tmp')
    try:
        temp.write_text(json.dumps({'ts': time.time(), 'items': items}, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, path)
    except OSError as exc:
        ctx.log('색인 저장 실패(이번 품번에만 사용): ' + str(exc), 'WARNING')
    ctx.log(f'품번 색인 완료: 상품 {len(items)}개', 'INFO')
    return items


def search(ctx, code):
    """→ [(상품번호, 색인 문구)] 최신 상품번호 순."""
    items, age = load_index(ctx)
    fresh = items is None
    if fresh:
        items = build_index(ctx)
    else:
        ctx.log(f'품번 색인 재사용(상품 {len(items)}개, {age / 3600:.1f}시간 전)', 'INFO')
    hits = [(no, text) for no, text in sorted(items.items(), key=lambda kv: -int(kv[0])) if has_code(text, code)]
    if not hits and not fresh and age > INDEX_REBUILD_AFTER:
        ctx.log('색인에 없음 → 색인을 새로 만들어 다시 찾기', 'INFO')
        items = build_index(ctx)
        hits = [(no, text) for no, text in sorted(items.items(), key=lambda kv: -int(kv[0])) if has_code(text, code)]
    return hits


def direct_number(value):
    """'750' / 올리비아로렌 상품 URL → 상품번호."""
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


def summary_text(doc):
    for selector in ('tr[rel="상품요약정보"] td', '.infoArea p.summary', '.headingArea p.summary'):
        node = doc.select_one(selector)
        text = re.sub(r'\s+', ' ', node.get_text(' ', strip=True)) if node else ''
        if text:
            return text
    return ''


def missing_file(check):
    """speedgabia는 없는 파일을 err_img/err_404.gif(200)로 돌려보냄 → 없음."""
    final = urlsplit(check.get('final_url') or '')
    return '/err_img/' in final.path or 'err_404' in final.path


def product_soldout(doc):
    for node in doc.select('.xans-product-action [class*=soldout], .xans-product-action .sub_sold, .btnArea [class*=soldout], #btnSoldout'):
        label = re.sub(r'\s+', '', node.get_text()).upper()
        hidden = any('displaynone' in (p.get('class') or []) or 'display:none' in (p.get('style') or '').replace(' ', '')
                     for p in [node] + [q for q in node.parents if hasattr(q, 'get')])
        if label in ('품절', 'SOLDOUT') and not hidden:
            return True
    return False


def split_color(value):
    """'챠콜 그레이(05)' → ('챠콜 그레이', '05'). 괄호 코드가 없으면 이름만."""
    match = re.fullmatch(r'(.*?)\s*\(([A-Za-z0-9]{1,4})\)\s*', value)
    return (match[1].strip(), match[2].upper()) if match and match[1].strip() else (value.strip(), '')


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
        if label and label not in ('홈', 'HOME', 'Home', '현재 위치', '>', '브랜드', '기획전') and label not in BRAND_ALIASES and label not in trail:
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


def detail_urls(doc, final, style):
    """#prdDetail 상품 본문 이미지(공통 인트로·프로모션·배너 제외). (전체, 품번 폴더 모델컷)."""
    urls, shots = [], []
    for image in doc.select('#prdDetail img, #prdDetail2 img'):
        if image.find_parent(class_=re.compile(r'review|board', re.I)):
            continue
        parts = absolute(source_of(image), final)
        if not parts or parts.hostname not in (DETAIL_HOST,) + SITE_HOSTS or DETAIL_SKIP.search(parts.path):
            continue
        url = urlunsplit(('https', parts.netloc, parts.path, parts.query, ''))
        if url in urls:
            continue
        urls.append(url)
        name = unquote(parts.path.rsplit('/', 1)[-1]).upper()
        if parts.hostname == DETAIL_HOST and parts.path.startswith('/product/') and style and style.upper() in name:
            shots.append(url)
    return urls, shots


def file_color_code(url, style):
    name = unquote(urlsplit(url).path.rsplit('/', 1)[-1]).upper()
    match = re.search(re.escape(style.upper()) + r'([A-Z0-9]{2,3})(?=[_.\-])', name)
    return match[1] if match else ''


def parse_product(ctx, number, code, direct):
    url = SITE + '/product/detail.html?product_no=' + number
    text, final = fetch_text(ctx, url)
    pause(ctx, REQUEST_GAP)
    doc = BeautifulSoup(text, 'html.parser')
    full = re.sub(r'\s+', ' ', js_string(text, 'product_name')).strip()
    if not full:
        node = doc.select_one('.infoArea .name, .headingArea h2, h2.name')
        full = node.get_text(' ', strip=True) if node else ''
    if not full:
        raise CrawlError('상품명 없음')
    found = [m for m in CODE.finditer(full.replace('_', ' '))]
    if not direct and not has_code(full, code):
        return None
    match = next((m for m in found if m[1].upper() == code), None) or (found[-1] if found else None)
    style = match[1].upper() if match else code
    notice = notice_rows(doc)
    raw_colors = colors_and_sizes(text, doc, notice.get('색상') or '')
    palette = []
    for raw, entry in raw_colors.items():
        if not raw:
            continue
        name, color_code = split_color(raw)
        palette.append(dict(name=name, code=color_code, raw=raw, sizes=entry['sizes'], stock=entry['stock']))
    if not palette:
        raise CrawlError('색상 옵션·고시 색상 모두 없음: 색상 후보를 만들 수 없음')
    stripped = re.sub(r'[\s_-]*(?<![A-Za-z0-9])' + re.escape(style) + r'(?![A-Za-z0-9])', ' ', full, flags=re.I)
    cleaned = clean_product_name(stripped, BRAND_ALIASES, [style]).strip(' _-/') or full
    product = dict(number=number, url=final, style=style, full=full, name=cleaned, palette=palette, notice=notice,
                   summary=summary_text(doc))
    if ctx.enabled('price'):
        product['price'] = price(ctx, doc)
    if ctx.enabled('category'):
        product['category'] = category(doc)
    if ctx.enabled('regular_price'):
        holder = {}
        collect_regular_price(ctx, holder, doc, 'cafe24')
        product['regular_price'] = holder.get('regular_price')
    product['details'], product['shots'] = detail_urls(doc, final, style)
    product['groups'] = gallery_groups(ctx, doc, final, number)
    codes = {c['code'] for c in palette if c['code']}
    hints = OrderedDict()
    for shot in product['shots']:
        found_code = file_color_code(shot, style)
        if found_code:
            hints.setdefault(found_code, 0)
            hints[found_code] += 1
    if hints:
        named = {c['code']: c['name'] for c in palette}
        ctx.log('상세 모델컷 파일명 색상코드(매핑 참고): ' + ', '.join(
            f"{k}={named.get(k, '옵션에 없음')} {v}장" for k, v in hints.items()), 'INFO')
        if codes and not set(hints) <= codes:
            ctx.log('파일명 색상코드 중 옵션에 없는 코드가 있음: ' + ', '.join(sorted(set(hints) - codes)), 'WARNING')
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
TH = 'padding:6px 10px;border:1px solid #ddd;background:#f6f6f6;text-align:left;white-space:nowrap;'
TD = 'padding:6px 10px;border:1px solid #ddd;'


def info_table(title, rows):
    if not rows:
        return ''
    body = ''.join('<tr><th style="' + TH + '">' + html.escape(k) + '</th><td style="' + TD + '">' + html.escape(v) + '</td></tr>'
                   for k, v in rows)
    return ('<h3>' + html.escape(title) + '</h3><table style="width:100%;border-collapse:collapse;font-size:13px;"><tbody>'
            + body + '</tbody></table>')


def merged_palette(products):
    colors = OrderedDict()
    for product in products:
        for color in product['palette']:
            sizes = colors.setdefault(color['raw'], [])
            sizes.extend(s for s in color['sizes'] if s not in sizes)
    return colors


def info_rows(products):
    """상품 상세정보: 상품명·품번·색상·사이즈·상품요약정보 + 고시(#desctable)가 있으면 그 항목."""
    first = products[0]
    colors = merged_palette(products)
    sizes = []
    for values in colors.values():
        sizes.extend(s for s in values if s not in sizes)
    rows = OrderedDict([('상품명', first['name']), ('품번', first['style'])])
    if colors:
        rows['색상'] = ' / '.join(colors)
    if sizes:
        rows['사이즈'] = ' / '.join(sizes)
    summary = next((p.get('summary') for p in products if p.get('summary')), '')
    if summary:
        rows['상품 설명'] = summary
    for product in products:
        for key, value in product['notice'].items():
            if key not in rows and not (key == '색상' and '색상' in rows):
                rows[key] = value
    return list(rows.items()), colors, sizes


def size_rows(colors, sizes):
    if not sizes:
        return []
    if len({tuple(v) for v in colors.values() if v}) <= 1:
        return [('사이즈', ' / '.join(sizes))]
    return [(name, ' / '.join(values)) for name, values in colors.items() if values]


def spec_image(ctx, style, cache):
    """본문에 없을 때 details/품번_details.jpg(상품정보·실측 표 이미지)를 확인. 없으면 ''."""
    url = SPEC_URL.format(style)
    ctx.checkpoint()
    check = check_public_image(url, timeout=10, max_bytes=20_000_000, retries=1, checkpoint=ctx.checkpoint)
    cache[url] = check
    if check['status'] == 'accessible' and not missing_file(check):
        ctx.log('상품정보·실측 이미지 추가: ' + url, 'INFO')
        return url
    if check['status'] == 'unknown':
        ctx.log('상품정보·실측 이미지 확인 일시 오류(생략): ' + url + ': ' + '; '.join(check['reasons']), 'WARNING')
    return ''


def fetch_json(ctx, url):
    """세정 사이즈 API(JSON). 실패하면 None(상세는 대체 표로 계속)."""
    for wait in (0, 3):
        if wait:
            pause(ctx, wait)
        ctx.checkpoint()
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                        '(KHTML, like Gecko) Chrome/124.0 Safari/537.36',
                                        'Accept': 'application/json', 'Referer': 'https://fapi.sejung.co.kr/size_spec.html'})
        try:
            with build_opener().open(request, timeout=20) as response:
                if urlsplit(response.geturl()).hostname != 'fapi.sejung.co.kr':
                    return None
                data = json.loads(response.read(2_000_000).decode('utf-8-sig'))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            ctx.log('세정 사이즈 API 응답 실패(' + type(exc).__name__ + '): ' + url, 'WARNING')
            continue
        return data.get('resultData') if isinstance(data, dict) and data.get('resultCode') == 'OK' else None
    return None


STYLE_FIELDS = (('상품명', 'style_nm'), ('색상', 'color'), ('사이즈', 'size'), ('소재', 'material'), ('성별', 'sex'),
                ('시즌', 'season'), ('제조국', 'made_in'), ('세탁', 'wash'), ('제조년월', 'prod_date'))


def sejung_spec(ctx, style):
    """(상품 상세정보 행, 사이즈 헤더, 사이즈 행). 데이터 없으면 빈 값."""
    query = '?style_cd=' + quote(style)
    info = fetch_json(ctx, SEJUNG_API + 'getStyleInfo' + query)
    rows = []
    if isinstance(info, dict):
        rows = [(label, re.sub(r'\s+', ' ', str(info.get(key) or '')).strip()) for label, key in STYLE_FIELDS]
        rows = [(k, v) for k, v in rows if v]
    head = fetch_json(ctx, SEJUNG_API + 'getSizeSpec1' + query)
    body = fetch_json(ctx, SEJUNG_API + 'getSizeSpec2' + query) if head else None
    header, table = [], []
    if isinstance(head, list) and isinstance(body, list):
        def seq(item):
            try:
                return float(item.get('input_seq_num') or 0)
            except (TypeError, ValueError):
                return 0
        head = sorted((h for h in head if isinstance(h, dict) and h.get('msmt_desc')), key=seq)
        header = [str(h['msmt_desc']).strip() for h in head]
        grouped = OrderedDict()
        for row in body:
            if isinstance(row, dict) and row.get('size_cd'):
                grouped.setdefault(str(row['size_cd']).strip(), {})[str(row.get('input_seq_num'))] = row.get('msmt_val')
        for size in sorted(grouped):
            values = grouped[size]
            table.append([size] + [str(values.get(str(h.get('input_seq_num'))) or '-') for h in head])
        if not table:
            header = []
    return rows, header, table


def spec_table(header, table):
    cell = 'padding:6px 10px;border:1px solid #ddd;text-align:center;'
    head = ''.join('<th style="' + cell + 'background:#f6f6f6;">' + html.escape(h) + '</th>' for h in ['사이즈'] + header)
    body = ''.join('<tr>' + ''.join('<td style="' + cell + '">' + html.escape(v) + '</td>' for v in row) + '</tr>' for row in table)
    return ('<h3>사이즈 정보</h3><p style="font-size:12px;color:#666;text-align:right;">[단위 : CM]</p>'
            '<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr>' + head + '</tr></thead><tbody>'
            + body + '</tbody></table><p style="font-size:12px;color:#666;">- 모니터 해상도 또는 사양에 따라 색상 차이가 있을 수 있습니다.<br>'
            '- 소재 및 측정 방법에 따라 1~3cm의 오차가 있을 수 있습니다.</p>')


def shared_detail(ctx, products):
    urls, cache = [], {}
    for product in products:
        urls.extend(u for u in product['details'] if u not in urls)
    style = products[0]['style']
    spec_rows, header, table = sejung_spec(ctx, style)
    if table:
        ctx.log(f'세정 사이즈 API 실측표 사용: {len(table)}개 사이즈 × {len(header)}개 항목', 'INFO')
        urls = [u for u in urls if '/details/' not in urlsplit(u).path]          # 같은 내용 이미지 중복 방지
        has_spec = True
    else:
        ctx.log('세정 사이즈 API 실측 없음(' + style + '): details 이미지·옵션 사이즈로 대체', 'WARNING')
        has_spec = any('/details/' in urlsplit(u).path for u in urls)
        if not has_spec:
            found = spec_image(ctx, style, cache)
            if found:
                urls.append(found)
                has_spec = True
    parts = []
    for url in urls:
        ctx.checkpoint()
        if url not in cache:
            cache[url] = check_public_image(url, timeout=10, max_bytes=20_000_000, retries=1, checkpoint=ctx.checkpoint)
        check = cache[url]
        if check['status'] == 'accessible' and missing_file(check):
            ctx.log('상세 이미지 제외(원본 없음 → err_404): ' + url, 'WARNING')
            if '/details/' in urlsplit(url).path and not table:
                has_spec = False
        elif check['status'] == 'accessible':
            parts.append('<img src="' + html.escape(url, quote=True) + '" alt="상품 상세" style="max-width:100%;height:auto;">')
        elif check['status'] == 'unknown':
            raise CrawlError('상세 이미지 확인 중 일시 오류: ' + url + ': ' + '; '.join(check['reasons']))
        else:
            ctx.log(f"상세 이미지 제외(inaccessible): {url}: " + '; '.join(check['reasons']), 'WARNING')
    if not parts:
        ctx.log('상품 본문 상세 이미지가 없거나 공개 접근 불가: 상세는 정보 표만', 'WARNING')
    rows, colors, sizes = info_rows(products)
    if spec_rows:
        known = {k for k, _ in spec_rows}
        extra = [(k, v) for k, v in rows if k not in known]
        parts.append(info_table('상품 상세정보', spec_rows + extra))
    else:
        parts.append(info_table('상품 상세정보', rows))
    if table:
        parts.append(spec_table(header, table))
    else:
        fallback = info_table('사이즈 정보', size_rows(colors, sizes))
        if fallback:
            note = ('위 상품정보 이미지의 실측 사이즈를 확인하세요.' if has_spec
                    else '공식몰에 실측 사이즈가 제공되지 않아 옵션 사이즈만 표기합니다.')
            parts.append(fallback + '<p style="font-size:12px;color:#666;">' + html.escape(note) + '</p>')
    if not any(p['notice'] for p in products) and not spec_rows:
        ctx.log('상품정보고시·세정 상품정보 없음: 상품 상세정보는 상품명·색상·사이즈·상품요약정보로 작성', 'INFO')
    compact, overflow = excel_detail(''.join(parts))
    if overflow:
        raise CrawlError('공통 상세 HTML Excel 셀 한도(32,767자) 초과. 원본을 임의 절단하지 않습니다.')
    return compact


def common(values):
    present = [v for v in values if v is not None]
    return present[0] if present and all(v == present[0] for v in present) else None


# ───────────────────────── 실행 ─────────────────────────
def crawl(ctx):
    ctx.log('올리비아로렌 수동 매핑 크롤러 ' + REVISION, 'INFO')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        message = '수동 색상 매핑은 대표이미지 또는 추가이미지 선택이 필요합니다.'
        ctx.fail_item(message)
        raise CrawlError(message)
    raw = re.sub(r'\s+', '', str(ctx.product_code or ''))
    number = direct_number(raw)
    code = raw.upper()
    if not number and not re.fullmatch(r'[A-Z]{2,}[0-9][A-Z0-9]{3,}', code):
        message = '품번 형식 오류: 기본 품번(예: OLBAL26W9181), 상품번호 또는 올리비아로렌 상품 URL을 입력하세요.'
        ctx.fail_item(message)
        raise CrawlError(message + ' / ' + raw)
    try:
        if number:
            hits = [(number, '상품번호 직접 입력')]
        else:
            ctx.stage('품번 색인 검색: ' + code)
            hits = search(ctx, code)
            if not hits and re.search(r'[0-9]{5,}$', code) and len(code) >= 12:
                base = code[:-2]
                hits = search(ctx, base)
                if hits:
                    ctx.log(f'{code}는 색인에 없고 끝 2자리를 뺀 {base}가 있음: 색상 지정 입력은 지원하지 않아 {base} 전체 색상을 수집', 'WARNING')
                    code = base
    except CrawlError as exc:
        ctx.fail_item('검색 실패: ' + str(exc))
        raise
    if not hits:
        message = '카테고리 색인(SHOP·OUTLET·SALE)에서 품번을 찾지 못함: ' + code
        ctx.fail_item(message)
        raise CrawlError(message)
    products, errors = [], []
    for no, label in hits:
        ctx.checkpoint()
        ctx.stage(f'상품 {no} 수집')
        try:
            product = parse_product(ctx, no, code, bool(number))
        except Blocked as exc:
            ctx.fail_item(str(exc))
            raise
        except CrawlError as exc:
            errors.append(f'상품 {no}: {exc}')
            ctx.fail_item(errors[-1])
            continue
        if product is None:
            ctx.log(f'상품 {no}: 상품명 품번이 {code}와 다름 → 제외', 'INFO')
            continue
        products.append(product)
    if not products:
        message = '정확히 일치하는 상품 없음: ' + code + (' / ' + ' / '.join(errors) if errors else '')
        if not errors:
            ctx.fail_item(message)
        raise CrawlError(message)
    style = products[0]['style']
    if number and not CODE.fullmatch(style):
        ctx.log('상품명에서 품번을 찾지 못해 입력값을 품번으로 사용: ' + style, 'WARNING')
    palette, by_key = [], {}
    for product in products:
        for color in product['palette']:
            key = (color['name'], color['code'])
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
              '원본 상품명': '\n'.join(dict.fromkeys(p['full'] for p in products))}
    colors = []
    for color in palette:
        value = {'color_name': color['name'], 'color_code': color['code']}
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
