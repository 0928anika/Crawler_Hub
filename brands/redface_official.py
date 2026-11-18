PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices
from detail_store import excel_detail

"""레드페이스 공식몰(theredface.com / Cafe24) -> Crawler Hub 로컬 JPEG 크롭 + 사람 색상 매핑.

- 갤러리(대표·추가) 사진은 그대로, 상세 통이미지는 '전체 폭 배경 여백'과 '세로 여백' 경계로만 개별 컷을 자른다.
- 색상은 Cafe24 색상 옵션의 공식 link_image(옵션 ↔ 이미지 DOM 연결)로 받은 사진만 사전 매핑한다.
  나머지 컷은 미매핑으로 보내 사람이 고른다. 색감·파일명·배너 문구로 색상을 추측하지 않는다(OCR 없음).
- 상세 HTML은 전 색상 공통: 공식 상세 이미지 URL + 상품정보고시 2열 표 + 공식 사이즈표.
"""
import ast
import html
import io
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageOps

REVISION = '2026-11-18-redface-1'
ORIGIN = 'https://theredface.com'
HOSTS = ('theredface.com', 'www.theredface.com', 'm.theredface.com')
ALIASES = ('레드페이스', 'REDFACE', 'RED FACE', 'THE REDFACE')
MAX_DOCUMENT = 3_000_000
MAX_IMAGE = 10_000_000
COLOR_TITLE = re.compile(r'색상|컬러|color|colour', re.I)
SIZE_TITLE = re.compile(r'사이즈|size|치수|호수', re.I)
SKIP_IMAGE = re.compile(r'/common/|/_d1/img/|/web/upload/icon|icon_|ico_|btn_|logo|banner|bn_|cashreceipt|escrow|allat|noimg|no_img|blank|'
                        r'/_wg/|/skin/|echosting|notice|care_|delivery|shipping|cs_info|배송|교환|반품|event|coupon|gift|사은품', re.I)
NOTICE_KEYS = re.compile(r'소재|제품소재|혼용|색상|치수|제조자|수입자|제조국|원산지|세탁|취급|제조연월|품질보증|A/S|책임자|종류|중량|사양')


class CrawlError(RuntimeError):
    pass


def unique(items):
    return list(dict.fromkeys(x for x in items if x))


def text(node):
    return re.sub(r'\s+', ' ', node.get_text(' ', strip=True)) if node else ''


def public_url(value, base=ORIGIN):
    value = str(value or '').strip()
    if value.startswith('//'):
        value = 'https:' + value
    parsed = urlsplit(urljoin(base, value))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise CrawlError('공개 URL이 아닙니다: ' + value[:120])
    url = parsed._replace(scheme='https').geturl()
    return quote(url, safe=":/?=&%+;,@!~*'()-._#")


