PORTAL_API_VERSION = 1
"""밀레 (공식 홈페이지) — 자동 색상 매핑 크롤러

검증 범위: 사용자 단독 크롤러(millet_crawler.py)에서 운영 확인된 URL·선택자·응답 키를 Crawler Hub SDK 규약으로 이식. 이 코드를 작성한 환경은 해당 사이트 접속이 차단되어 실사이트 실행은 미검증이며, SDK 계약·선택 항목 생략·표 재구성은 오프라인 fixture 테스트(tests/test_oberon_brands.py)로만 확인함.
"""
REVISION = '2026-11-18-oberon-2'

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
# ───────────────────────── 밀레 공식몰 (자동 매핑) ─────────────────────────
# 색상마다 상품번호(페이지)가 따로 있고 상세 품번 = 품번+색상코드(MTSSB970 + 16).
# 상세의 컬러 칩(.color-items)이 같은 품번의 다른 색상 페이지를 공식으로 연결 → 색상 페이지 사진 = 그 색상.
SITE = 'https://www.millet.co.kr'
HOSTS = ('www.millet.co.kr', 'millet.co.kr')
ALIASES = ['밀레', 'MILLET']
SKIP = re.compile(r'/data/editor/|/static/imgs/|no_image|noimg|blank|btn_|icon|ico_|logo|banner|\.svg', re.I)


