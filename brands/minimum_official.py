PORTAL_API_VERSION = 1
"""미니멈 (공식 홈페이지) — Cafe24 혼합 색상 매핑 크롤러

검증 범위: 사용자 단독 크롤러(미니멈 크롤러)에서 운영 확인된 URL·선택자·응답 키를 Crawler Hub SDK 규약으로 이식. 이 코드를 작성한 환경은 해당 사이트 접속이 차단되어 실사이트 실행은 미검증이며, SDK 계약·선택 항목 생략·표 재구성은 오프라인 fixture 테스트(tests/test_oberon_brands.py)로만 확인함.
"""
REVISION = '2026-11-12-oberon-1'
CONFIG = {'site': 'https://minimummall.com',
 'hosts': ('minimummall.com', 'www.minimummall.com'),
 'brand': '미니멈',
 'aliases': ['미니멈', 'MINIMUM'],
 'notice': '#desctable tr',
 'zoom': True,
 'detail_skip': '/intro_|/EZ_studio/|detail_notice|/web/upload/',
 'detail_tiers': ['#prdDetail'],
 'input': '([A-Z0-9][A-Z0-9-]{2,24}?)(?:_([A-Z0-9]{1,4}))?',
 'format': '색상코드를 제외한 기본 품번(영문 대문자·숫자). 자체상품코드에 색상코드가 있는 상품만 품번_색상코드 지정 가능'}

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

# ───────────────────────── Cafe24 공통 엔진 (혼합 매핑) ─────────────────────────
# 1) 색상 옵션이 1개인 상품 페이지 또는 공식 옵션 연결 사진(link_image·색상 슬라이드)은 자동 결과(emit_result)
# 2) 색상 연결이 없는 갤러리 사진은 수동 매핑 후보(emit_unmapped) — 사진 색감으로 추측하지 않음
SIZE_DIR = re.compile(r'/web/product/(extra/)?(big|medium|small|tiny)/')
NOT_DETAIL = re.compile(r'xans-product-image|xans-product-addimage|listImg|keyImg|xans-layout|header|footer|sns|'
                        r'xans-product-relation|recent|review|board', re.I)
DETAIL_SKIP = re.compile(r'/common/|/_d1/img/|/web/upload/icon|icon_|ico_|btn_|logo|banner|bn_|cashreceipt|escrow|'
                         r'allat|noimg|no_img|blank|/_wg/|/skin/|echosting|care_|delivery|shipping|cs_info|배송|교환|반품', re.I)
COLOR_TITLE = re.compile(r'색상|컬러|color|colour', re.I)
SIZE_TITLE = re.compile(r'사이즈|size|치수|호수', re.I)
DEFAULT_TIERS = ['.xans-product-additional .guide_contents', '.xans-product-additional',
                 'div#prdDetail, div.prdDetail, .cont_detail, #detail_cont',
                 '[class*=detailArea] .guide_contents, .detaarea .guide_contents']


def variants(url):
    """같은 파일명의 크기 폴더 후보: big → medium → 화면 주소 → small (실제 열리는 첫 주소 사용)."""
    match = SIZE_DIR.search(url)
    if not match:
        return [url]
    extra = match[1] or ''
    swap = lambda size: url[:match.start()] + '/web/product/' + extra + size + '/' + url[match.end():]
    return list(dict.fromkeys([swap('big'), swap('medium'), url, swap('small')]))


def info_rows(doc):
    rows = {}
    for tr in doc.select('.infoArea tr, .xans-product-detaildesign tr'):
        th, td = tr.find('th'), tr.find('td')
        if th and td:
            key = th.get_text(' ', strip=True)
            if key and key not in rows:
                rows[key] = re.sub(r'\s+', ' ', td.get_text(' ', strip=True))
    return rows


def js_value(text, name):
    match = re.search(r'\b(?:var\s+)?' + name + r"""\s*=\s*(['"])(.*?)\1\s*;""", text)
    return match[2].strip() if match else ''


def stock_data(text):
    match = re.search(r"var\s+option_stock_data\s*=\s*'((?:\\.|[^'\\])*)'", text)
    if not match:
        return None
    raw = match[1]
    for parser in (lambda s: json.loads(json.loads('"' + s + '"')),
                   lambda s: json.loads(s.encode().decode('unicode_escape').encode('latin1').decode('utf-8'))):
        try:
            value = parser(raw)
            if isinstance(value, dict):
                return value
        except (ValueError, UnicodeError):
            continue
    raise CrawlError('공식 옵션 재고 데이터(option_stock_data) 해석 실패')