def request(ctx, url, limit=MAX_DOCUMENT, optional=False, site_only=True):
    url = public_url(url)
    if site_only and urlsplit(url).hostname not in HOSTS:
        raise CrawlError('레드페이스 공식몰 밖 주소는 문서로 요청하지 않습니다: ' + url)
    for attempt in range(2):
        ctx.checkpoint()
        ctx.log('GET ' + url, 'INFO')
        failure, retry = None, False
        try:
            req = Request(url, headers={'User-Agent': 'Mozilla/5.0 CrawlerHub', 'Accept-Encoding': 'identity', 'Referer': ORIGIN + '/'})
            with build_opener().open(req, timeout=25) as response:
                data = bytearray()
                while True:
                    ctx.checkpoint()
                    chunk = response.read(min(65536, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > limit:
                        raise CrawlError('응답이 크기 제한을 넘었습니다: ' + url)
            result = bytes(data)
        except HTTPError as exc:
            failure = 'HTTP ' + str(exc.code) + ': ' + url
            if optional and exc.code in (403, 404, 410):
                ctx.log(failure + ' → 이 자료만 제외', 'WARNING')
                return None
            retry = exc.code in (408, 429, 500, 502, 503, 504)
        except (URLError, TimeoutError, OSError) as exc:
            failure = '네트워크 오류 ' + str(exc) + ': ' + url
            retry = True
        ctx.checkpoint()
        if failure is None:
            return result
        if not retry or attempt:
            raise CrawlError(failure)
        ctx.log(failure + ' → 1회 재시도', 'WARNING')
        for _ in range(15):
            ctx.checkpoint()
            time.sleep(.1)
    raise CrawlError('요청 미완료: ' + url)


def decode(raw):
    if isinstance(raw, str):
        return raw
    for enc in ('utf-8', 'cp949'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def code_token(code, value):
    return re.search(r'(?<![A-Z0-9])' + re.escape(code) + r'(?![A-Z0-9])', str(value or '').upper().replace('-', '')) is not None


# ───────────────────────── 검색 ─────────────────────────
def find_products(ctx, code):
    queue, seen, links = [ORIGIN + '/product/search.html?' + urlencode({'keyword': code})], set(), []
    while queue:
        ctx.checkpoint()
        url = queue.pop(0)
        if url in seen:
            continue
        if len(seen) >= 20:
            raise CrawlError('검색 결과 페이지가 20쪽을 넘습니다')
        seen.add(url)
        soup = BeautifulSoup(decode(request(ctx, url)), 'html.parser')
        for bad in soup.select('[class*="recent"], [class*="recommend"], [class*="related"], header, #header, footer, #footer'):
            bad.decompose()
        scope = soup.select_one('.xans-search-result') or soup.select_one('.prdList') or soup
        for a in scope.select('a[href*="/product/"]'):
            href = a.get('href', '')
            box = a.find_parent('li') or a
            label = text(box) + ' ' + ' '.join(i.get('alt', '') for i in box.select('img')) + ' ' + href
            if not code_token(code, label):
                continue  # 다른 품번(유사 품번 포함) 제외. 상세의 상품명·자체상품코드로 다시 확인한다.
            target = public_url(href, url)
            parsed = urlsplit(target)
            if parsed.hostname not in HOSTS:
                continue
            match = re.search(r'/product/[^/]+/(\d+)/', parsed.path)
            gid = match[1] if match else (parse_qs(parsed.query).get('product_no') or [''])[0]
            if gid.isdigit() and gid not in [g for g, _ in links]:
                links.append((gid, target))
        for a in soup.select('.ec-base-paginate a[href], .xans-search-paging a[href]'):
            href = a.get('href', '')
            if href.startswith(('#', 'javascript:')):
                continue
            page = (parse_qs(urlsplit(public_url(href, url)).query).get('page') or [''])[0]
            if page.isdigit():
                target = ORIGIN + '/product/search.html?' + urlencode({'keyword': code, 'page': page})
                if target not in seen:
                    queue.append(target)
    return links


# ───────────────────────── 상품 상세 ─────────────────────────
def js_string(raw, name):
    match = re.search(r'\b' + name + r"""\s*=\s*(['"])(.*?)\1\s*;""", raw)
    return match[2].strip() if match else ''


def split_color(label):
    label = re.sub(r'\s+', ' ', str(label or '')).strip()
    label = re.sub(r'\s*\[?(품절|일시품절|sold\s*out)\]?\s*$', '', label, flags=re.I).strip()
    for pattern, name_i, code_i in ((r'(.*?)\s*\(\s*([A-Za-z0-9]{1,6})\s*\)', 1, 2), (r'\(\s*([A-Za-z0-9]{1,6})\s*\)\s*(.+)', 2, 1),
                                    (r'([0-9]{2,3})\s+([A-Za-z가-힣].*)', 2, 1)):
        match = re.fullmatch(pattern, label)
        if match:
            return {'color_name': match[name_i].strip(), 'color_code': match[code_i].strip().upper()}
    return {'color_name': label, 'color_code': ''} if label else None


def option_rows(raw):
    match = re.search(r"\bvar\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")", raw)
    if not match:
        return []
    try:
        data = json.loads(ast.literal_eval(match[1]))
    except (ValueError, SyntaxError):
        return []
    return [v for v in data.values() if isinstance(v, dict)] if isinstance(data, dict) else []


def option_axes(row):
    names = row.get('option_name_original') or []
    values = row.get('option_value_orginal') or row.get('option_value_original') or []
    if not isinstance(names, list) or not isinstance(values, list):
        return None, None
    ci = next((i for i, n in enumerate(names) if COLOR_TITLE.search(str(n))), None)
    si = next((i for i, n in enumerate(names) if SIZE_TITLE.search(str(n))), None)
    color = str(values[ci]).strip() if ci is not None and ci < len(values) else None
    size = str(values[si]).strip() if si is not None and si < len(values) else None
    if ci is not None and ci == si and color and '/' in color:
        color, size = [p.strip() for p in color.split('/', 1)]
    return color, size


def info_rows(soup):
    out = {}
    for tr in soup.select('.infoArea tr, .xans-product-detaildesign tr'):
        th, td = tr.select_one('th'), tr.select_one('td')
        if th and td and text(th) and text(th) not in out:
            out[text(th)] = text(td)
    return out


def big_variant(url):
    return re.sub(r'/web/product/(extra/)?(medium|small|tiny|list)/', lambda m: '/web/product/' + (m[1] or '') + 'big/', url)


def parse_product(ctx, raw, code, url):
    soup = BeautifulSoup(raw, 'html.parser')
    info = info_rows(soup)
    original = (js_string(raw, 'product_name') or info.get('상품명', '') or text(soup.select_one('.headingArea h2'))
                or text(soup.select_one('.infoArea h2')))
    if not original:
        meta = soup.find('meta', attrs={'property': 'og:title'})
        original = meta.get('content', '') if meta else ''
    custom = info.get('자체상품코드', '') or info.get('상품코드', '') or js_string(raw, 'product_code')
    if not (code_token(code, original) or code_token(code, custom)):
        return None  # 상품명·자체상품코드 어디에도 정확한 품번이 없음
    name = re.sub(r'^\s*(\[[^\]]*\]\s*)+', '', original)
    name = re.sub(r'[\s_/(\[-]*(?<![A-Z0-9])' + re.escape(code) + r'(?:[_-][A-Z0-9]{1,4})?(?![A-Z0-9])[)\]]?', ' ', name, flags=re.I)
    for alias in ALIASES:
        name = re.sub(r'^\s*' + re.escape(alias) + r'(?=\s|$)', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip(' _-/')
    if not name:
        raise CrawlError('상품명이 비어 있습니다')
    page = url
    for tag in soup.select('link[rel="canonical"], meta[property="og:url"]'):
        value = (tag.get('href') or tag.get('content') or '').strip()
        gid = re.search(r'/product/[^/]+/(\d+)/|product_no=(\d+)', url)
        if value and gid and (gid[1] or gid[2]) in value:
            page = public_url(value)
            break
    colors, links = [], {}
    for row in option_rows(raw):
        color, _ = option_axes(row)
        entry = split_color(color) if color else None
        if entry and entry not in colors:
            colors.append(entry)
    for area in soup.select('[option_title]'):
        if not COLOR_TITLE.search(area.get('option_title', '')):
            continue
        for option in area.select('option, li[option_value], a[option_value]'):
            value = option.get('option_value') or option.get('value') or ''
            if value in ('*', '**', '') and not option.get('option_value'):
                continue
            entry = split_color(option.get('option_value') or text(option))
            if not entry:
                continue
            if entry not in colors:
                colors.append(entry)
            image = option.get('link_image') or ''
            if image and '/web/product/' in image:
                links.setdefault(public_url(image, page), []).append(entry)
    if not colors:
        raise CrawlError('공식 색상 옵션이 없어 색상 후보를 만들 수 없습니다(임의 색상 생성 안 함)')
    gallery = []
    for og in soup.select('meta[property="og:image"]'):
        value = og.get('content', '')
        if '/web/product/' in value:
            gallery.append(public_url(value, page))
    for img in soup.select('img.BigImage, .keyImg img, .xans-product-image img, .xans-product-addimage img, .listImg img'):
        value = img.get('ec-data-src') or img.get('data-src') or img.get('src') or ''
        if '/web/product/' in value and not value.lower().endswith('.gif'):
            gallery.append(big_variant(public_url(value, page)))
    detail = []
    area = soup.select_one('#prdDetail') or soup.select_one('.xans-product-additional') or soup.select_one('.cont_detail')
    if area is not None:
        for img in area.select('img'):
            value = img.get('ec-data-src') or img.get('data-src') or img.get('src') or ''
            if value and not value.startswith('data:') and not SKIP_IMAGE.search(value):
                detail.append(public_url(value, page))
    return dict(product_url=page, product_name=name, original_name=original, colors=colors, links=links,
                gallery=unique(gallery), detail=unique(detail), raw=raw, soup=soup, area=area)


# ───────────────────────── 선택 항목 ─────────────────────────
def color_key(entry):
    return (entry['color_code'] or '', entry['color_name'] if not entry['color_code'] else '')


def collect_optional(ctx, product):
    result, raw = {}, product['raw']
    if ctx.enabled('size') or ctx.enabled('price'):
        by_color = {color_key(c): [] for c in product['colors']}
        for row in option_rows(raw):
            color, size = option_axes(row)
            entry = split_color(color) if color else None
            if entry and color_key(entry) in by_color:
                by_color[color_key(entry)].append((row, size))
        if ctx.enabled('size'):
            sizes = [unique(s for _, s in rows if s) for rows in by_color.values()]
            if sizes and sizes[0] and all(s == sizes[0] for s in sizes):
                result['size'] = ','.join(sizes[0])  # 품절·비노출 포함, 앞자리 0 유지
            else:
                ctx.log('색상별 사이즈가 다르거나 옵션이 없어 공통 사이즈를 확정하지 않습니다(합집합 출력 안 함).', 'WARNING')
        if ctx.enabled('price'):
            node = product['soup'].select_one('#span_product_price_sale') or product['soup'].select_one('#span_product_price_text')
            digits = re.sub(r'[^\d]', '', node.get_text()) if node else ''
            extras = {str(row.get('option_price')) for rows in by_color.values() for row, _ in rows if row.get('option_price') not in (None, '')}
            if digits and int(digits) > 0 and len(extras) <= 1:
                result['price'] = int(digits)
            else:
                ctx.log('공식 판매가가 없거나 옵션별 가격이 달라 판매가를 빈칸으로 둡니다.', 'WARNING')
    collect_regular_price(ctx, result, raw, 'cafe24')
    if ctx.enabled('category'):
        values = unique(text(li) for li in product['soup'].select('.xans-product-headcategory li')
                        if text(li) not in ('', '홈', 'HOME', 'Home', '현재 위치', '>'))
        if values:
            result['category'] = ' > '.join(values)
        else:
            ctx.log('공식 상품 페이지의 카테고리 경로가 없습니다.', 'WARNING')
    return result


def table_html(table):
    """원본 표의 머리글·rowspan/colspan·단위 텍스트를 보존해 속성을 정리한 표로 재구성."""
    rows = []
    for tr in table.select('tr'):
        cells = []
        for cell in tr.find_all(['th', 'td'], recursive=False):
            span = ''.join(' %s="%s"' % (k, cell.get(k)) for k in ('rowspan', 'colspan') if str(cell.get(k) or '').isdigit() and int(cell.get(k)) > 1)
            cells.append('<%s%s>%s</%s>' % (cell.name, span, html.escape(text(cell)), cell.name))
        if cells and any(re.sub(r'<[^>]+>', '', c).strip() for c in cells):
            rows.append('<tr>' + ''.join(cells) + '</tr>')
    return '<table><tbody>' + ''.join(rows) + '</tbody></table>' if len(rows) >= 2 else ''


def detail_tables(area):
    notice, sizes = [], []
    if area is None:
        return '', ''
    for table in area.select('table'):
        pairs = []
        for tr in table.select('tr'):
            th, td = tr.select_one('th'), tr.select_one('td')
            if th is not None and td is not None and len(tr.find_all(['th', 'td'], recursive=False)) == 2 and text(th) and text(td):
                pairs.append((text(th), text(td)))
        head = text(table.select_one('tr'))
        if len(pairs) >= 2 and sum(bool(NOTICE_KEYS.search(k)) for k, _ in pairs) >= 2:
            notice = pairs
        elif SIZE_TITLE.search(head):
            grid = table_html(table)
            if grid and grid not in sizes:
                sizes.append(grid)
    notice_html = ('<h3>상품정보고시</h3><table><tbody>' + ''.join('<tr><th>%s</th><td>%s</td></tr>' % (html.escape(k), html.escape(v)) for k, v in notice)
                   + '</tbody></table>') if notice else ''
    size_html = ('<h3>사이즈 정보</h3>' + ''.join(sizes)) if sizes else ''
    return notice_html, size_html


def build_detail(ctx, products):
    images, notice, sizes = [], '', []
    for product in products:
        images += [u for u in product['detail'] if u not in images]
        n, s = detail_tables(product['area'])
        notice = notice or n
        if s and s not in sizes:
            sizes.append(s)
    markup = '<div>' + ''.join('<img src="%s" style="max-width:100%%;display:block;">' % html.escape(u, quote=True) for u in images) + notice + ''.join(sizes) + '</div>'
    compact, overflow = excel_detail(markup)
    if overflow:
        raise CrawlError('상세 HTML이 엑셀 한 셀 한도를 넘습니다(임의로 자르지 않음)')
    if not notice:
        ctx.log('공식 상세에서 상품정보고시 표를 찾지 못했습니다. 상세는 고시 없이 완성합니다.', 'WARNING')
    return compact


# ───────────────────────── 이미지: 다운로드·분할·크롭 ─────────────────────────
def load_image(ctx, url, cache):
    if url in cache:
        return None if cache[url] is True else cache[url]  # True = 이미 잘라서 닫은 원본
    payload = request(ctx, url, MAX_IMAGE, optional=True, site_only=False)
    image = None
    if payload:
        try:
            with Image.open(io.BytesIO(payload)) as opened:
                if opened.format not in ('JPEG', 'PNG', 'WEBP') or getattr(opened, 'is_animated', False) or getattr(opened, 'n_frames', 1) > 1:
                    ctx.log('정지 JPEG/PNG/WebP가 아니어서 제외: ' + url, 'INFO')
                elif opened.width * opened.height > 60_000_000:
                    ctx.log('픽셀 수 초과로 제외: ' + url, 'INFO')
                else:
                    opened.load()
                    image = ImageOps.exif_transpose(opened).convert('RGB')
        except (OSError, ValueError) as exc:
            ctx.log('이미지 해독 실패로 제외(' + str(exc)[:60] + '): ' + url, 'INFO')
    cache[url] = image
    return image


def background(image):
    """원본 맨 위 줄이 한 가지 색이면 그 색을 배경으로 본다. 아니면 흰색."""
    row = image.crop((0, 0, image.width, 1)).resize((32, 1))
    raw = row.tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    if max(max(p[i] for p in pixels) - min(p[i] for p in pixels) for i in range(3)) <= 8 and min(sum(p) for p in pixels) >= 600:
        return tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))
    return (255, 255, 255)


def runs_of(counts, threshold, gap):
    out, start, last = [], None, None
    for i, count in enumerate(counts):
        if count > threshold:
            if start is None:
                start = i
            last = i
        elif start is not None and i - last > gap:
            out.append((start, last + 1))
            start = None
    if start is not None:
        out.append((start, last + 1))
    return out


def photo_like(mask, box):
    """내용 밀도: 사진은 배경 아닌 화소가 촘촘하고, 글자·표·실측선 도식은 듬성하다(근사 판정)."""
    region = mask.crop(box)
    data = region.tobytes()
    density = data.count(255) / max(1, len(data))
    region.close()
    return density


def segment(image):
    """전체 폭 배경 여백(가로 띠)으로 블록을 나누고, 블록 안의 세로 배경 여백으로 나란한 사진을 나눈다.
    반환: [(left, top, right, bottom, 근거)] 원본 픽셀 정수 좌표."""
    work = image.copy()
    work.thumbnail((1000, 1_000_000))
    w, h = work.size
    scale = image.width / w
    bg = Image.new('RGB', work.size, background(image))
    mask = ImageChops.difference(work, bg).convert('L').point(lambda p: 255 if p > 24 else 0)
    bg.close()
    raw = mask.tobytes()
    rows = [raw[y * w:(y + 1) * w].count(255) for y in range(h)]
    boxes = []
    for top, bottom in runs_of(rows, max(4, w * .01), 10):
        if bottom - top < 120:
            continue  # 색상명·문구 띠
        block = mask.crop((0, top, w, bottom))
        braw = block.tobytes()
        bw = block.width
        cols = [sum(braw[y * bw + x] == 255 for y in range(0, bottom - top, 2)) for x in range(bw)]
        block.close()
        for left, right in runs_of(cols, 1, 10):
            if right - left < w * .2:
                continue
            inner = mask.crop((left, top, right, bottom)).getbbox()
            if not inner:
                continue
            l, t, r, b = left + inner[0], top + inner[1], left + inner[2], top + inner[3]
            if b - t < 120 or photo_like(mask, (l, t, r, b)) < .22:
                continue  # 글자·표·도식으로 보이는 듬성한 블록
            boxes.append((l, t, r, b))
    mask.close()
    out = []
    for l, t, r, b in boxes:
        pad = 8
        box = [int(max(0, (l - pad) * scale)), int(max(0, (t - pad) * scale)), int(min(image.width, (r + pad) * scale)), int(min(image.height, (b + pad) * scale))]
        bw, bh = box[2] - box[0], box[3] - box[1]
        if bh / bw > 1.8:  # 세로로 긴 컷: 실제 원본 배경 픽셀로만 좌우를 넓힘(가짜 여백 생성 안 함)
            need = int(bh / 1.8) + 1
            if need <= image.width:
                extra = need - bw
                box[0] = max(0, box[0] - extra // 2)
                box[2] = min(image.width, box[0] + need)
                box[0] = box[2] - need
        elif bw / bh > 1.8:
            need = int(bw / 1.8) + 1
            if need <= image.height:
                extra = need - bh
                box[1] = max(0, box[1] - extra // 2)
                box[3] = min(image.height, box[1] + need)
                box[1] = box[3] - need
        bw, bh = box[2] - box[0], box[3] - box[1]
        if min(bw, bh) < 500 or max(bw, bh) / min(bw, bh) > 1.8:
            continue
        out.append(tuple(box))
    return out


def save_cut(ctx, image, box, seen):
    try:
        path = ctx.save_image(image, crop=box)
    except ValueError as exc:
        ctx.log('저장 규격 미달로 제외: ' + str(exc), 'INFO')
        return None
    if path.name in seen:
        return None
    seen.add(path.name)
    return path


def cuts_from(ctx, url, image, seen):
    """한 원본에서 개별 컷 목록. 원래 비율이 개별 컷이면 통째, 통이미지면 여백 경계로 분할."""
    w, h = image.size
    if min(w, h) >= 500 and max(w, h) / min(w, h) <= 1.8:
        path = save_cut(ctx, image, None, seen)
        return [(path, 'whole', None)] if path else []
    if max(w, h) / min(w, h) <= 1.8:
        return []
    out = []
    boxes = segment(image)
    if not boxes:
        ctx.log('여백 경계로 안전하게 나눌 수 있는 개별 컷이 없어 제외: ' + url, 'INFO')
    for box in boxes:
        ctx.checkpoint()
        path = save_cut(ctx, image, list(box), seen)
        if path:
            out.append((path, 'crop', list(box)))
    return out


def release(cache, image):
    for key, value in cache.items():
        if value is image:
            cache[key] = True
    image.close()


# ───────────────────────── 진입점 ─────────────────────────
def crawl(ctx):
    code = re.sub(r'[\s-]+', '', str(ctx.product_code)).upper()
    if not re.fullmatch(r'[A-Z0-9]{6,20}', code):
        ctx.fail_item('품번 형식 오류: 색상코드 없는 기본 품번(예: REWFJKWBF560)')
        raise CrawlError('품번 형식 오류')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        ctx.fail_item('이미지 저장 유형은 대표이미지 또는 추가이미지 선택이 필요합니다.')
        raise CrawlError('이미지 항목 미선택')
    ctx.log('레드페이스 코드 버전: ' + REVISION, 'INFO')
    ctx.stage('레드페이스 공식 검색: ' + code)
    try:
        links = find_products(ctx, code)
    except (CrawlError, OSError, ValueError) as exc:
        ctx.fail_item('공식 검색 실패: ' + str(exc))
        raise
    if not links:
        ctx.fail_item('공식 검색에 정확히 일치하는 품번이 없습니다: ' + code)
        raise CrawlError('검색 결과 없음')
    products, failures = [], []
    for gid, href in links:
        ctx.checkpoint()
        try:
            raw = decode(request(ctx, ORIGIN + '/product/detail.html?product_no=' + gid, optional=True) or request(ctx, href))
            product = parse_product(ctx, raw, code, href)
            if product is None:
                ctx.log('상세의 상품명·자체상품코드에 품번이 없어 제외: ' + href, 'INFO')
                continue
            product['optional'] = collect_optional(ctx, product)
            products.append(product)
        except (CrawlError, OSError, ValueError) as exc:
            failures.append(href + ' / ' + str(exc))
            ctx.log(href + ' 실패: ' + str(exc), 'ERROR')
    if not products:
        ctx.fail_item('정확한 공식 상품 정보를 확인할 수 없습니다' + (': ' + ' / '.join(failures[-2:]) if failures else ''))
        raise CrawlError('정확한 상품 없음')
    colors = []
    for product in products:
        colors += [c for c in product['colors'] if c not in colors]
    common = {}
    for field in ('size', 'price', 'category'):
        if ctx.enabled(field):
            values = [p['optional'].get(field) for p in products]
            if values and values[0] not in (None, '') and all(v == values[0] for v in values):
                common[field] = values[0]
            else:
                ctx.log('상품 링크 간 공통 ' + field + ' 값이 없거나 달라 빈칸으로 둡니다.', 'WARNING')
    merge_regular_prices(ctx, common, [p['optional'] for p in products])
    cache, seen, pending = {}, set(), []
    ctx.stage('갤러리·상세 이미지 다운로드 및 개별 컷 저장')
    # 1) 색상 옵션 link_image: 공식 옵션 ↔ 이미지 DOM 연결이 있는 사진만 사전 매핑
    linked = set()
    for product in products:
        for url, entries in product['links'].items():
            ctx.checkpoint()
            linked.add(url)
            image = load_image(ctx, big_variant(url), cache) or load_image(ctx, url, cache)
            if image is None:
                continue
            mapped = entries[0] if len({color_key(e) for e in entries}) == 1 else None
            evidence = ('Cafe24 색상 옵션 "' + mapped['color_name'] + (' (' + mapped['color_code'] + ')' if mapped['color_code'] else '') +
                        '"의 공식 link_image: ' + url) if mapped else ''
            for path, kind, box in cuts_from(ctx, url, image, seen):
                pending.append((path, mapped, evidence, url, box))
            release(cache, image)
    # 2) 갤러리 + 상세 통이미지: 모두 미매핑 후보 (색감·파일명으로 색상 추측 안 함)
    for product in products:
        for url in product['gallery'] + product['detail']:
            if url in linked:
                continue
            ctx.checkpoint()
            image = load_image(ctx, url, cache)
            if image is not None:
                for path, kind, box in cuts_from(ctx, url, image, seen):
                    pending.append((path, None, '', url, box))
                release(cache, image)
    if not pending:
        ctx.fail_item('규격(양변 500px·비율 1.8 이하)을 통과한 개별 상품 컷이 없습니다.')
        raise CrawlError('개별 컷 없음')
    base = dict(product_code=code, product_name=products[0]['product_name'], product_url=products[0]['product_url'], **common)
    base['원본 상품명'] = '\n'.join(unique(p['original_name'] for p in products))
    if ctx.enabled('detail_page'):
        for product in products:  # 실제로 열리지 않은 상세 이미지는 HTML에서 제외
            product['detail'] = [u for u in product['detail'] if u not in cache or cache[u] is not None]
        try:
            base['detail_page'] = build_detail(ctx, products)
        except CrawlError as exc:
            ctx.fail_item(str(exc))
            raise
    for path, mapped, evidence, source, box in pending:
        ctx.checkpoint()
        record = dict(base)
        record['크롭 원본 이미지'] = source
        record['크롭 좌표'] = json.dumps(box) if box else ''
        ctx.emit_local_image(record, colors, path, mapped_color=mapped, mapping_evidence=evidence,
                             shared_detail_html=base.get('detail_page', ''))
    if failures:
        ctx.fail_item('일부 공식 상품 링크 수집 실패: ' + ' / '.join(failures))
    ctx.log(code + ': 개별 컷 ' + str(len(pending)) + '장 전달. 색상 매핑하기에서 사진을 분류하세요.', 'SUCCESS')
