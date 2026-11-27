PORTAL_API_VERSION = 1
"""크로커다일 크롭 (던필드몰) — 갤러리 + 상세 통이미지 개별 컷 크롭, 로컬 JPEG 색상 매핑

- 던필드몰 크로커다일은 갤러리(대표+추가 5장)가 대표 색상 1~2개 사진뿐이고 나머지 색상은 상세 통이미지
  (COLOR VIEW·색상별 앞/뒤 컷) 안에만 있다(2026-11-27 실조회 CDAACJP3202=8686, CDAACTP3703=8672).
- 상품 조회·옵션·가격·상세 HTML은 crocodile_official(던필드몰 URL 버전) 로직을 그대로 쓴다.
- 갤러리 사진은 통째로, 상세 통이미지는 흰 여백·가는 흰 선 경계로만 개별 컷을 자른다(패션플러스 크롭필요건 엔진 재사용,
  고정 좌표·등분·확대 없음). 사이즈표·공통 배너는 컷 후보에서 제외.
- 사전 매핑: 색상 옵션 1개 페이지의 갤러리, 공식 link_image, 사진에 붙은 색상명 글자 OCR(Windows 기본 OCR)이
  공식 색상 하나와만 일치할 때. 나머지는 사용자가 색상 매핑하기에서 분류.
"""
REVISION = '2026-11-27-crocodile-crop-1'
CONFIG = {'site': 'https://dawnfieldmall.com',
 'hosts': ('dawnfieldmall.com', 'www.dawnfieldmall.com'),
 'brand': '크로커다일',
 'aliases': ['크로커다일', 'CROCODILE', '던필드', 'DAWNFIELD'],
 'title_brand': True,
 'input': '([A-Z0-9][A-Z0-9-]{2,24}?)(?:_([A-Z0-9]{1,4}))?',
 'format': '색상코드를 제외한 기본 품번(영문 대문자·숫자). 자체상품코드에 색상코드가 있는 상품만 품번_색상코드 지정 가능'}

# ───────────────────────── 공통 도우미 (브랜드 파일마다 동일하게 포함) ─────────────────────────
import difflib
import hashlib
import html
import io
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qs, unquote
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError
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
    parsed=urlsplit(url)
    # 공식몰의 product_images 원본과 동일 경로인 Cafe24 호스트의 중복 점·c 누락 오타를 복구.
    if re.fullmatch(r'dfamall\d*\.{2,}(?:cafe24|afe24)\.com',parsed.hostname or '',re.I) and parsed.path.startswith('/product_images/'):
        url=CONFIG['site']+parsed.path+('?' + parsed.query if parsed.query else '')
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
                            (r'^([A-Z0-9]{1,4})_(.+)$', 2, 1),
                            (r'^([A-Za-z가-힣][A-Za-z가-힣 /_-]*?)\s*(\d{2})$', 1, 2)):
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



# ───────────────────────── 크롭 엔진 (fashionplus_item_crop 2026-11-20 그대로) ─────────────────────────
MAX_IMAGE = 30_000_000
MAX_OCR_TILES = 40
PAD_MIN_WHITE = 245
PAD_MAX_SHARE = .4

POWERSHELL_OCR = r'''
param([string]$Manifest)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$method=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'})[0]
function Await($operation,$type){$task=$method.MakeGenericMethod($type).Invoke($null,@($operation));$task.Wait();return $task.Result}
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if($null -eq $engine){throw 'Windows OCR language unavailable. Install Korean or English OCR language in Windows Settings.'}
$items=Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8|ConvertFrom-Json
$results=@(foreach($item in $items){
    $stream=$null;$bitmap=$null
    try {
        $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync([string]$item.path)) ([Windows.Storage.StorageFile])
        $stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        @{index=$item.index;text=$result.Text;error=''}
    } catch { @{index=$item.index;text='';error=$_.Exception.Message} }
    finally {if($null -ne $stream){$stream.Dispose()};if($null -ne $bitmap){$bitmap.Dispose()}}
})
ConvertTo-Json -InputObject $results -Depth 5 -Compress
'''
TRANSLIT = {'블랙': 'BLACK', '화이트': 'WHITE', '아이보리': 'IVORY', '카키': 'KHAKI', '네이비': 'NAVY', '베이지': 'BEIGE', '그레이': 'GRAY',
            '차콜': 'CHARCOAL', '브라운': 'BROWN', '레드': 'RED', '블루': 'BLUE', '그린': 'GREEN', '핑크': 'PINK', '퍼플': 'PURPLE',
            '옐로우': 'YELLOW', '오렌지': 'ORANGE', '민트': 'MINT', '카멜': 'CAMEL', '와인': 'WINE', '올리브': 'OLIVE', '크림': 'CREAM'}

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
    counts = row_counts(mask, box)
    return sum(counts) / max(1, (box[2] - box[0]) * (box[3] - box[1]))