def page_colors(doc, text, notice):
    """공식 옵션 → [(색상 원문, {'sizes':[], 'prices':[], 'image':''})]. 품절 옵션 포함, 숨김 옵션 제외."""
    data = stock_data(text)
    result = {}
    if data:
        for item in data.values():
            if str(item.get('is_display') or 'T') == 'F':
                continue
            names = item.get('option_name_original') or item.get('option_name') or []
            if isinstance(names, str):
                names = [n.strip() for n in names.split('#$%') if n.strip()] or [names]
            values = item.get('option_value_orginal') or item.get('option_value_original') or []
            if not values and item.get('option_value'):
                values = str(item['option_value']).split('-') if len(names) >= 2 else [item['option_value']]
            if not isinstance(values, list):
                values = [values]
            ci = next((i for i, n in enumerate(names) if COLOR_TITLE.search(str(n))), None)
            si = next((i for i, n in enumerate(names) if SIZE_TITLE.search(str(n))), None)
            if ci is None and si is None and len(values) >= 2:
                ci, si = 0, 1
            elif ci is None and si is None:
                si = 0
            color = str(values[ci]).strip() if ci is not None and ci < len(values) else ''
            size = str(values[si]).strip() if si is not None and si < len(values) else ''
            entry = result.setdefault(color, {'sizes': [], 'prices': [], 'image': ''})
            if size and size not in entry['sizes']:
                entry['sizes'].append(size)
            price = amount(item.get('option_price'))
            if price:
                entry['prices'].append(price)
            if item.get('item_image_file') and not entry['image']:
                entry['image'] = str(item['item_image_file'])
    else:
        colors, sizes = [], []
        for select in doc.select('select[option_title]'):
            values = [o.get_text(' ', strip=True) for o in select.select('option') if (o.get('value') or '') not in ('', '*', '**')]
            (colors if COLOR_TITLE.search(select.get('option_title') or '') else sizes).extend(values)
        for color in colors or ['']:
            result.setdefault(color, {'sizes': list(dict.fromkeys(sizes)), 'prices': [], 'image': ''})
    if '' in result and len(result) == 1 and notice.get('색상'):
        result = {notice['색상']: result['']}
    return list(result.items())


def linked_images(doc, text, final):
    """색상 옵션의 공식 연결 이미지(link_image) 또는 색상칩 연결 → {옵션 원문: [URL]}."""
    out = {}
    chips = {}
    match = re.search(r'"aOptionColorchip"\s*:\s*(\{[^{}]*\})', text)
    if match:
        try:
            chips = {k.lower(): v for k, v in json.loads(match[1]).items()}
        except ValueError:
            chips = {}
    for box in doc.select('ul[option_title], select[option_title]'):
        if not COLOR_TITLE.search(box.get('option_title') or ''):
            continue
        for item in box.select('li[option_value], option[link_image]'):
            name = (item.get('option_value') or item.get('value') or item.get_text(strip=True) or '').strip()
            url = item.get('link_image') or ''
            if not url and item.name == 'li':
                styled = item.select_one('[style*=background]')
                hexa = re.search(r'#([0-9a-fA-F]{6})', (styled.get('style') if styled else '') or '')
                url = chips.get('#' + hexa[1].lower(), '') if hexa else ''
            url = absolute(final, url)
            if name and url:
                out.setdefault(name, [])
                if url not in out[name]:
                    out[name].append(url)
    if CONFIG.get('slides'):
        for slide in doc.select('.slide-colors .slide[data-name], .slide[data-name]'):
            name = (slide.get('data-name') or '').strip()
            urls = [absolute(final, img_src(i)) for i in slide.select('img')]
            urls = [u for u in urls if u and not DETAIL_SKIP.search(u)]
            if name and urls:
                for url in urls:
                    out.setdefault(name, [])
                    if url not in out[name]:
                        out[name].append(url)
    return out


