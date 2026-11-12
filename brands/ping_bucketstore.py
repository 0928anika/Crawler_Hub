PORTAL_API_VERSION = 1
"""핑 (버킷스토어) — 자동 색상 매핑 크롤러

검증 범위: 사용자 단독 크롤러(bucketstore_crawler.py)에서 운영 확인된 URL·선택자·응답 키를 Crawler Hub SDK 규약으로 이식. 이 코드를 작성한 환경은 해당 사이트 접속이 차단되어 실사이트 실행은 미검증이며, SDK 계약·선택 항목 생략·표 재구성은 오프라인 fixture 테스트(tests/test_oberon_brands.py)로만 확인함.
"""
REVISION = '2026-11-12-oberon-1'

# ───────────────────────── 공통 도우미 (브랜드 파일마다 동일하게 포함) ─────────────────────────
import hashlib
import html
import io
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qs, unquote
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots
from detail_store import excel_detail
from regular_price import amount, collect_regular_price, merge_regular_prices

POLICY = {'checked_on': '2026-11-12', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP·비율 1.8은 프로젝트 보수적 정책이며 WING 공식 비율 제한이 아님. 등록 승인 전체는 보장하지 않음.'}
DETAIL_MAX_BYTES = 30000000
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
# 대표·추가 후보에서 빼는 구체적 신호(파일 경로·alt). 일반 순번 _01/_02는 신호가 아님.
SHOT_NEGATIVE = re.compile(r'size[_-]?(?:guide|chart|info)|sizechart|measure|사이즈|실측|측정|고시|notice|banner|'
                           r'collage|swatch|colorchip|color_chip|logo|icon|cs_info|delivery|shipping|배송|교환|반품|'
                           r'event|coupon|review', re.I)


class CrawlError(Exception):
    """현재 입력 행의 수집 실패."""


def fetch(ctx, url, hosts, *, data=None, headers=None, limit=8000000, timeout=20, method=None):
    """제한된 시간·용량·재시도로 공식 출처만 조회. 4xx는 재시도하지 않는다."""
    url = quote(url, safe=':/?&=%+#,;@!$*()~[]')
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or (hosts and parts.hostname not in hosts):
        raise CrawlError('허용되지 않은 조회 주소: ' + url)
    head = {'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9', 'Accept': '*/*'}
    head.update(headers or {})
    failure = ''
    for attempt in range(3):
        ctx.checkpoint()
        try:
            req = Request(url, data=data, headers=head, method=method)
            with build_opener().open(req, timeout=timeout) as response:
                final = response.geturl()
                if hosts and urlsplit(final).hostname not in hosts:
                    raise CrawlError('공식 출처 밖으로 이동: ' + final)
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise CrawlError('응답 최대 용량 초과: ' + url)
                return body, final, response.headers.get('Content-Type', '')
        except HTTPError as exc:
            code = exc.code
            exc.close()
            failure = 'HTTP ' + str(code) + ': ' + url
            if code in (400, 401, 403, 404, 410):
                raise CrawlError(failure) from None
        except (URLError, TimeoutError, OSError) as exc:
            failure = '통신 오류: ' + url + ': ' + str(exc)
        finally:
            ctx.checkpoint()
        if attempt < 2:
            ctx.log(str(attempt + 1) + '/2 재시도: ' + failure, 'WARNING')
    raise CrawlError(failure or ('요청 실패: ' + url))


def decode(body, content_type=''):
    match = re.search(r'charset=([\w-]+)', content_type or '', re.I)
    for encoding in ([match[1]] if match else []) + ['utf-8-sig', 'cp949']:
        try:
            return body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode('utf-8', errors='replace')


def soup(text):
    return BeautifulSoup(text, 'html.parser')


def get_html(ctx, url, hosts, **kw):
    body, final, ctype = fetch(ctx, url, hosts, **kw)
    return decode(body, ctype), final


def get_json(ctx, url, hosts, **kw):
    headers = dict({'Accept': 'application/json, text/plain, */*'}, **kw.pop('headers', {}))
    body, final, ctype = fetch(ctx, url, hosts, headers=headers, **kw)
    try:
        return json.loads(decode(body, ctype))
    except ValueError as exc:
        raise CrawlError('JSON 응답 해석 실패: ' + url) from exc


def absolute(base, src):
    src = (src or '').strip()
    if not src or src.startswith('data:') or src.startswith('javascript'):
        return ''
    if src.startswith('//'):
        src = 'https:' + src
    url = urljoin(base, src)
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    return url.split('#')[0]


def img_src(tag):
    for key in ('ec-data-src', 'data-src', 'data-original', 'data-lazy', 'src'):
        value = (tag.get(key) or '').strip()
        if value and not value.startswith('data:'):
            return value
    return ''


def esc(value):
    return html.escape(re.sub(r'\s+', ' ', str(value or '')).strip(), quote=True)


def bounded(pattern, text):
    return re.search(r'(?<![A-Za-z0-9])' + re.escape(pattern) + r'(?![A-Za-z0-9])', text or '', re.I) is not None


def clean_name(raw, aliases, codes, drop_tags=True):
    """브랜드명·공식 품번이 실제로 들어 있을 때만 제거. 앞쪽 [행사/브랜드] 태그는 출력 정리로 제거."""
    name = str(raw or '')
    if drop_tags:
        name = re.sub(r'^\s*(?:\[[^\]]*\]\s*)+', '', name)
    for token in sorted({t for t in list(aliases) + list(codes) if t}, key=len, reverse=True):
        name = re.sub(r'[\(\[]\s*' + re.escape(token) + r'(?:[_\-][A-Za-z0-9]{1,4})?\s*[\)\]]', ' ', name, flags=re.I)
        name = re.sub(r'(?<![A-Za-z0-9가-힣])' + re.escape(token) + r'(?:_[A-Za-z0-9]{1,4})?(?![A-Za-z0-9가-힣])', ' ', name, flags=re.I)
    name = re.sub(r'\(\s*\)|\[\s*\]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip(' -_/|·')
    if not name:
        raise CrawlError('정리 후 실제 상품명이 비었습니다: ' + str(raw))
    return name


def split_color(text):
    """'블랙(BK)'·'(39)Ivory'·'39 Ivory'·'BK_블랙' → (이름, 코드). 근거 없는 숫자는 코드로 보지 않는다."""
    value = re.sub(r'\s+', ' ', str(text or '')).strip()
    for pattern, ni, ci in ((r'^(.+?)\s*\(\s*([A-Za-z0-9]{1,4})\s*\)$', 1, 2),
                            (r'^\(\s*([A-Za-z0-9]{1,4})\s*\)\s*(.+)$', 2, 1),
                            (r'^(\d{2,3})\s+([A-Za-z가-힣].*)$', 2, 1),
                            (r'^([A-Z0-9]{1,4})_(.+)$', 2, 1)):
        match = re.match(pattern, value)
        if match:
            return match[ni].strip(), match[ci].strip().upper()
    return value, ''


def color_entry(name, code=''):
    name, code = str(name or '').strip(), str(code or '').strip()
    if not name and not code:
        raise CrawlError('실제 색상명 또는 색상코드가 없습니다.')
    label = name + ' (' + code + ')' if name and code else name or code
    return {'color_name': name, 'color_code': code, 'label': label}


def palette_of(colors):
    result = []
    for color in colors:
        entry = {'color_name': color['color_name'], 'color_code': color['color_code']}
        if entry not in result:
            result.append(entry)
    if not result:
        raise CrawlError('공식 색상 후보가 없어 수집할 수 없습니다.')
    return result


def joined(values):
    return ','.join(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


# ── 이미지 검사 ──
def inspect(ctx, url, cache, max_bytes=None):
    """공개 GET(쿠키·Referer 없음) + 실제 바이트 해독. 결과는 URL별 캐시."""
    key = (url, max_bytes or POLICY['max_bytes'])
    if key not in cache:
        ctx.checkpoint()
        result = check_public_image(url, timeout=12, max_bytes=max_bytes or POLICY['max_bytes'], retries=1, checkpoint=ctx.checkpoint)
        if result['status'] == 'accessible':
            try:
                with Image.open(io.BytesIO(result['data'])) as im:
                    result['animated'] = bool(getattr(im, 'is_animated', False))
            except (UnidentifiedImageError, OSError, ValueError):
                result['animated'] = False
            result['digest'] = hashlib.sha256(result['data']).hexdigest()
        cache[key] = result
        ctx.checkpoint()
    return cache[key]


def shot_problem(result, url, context=''):
    """대표·추가 후보 규칙. 빈 문자열이면 통과."""
    if result['status'] != 'accessible':
        return '공개 접근/해독 불가(' + result['status'] + '): ' + ', '.join(result.get('reasons') or [])
    width, height, fmt = result.get('width') or 0, result.get('height') or 0, result.get('format')
    if fmt not in POLICY['formats']:
        return '정책 외 형식 ' + str(fmt)
    if result.get('animated'):
        return '애니메이션 이미지'
    if len(result['data']) > POLICY['max_bytes']:
        return '10,000,000바이트 초과'
    if min(width, height) < POLICY['min_side']:
        return '짧은 변 500px 미만 ' + str(width) + 'x' + str(height)
    if max(width, height) / min(width, height) > POLICY['max_aspect']:
        return '통짜·배너 의심 비율 ' + str(width) + 'x' + str(height)
    if SHOT_NEGATIVE.search(unquote(urlsplit(url).path) + ' ' + str(context or '')):
        return '사이즈표·고시·배너 등 제외 신호'
    return ''


def screen_shots(ctx, urls, cache, seen=None, contexts=None):
    """규칙을 통과한 개별 컷 URL 목록(원래 순서). 같은 바이트는 1장만."""
    seen = set() if seen is None else seen
    accepted = []
    for url in urls:
        ctx.checkpoint()
        result = inspect(ctx, url, cache)
        problem = shot_problem(result, url, (contexts or {}).get(url, ''))
        if problem:
            ctx.log('사진 후보 제외: ' + problem + ' / ' + url, 'WARNING')
            continue
        if result['digest'] in seen:
            continue
        seen.add(result['digest'])
        accepted.append(url)
        ctx.log('사진 후보 채택: ' + str(result['format']) + ' ' + str(result['width']) + 'x' + str(result['height']) + ' / ' + url, 'INFO')
    return accepted


def first_open(ctx, variants, cache):
    """같은 사진의 big→medium→small 등 후보 중 실제 열리는 첫 주소."""
    for url in variants:
        if inspect(ctx, url, cache)['status'] == 'accessible':
            return url
    return ''


def auto_slots(ctx, urls, cache, mapping_evidence):
    """공식 색상 연결이 확인된 사진만 자동 채택. 대표 1 + 추가 5슬롯."""
    candidates = []
    for url in urls:
        result = inspect(ctx, url, cache)
        if result['status'] != 'accessible':
            continue
        validation = validate_gallery_image(result['data'], role='product_shot', mapping_verified=True,
            single_shot_verified=True, mapping_evidence=mapping_evidence,
            content_evidence='공식 상품 갤러리 영역; 형식·픽셀·비율·제외 신호 검사 통과 ' + str(result['width']) + 'x' + str(result['height']))
        candidates.append({'url': url, 'validation': validation})
    main, extras, warning = gallery_slots(candidates)
    if warning:
        ctx.log(warning, 'WARNING')
    return main or '', extras


def apply_images(ctx, record, main, extras):
    if ctx.enabled('main_image'):
        record['main_image'] = main
    if ctx.enabled('additional_images'):
        record['additional_images'] = ','.join(extras)


# ── 상세 HTML: 상품 이미지 + 상품정보고시 + 사이즈표(그림→표→주석) ──
TH = '<th style="border:1px solid #ddd;padding:6px 10px;background:#f6f6f6;text-align:left">'
TD = '<td style="border:1px solid #ddd;padding:6px 10px">'
TABLE = '<table style="width:100%;border-collapse:collapse;font-size:13px"><tbody>'


def notice_table(pairs):
    rows = [(k, v) for k, v in pairs if str(k or '').strip() and str(v or '').strip()]
    if not rows:
        return ''
    body = ''.join('<tr>' + TH + esc(k) + '</th>' + TD + esc(v) + '</td></tr>' for k, v in rows)
    return '<h3>상품정보고시</h3>' + TABLE + body + '</tbody></table>'


def grid_table(rows, header_rows=1):
    """[[셀,...]] 또는 [[(텍스트,colspan,rowspan),...]] → 실제 표."""
    out = []
    for index, row in enumerate(rows):
        cells = []
        for cell in row:
            text, colspan, rowspan = cell if isinstance(cell, tuple) else (cell, 1, 1)
            tag = 'th' if index < header_rows else 'td'
            attrs = (' colspan="' + str(colspan) + '"' if colspan > 1 else '') + (' rowspan="' + str(rowspan) + '"' if rowspan > 1 else '')
            style = TH if tag == 'th' else TD
            cells.append(style.replace('<' + tag, '<' + tag + attrs, 1) + esc(text) + '</' + tag + '>')
        out.append('<tr>' + ''.join(cells) + '</tr>')
    return TABLE + ''.join(out) + '</tbody></table>' if out else ''


def copy_table(node):
    """원본 table의 머리글·단위·rowspan/colspan을 보존해 깨끗한 표로 재구성."""
    rows = []
    header_rows = 0
    for tr in node.select('tr'):
        cells = []
        for cell in tr.find_all(['th', 'td'], recursive=False):
            span = lambda k: int(cell.get(k)) if str(cell.get(k, '')).isdigit() and 1 < int(cell.get(k)) <= 100 else 1
            cells.append((cell.get_text(' ', strip=True), span('colspan'), span('rowspan')))
        if cells and any(c[0] for c in cells):
            if all(c.name == 'th' for c in tr.find_all(['th', 'td'], recursive=False)) and header_rows == len(rows):
                header_rows += 1
            rows.append(cells)
    if len(rows) < 2:
        return ''
    return grid_table(rows, header_rows or 1)


def size_section(blocks):
    """blocks: [(그림URL 목록, 표HTML, 주석 목록)] — 각 묶음에서 그림 먼저, 표, 주석 순."""
    parts = []
    for figures, table, notes in blocks:
        chunk = ''.join('<p><img src="' + html.escape(u, quote=True) + '" alt="사이즈 측정 안내"></p>' for u in figures)
        chunk += table or ''
        chunk += ''.join('<p style="font-size:12px;color:#666">' + esc(n) + '</p>' for n in notes if str(n).strip())
        if chunk:
            parts.append(chunk)
    return '<h3>사이즈 정보</h3>' + ''.join(parts) if parts else ''


def detail_images(ctx, urls, cache):
    """상세 이미지: 공개 접근 확인. 거부(403/404/HTML)는 해당 파일만 경고 후 생략, 일시 오류는 실패."""
    kept = []
    for url in dict.fromkeys(urls):
        ctx.checkpoint()
        result = inspect(ctx, url, cache, DETAIL_MAX_BYTES)
        if result['status'] == 'accessible':
            kept.append(url)
        elif result['status'] == 'inaccessible':
            ctx.log('상세 이미지 공개 접근 거부/없음 → 해당 파일만 생략: ' + url + ' / ' + ', '.join(result['reasons']), 'WARNING')
        else:
            raise CrawlError('선택한 상세 이미지 확인 미완료(일시 오류): ' + url)
    return kept


def build_detail(ctx, images, notice='', sizes=''):
    markup = ''.join('<p><img src="' + html.escape(u, quote=True) + '" alt="상품 상세"></p>' for u in images)
    markup = '<div>' + markup + notice + sizes + '</div>'
    if not images and not notice and not sizes:
        ctx.log('공식 상세 이미지·고시·사이즈표를 찾지 못했습니다. 없는 자료는 만들지 않습니다.', 'WARNING')
        return ''
    if not notice:
        ctx.log('HTML 상품정보고시 표 없음(상세 이미지 안에 포함됐을 수 있음): 생략', 'INFO')
    compact, overflow = excel_detail(markup)
    if overflow:
        raise CrawlError('상세 HTML을 정리해도 Excel 셀 한도(32,767자) 초과')
    return compact


def finish_failures(ctx, failures, emitted, raw):
    if failures:
        raise RuntimeError('일부 상품/색상 수집 미완료: ' + ' / '.join(failures))
    if not emitted:
        raise CrawlError('정확히 일치하는 공식 상품을 찾지 못했습니다: ' + raw)

# ───────────────────────── 핑(PING) · 버킷스토어 (자동 매핑) ─────────────────────────
# 버킷스토어는 색상마다 상품코드가 따로(기본품번_색상코드) 있고 색상별 imageList를 제공 → 색상 1개 = 상품 1개.
SITE = 'https://bucketstore.com'
API = SITE + '/bucketapi'
HOSTS = ('bucketstore.com', 'www.bucketstore.com')
SKIP = re.compile(r'extmall_brand|/contents/brand/|card-company|not_found|logo|icon|banner', re.I)


def unwrap(data):
    if isinstance(data, dict):
        for key in ('content', 'data'):
            if isinstance(data.get(key), (dict, list)):
                return data[key]
    return data


def api(ctx, path, referer):
    return unwrap(get_json(ctx, API + path, HOSTS, headers={'Referer': referer}))


def next_text(text):
    parts = []
    for match in re.finditer(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)', text, re.S):
        try:
            parts.append(json.loads(match[1]))
        except ValueError:
            continue
    return ''.join(parts)


def page_data(ctx, code):
    """공식 상세 페이지 데이터(Next.js RSC 우선, 일반 HTML 내장 데이터 보조)."""
    url = SITE + '/goods/detail/' + code
    try:
        body, _, ctype = fetch(ctx, url, HOSTS, headers={'RSC': '1', 'Referer': url})
        text = decode(body, ctype)
        if '"goodsDetail"' in text:
            return text
    except CrawlError as exc:
        if 'HTTP 404' in str(exc):
            raise
        ctx.log('RSC 데이터 조회 실패, 일반 HTML로 재시도: ' + str(exc), 'WARNING')
    text, _ = get_html(ctx, url, HOSTS)
    inner = next_text(text)
    return inner if '"goodsDetail"' in inner else text


def grab(text, key, opener='{'):
    index = text.find('"' + key + '":' + opener)
    if index < 0:
        return None
    start = text.index(opener, index)
    close = '}' if opener == '{' else ']'
    depth, inside, escape = 0, False, False
    for pos in range(start, len(text)):
        ch = text[pos]
        if inside:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                inside = False
        elif ch == '"':
            inside = True
        elif ch == opener:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:pos + 1])
                except ValueError:
                    return None
    return None


def search(ctx, style, requested):
    referer = SITE + '/search/goods?' + urlencode({'search_keyword_list': style})
    data = api(ctx, '/goods/search/v4?' + urlencode({'page': 1, 'size': 100, 'search_keyword_list': style, 'order_type': 'newest'}), referer)
    body = data.get('body') if isinstance(data, dict) else data
    hits = []
    for goods in body or []:
        code = str(goods.get('code') or '').upper()
        if code.split('_')[0] != style or (requested and code != style + '_' + requested):
            continue
        hits.append(dict(code=code, name=goods.get('name') or '', brand=goods.get('brandName') or '',
                         tag=(goods.get('price') or {}).get('tag'), real=(goods.get('price') or {}).get('real')))
    return hits


def category(ctx, code):
    data = api(ctx, '/goods/lambda?' + urlencode({'goods_code_list': code}), SITE + '/goods/detail/' + code)
    data = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else {}
    kind = data.get('itemkind') or {}
    names = [kind.get(k) for k in ('category1', 'category2', 'category3') if kind.get(k)]
    if not names and data.get('category'):
        first = data['category'][0]
        names = [first.get('categoryDepth' + str(i) + 'Name') for i in (1, 2, 3) if first.get('categoryDepth' + str(i) + 'Name')]
    return ' > '.join(names)


def measure_table(rows):
    if not rows:
        return ''
    columns = []
    for row in rows:
        for key in row:
            if key != 'option2Name' and key not in columns:
                columns.append(key)
    return grid_table([['사이즈'] + columns] + [[row.get('option2Name', '')] + [row.get(c, '') for c in columns] for row in rows])


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'([A-Z0-9]{6,20})(?:_([A-Z0-9]{1,4}))?', raw)
    if not match:
        ctx.fail_item('품번 형식 오류: 기본 품번(예: 111E3TI001) 또는 품번_색상코드')
        raise CrawlError('품번 형식 오류')
    style, requested = match[1], match[2]
    ctx.log('버킷스토어 코드 버전: ' + REVISION, 'INFO')
    cache, failures, emitted = {}, [], set()
    try:
        ctx.stage('버킷스토어 공식 검색: ' + style)
        hits = search(ctx, style, requested)
        if not hits:
            raise CrawlError('정확히 일치하는 상품코드가 검색되지 않았습니다: ' + raw)
        path = None
        if ctx.enabled('category'):
            try:
                path = category(ctx, hits[0]['code'])
            except CrawlError as exc:
                raise CrawlError('선택한 카테고리 조회 실패: ' + str(exc))
        for hit in hits:
            code = hit['code']
            ctx.checkpoint()
            ctx.stage('버킷스토어 색상 상품 ' + code)
            try:
                url = SITE + '/goods/detail/' + code
                text = page_data(ctx, code)
                goods = grab(text, 'goodsDetail') or {}
                option = next((o for o in goods.get('optionList') or [] if str(o.get('goodsCode') or '').upper() == code), None)
                color_code = code.split('_', 1)[1] if '_' in code else ''
                color = color_entry((option or {}).get('option1Name') or '', color_code)
                name_raw = goods.get('goodsName') or hit['name']
                if not name_raw:
                    raise CrawlError('공식 상품명 없음')
                brand = goods.get('brandName') or hit['brand'] or ''
                record = dict(product_code=style, product_name=clean_name(name_raw, [brand, '핑', 'PING'], [style, code]), product_url=url,
                              color=color['label'], color_name=color['color_name'], color_code=color['color_code'],
                              **{'원본 상품명': name_raw, '사이트 브랜드': brand})
                if ctx.enabled('size'):
                    record['size'] = joined(o.get('option2Name') for o in sorted((option or {}).get('option2List') or [], key=lambda x: x.get('displayOrder') or 0))
                    if not record['size']:
                        ctx.log('공식 사이즈 옵션 없음: 빈칸', 'WARNING')
                info = goods.get('priceInfo') or {}
                if ctx.enabled('price'):
                    record['price'] = amount(hit['real'] or info.get('currentPrice') or info.get('realPrice'))
                if ctx.enabled('regular_price'):
                    ctx.checkpoint()
                    record['regular_price'] = amount(hit['tag'] or info.get('tagPrice'))
                    if record['regular_price'] is None:
                        ctx.log('독립 정상가(tag 가격) 없음/0/형식 오류: 빈칸, 나머지 수집 계속', 'WARNING')
                if ctx.enabled('category'):
                    record['category'] = path
                photos = [absolute(SITE, u) for u in goods.get('imageList') or []]
                photos = [u for u in photos if u and not SKIP.search(u)]
                if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                    accepted = screen_shots(ctx, photos, cache)
                    main, extras = auto_slots(ctx, accepted, cache, '버킷스토어 색상별 상품코드 ' + code + ' 공식 imageList') if accepted else ('', [])
                    apply_images(ctx, record, main, extras)
                if ctx.enabled('detail_page'):
                    detail = api(ctx, '/goods/' + code + '/detail?isAdmin=false', url) or {}
                    body = [absolute(SITE, u) for u in re.findall(r'<img[^>]+src="([^"]+)"', detail.get('goodsDescription') or '')]
                    body = [u for u in body if u and not SKIP.search(u)]
                    pairs = [((n.get('item') or '').strip(), re.sub(r'\s*\r?\n\s*', ' ', n.get('content') or '').strip()) for n in detail.get('notificationInfoList') or []]
                    figure = re.search(r'"src":"(https?://[^"]+/size/[^"]+\.(?:jpe?g|png))","alt":"[^"]*사이즈[^"]*"', text)
                    figures = detail_images(ctx, [figure[1]], cache) if figure else []
                    measure = measure_table(grab(text, 'actualMeasurementSizeList', '[') or [])
                    sizes = size_section([(figures, measure, ['단위: cm'] if measure else [])]) if figures or measure else ''
                    record['detail_page'] = build_detail(ctx, detail_images(ctx, body or photos, cache), notice_table(pairs), sizes)
                if color['label'] in emitted:
                    continue
                ctx.emit_result(record)
                emitted.add(color['label'])
            except CrawlError as exc:
                message = code + ': ' + str(exc)
                failures.append(message)
                ctx.fail_item(message)
        finish_failures(ctx, failures, emitted, raw)
    except Exception as exc:
        if not failures:
            ctx.fail_item(str(exc))
        raise
