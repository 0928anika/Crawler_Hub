PORTAL_API_VERSION = 1

import hashlib
import html
import io
import json
import re
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, quote, unquote
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from PIL import Image, UnidentifiedImageError

SITE = 'https://www.style24.com'
POLICY = {'checked': '2026-11-11', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP·비율 1.8은 프로젝트 정책. 전체 등록 승인은 미검증.'}
# 공식 STYLE24 HTML 확인: M264DMB07P BE/IV, M264DMB08P BR.
# 색상별 공식 링크 + 활성 colorCd + 해당 갤러리의 품번/색상 원본으로 연결.
# 실제 본문 전체(에디터 이미지·설명·상품 정보·라벨·실측)를 원래 순서대로 보존.
# 공통 공지의 사이즈 조견표만 끝에 추가. 선물상자/홍보/로고/공통 CARE 제외.
# 고시는 상품 본문 이미지 원본을 보존하며 별도 표로 생성하지 않는다.
# 이미지/HTML 및 선택 항목은 실사이트와 오프라인 테스트로 구분 검증.
# 실제 Hub 최종 정제/재실행 교체와 WING 업로드 승인은 별도 환경에서 확인 필요.

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
    # 의류의 작은 프린트를 글자로 오인하지 않도록 글자 성분의 지배성을 요구.
    if small_count >= 150 and 0 < total_dark < w*h*.16 and small_area > total_dark*.85:
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
        if not gallery and ('통신 오류' in str(e) or re.search(r'HTTP 5\d\d', str(e))):
            raise CrawlError('선택한 상세 이미지 통신 확인 미완료: ' + url + ': ' + str(e)) from e
        ctx.log(f'이미지 제외: {url}: {e}', 'WARNING')
        info = None
    cache[url] = info
    return info


class LookupFailure(CrawlError):
    """공식 응답에서 입력 상품을 확인하지 못함."""


def text(node):
    return node.get_text(' ', strip=True) if node is not None else ''


def structured(doc, kind):
    for node in doc.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(node.get_text())
        except ValueError as exc:
            raise CrawlError('공식 구조화 데이터 해석 실패') from exc
        if isinstance(value, dict) and value.get('@type') == kind:
            return value
    return {}


def load_product(ctx, url, expected):
    try:
        data, final, _ = fetch(ctx, url)
    except CrawlError as exc:
        cause = exc.__cause__
        if isinstance(cause, HTTPError) and cause.code in (404, 410):
            raise LookupFailure('공식 상품 없음: ' + expected) from exc
        raise
    if urlsplit(final).hostname != 'www.style24.com' or urlsplit(final).path != '/goods/detail/form':
        raise LookupFailure('공식 상품 페이지를 확인하지 못함: ' + final)
    doc = BeautifulSoup(data.decode('utf-8-sig'), 'html.parser')
    field = doc.select_one('#cartForm input[name="goodsCd"]')
    group = structured(doc, 'ProductGroup')
    if field is None or field.get('value', '').upper() != expected or str(group.get('productGroupID', '')).upper() != expected:
        raise LookupFailure('요청 품번과 공식 goodsCd/productGroupID 불일치: ' + expected)
    if str(group.get('brand', {}).get('name', '')).casefold() != 'moimoln':
        raise CrawlError('공식 상품 브랜드 필드가 모이몰른이 아님: ' + expected)
    meta = doc.select_one('meta[property="og:url"]')
    link = meta.get('content', '') if meta else ''
    parts = urlsplit(link)
    params = parse_qs(parts.query)
    color = params.get('colorCd', [''])[0]
    if parts.scheme not in ('http', 'https') or parts.hostname != 'www.style24.com' or parts.path != '/goods/detail/form' or params.get('goodsCd') != [expected] or not color:
        raise CrawlError('공식 색상별 상품 링크를 확인하지 못함: ' + expected)
    return doc, link, color


def palette(doc, base, page_url):
    result = {}
    for anchor in doc.select('.option_box .opt_color a[href]'):
        link = urljoin(page_url, anchor['href'])
        parts = urlsplit(link)
        params = parse_qs(parts.query)
        code = params.get('colorCd', [''])[0]
        if parts.hostname != 'www.style24.com' or parts.path != '/goods/detail/form' or params.get('goodsCd') != [base] or not code:
            continue
        label = anchor.find_next_sibling('span')
        result.setdefault(code, dict(code=code, name=text(label), url=link))
    if not result:
        raise CrawlError('공식 색상 링크 목록 없음/구조 변경')
    return list(result.values())


def resolve_product(ctx):
    value = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]+(?:[-_][A-Z0-9]+)*', value):
        raise LookupFailure('품번 입력 형식 오류: ' + value)
    url = SITE + '/goods/detail/form?' + urlencode({'goodsCd': value})
    try:
        doc, link, color = load_product(ctx, url, value)
        return value, None, doc, link, color
    except LookupFailure:
        match = re.fullmatch(r'(.+)[_-]([A-Z0-9]+)', value)
        if not match:
            raise
        base, requested = match.groups()
        doc, link, color = load_product(ctx, SITE + '/goods/detail/form?' + urlencode({'goodsCd': base}), base)
        choices = palette(doc, base, link)
        if requested not in {p['code'] for p in choices}:
            raise LookupFailure('공식 목록에 지정 색상코드 없음: ' + requested)
        return base, requested, doc, link, color