def gallery_groups(ctx, doc, final, product_no):
    """대표(og:image 원본 우선) + 추가(/extra/) 그룹. 각 그룹은 같은 사진의 크기 후보."""
    main = []
    for og in doc.select('meta[property="og:image"]'):
        url = absolute(final, og.get('content'))
        if '/web/product/' in url and not url.lower().endswith('.gif'):
            main.append(url)
    for img in doc.select('img.BigImage, .keyImg img, .imgArea .bigImage, .xans-product-image .thumbnail img'):
        if img.find_parent(class_=re.compile(r'xans-product-addimage|listImg')):
            continue
        url = absolute(final, img_src(img))
        if '/web/product/' in url and '/extra/' not in url:
            main.extend(variants(url))
    groups = [list(dict.fromkeys(main))] if main else []
    for img in doc.select('.xans-product-addimage img, .listImg img'):
        url = absolute(final, img_src(img))
        if '/web/product/extra/' in url:
            group = variants(url)
            if group not in groups:
                groups.append(group)
    if CONFIG.get('zoom') and len(groups) < 2:
        try:
            text, zoom_final = get_html(ctx, CONFIG['site'] + '/product/image_zoom2.html?product_no=' + product_no, CONFIG['hosts'])
            for img in soup(text).select('img'):
                url = absolute(zoom_final, img_src(img))
                if '/web/product/' in url:
                    group = variants(url)
                    if not any(set(group) & set(g) for g in groups):
                        groups.append(group)
        except CrawlError as exc:
            ctx.log('확대 이미지 페이지 확인 불가: ' + str(exc), 'WARNING')
    return groups


def detail_parts(ctx, doc, final, style):
    """공식 상세 영역의 이미지(제외 영역 요청 없음) + HTML 고시 + 사이즈 블록."""
    images, figures = [], []
    size_blocks = []
    if CONFIG.get('size_wrap'):
        box = doc.select_one('.size-wrap')
        if box:
            figure = box.select_one('.image-box img')
            figure_url = absolute(final, img_src(figure)) if figure else ''
            table = box.select_one('table')
            notes = [li.get_text(' ', strip=True) for li in box.select('ul li')][:6]
            table_html = copy_table(table) if table else ''
            if table is not None and not table_html:
                raise CrawlError('공식 사이즈표가 있으나 행을 읽지 못했습니다(파싱 실패).')
            if figure_url:
                figures.append(figure_url)
            size_blocks.append(([figure_url] if figure_url else [], table_html, notes))
    for tier in CONFIG.get('detail_tiers') or DEFAULT_TIERS:
        found = []
        for area in doc.select(tier):
            for img in area.select('img'):
                if img.find_parent(class_=NOT_DETAIL) or img.find_parent(class_='size-wrap') or img.find_parent(class_=re.compile(r'^slide')):
                    continue
                url = absolute(final, img_src(img))
                if not url or url in found or url in figures:
                    continue
                path = unquote(urlsplit(url).path)
                if DETAIL_SKIP.search(path) or (CONFIG.get('detail_skip') and re.search(CONFIG['detail_skip'], path, re.I)):
                    continue
                # 외부 호스팅 상세 이미지는 해당 품번이 파일 경로에 있을 때만(다른 상품 공용 이미지 제외)
                if urlsplit(url).hostname and any(urlsplit(url).hostname.endswith(h) for h in CONFIG.get('style_hosts', ())) and style.upper() not in path.upper():
                    continue
                found.append(url)
        if found:
            images = found
            break
    pairs = []
    if CONFIG.get('notice'):
        for tr in doc.select(CONFIG['notice']):
            cells = tr.find_all(['th', 'td'], recursive=False)
            if len(cells) == 2:
                key, value = cells[0].get_text(' ', strip=True), re.sub(r'\s+', ' ', cells[1].get_text(' ', strip=True))
                if key and value and key not in dict(pairs):
                    pairs.append((key, value))
        if doc.select(CONFIG['notice']) and not pairs:
            raise CrawlError('공식 상품정보고시 표가 있으나 항목을 읽지 못했습니다(파싱 실패).')
    return images, pairs, size_blocks