def row_counts(mask, box):
    """box 영역의 행별 내용(배경 아닌) 화소 수. C 수준 bytes.count로 계산."""
    piece = mask.crop(box)
    w, h = piece.size
    raw = piece.tobytes()
    piece.close()
    return [raw[y * w:(y + 1) * w].count(255) for y in range(h)]


def col_counts(mask, box):
    piece = mask.crop(box).transpose(Image.Transpose.TRANSPOSE)
    w, h = piece.size
    raw = piece.tobytes()
    piece.close()
    return [raw[y * w:(y + 1) * w].count(255) for y in range(h)]


def split_seams(mask, box, s, depth=0):
    """사진끼리 여백 없이 붙어 있어도 사이의 '거의 흰 가는 선'(행/열 전체가 배경)에서 나눈다. 원본 해상도 기준."""
    l, t, r, b = box
    if depth > 4:
        return [box]
    rows = row_counts(mask, box)
    parts = [(t + a, t + z) for a, z in runs_of(rows, max(1, (r - l) * .03), 0) if z - a >= 60 * s]
    if len(parts) > 1:
        return [x for a, z in parts for x in split_seams(mask, (l, a, r, z), s, depth + 1)]
    cols = col_counts(mask, box)
    parts = [(l + a, l + z) for a, z in runs_of(cols, max(1, (b - t) * .03), 0) if z - a >= 60 * s]
    if len(parts) > 1:
        return [x for a, z in parts for x in split_seams(mask, (a, t, z, b), s, depth + 1)]
    return [box]


def solid_background(image):
    """맨 위 줄이 한 가지 밝은 색이면 그 색, 아니면 None(흰 여백 보충 불가)."""
    row = image.crop((0, 0, image.width, 1)).resize((32, 1))
    raw = row.tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    if max(max(p[i] for p in pixels) - min(p[i] for p in pixels) for i in range(3)) > 8:
        return None
    color = tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))
    return color if min(color) >= PAD_MIN_WHITE else None


def clean_reach(mask, box, side, limit, margin):
    """box의 한 변 바깥으로 내용 없이(실제 배경) 넓힐 수 있는 길이(최대 limit). 글자·이웃 사진 앞 margin은 남긴다."""
    l, t, r, b = box
    width, height = mask.size
    if limit <= 0:
        return 0
    if side == 'top':
        strip = (l, max(0, t - limit - margin), r, t)
    elif side == 'bottom':
        strip = (l, b, r, min(height, b + limit + margin))
    elif side == 'left':
        strip = (max(0, l - limit - margin), t, l, b)
    else:
        strip = (r, t, min(width, r + limit + margin), b)
    if strip[2] <= strip[0] or strip[3] <= strip[1]:
        return 0
    inner = mask.crop(strip).getbbox()
    span = (strip[3] - strip[1]) if side in ('top', 'bottom') else (strip[2] - strip[0])
    if inner is None:
        return min(limit, span)
    if side == 'top':
        free = span - inner[3]
    elif side == 'bottom':
        free = inner[1]
    elif side == 'left':
        free = span - inner[2]
    else:
        free = inner[0]
    return max(0, min(limit, free - margin))