def product_name(doc, base):
    node = doc.select_one('.descript_box .name')
    name = text(node)
    for alias in ('모이몰른', 'moimoln', base):
        name = re.sub(r'\[\s*' + re.escape(alias) + r'\s*\]', '', name, flags=re.I)
        name = re.sub(r'(?<![A-Za-z0-9가-힣])' + re.escape(alias) + r'(?![A-Za-z0-9가-힣])', '', name, flags=re.I)
    # 확인한 공식 시즌 표기만 제거한다. 상품명 자체의 다른 대괄호 문구는 보존.
    name = re.sub(r'\[\s*(?:20)?\d{2}\s*(?:봄|여름|가을|겨울|SPRING|SUMMER|FALL|AUTUMN|WINTER|SS|FW)\s*\]', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip(' _-')
    if not name:
        raise CrawlError('정리된 실제 상품명 없음')
    return name


def option_nodes(doc, code):
    return [n for n in doc.select('.option_box .opt_size input[name="opt"][optcd2]') if n.get('optcd1') == code]


def sizes(doc, code):
    values = list(dict.fromkeys(n['optcd2'].strip() for n in option_nodes(doc, code) if n['optcd2'].strip()))
    if not values:
        raise CrawlError('공식 사이즈 옵션 없음/구조 변경: ' + code)
    return ','.join(values)


def money(node):
    value = text(node)
    if not re.fullmatch(r'[0-9][0-9,]*', value):
        raise ValueError('가격 숫자 형식 확인 불가: ' + value)
    return int(value.replace(',', ''))


def sale_price(ctx, doc, code):
    value = money(doc.select_one('.descript_box .sale_price em'))
    extras = {int(n.get('addprice', '0')) for n in option_nodes(doc, code)}
    if len(extras) > 1:
        ctx.log('사이즈별 추가금이 달라 판매가 단일 출력 불가', 'WARNING')
        return None
    return value + next(iter(extras), 0)


def regular_price(ctx, doc):
    try:
        value = money(doc.select_one('.descript_box .org_price em'))
        if value <= 0:
            raise ValueError('정상가가 0 이하')
        return value
    except Exception as exc:
        ctx.log('정상가 확인 불가: ' + str(exc) + ' / 나머지 수집 계속', 'WARNING')
        return None


def category(doc):
    crumb = structured(doc, 'BreadcrumbList')
    return ' > '.join(str(n.get('name', '')) for n in crumb.get('itemListElement', []) if urlsplit(str(n.get('item', ''))).path != '/goods/detail/form' and str(n.get('name', '')).casefold() != 'moimoln' and n.get('name'))


def image_url(node, link):
    src = node.get('src') or node.get('data-src') or ''
    return quote(urljoin(link, src), safe=':/?&=%+#') if src else ''


def image_role(url, base, color):
    parts = urlsplit(url)
    if parts.hostname != 'image.style24.com' or not parts.path.startswith('/Upload/ProductImage/'):
        return None
    match = re.search('/' + re.escape(base) + '_' + re.escape(color) + r'_(\d+|D\d+|I\d+)\.(?:jpg|jpeg|png|webp)(?:/|$)', parts.path, re.I)
    return match[1].upper() if match else None


def gallery(ctx, doc, link, base, color, cache):
    # 원문에 실제 제공되는 원본 및 갤러리 URL만 사용. CDN 주소를 변조하지 않는다.
    nodes = doc.select('.pd-core__color-item img[data-thumbnail="goodsDetailOptionWeb"]')
    nodes += doc.select('img[data-thumbnail="goodsDetailImageWeb"], img[data-thumbnail="goodsDetailImageMob"]')
    accepted, seen_cuts, hashes = [], set(), set()
    for node in nodes:
        ctx.checkpoint()
        url = image_url(node, link)
        cut = image_role(url, base, color)
        if not cut or cut.startswith('I') or cut in seen_cuts:
            continue
        info = image_info(ctx, url, cache, gallery=True)
        if not info:
            continue
        fmt, w, h, animated, digest, signal = info
        reasons = []
        if fmt not in POLICY['formats'] or animated:
            reasons.append('미지원 형식/애니메이션')
        if min(w, h) < POLICY['min_side'] or max(w, h) / min(w, h) > POLICY['max_aspect']:
            reasons.append('크기/통짜 의심 비율')
        if re.search(r'배너|고시|사이즈|실측|측정|collage|banner|chart|measure', node.get('alt', ''), re.I):
            reasons.append('배너/표/측정자료 역할')
        if signal:
            reasons.append(signal)
        if reasons:
            ctx.log('갤러리 제외: ' + ', '.join(reasons) + ': ' + url, 'WARNING')
            continue
        seen_cuts.add(cut)
        if digest in hashes:
            continue
        hashes.add(digest)
        accepted.append(url)
        ctx.log('개별 상품컷 채택: 공식 색상 URL/품번/색상코드/갤러리 역할 및 실제 규격·부정 신호 검사 통과: ' + url, 'INFO')
    if not accepted:
        ctx.log('유효 갤러리 0장: 이미지 검수 필요', 'WARNING')
        return '', ''
    extra = accepted[1:] or accepted[:1]
    if len(accepted) == 1:
        ctx.log('유효컷 1장: 추가 5슬롯에도 같은 컷 재사용', 'WARNING')
    return accepted[0], ','.join(extra[i % len(extra)] for i in range(5))


def cell_text(node):
    return escape(node.get_text('\n', strip=True)).replace('\n', '<br>')


def table_html(table):
    rows = []
    for row in table.select('tr'):
        if row.find_parent('table') is not table:
            continue
        cells = []
        for cell in row.find_all(['th', 'td'], recursive=False):
            attrs = ''.join(' ' + key + '="' + str(int(cell[key])) + '"' for key in ('rowspan', 'colspan') if str(cell.get(key, '')).isdigit())
            cells.append('<' + cell.name + attrs + '>' + cell_text(cell) + '</' + cell.name + '>')
        if cells:
            rows.append('<tr>' + ''.join(cells) + '</tr>')
    if not rows:
        raise CrawlError('공식 표가 있지만 행 추출 실패')
    return '<table border="1" cellpadding="6" style="width:100%;border-collapse:collapse"><tbody>' + ''.join(rows) + '</tbody></table>'


def detail(ctx, doc, link, base, color, cache):
    parts, seen = [], set()
    body = doc.select_one('.pd-core__wrap')
    if body is None:
        raise CrawlError('공식 상세 본문 구조를 확인하지 못함: ' + link)

    def render(node):
        # 본문 DOM 순서를 유지한다. 대표/추가 이미지의 파일명·색상 필터를
        # 공유 본문 이미지에 적용하지 않는다(에디터 본문에는 색상 코드가 없음).
        if isinstance(node, Comment):
            return ''
        if isinstance(node, NavigableString):
            return escape(str(node))
        if not isinstance(node, Tag):
            return ''
        classes = set(node.get('class', []))
        if (node.name in ('script', 'style', 'iframe', 'form', 'button', 'input')
                or classes.intersection({'logo', 'pd-core__color', 'pd-core__care'})):
            return ''
        if node.name == 'img':
            ctx.checkpoint()
            url = image_url(node, link)
            address = urlsplit(url)
            if (address.scheme not in ('http', 'https')
                    or address.hostname != 'image.style24.com' or url in seen):
                return ''
            seen.add(url)
            if not image_info(ctx, url, cache):
                return ''
            return '<img src="' + escape(url, quote=True) + '" style="max-width:100%"><br>'
        if node.name == 'br':
            return '<br>'
        if node.name == 'table':
            return table_html(node)
        children = ''.join(render(child) for child in node.children)
        if not children.strip():
            return ''
        allowed = {'p', 'div', 'span', 'h2', 'h3', 'h4', 'strong', 'b', 'em', 'ul', 'ol', 'li'}
        name = node.name if node.name in allowed else 'div'
        return '<' + name + '>' + children + '</' + name + '>'

    parts.append(render(body))
    # 사용자 요청: 고시는 본문 이미지에 있으므로 별도 표를 만들지 않는다.
    # 하단 공통 공지 전체를 넣지 않고 공식 사이즈 조견표 이미지만 고른다.
    charts = []
    for node in doc.select('.notice_content img'):
        url = image_url(node, link)
        address = urlsplit(url)
        filename = unquote(address.path).rsplit('/', 1)[-1]
        if (address.hostname == 'image.style24.com'
                and address.path.startswith('/Upload/editor/')
                and re.search(r'(?:^|_)모이몰른_사이즈_조견표(?:_|\.|$)', filename)):
            charts.append(url)
    if not charts:
        ctx.log('공식 하단 사이즈 조견표 없음: 생략', 'WARNING')
    for url in dict.fromkeys(charts):
        if url in seen:
            continue
        seen.add(url)
        if image_info(ctx, url, cache):
            parts.append('<img src="' + escape(url, quote=True) + '" style="max-width:100%"><br>')
    result = ''.join(parts)
    if len(result.encode('utf-16-le')) // 2 > 32767:
        raise CrawlError('상세 HTML 엑셀 셀 한도 초과. 임의 절단하지 않음')
    return result


def crawl(ctx):
    failures = []
    try:
        ctx.stage('모이몰른 공식 품번/색상 확인')
        base, requested, first_doc, first_link, first_color = resolve_product(ctx)
        colors = palette(first_doc, base, first_link)
    except Exception as exc:
        ctx.fail_item(str(exc))
        raise
    for choice in colors:
        ctx.checkpoint()
        code = choice['code']
        if requested and requested != code:
            continue
        try:
            ctx.stage(base + '_' + code + ' 수집')
            if code == first_color:
                doc, link, current = first_doc, first_link, first_color
            else:
                doc, link, current = load_product(ctx, choice['url'], base)
            if current != code:
                raise CrawlError('요청 색상과 공식 활성 색상 불일치: ' + code + '/' + current)
            record = dict(product_code=base, product_name=product_name(doc, base), color=choice['name'] or code, color_name=choice['name'], color_code=code, product_url=link)
            if ctx.enabled('size'):
                record['size'] = sizes(doc, code)
            if ctx.enabled('price'):
                record['price'] = sale_price(ctx, doc, code)
            if ctx.enabled('regular_price'):
                ctx.checkpoint()
                record['regular_price'] = regular_price(ctx, doc)
                ctx.checkpoint()
            if ctx.enabled('category'):
                record['category'] = category(doc)
            cache = {}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main, extra = gallery(ctx, doc, link, base, code, cache)
                if ctx.enabled('main_image'):
                    record['main_image'] = main
                if ctx.enabled('additional_images'):
                    record['additional_images'] = extra
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail(ctx, doc, link, base, code, cache)
            ctx.emit_result(record)
        except Exception as exc:
            message = base + '_' + code + ': ' + str(exc)
            ctx.fail_item(message)
            failures.append(message)
    if failures:
        raise CrawlError('\n'.join(failures))