def find_products(ctx, base):
    """공식 검색 결과의 product_no (페이지네이션 30쪽 제한). 최근본/추천 영역 제외."""
    queue = [CONFIG['site'] + '/product/search.html?' + urlencode({'keyword': base})]
    visited, numbers = set(), []
    while queue:
        url = queue.pop(0)
        if url in visited:
            continue
        if len(visited) >= 30:
            raise CrawlError('공식 검색 30페이지 초과')
        visited.add(url)
        text, final = get_html(ctx, url, CONFIG['hosts'])
        doc = soup(text)
        for noise in doc.select('[class*="recent"], [class*="recommend"], [class*="related"], #header, #footer, header, footer'):
            noise.decompose()
        scope = doc.select_one('.xans-search-result') or doc.select_one('.prdList') or doc
        for li in scope.select('li[id^="anchorBoxId_"]'):
            match = re.match(r'anchorBoxId_(\d+)', li.get('id', ''))
            if match and match[1] not in numbers:
                numbers.append(match[1])
        if not scope.select('li[id^="anchorBoxId_"]'):
            for a in scope.select('a[href*="/product/"], a[href*="product_no="]'):
                match = re.search(r'/product/[^/]+/(\d+)/|product_no=(\d+)', a.get('href', ''))
                number = match and (match[1] or match[2])
                if number and number not in numbers:
                    numbers.append(number)
        for a in doc.select('.xans-search-paging a[href]'):
            link = absolute(final, a['href'])
            params = parse_qs(urlsplit(link).query)
            if urlsplit(link).hostname in CONFIG['hosts'] and params.get('keyword') == [base] and link not in visited:
                queue.append(link)
    if not numbers and CONFIG.get('api_search'):
        numbers = api_search(ctx, base)
    return numbers


def api_search(ctx, base):
    """사이트 검색이 JS 외부검색일 때: 공식 메인페이지가 공개하는 Cafe24 프런트 앱 키로 상품명 검색(읽기 전용)."""
    mall, version = CONFIG['api_search']
    text, _ = get_html(ctx, CONFIG['site'] + '/', CONFIG['hosts'])
    keys = list(dict.fromkeys(re.findall(r'(?:client_id|cafe24_app_key)=([A-Za-z0-9]{20,24})', text)))
    found = []
    for key in keys:
        url = ('https://' + mall + '.cafe24api.com/api/v2/products?' + urlencode({'product_name': base, 'display': 'T', 'limit': 100,
               'fields': 'product_no,product_name', 'cafe24_app_key': key, 'cafe24_api_version': version}))
        try:
            data = get_json(ctx, url, (mall + '.cafe24api.com',), headers={'Origin': CONFIG['site']})
        except CrawlError as exc:
            ctx.log('Cafe24 공개 상품 검색 실패: ' + str(exc), 'WARNING')
            continue
        for product in data.get('products') or []:
            if bounded(base, str(product.get('product_name') or '')) and str(product.get('product_no')) not in found:
                found.append(str(product.get('product_no')))
        break
    return found


def identity(doc, text, base, info):
    """공식 자체상품코드·모델·상품명 끝 코드로 정확한 품번 확인 → (style, 색상코드) 또는 None."""
    raw = js_value(text, 'product_name') or info.get('상품명') or ''
    if not raw:
        heading = doc.select_one('.infoArea h2, .infoArea h1, .infoArea h3, .headingArea h2, .headingArea h1')
        raw = heading.get_text(' ', strip=True) if heading else ''
    if not raw:
        og = doc.select_one('meta[property="og:title"]')
        raw = og.get('content', '').strip() if og else ''
    if not raw:
        raise CrawlError('공식 상품명 없음')
    for value in (info.get('자체상품코드') or js_value(text, 'custom_product_code'), info.get('모델명'), info.get('모델')):
        value = str(value or '').strip().upper()
        if not value:
            continue
        match = re.fullmatch(r'([A-Z0-9][A-Z0-9\-]*?)_([A-Z0-9]{1,4})', value)
        style, code = (match[1], match[2]) if match else (value, '')
        if style == base or value == base:
            return raw, style if style == base else value, code
    if bounded(base, raw.upper()):
        match = re.search(r'(?<![A-Z0-9])' + re.escape(base) + r'_([A-Z0-9]{1,4})(?![A-Z0-9])', raw.upper())
        return raw, base, match[1] if match else ''
    return None


