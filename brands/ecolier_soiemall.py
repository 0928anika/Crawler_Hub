PORTAL_API_VERSION = 1
"""에꼴리에 (소이몰) — 혼합 색상 매핑 크롤러

검증 범위: 사용자 단독 크롤러(ecolier_crawler_1.py)에서 운영 확인된 URL·선택자·응답 키를 Crawler Hub SDK 규약으로 이식. 이 코드를 작성한 환경은 해당 사이트 접속이 차단되어 실사이트 실행은 미검증이며, SDK 계약·선택 항목 생략·표 재구성은 오프라인 fixture 테스트(tests/test_oberon_brands.py)로만 확인함.
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

# ───────────────────────── 에꼴리에 · 소이몰 (고도몰, 혼합 매핑) ─────────────────────────
# 소이몰은 소이·틴톡·에꼴리에를 함께 판매 → 검색 목록의 공식 브랜드 표기가 있으면 에꼴리에만(사용자 지정 판매 범위).
# 색상 옵션별 사진 연결 정보가 없음 → 색상 1개 상품은 자동, 여러 색상 상품의 갤러리는 수동 색상 매핑.
SITE = 'https://www.soiemall.co.kr'
HOSTS = ('www.soiemall.co.kr', 'soiemall.co.kr')
BRAND = '에꼴리에'
ALIASES = ['에꼴리에', 'ECOLIER']
KIND_ORDER = ('magnify', 'detail', 'main', 'add1', 'list')


def split_list(value):
    return [x.strip() for x in re.split(r'[,/]', value or '') if x.strip() and x.strip() not in ('-', '상세참조', '상세페이지 참조', '상세설명참조')]


def search(ctx, code):
    hits, page = [], 1
    while page <= 20:
        text, final = get_html(ctx, SITE + '/goods/goods_search.php?' + urlencode({'keyword': code, 'page': page}), HOSTS)
        doc = soup(text)
        items = doc.select('.goods_list_cont .item_cont') or doc.select('.item_cont')
        new = 0
        for item in items:
            anchor = item.select_one('a[href*="goodsNo="]')
            match = re.search(r'goodsNo=(\d+)', anchor.get('href', '')) if anchor else None
            if not match or any(h[0] == match[1] for h in hits):
                continue
            name = item.select_one('.item_name')
            brand = item.select_one('.item_brand')
            hits.append((match[1], name.get_text(' ', strip=True) if name else '', brand.get_text(' ', strip=True) if brand else ''))
            new += 1
        if not new:
            break
        page += 1
    return hits


def product(ctx, number):
    url = SITE + '/goods/goods_view.php?goodsNo=' + number
    text, final = get_html(ctx, url, HOSTS)
    doc = soup(text)
    heading = doc.select_one('.item_detail_tit h3')
    og = doc.select_one('meta[property="og:title"]')
    full = (heading.get_text(' ', strip=True) if heading else '') or (og.get('content', '') if og else '')
    model = ''
    for dl in doc.select('.item_detail_list dl'):
        dt, dd = dl.select_one('dt'), dl.select_one('dd')
        if dt and dd and '모델' in dt.get_text():
            model = dd.get_text(' ', strip=True)
    info = []
    for tr in doc.select('.detail_info_box tr'):
        cells = tr.select('th, td')
        for i in range(0, len(cells) - 1, 2):
            key, value = cells[i].get_text(' ', strip=True), re.sub(r'\s+', ' ', cells[i + 1].get_text(' ', strip=True))
            if key and key not in dict(info):
                info.append((key, value))
    return dict(url=final if 'goodsNo=' + number in final else url, number=number, doc=doc, full=full, model=model, info=info)


def colors_of(page):
    doc, info = page['doc'], dict(page['info'])
    select = doc.select_one('select[name="optionNo_0"]')
    values, label = [], ''
    if select:
        first = select.select_one('option')
        label = first.get_text(' ', strip=True) if first else ''
        values = [o.get('value').strip() for o in select.select('option') if (o.get('value') or '').strip()]
    if values and re.search(r'컬러|색상|COLOR', label, re.I):
        names = list(dict.fromkeys(values))
    else:
        names = split_list(info.get('색상', ''))
    return [color_entry(*split_color(n)) for n in names]


def gallery(page):
    by_kind = {}
    for img in page['doc'].select('img'):
        for key in ('data-original', 'src', 'data-src'):
            url = absolute(page['url'], img.get(key) or '')
            match = re.search(r'/goods/' + page['number'] + r'/image/([a-z0-9]+)/', url)
            if match and '/thumb/' not in url:
                by_kind.setdefault(match[1], [])
                if url not in by_kind[match[1]]:
                    by_kind[match[1]].append(url)
    for kind in KIND_ORDER:
        if by_kind.get(kind):
            return by_kind[kind]  # 같은 사진의 크기별 사본이 섞이지 않게 가장 큰 종류 하나만 사용
    return []


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{3,20}', raw):
        ctx.fail_item('품번 형식 오류: 기본 품번(예: 25D0901)')
        raise CrawlError('품번 형식 오류')
    ctx.log('에꼴리에 소이몰 코드 버전: ' + REVISION, 'INFO')
    ctx.allow_automatic_results = True
    wants = ctx.enabled('main_image') or ctx.enabled('additional_images')
    cache, failures, emitted, pages = {}, [], set(), []
    try:
        ctx.stage('소이몰 검색: ' + raw)
        hits = search(ctx, raw)
        for number, name, brand in hits:
            if brand and BRAND not in brand:
                ctx.log('다른 브랜드 상품 제외(' + brand + '): ' + name, 'INFO')
                continue
            if not bounded(raw, name):
                continue
            try:
                page = product(ctx, number)
            except CrawlError as exc:
                failures.append(number + ': ' + str(exc))
                ctx.fail_item(number + ': ' + str(exc))
                continue
            if bounded(raw, page['model']) or bounded(raw, page['full']):
                pages.append(page)
        if not pages and not failures:
            for number, name, brand in hits[:5]:
                if brand and BRAND not in brand:
                    continue
                page = product(ctx, number)
                if bounded(raw, page['model']) or bounded(raw, page['full']):
                    pages.append(page)
        if not pages:
            if failures:
                raise RuntimeError('상품 링크 수집 미완료: ' + ' / '.join(failures))
            raise CrawlError('정확히 일치하는 에꼴리에 상품을 찾지 못했습니다: ' + raw)
        shared = ''
        if ctx.enabled('detail_page'):
            images = []
            for page in pages:
                box = page['doc'].select_one('.detail_explain_box') or page['doc'].select_one('#detail')
                for img in box.select('img') if box else []:
                    url = absolute(page['url'], img_src(img))
                    if url and url not in images and not re.search(r'banner|top1|bottom1|logo|icon|delivery|배송', url, re.I):
                        images.append(url)
            shared = build_detail(ctx, detail_images(ctx, images, cache), notice_table(pages[0]['info']))

        def record_of(page):
            name = clean_name(page['full'], ALIASES, [raw])
            record = dict(product_code=raw, product_name=name, product_url=page['url'], **{'원본 상품명': page['full']})
            info = dict(page['info'])
            if ctx.enabled('size'):
                record['size'] = joined(split_list(info.get('치수', '')))
                if not record['size']:
                    ctx.log('상품필수정보 치수 없음: 사이즈 빈칸', 'WARNING')
            if ctx.enabled('price'):
                node = page['doc'].select_one('input[name="set_goods_price"]')
                record['price'] = amount(node.get('value') if node else None)
                if record['price'] is None:
                    ctx.log('공식 판매가 확인 불가: 빈칸', 'WARNING')
            if ctx.enabled('regular_price'):
                ctx.checkpoint()
                node = page['doc'].select_one('input[name="set_goods_fixedPrice"]')
                record['regular_price'] = amount(node.get('value') if node else None)
                if record['regular_price'] is None:
                    ctx.log('독립 정상가(set_goods_fixedPrice) 없음/0/형식 오류: 빈칸, 나머지 수집 계속', 'WARNING')
            if ctx.enabled('category'):
                record['category'] = ' > '.join(t.get_text(' ', strip=True) for t in page['doc'].select('.location_wrap .location_tit') if t.get_text(strip=True))
            if ctx.enabled('detail_page'):
                record['detail_page'] = shared
            return record

        manual, palette = [], []
        for page in pages:
            ctx.checkpoint()
            colors = colors_of(page)
            if not colors:
                message = page['url'] + ': 공식 색상 후보 없음'
                failures.append(message)
                ctx.fail_item(message)
                continue
            photos = gallery(page) if wants else []
            if len(colors) == 1 or not wants:
                for color in colors:
                    if color['label'] in emitted:
                        continue
                    record = record_of(page)
                    record.update(color=color['label'], color_name=color['color_name'], color_code=color['color_code'])
                    if wants:
                        accepted = screen_shots(ctx, photos, cache)
                        main, extras = auto_slots(ctx, accepted, cache, '공식 상품 페이지의 색상 옵션 1개: ' + color['label']) if accepted else ('', [])
                        apply_images(ctx, record, main, extras)
                    ctx.emit_result(record)
                    emitted.add(color['label'])
                continue
            palette.extend(c for c in colors if c not in palette)
            manual.extend((u, page) for u in screen_shots(ctx, photos, cache))
        if manual:
            ctx.stage('수동 색상 매핑 후보 전송')
            common = record_of(pages[0])
            for key in ('price', 'regular_price'):
                if ctx.enabled(key):
                    values = [record_of(p).get(key) for p in pages]
                    common[key] = values[0] if all(v == values[0] for v in values) else None
            for url, page in manual:
                ctx.emit_unmapped(dict(common), palette_of(palette), url, shared_detail_html=shared)
            emitted.add('manual')
        finish_failures(ctx, failures, emitted, raw)
    except Exception as exc:
        if not failures:
            ctx.fail_item(str(exc))
        raise