def pad_fit(mask, box, size, s):
    """fit_min이 실패한 컷: 실제 배경으로 최대한 넓힌 뒤 모자란 만큼 흰 여백 보충 계획.
    반환 (실제 잘라낼 box, (왼, 위, 오른, 아래) 보충 px) 또는 None."""
    width, height = size
    l, t, r, b = box
    margin = max(4, int(6 * s))
    pads = [0, 0, 0, 0]
    for axis in ('h', 'v'):
        span = (r - l) if axis == 'h' else (b - t)
        need = 500 - span
        if need <= 0:
            continue
        lo, hi = ('left', 'right') if axis == 'h' else ('top', 'bottom')
        cur = (l, t, r, b)
        reach_lo, reach_hi = clean_reach(mask, cur, lo, need, margin), clean_reach(mask, cur, hi, need, margin)
        ext_lo = min(reach_lo, need // 2)
        ext_hi = min(reach_hi, need - ext_lo)
        ext_lo = min(reach_lo, need - ext_hi)
        rest = need - ext_lo - ext_hi
        if axis == 'h':
            l, r = l - ext_lo, r + ext_hi
        else:
            t, b = t - ext_lo, b + ext_hi
        if rest:
            first, second = rest // 2, rest - rest // 2
            if max(first, second) > 500 * PAD_MAX_SHARE:
                return None
            if axis == 'h':
                pads[0], pads[2] = first, second
            else:
                pads[1], pads[3] = first, second
    real = (l, t, r, b)
    edges = []
    if pads[0]:
        edges.append((l, t, min(r, l + 3), b))
    if pads[2]:
        edges.append((max(l, r - 3), t, r, b))
    if pads[1]:
        edges.append((l, t, r, min(b, t + 3)))
    if pads[3]:
        edges.append((l, max(t, b - 3), r, b))
    for edge in edges:  # 보충하는 쪽 가장자리가 사진·글자에 닿아 있으면(배경이 아니면) 이음매가 생기므로 불가
        if mask.crop(edge).getbbox() is not None:
            return None
    if not white_extension(mask, tuple(box), real):
        return None
    final_w, final_h = r - l + pads[0] + pads[2], b - t + pads[1] + pads[3]
    if min(final_w, final_h) < 500 or max(final_w, final_h) / min(final_w, final_h) > 1.8:
        return None
    return real, tuple(pads)


def white_extension(mask, inner, outer):
    """outer로 넓힌 부분이 실제 배경(내용 2% 미만)인지. 이웃 사진을 끌어오지 않기 위한 검사."""
    total = sum(row_counts(mask, outer))
    own = sum(row_counts(mask, inner))
    area = (outer[2] - outer[0]) * (outer[3] - outer[1]) - (inner[2] - inner[0]) * (inner[3] - inner[1])
    if area <= 0:
        return True
    extra = total - own
    if extra > area * .002:
        return False  # 넓힌 부분에 글자·이웃 사진 등 내용이 있으면 불가
    # 넓힌 가장자리 줄에 글자가 걸치는지(전체 비율은 작아도 한 줄에 몰린 내용)도 확인
    for edge in ((outer[0], outer[1], outer[2], inner[1]), (outer[0], inner[3], outer[2], outer[3]),
                 (outer[0], inner[1], inner[0], inner[3]), (inner[2], inner[1], outer[2], inner[3])):
        if edge[2] > edge[0] and edge[3] > edge[1] and max(row_counts(mask, edge) or [0]) > max(3, (edge[2] - edge[0]) * .02):
            return False
    return True


def fit_ratio(mask, box, size):
    """비율 1.8 초과 컷은 실제 배경 화소로만 짧은 변을 넓힌다(가짜 여백·확대 없음). 불가능하면 None."""
    width, height = size
    l, t, r, b = box
    bw, bh = r - l, b - t
    if bh / bw > 1.8:
        need = int(bh / 1.8) + 1
        if need > width:
            return None
        nl = max(0, min(l - (need - bw) // 2, width - need))
        out = (nl, t, nl + need, b)
    elif bw / bh > 1.8:
        need = int(bw / 1.8) + 1
        if need > height:
            return None
        nt = max(0, min(t - (need - bh) // 2, height - need))
        out = (l, nt, r, nt + need)
    else:
        return box
    return out if white_extension(mask, box, out) else None


def segment(image):
    """원본 해상도에서
    ① 전체 폭 배경 여백(가로 띠)으로 블록을 나누고 ② 블록 안 세로 여백으로 나란한 사진을 나누고
    ③ 여백 없이 붙은 사진은 사이의 가는 흰 선에서 다시 나눈다.
    낮은 글자 띠는 색상명 후보로 모으고, 띠 안의 단어 덩어리(열)별로 위치를 기록한다.
    반환: (photos=[(box, (띠번호, 단어번호)|None)], labels=[(띠 box, [단어 box...])])"""
    width, height = image.size
    s = width / 1000
    solid = solid_background(image)
    bg = Image.new('RGB', image.size, background(image))
    mask = ImageChops.difference(image, bg).convert('L').point(lambda p: 255 if p > 24 else 0)
    bg.close()
    events, labels = [], []
    try:
        rows = row_counts(mask, (0, 0, width, height))
        for top, bottom in runs_of(rows, max(4, width * .01), int(10 * s)):
            whole = mask.crop((0, top, width, bottom)).getbbox()
            if not whole:
                continue
            if bottom - top < 120 * s:
                if 8 * s <= bottom - top and width * .03 <= whole[2] - whole[0] <= width * .85:
                    pad = int(12 * s)
                    band = (0, max(0, top - pad), width, min(height, bottom + pad))
                    words = [(max(0, a - pad), band[1], min(width, z + pad), band[3])
                             for a, z in runs_of(col_counts(mask, (0, top, width, bottom)), 0, int(15 * s))]
                    labels.append((band, words))
                    events.append(('label', len(labels) - 1))
                continue
            found = False
            for left, right in runs_of(col_counts(mask, (0, top, width, bottom)), 1, int(10 * s)):
                if right - left < width * .2:
                    continue
                for piece in split_seams(mask, (left, top, right, bottom), s):
                    inner = mask.crop(piece).getbbox()
                    if not inner:
                        continue
                    box = (piece[0] + inner[0], piece[1] + inner[1], piece[0] + inner[2], piece[1] + inner[3])
                    if box[3] - box[1] < 120 * s or photo_like(mask, box) < .22:
                        continue  # 글자·표·실측선 도식처럼 듬성한 영역
                    pad = int(8 * s)
                    padded = (max(piece[0], box[0] - pad), max(piece[1], box[1] - pad), min(piece[2], box[2] + pad), min(piece[3], box[3] + pad))
                    events.append(('photo', padded))
                    found = True
            if not found:
                events.append(('reset', None))  # 사진이 아닌 큰 영역(실측표 등)에서 색상명의 범위를 끊는다
        # 색상명 → 사진 연결: 사진마다 위·아래로 가장 가까운 글자 띠(사진 아닌 큰 영역은 넘지 않음)
        photos = []
        for i, (kind, value) in enumerate(events):
            if kind != 'photo':
                continue
            near = []
            for step in (-1, 1):
                j = i + step
                while 0 <= j < len(events) and events[j][0] != 'reset':
                    if events[j][0] == 'label':
                        band = labels[events[j][1]][0]
                        near.append((max(0, value[1] - band[3] if step < 0 else band[1] - value[3]), step, events[j][1]))
                        break
                    j += step
            near.sort()
            photos.append((value, near[0][2] if near else None, near[0][0] if near else None))
        out = []
        for box, label, gap in photos:
            fitted = fit_ratio(mask, box, image.size)
            if fitted is None:
                continue
            pads = None
            if min(fitted[2] - fitted[0], fitted[3] - fitted[1]) < 500:
                ratio_box = fitted
                fitted = fit_min(mask, ratio_box, image.size)  # 실제 배경으로만 넓힘(확대 없음)
                if fitted is not None and max(fitted[2] - fitted[0], fitted[3] - fitted[1]) / min(fitted[2] - fitted[0], fitted[3] - fitted[1]) > 1.8:
                    fitted = None
                if fitted is None and solid is not None:
                    plan = pad_fit(mask, ratio_box, image.size, s)  # 실제 흰 배경 + 모자란 만큼 같은 흰색 보충
                    if plan is not None:
                        fitted, pads = plan
                        if not any(pads):
                            pads = None  # 실제 흰 배경만으로 충분(한쪽으로 치우쳐 넓힘)
                if fitted is None:
                    continue
            key = None
            if label is not None:
                words = labels[label][1]
                if len(words) == 1:
                    key = (label, 0)
                else:  # 여러 색상명이 한 줄에 나란히 있으면 사진 바로 위(가로 위치가 겹치는) 단어만
                    overlaps = [max(0, min(fitted[2], w[2]) - max(fitted[0], w[0])) for w in words]
                    best = max(range(len(words)), key=lambda k: overlaps[k])
                    if overlaps[best] > 0 and sorted(overlaps)[-2:][0] < overlaps[best]:
                        key = (label, best)
            out.append([tuple(int(v) for v in fitted), key, pads and (solid, pads), gap])
        # 한 색상명(단어)은 가장 가까운 사진 하나에만: 위쪽 그림·배너가 아래 사진의 색상명을 같이 받지 않게
        best = {}
        for entry in out:
            if entry[1] is not None and (entry[1] not in best or entry[3] < best[entry[1]][3]):
                best[entry[1]] = entry
        for entry in out:
            if entry[1] is not None and best[entry[1]] is not entry:
                entry[1] = None
        return [(box, key, pad) for box, key, pad, _ in out], labels
    finally:
        mask.close()


def ocr_tiles(ctx, tiles, work):
    """Windows 기본 OCR로 글자 띠들을 한 번에 읽는다(상품당 PowerShell 1회). 윈도우가 아니거나 실패하면 빈 결과(→ 미매핑)."""
    if not tiles:
        return {}
    if os.name != 'nt':
        ctx.log('Windows가 아니어서 색상명 OCR을 건너뜁니다 → 사진은 수동 매핑', 'INFO')
        return {}
    if len(tiles) > MAX_OCR_TILES:
        ctx.log('색상명 후보 띠가 ' + str(len(tiles)) + '개로 많아 앞 ' + str(MAX_OCR_TILES) + '개만 OCR합니다(나머지 컷은 수동 매핑)', 'WARNING')
        tiles = tiles[:MAX_OCR_TILES]
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    items = []
    for i, tile in enumerate(tiles):
        ctx.checkpoint()
        factor = min(2.0, 2400 / tile.width, 3500 / max(1, tile.height))
        big = tile.resize((max(1, round(tile.width * factor)), max(1, round(tile.height * factor))), Image.Resampling.LANCZOS)
        path = work / ('label_' + str(i) + '.png')
        big.save(path)
        big.close()
        items.append({'index': i, 'path': str(path.resolve())})
    script = work / 'recognize.ps1'
    script.write_text(POWERSHELL_OCR, encoding='utf-8-sig')
    manifest = work / 'ocr_input.json'
    manifest.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script.resolve()), '-Manifest', str(manifest.resolve())]
    texts = {}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + min(120, 40 + len(items) * 2)
        try:
            while process.poll() is None:
                ctx.checkpoint()
                if time.monotonic() > deadline:
                    raise CrawlError('Windows OCR 시간 초과')
                time.sleep(.1)
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read().decode('utf-8-sig', errors='replace')
            if process.returncode:
                raise CrawlError('Windows OCR 실패: ' + stderr.read().decode('utf-8', errors='replace')[:300])
            result = json.loads(output)
            for item in result if isinstance(result, list) else [result]:
                texts[int(item['index'])] = str(item.get('text') or '')
                if item.get('error'):
                    ctx.log('OCR 오류(해당 띠 미매핑): ' + str(item['error'])[:200], 'WARNING')
        except (CrawlError, ValueError, OSError, KeyError) as exc:
            ctx.log(str(exc) + ' → 사진은 수동 매핑', 'WARNING')
            return {}
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
    return texts


def save_cut(ctx, image, box, seen, pad=None):
    try:
        if pad:
            color, (pl, pt, pr, pb) = pad
            piece = image.crop(tuple(box))
            canvas = Image.new('RGB', (piece.width + pl + pr, piece.height + pt + pb), color)
            canvas.paste(piece, (pl, pt))
            piece.close()
            try:
                path = ctx.save_image(canvas)
            finally:
                canvas.close()
        else:
            path = ctx.save_image(image, crop=box)
    except ValueError as exc:
        ctx.log('저장 규격 미달로 제외: ' + str(exc), 'INFO')
        return None
    if path.name in seen:
        return None
    seen.add(path.name)
    return path


def cuts_from(ctx, url, image, seen, tiles=None, split=False):
    """한 원본에서 개별 컷 목록 [(path, box, 글자 타일 번호|None)].
    갤러리 사진(split=False)은 통째로. 공식 상세 이미지(split=True)는 여백·가는 흰 선 경계로 나눠 2컷 이상이면 그 컷들,
    아니면 원본이 개별 컷 규격일 때만 통째. 색상명 후보 글자는 tiles에 모아 나중에 한 번에 OCR."""
    w, h = image.size
    whole_ok = min(w, h) >= 500 and max(w, h) / min(w, h) <= 1.8
    photos, labels = (segment(image) if split else ([], []))
    photos = [(box, key, pad) for box, key, pad in photos]
    if len(photos) < 2:
        if whole_ok:
            path = save_cut(ctx, image, None, seen)
            return [(path, None, None, None)] if path else []
        if split and not photos:
            ctx.log('여백 경계로 안전하게 나눌 수 있는 개별 컷이 없어 제외: ' + url, 'INFO')
        if not photos:
            return []
    keys = {}
    if tiles is not None:
        for key in sorted({key for _, key, _ in photos if key is not None}):
            keys[key] = len(tiles)
            label, word = key
            tiles.append((image.crop(tuple(labels[label][1][word])), url, labels[label][1][word]))
    out = []
    for box, key, pad in photos:
        ctx.checkpoint()
        path = save_cut(ctx, image, list(box), seen, pad)
        if path:
            out.append((path, list(box), keys.get(key), pad[1] if pad else None))
    return out


def release(cache, image):
    for key, value in cache.items():
        if value is image:
            cache[key] = True
    image.close()


def fit_min(mask, box, size):
    """500px 미만 변을 주변 '실제 배경'으로만 넓힌다(확대·가짜 여백 없음). 이웃 내용이 들어오면 None."""
    width, height = size
    l, t, r, b = box
    if r - l < 500:
        if width < 500:
            return None
        nl = max(0, min(l - (500 - (r - l)) // 2, width - 500))
        l, r = nl, nl + 500
    if b - t < 500:
        if height < 500:
            return None
        nt = max(0, min(t - (500 - (b - t)) // 2, height - 500))
        t, b = nt, nt + 500
    out = (l, t, r, b)
    if out == tuple(box):
        return out
    return out if white_extension(mask, tuple(box), out) else None





TRANSLIT = {'블랙': 'BLACK', '화이트': 'WHITE', '아이보리': 'IVORY', '카키': 'KHAKI', '네이비': 'NAVY', '베이지': 'BEIGE', '그레이': 'GRAY',
            '차콜': 'CHARCOAL', '브라운': 'BROWN', '레드': 'RED', '블루': 'BLUE', '그린': 'GREEN', '핑크': 'PINK', '퍼플': 'PURPLE',
            '옐로우': 'YELLOW', '오렌지': 'ORANGE', '민트': 'MINT', '카멜': 'CAMEL', '와인': 'WINE', '올리브': 'OLIVE', '크림': 'CREAM',
            '터키쉬블루': 'TURKISH BLUE', '소라': 'SKY BLUE', '오트밀': 'OATMEAL', '멜란지': 'MELANGE', '머스타드': 'MUSTARD'}
PREFIX = {'다크': 'DARK', '라이트': 'LIGHT', '딥': 'DEEP', '연': 'LIGHT', '진': 'DARK', '미디엄': 'MEDIUM', '페일': 'PALE'}


def english(name):
    """공식 한글 색상명 → 고정 영문 표기(다크그레이 → DARK GRAY, GREY 표기도 같이). 표에 없으면 ''."""
    name = re.sub(r'\s+', '', str(name or ''))
    if name in TRANSLIT:
        return TRANSLIT[name]
    for prefix, word in PREFIX.items():
        if name.startswith(prefix) and name[len(prefix):] in TRANSLIT:
            return word + ' ' + TRANSLIT[name[len(prefix):]]
    return ''


def match_color(recognized, colors):
    """OCR 글자를 공식 색상 옵션(정답지)과 대조. 코드·이름·고정 영문 표기 정확 일치 우선, 없으면 유사 일치(0.8 이상).
    정확히 한 색상만 남을 때만 그 색상, 아니면 (None, 사유)."""
    upper = recognized.upper().replace('GREY', 'GRAY')
    tokens = set(re.findall(r'[A-Z0-9]+', upper))
    words = set(re.findall(r'[가-힣]+', recognized))
    words |= {re.sub(r'(컬러|색상|색)$', '', w) for w in words}
    squashed = re.sub(r'\s+', '', recognized)
    exact, near = [], []
    for color in colors:
        name, code = color['color_name'].strip(), color['color_code'].strip().upper()
        eng = english(name)
        reasons = []
        if code and code in tokens:
            reasons.append('코드 ' + code)
        if name and (name in words or re.sub(r'\s+', '', name) in squashed or name.upper() in tokens):
            reasons.append('이름 ' + name)
        elif eng and all(part in tokens for part in eng.split()):
            reasons.append('영문 표기 ' + eng + '=' + name)
        if reasons:
            exact.append((color, ', '.join(reasons)))
            continue
        targets = [t for t in ([name.upper()] if re.fullmatch(r'[A-Z0-9 ]+', name.upper()) else []) + ([eng.replace(' ', '')] if eng else []) if len(t) >= 4]
        best = (0.0, '')
        joined_tokens = re.sub(r'[^A-Z0-9]', '', upper)
        for target in targets:
            for token in tokens | ({joined_tokens} if 3 <= len(joined_tokens) <= 20 else set()):
                if len(token) >= 3:
                    score = difflib.SequenceMatcher(None, token, target).ratio()
                    best = max(best, (score, token + '≈' + target))
        for word in words:
            if len(name) >= 2 and re.fullmatch(r'[가-힣]+', name) and len(word) >= 2:
                score = difflib.SequenceMatcher(None, word, name).ratio()
                best = max(best, (score, word + '≈' + name))
        if best[0] >= .8:
            near.append((color, best))
    if len(exact) == 1:
        return exact[0][0], exact[0][1]
    if exact:
        # 코드까지 맞는 색상이 하나면 그 색상(예: '다크그레이 97'이 '그레이'와도 이름이 겹치는 경우)
        coded = [e for e in exact if e[1].startswith('코드 ')]
        if len(coded) == 1:
            return coded[0][0], coded[0][1]
        # '다크그레이'가 맞으면 그 안에 들어 있는 '그레이'는 같이 맞은 것으로 보지 않는다(더 구체적인 이름 하나만 남을 때)
        def phrase(color):
            return english(color['color_name']) or re.sub(r'\s+', '', color['color_name']).upper()
        longest = max(exact, key=lambda e: len(phrase(e[0])))
        if all(e is longest or (phrase(e[0]) in phrase(longest[0]) and len(phrase(e[0])) < len(phrase(longest[0]))) for e in exact):
            return longest[0], longest[1] + ' (더 짧은 이름 ' + ', '.join(e[0]['color_name'] for e in exact if e is not longest) + '보다 구체적)'
        return None, '여러 색상 동시 일치'
    near.sort(key=lambda x: -x[1][0])
    if len(near) == 1 or (len(near) > 1 and near[0][1][0] - near[1][1][0] >= .1):
        color, (score, how) = near[0]
        return color, '유사 일치 ' + how + ' (' + format(score, '.2f') + ')'
    return None, ('유사 색상 여러 개' if near else '')


CROP_SKIP = re.compile(r'(?:^|/)size[^/]*\.(?:jpe?g|png|webp|gif)$|sizechart|size_?guide|/common/|notice|banner', re.I)


def load_image(ctx, url, cache, fetched):
    """공개 GET 원본 → RGB. 실패·애니메이션·형식 외는 None(해당 사진만 제외)."""
    if url in cache:
        return None if cache[url] is True else cache[url]
    result = inspect(ctx, url, fetched, MAX_IMAGE)
    image = None
    if result['status'] != 'accessible':
        ctx.log('이미지 공개 접근/해독 불가로 이 사진만 제외: ' + url + ' / ' + ', '.join(result.get('reasons') or []), 'WARNING')
    elif result.get('animated') or result.get('format') not in POLICY['formats']:
        ctx.log('정지 JPEG/PNG/WebP가 아니어서 제외: ' + url, 'INFO')
    else:
        try:
            with Image.open(io.BytesIO(result['data'])) as opened:
                if opened.width * opened.height > 60_000_000:
                    ctx.log('픽셀 수 초과로 제외: ' + url, 'INFO')
                else:
                    opened.load()
                    image = ImageOps.exif_transpose(opened).convert('RGB')
        except (OSError, ValueError) as exc:
            ctx.log('이미지 해독 실패로 제외(' + str(exc)[:60] + '): ' + url, 'INFO')
    fetched.pop((url, MAX_IMAGE), None)  # 원본 바이트는 컷 저장 뒤 필요 없음(메모리)
    cache[url] = image
    return image


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(CONFIG['input'], raw)
    if not match:
        ctx.fail_item('품번 형식 오류: ' + CONFIG['format'])
        raise CrawlError('품번 형식 오류')
    base, requested = match[1], (match.group(2) if match.re.groups >= 2 else None)
    ctx.log(CONFIG['brand'] + ' 코드 버전: ' + REVISION, 'INFO')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        ctx.fail_item('크롭 버전은 대표이미지 또는 추가이미지 선택이 필요합니다.')
        raise CrawlError('이미지 미선택')
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

        palette = palette_of([c for p in pages for c in p['colors']])
        if requested:
            chosen = [c for c in palette if c['color_code'] == requested]
            palette = chosen or palette

        def base_record(page):
            record = dict(product_code=base, product_name=page['name'], product_url=page['final'], **{'원본 상품명': page['raw_name']})
            if page['site_brand']:
                record['사이트 브랜드'] = page['site_brand']
            if ctx.enabled('category'):
                record['category'] = ' > '.join(dict.fromkeys(
                    t for t in (re.sub(r'\s+', ' ', li.get_text(' ', strip=True)) for li in page['doc'].select('.xans-product-headcategory li'))
                    if t and t.lower() not in ('현재 위치', '홈', 'home', '>') and t != page['raw_name']))
            collect_regular_price(ctx, record, page['doc'], 'cafe24')
            return record

        def page_price(page, color):
            values = set(color['prices'])
            if len(values) == 1:
                return values.pop()
            if len(values) > 1:
                return None
            node = page['doc'].select_one('#span_product_price_sale') or page['doc'].select_one('#span_product_price_text')
            value = amount(re.sub(r'[^\d]', '', node.get_text(' ', strip=True))) if node else None
            if value is None:
                meta = page['doc'].select_one('meta[property="product:sale_price:amount"]') or page['doc'].select_one('meta[property="product:price:amount"]')
                value = amount(meta.get('content')) if meta else None
            return value

        first = pages[0]
        common = base_record(first)
        if ctx.enabled('regular_price') and len(pages) > 1:
            merge_regular_prices(ctx, common, [base_record(p) for p in pages])
        in_palette = [(p, c) for p in pages for c in p['colors'] if {'color_name': c['color_name'], 'color_code': c['color_code']} in palette]
        if ctx.enabled('size'):
            common['size'] = common_value(ctx, [joined(c['sizes']) for _, c in in_palette], '사이즈')
        if ctx.enabled('price'):
            common['price'] = common_value(ctx, [page_price(p, c) for p, c in in_palette], '판매가')
        if ctx.enabled('detail_page'):
            common['detail_page'] = shared

        # 원본 사진 목록: [(url, 컷 분할 여부, 사전 매핑 색상, 근거)]
        sources, listed = [], set()

        def add(url, split, color=None, evidence=''):
            if url and url not in listed:
                listed.add(url)
                sources.append((url, split, color, evidence))

        def palette_color(color):
            entry = {'color_name': color['color_name'], 'color_code': color['color_code']}
            return entry if entry in palette else None

        for page in pages:
            ctx.checkpoint()
            ctx.stage(CONFIG['brand'] + ' 갤러리 확인: ' + page['final'])
            photos = [first_open(ctx, g, cache) for g in gallery_groups(ctx, page['doc'], page['final'], page['number'])]
            single = palette_color(page['colors'][0]) if len(page['colors']) == 1 else None
            links = linked_images(page['doc'], page['text'], page['final']) if len(page['colors']) > 1 else {}
            for color in page['colors']:
                mapped = palette_color(color)
                for url in links.get(color['original']) or links.get(color['color_name']) or []:
                    add(url, False, mapped, '공식 색상 옵션 연결 이미지(link_image): ' + color['original'] if mapped else '')
            for url in photos:
                add(url, False, single, ('공식 상품 페이지의 색상 옵션이 1개: ' + page['colors'][0]['label']) if single else '')
        for url in details[0]:
            if CROP_SKIP.search(unquote(urlsplit(url).path)):
                ctx.log('사이즈표·공통 이미지는 컷 후보에서 제외(상세 HTML에는 유지): ' + url, 'INFO')
                continue
            add(url, True)

        images, seen, pending, tiles = {}, set(), [], []
        work = Path(ctx.output_dir) / '_crocodile_crop_ocr' / str(ctx.task_id)
        for n, (url, split, mapped, evidence) in enumerate(sources, 1):
            ctx.checkpoint()
            ctx.stage('이미지 ' + str(n) + '/' + str(len(sources)) + ' 다운로드·컷 저장')
            image = load_image(ctx, url, images, cache)
            if image is None:
                continue
            for path, box, key, pads in cuts_from(ctx, url, image, seen, tiles if split else None, split=split):
                pending.append([path, mapped, evidence, url, box, key, pads])
            release(images, image)
        if not pending:
            raise CrawlError('규격(양변 500px·비율 1.8 이하)을 통과한 개별 상품 컷이 없습니다.')
        if tiles:
            ctx.stage('색상명 글자 OCR (' + str(len(tiles)) + '개, 1회 실행)')
            texts = ocr_tiles(ctx, [t for t, _, _ in tiles], work)
            decided = {}
            for key, (tile, source, ybox) in enumerate(tiles):
                tile.close()
                if key not in texts:
                    continue
                color, why = match_color(texts[key], palette)
                if color:
                    decided[key] = (color, 'Windows OCR "' + re.sub(r'\s+', ' ', texts[key]).strip()[:60] + '" → ' + why +
                                    ' / 사진에 붙은 글자 y=' + str(ybox[1]) + '~' + str(ybox[3]) + ' / ' + source)
                else:
                    ctx.log('색상명 OCR "' + texts[key].strip()[:40] + '" ' + (why or '공식 색상과 불일치') + ' → 수동 매핑', 'INFO')
            for entry in pending:
                if entry[1] is None and entry[5] in decided:
                    entry[1], entry[2] = decided[entry[5]]
        for path, mapped, evidence, source, box, _, pads in pending:
            ctx.checkpoint()
            row = dict(common)
            row['크롭 원본 이미지'] = source
            row['크롭 좌표'] = json.dumps(box) if box else ''
            row['흰 여백 보충'] = ('왼 %d·위 %d·오른 %d·아래 %dpx (원본 배경 흰색, 상품 화소 그대로)' % pads) if pads else ''
            row['OCR 근거'] = evidence
            ctx.emit_local_image(row, palette, path, mapped_color=mapped, mapping_evidence=evidence, shared_detail_html=shared)
        emitted.add('local')
        ctx.log(base + ': 개별 컷 ' + str(len(pending)) + '장 전달 (사전 매핑 ' + str(sum(1 for p in pending if p[1])) +
                '장, 색상 ' + str(len(palette)) + '개). 색상 매핑하기에서 분류하세요.', 'SUCCESS')
        finish_failures(ctx, failures, emitted, raw)
    except Exception as exc:
        if not failures:
            ctx.fail_item(str(exc))
        raise