def common_value(ctx, values, label):
    values = [v for v in values]
    if values and values[0] not in (None, '') and all(v == values[0] for v in values):
        return values[0]
    if values:
        ctx.log('수동 후보 공통 ' + label + ' 미확정(색상/링크별 값이 다르거나 없음): 빈칸', 'WARNING')
    return '' if label == '사이즈' else None


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(CONFIG['input'], raw)
    if not match:
        ctx.fail_item('품번 형식 오류: ' + CONFIG['format'])
        raise CrawlError('품번 형식 오류')
    base, requested = match[1], (match.group(2) if match.re.groups >= 2 else None)
    ctx.log(CONFIG['brand'] + ' 코드 버전: ' + REVISION, 'INFO')
    ctx.allow_automatic_results = True
    wants = ctx.enabled('main_image') or ctx.enabled('additional_images')
    cache, failures, emitted = {}, [], set()
    pages, manual, palette, details = [], [], [], ([], [], [])
    try:
        ctx.stage(CONFIG['brand'] + ' 공식 품번 검색: ' + base)
        for number in find_products(ctx, base):
            ctx.checkpoint()
            url = CONFIG['site'] + '/product/detail.html?product_no=' + number
            try:
                text, final = get_html(ctx, url, CONFIG['hosts'])
                doc = soup(text)
                info = info_rows(doc)
                found = identity(doc, text, base, info)
                if not found:
                    ctx.log('품번 불일치 상품 제외: ' + final, 'INFO')
                    continue
                name_raw, style, page_code = found
                if requested and page_code != requested:
                    ctx.log('지정 색상코드와 다른 상품 링크 제외: ' + final, 'INFO')
                    continue
                for tag in doc.select('link[rel="canonical"], meta[property="og:url"]'):
                    link = absolute(final, tag.get('href') or tag.get('content'))
                    if urlsplit(link).hostname in CONFIG['hosts'] and ('/' + number + '/' in link or 'product_no=' + number in link):
                        final = link
                        break
                notice = {}
                if CONFIG.get('notice'):
                    for tr in doc.select(CONFIG['notice']):
                        cells = tr.find_all(['th', 'td'], recursive=False)
                        if len(cells) == 2:
                            notice[cells[0].get_text(' ', strip=True)] = cells[1].get_text(' ', strip=True)
                options = page_colors(doc, text, notice)
                if page_code and len(options) > 1:
                    mine = [o for o in options if re.match(re.escape(page_code) + r'(\b|/|\s|\(|_|$)', o[0].upper())]
                    options = mine or options
                colors = []
                for original, data in options:
                    cname, ccode = split_color(original)
                    if not ccode and page_code and len(options) == 1:
                        ccode = page_code
                    if not cname and not ccode:
                        continue
                    colors.append(dict(color_entry(cname, ccode), original=original, **data))
                if not colors and page_code:
                    colors = [dict(color_entry('', page_code), original='', sizes=[], prices=[], image='')]
                if not colors:
                    raise CrawlError('공식 색상 후보가 없습니다: ' + final)
                tags = re.findall(r'^\s*\[([^\]]+)\]', name_raw)
                page = dict(doc=doc, text=text, final=final, number=number, style=style, colors=colors,
                            name=clean_name(name_raw, CONFIG['aliases'], [base, style + ('_' + page_code if page_code else '')]),
                            raw_name=name_raw, site_brand=tags[0].strip() if tags and CONFIG.get('title_brand') else '')
                if ctx.enabled('detail_page'):
                    images, pairs, blocks = detail_parts(ctx, doc, final, style)
                    details[0].extend(u for u in images if u not in details[0])
                    if pairs and not details[1]:
                        details[1].extend(pairs)
                    for block in blocks:
                        if block not in details[2]:
                            details[2].append(block)
                pages.append(page)
            except CrawlError as exc:
                message = url + ': ' + str(exc)
                failures.append(message)
                ctx.fail_item(message)
        if not pages:
            if failures:
                raise RuntimeError('상품 링크 수집 미완료: ' + ' / '.join(failures))
            raise CrawlError('정확히 일치하는 공식 상품을 찾지 못했습니다: ' + raw)
        shared = ''
        if ctx.enabled('detail_page'):
            ctx.stage('공통 상세 HTML 구성')
            shared = build_detail(ctx, detail_images(ctx, details[0], cache), notice_table(details[1]), size_section(details[2]))

        def base_record(page):
            record = dict(product_code=base, product_name=page['name'], product_url=page['final'], **{'원본 상품명': page['raw_name']})
            if page['site_brand']:
                record['사이트 브랜드'] = page['site_brand']
            if ctx.enabled('category'):
                record['category'] = ' > '.join(dict.fromkeys(
                    t for t in (re.sub(r'\s+', ' ', li.get_text(' ', strip=True)) for li in page['doc'].select('.xans-product-headcategory li'))
                    if t and t.lower() not in ('현재 위치', '홈', 'home', '>') and t != page['raw_name']))
            collect_regular_price(ctx, record, page['doc'], 'cafe24')
            if ctx.enabled('detail_page'):
                record['detail_page'] = shared
            return record

        def page_price(page, color):
            if not ctx.enabled('price'):
                return None
            values = set(color['prices'])
            if len(values) == 1:
                return values.pop()
            if len(values) > 1:
                ctx.log(color['label'] + ' 사이즈별 판매가 상이: 빈칸', 'WARNING')
                return None
            node = page['doc'].select_one('#span_product_price_sale') or page['doc'].select_one('#span_product_price_text')
            value = amount(re.sub(r'[^\d]', '', node.get_text(' ', strip=True))) if node else None
            if value is None:
                meta = page['doc'].select_one('meta[property="product:sale_price:amount"]') or page['doc'].select_one('meta[property="product:price:amount"]')
                value = amount(meta.get('content')) if meta else None
            if value is None:
                ctx.log('공식 판매가 확인 불가: 빈칸', 'WARNING')
            return value

        def emit_auto(page, color, urls, evidence):
            key = color['color_code'] or 'name:' + color['color_name']
            if key in emitted:
                ctx.log('이미 수집한 색상의 추가 링크 생략(첫 링크 기준): ' + page['final'], 'INFO')
                return
            record = base_record(page)
            record.update(color=color['label'], color_name=color['color_name'], color_code=color['color_code'])
            if ctx.enabled('size'):
                record['size'] = joined(color['sizes'])
            if ctx.enabled('price'):
                record['price'] = page_price(page, color)
            if wants:
                ctx.stage(CONFIG['brand'] + ' 색상 ' + color['label'])
                accepted = screen_shots(ctx, urls, cache)
                main, extras = auto_slots(ctx, accepted, cache, evidence) if accepted else ('', [])
                if not accepted:
                    ctx.log(color['label'] + ': 공식 연결 사진이 검사를 통과하지 못해 이미지 빈칸', 'WARNING')
                apply_images(ctx, record, main, extras)
            ctx.emit_result(record)
            emitted.add(key)

        for page in pages:
            ctx.checkpoint()
            try:
                photos = [first_open(ctx, g, cache) for g in gallery_groups(ctx, page['doc'], page['final'], page['number'])] if wants else []
                photos = [u for u in photos if u]
                if len(page['colors']) == 1 or not wants:
                    evidence = '공식 상품 페이지의 색상 옵션이 1개' + (' (자체코드 ' + page['style'] + '_' + page['colors'][0]['color_code'] + ')' if page['colors'][0]['color_code'] else '')
                    for color in page['colors']:
                        emit_auto(page, color, photos if len(page['colors']) == 1 else [], evidence)
                    continue
                links = linked_images(page['doc'], page['text'], page['final'])
                linked_digests = set()
                for color in page['colors']:
                    urls = links.get(color['original']) or links.get(color['color_name']) or []
                    if not urls:
                        continue
                    for url in urls:
                        result = inspect(ctx, url, cache)
                        if result['status'] == 'accessible':
                            linked_digests.add(result['digest'])
                    if requested and color['color_code'] != requested:
                        continue
                    emit_auto(page, color, urls, '공식 색상 옵션 연결 이미지(link_image/색상 슬라이드): ' + color['original'])
                for color in page['colors']:
                    if color not in palette:
                        palette.append(color)
                if requested:
                    ctx.log('지정 색상과 공식 연결이 없는 공통 사진은 수동 후보에서 제외합니다.', 'INFO')
                    continue
                for url in screen_shots(ctx, photos, cache, set(linked_digests)):
                    manual.append((url, page))
            except CrawlError as exc:
                message = page['final'] + ': ' + str(exc)
                failures.append(message)
                ctx.fail_item(message)
        if manual:
            ctx.stage('수동 색상 매핑 후보 전송')
            first = pages[0]
            common = base_record(first)
            records = [base_record(p) for p in pages] if ctx.enabled('regular_price') else []
            if ctx.enabled('regular_price'):
                merge_regular_prices(ctx, common, records)
            if ctx.enabled('size'):
                common['size'] = common_value(ctx, [joined(c['sizes']) for c in palette], '사이즈')
            if ctx.enabled('price'):
                common['price'] = common_value(ctx, [page_price(p, c) for p in pages for c in p['colors'] if c in palette], '판매가')
            colors = palette_of(palette)
            for url, page in manual:
                record = dict(common)
                record['product_url'] = first['final']
                ctx.emit_unmapped(record, colors, url, shared_detail_html=shared)
            emitted.add('manual')
            ctx.log('공식 연결 없는 사진 ' + str(len(manual)) + '장을 색상 매핑 후보로 전송했습니다.', 'SUCCESS')
        finish_failures(ctx, failures, emitted, raw)
    except Exception as exc:
        if not failures:
            ctx.fail_item(str(exc))
        raise