def search(ctx, key):
    data = {'keyword': key, 'keywordRe': '', 'cateIdx': '0', 'brandIdx': '0', 'storeNo': '0', 'page': '1', 'psort': 'new'}
    body, _, ctype = fetch(ctx, SITE + '/front/product/product_list.ajax', HOSTS, data=urlencode(data).encode(), method='POST',
                           headers={'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                                    'Referer': SITE + '/front/product/search?' + urlencode({'keyword': key, 'psort': 'new'})})
    out = []
    for a in soup(decode(body, ctype)).select('a[href*="/front/product/"]'):
        found = re.search(r'/front/product/(\d+)', a.get('href', ''))
        box = a.find_parent('li') or a.find_parent('div') or a
        blob = (box.get_text(' ', strip=True) + ' ' + ' '.join(i.get('alt', '') for i in box.select('img'))).upper().replace('-', '')
        if found and key in blob and found[1] not in out:
            out.append(found[1])
    return out


def notice_rows(doc):
    """공식 상품정보고시 표: '색상' 항목이 있는 표의 항목-값."""
    for table in doc.select('table'):
        rows = []
        for tr in table.select('tr'):
            th, td = tr.select_one('th'), tr.select_one('td')
            if th and td and th.get_text(strip=True):
                rows.append((th.get_text(' ', strip=True), re.sub(r'\s+', ' ', td.get_text(' ', strip=True))))
        if any(k == '색상' for k, _ in rows):
            return rows
    return []


def color_of(code, field):
    """상세 품번 끝과 고시 '색상: WHITE(10), BLACK(16)'의 코드가 일치하는 항목만 (긴 코드 우선)."""
    pairs = re.findall(r'([^,()]+?)\s*\(\s*([A-Za-z0-9]+)\s*\)', field or '')
    for name, cd in sorted(pairs, key=lambda p: -len(p[1])):
        if code.upper().endswith(cd.upper()):
            return name.strip(), cd.upper()
    return '', ''


def crawl(ctx):
    raw_input = re.sub(r'[\s\-]+', '', str(ctx.product_code)).upper()
    if not re.fullmatch(r'[A-Z0-9]{5,20}', raw_input):
        ctx.fail_item('품번 형식 오류: 기본 품번(예: MTSSB970) 또는 품번+색상코드(예: MTSSB97016)')
        raise CrawlError('품번 형식 오류')
    ctx.log('밀레 코드 버전: ' + REVISION, 'INFO')
    cache, failures, emitted = {}, [], set()
    try:
        ctx.stage('밀레 공식 검색: ' + raw_input)
        queue, done = search(ctx, raw_input), set()
        for cut in (2, 1, 3):  # 품번+색상코드 입력: 목록 카드에는 기본 품번만 보일 수 있음 → 상세에서 정확히 재대조
            if queue or len(raw_input) - cut < 5:
                break
            queue = search(ctx, raw_input[:-cut])
        if not queue:
            raise CrawlError('공식 검색 결과에 품번이 들어간 상품이 없습니다: ' + raw_input)
        while queue:
            ctx.checkpoint()
            pid = queue.pop(0)
            if pid in done:
                continue
            done.add(pid)
            url = SITE + '/front/product/' + pid
            try:
                text, final = get_html(ctx, url, HOSTS)
                doc = soup(text)
                spans = [s.get_text(strip=True) for s in doc.select('.item-detail-info .cate span')]
                full_code = re.sub(r'[\s\-]+', '', spans[1] if len(spans) > 1 else '').upper()
                rows = notice_rows(doc)
                color_name, cc = color_of(full_code, dict(rows).get('색상', ''))
                style = full_code[:-len(cc)] if cc else full_code
                same = bool(full_code) and (raw_input in (style, full_code) or (raw_input.startswith(style) and len(raw_input) - len(style) <= 3)
                                             or (full_code.startswith(raw_input) and re.fullmatch(r'[A-Z0-9]{1,3}', full_code[len(raw_input):]) is not None))
                if not same:
                    ctx.log('품번 불일치 제외: ' + (full_code or '?') + ' / ' + final, 'INFO')
                    continue
                for a in doc.select('.color-items a[href]'):
                    sib = re.search(r'/front/product/(\d+)', a.get('href', ''))
                    if sib and sib[1] not in done and sib[1] not in queue:
                        queue.append(sib[1])  # 같은 품번의 다른 색상(공식 컬러 칩)
                if not cc:
                    if raw_input != full_code and not full_code.startswith(raw_input):
                        continue
                    raise CrawlError('상세 품번(' + full_code + ')과 고시 색상 코드가 맞지 않아 색상을 확정할 수 없습니다(추측하지 않음)')
                if raw_input not in (style, full_code):
                    continue  # 품번+색상코드 입력이면 그 색상만
                color = color_entry(color_name, cc)
                if color['label'] in emitted:
                    continue
                node = doc.select_one('.item-detail-info-name .name, h3.name')
                full = node.get_text(' ', strip=True) if node else ''
                if not full:
                    og = doc.select_one('meta[property="og:title"]')
                    full = (og.get('content') if og else '') or ''
                record = dict(product_code=style, product_name=clean_name(full, ALIASES, [style, full_code]), product_url=final,
                              color=color['label'], color_name=color['color_name'], color_code=color['color_code'], **{'원본 상품명': full})
                if ctx.enabled('size'):
                    record['size'] = joined(inp.get('value', '') for inp in doc.select('.size-choice input[name="size"]'))
                box = doc.select_one('.item-detail-price')
                if ctx.enabled('price'):
                    node = box.select_one('.price-info .price, .price') if box else None
                    record['price'] = amount(re.sub(r'[^\d]', '', node.get_text())) if node and re.search(r'\d', node.get_text()) else None
                if ctx.enabled('regular_price'):
                    ctx.checkpoint()
                    node = box.select_one('del') if box else None
                    record['regular_price'] = amount(re.sub(r'[^\d]', '', node.get_text())) if node and re.search(r'\d', node.get_text()) else None
                    if record['regular_price'] is None:
                        ctx.log('독립 정상가(취소선 del) 없음/0/형식 오류: 빈칸, 나머지 수집 계속', 'WARNING')
                photos = []
                for img in doc.select('.item-img-thumb img'):
                    src = absolute(final, img_src(img)).split('?')[0]
                    if src and src not in photos and not SKIP.search(src):
                        photos.append(src)
                body = []
                cont = doc.select_one('.item-datail-cont') or doc.select_one('.item-detail-cont')
                if cont is not None:
                    for img in cont.select('img'):
                        if img.find_parent(class_=re.compile(r'swiper-slide|prd-cont')):
                            continue  # 추천상품 슬라이드
                        src = absolute(final, img_src(img)).split('?')[0]
                        if src and src not in body and src not in photos and not SKIP.search(src):
                            body.append(src)
                main = ''
                if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                    accepted = screen_shots(ctx, photos, cache)
                    main, extras = auto_slots(ctx, accepted, cache, '색상별 상품 페이지(' + full_code + ')의 공식 .item-img-thumb') if accepted else ('', [])
                    apply_images(ctx, record, main, extras)
                if ctx.enabled('detail_page'):
                    record['detail_page'] = build_detail(ctx, detail_images(ctx, [u for u in photos if u != main] + body, cache), notice_table(rows))
                ctx.emit_result(record)
                emitted.add(color['label'])
            except CrawlError as exc:
                failures.append(url + ': ' + str(exc))
                ctx.fail_item(url + ': ' + str(exc))
        finish_failures(ctx, failures, emitted, raw_input)
    except Exception as exc:
        if not failures:
            ctx.fail_item(str(exc))
        raise
