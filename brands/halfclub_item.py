PORTAL_API_VERSION = 1
"""하프클럽(halfclub.com) 품번 → 수동 색상 매핑(02 수동색상매핑 / URL 방식). 브랜드 공통.

사용자 단독 하프클럽 크롤러(2026-11-20 전달)의 구조를 허브 SDK로 옮김:
- 검색: hapix.halfclub.com/searches/prdList (siteCd=1) → 실패 시 cf-hapi.halfclub.com(siteCd=2, 베네통 하프클럽 어댑터 확인 경로).
  상품코드(prdCd '_' 앞) 또는 상품명 속 품번이 '정확히' 같은 상품만. 숫자 상품번호·상품 URL 직접 입력 지원.
- 상세: /product/{번호} 의 script#pdp-data 또는 __NUXT_DATA__(productDetailData-{번호}).
- 색상·사이즈: optionItem(색상/사이즈 stockNo 연결). 색상 옵션이 없으면 임의 색상을 만들지 않고 실패.
- 사진: productImage.basicExtNm·addNExtNm(cdn2.halfclub.com 원본). 갤러리는 색상별 연결이 없어 사람 매핑.
- 상세: 상품 본문(prdDescContClob) 이미지(행사·공지·배너 경로 제외) + 상품정보제공고시(notiItemMap) 2열 표.
- 가격: 판매가 = 화면 표시가(price, 없으면 selPrc), 정상가 = 독립 normPrc만(없으면 빈칸).
같은 품번이 여러 판매자에 있으면 사진은 모두 후보로, 판매가·정상가는 같을 때만 채운다.
"""

import html
import http.client
import io
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

from crawler_rules import check_public_image

REVISION = '2026-11-20-halfclub-item-1'
BASE = 'https://www.halfclub.com'
CDN = 'https://cdn2.halfclub.com/'
SEARCHES = (('https://hapix.halfclub.com/searches/prdList/', '1'), ('https://cf-hapi.halfclub.com/searches/prdList/', '2'))
MAX_RESULTS = 300
DETAIL_SKIP = re.compile(r'/styleid_img/event/|/styleid_public/info/|/event/|/notice|banner|/common/|icon|logo|btn_|blank|noimg|\.svg', re.I)
POLICY = dict(checked_on='2026-11-10', source='https://marketplace.coupang.com/mba/listing', min_side=500, max_bytes=10_000_000,
              formats=['JPEG', 'PNG', 'WEBP'], max_aspect=1.8)
WRAP = ('Reactive', 'ShallowReactive', 'Ref', 'ShallowRef', 'NuxtError', 'Null')


class CollectionError(RuntimeError):
    """수집 실패(통신·형식·후보 없음)."""


class MissingResource(CollectionError):
    """HTTP 404."""


def public_opener(ctx, deadline):
    """요청 단위 IPv4 우선 연결(전역 소켓 설정 변경 없음). 베네통 하프클럽 어댑터와 같은 방식."""
    def connect(address, timeout, source_address=None, **unused):
        host, port = address
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses.sort(key=lambda item: item[0] != socket.AF_INET)
        last_error = None
        for family, socktype, protocol, _, target in addresses:
            ctx.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 시간 제한 초과')
            connection = socket.socket(family, socktype, protocol)
            try:
                connection.settimeout(min(3, remaining))
                if source_address:
                    connection.bind(source_address)
                connection.connect(target)
                connection.settimeout(min(5, max(.01, deadline - time.monotonic())))
                return connection
            except OSError as exc:
                connection.close()
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError('공개 호스트 주소 없음: ' + host)

    def factory(tls):
        def create(host, **kwargs):
            cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
            connection = cls(host, **kwargs)
            connection._create_connection = connect
            return connection
        return create

    class HTTP(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(factory(False), request)

    class HTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(factory(True), request, context=self._context)

    return urllib.request.build_opener(HTTP(), HTTPS())


def fetch(ctx, url, limit=6_000_000):
    """익명 GET(쿠키·로그인·Referer 없음), 20초·용량 제한, 일시 오류 1회 재시도."""
    deadline = time.monotonic() + 20
    opener = public_opener(ctx, deadline)
    for attempt in range(2):
        ctx.checkpoint()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 20초 제한 초과')
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'})
            with opener.open(request, timeout=min(5, remaining)) as response:
                chunks, size = [], 0
                while True:
                    ctx.checkpoint()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('공개 응답 전체 20초 제한 초과')
                    block = response.read1(min(65536, limit + 1 - size))
                    if not block:
                        break
                    chunks.append(block)
                    size += len(block)
                    if size > limit:
                        raise CollectionError('응답 크기 초과: ' + url)
            return b''.join(chunks)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise MissingResource('HTTP 404: ' + url) from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise CollectionError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt:
                raise CollectionError('통신 실패: %s / %s' % (url, exc)) from exc
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(.1)
    raise CollectionError('요청 실패: ' + url)


def key(value):
    return re.sub(r'[\s\-_]', '', str(value or '')).upper()


def token_in(code, text):
    return re.search(r'(?<![A-Z0-9])' + re.escape(code) + r'(?![A-Z0-9])', str(text or '').upper()) is not None


def direct_number(raw):
    match = re.fullmatch(r'\s*https?://(?:www\.|m\.)?halfclub\.com/product/(\d+)(?:[/?#].*)?\s*', raw, re.I) or \
        re.fullmatch(r'\s*(\d{6,12})\s*', raw)
    return match[1] if match else ''


def search(ctx, code):
    """정확한 품번 상품번호 목록. 첫 검색 경로 실패 시 두 번째 경로."""
    errors = []
    for endpoint, site in SEARCHES:
        try:
            return search_one(ctx, code, endpoint, site)
        except (CollectionError, ValueError, KeyError, TypeError) as exc:
            errors.append(endpoint + ': ' + str(exc))
            ctx.log('하프클럽 검색 경로 실패, 다음 경로 시도: ' + errors[-1], 'WARNING')
    raise CollectionError('하프클럽 공개 검색 실패: ' + ' / '.join(errors))


def search_one(ctx, code, endpoint, site):
    numbers, offset = [], 0
    while True:
        ctx.checkpoint()
        url = endpoint + '?' + urllib.parse.urlencode(dict(keyword=code, siteCd=site, device='pc', limit=str(offset) + ',100', sortSeq=12))
        result = json.loads(fetch(ctx, url).decode('utf-8-sig'))['data']['result']
        if result.get('timed_out'):
            raise CollectionError('공개 검색 시간 초과')
        hits = result['hits']
        total = hits.get('total', 0)
        if isinstance(total, dict):
            total = total.get('value', 0)
        entries = hits.get('hits') or []
        for hit in entries:
            item = hit.get('_source') or {}
            product_code = key(str(item.get('prdCd') or '').split('_')[0])
            if product_code != code and not token_in(code, item.get('prdNm') or item.get('prdSearchKeyword') or ''):
                continue
            number = str(item.get('prdNo') or hit.get('_id') or '')
            if number.isdigit() and number not in numbers:
                numbers.append(number)
        offset += len(entries)
        if not entries or offset >= int(total or 0) or offset >= MAX_RESULTS:
            return numbers


def nuxt_data(text):
    match = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    if not match:
        return {}
    array = json.loads(match.group(1))
    cache = {}

    def walk(index, depth=0):
        if depth > 200 or not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(array):
            return None
        if index in cache:
            return cache[index]
        value = array[index]
        if isinstance(value, list):
            if value and isinstance(value[0], str) and value[0] in WRAP + ('EmptyRef', 'EmptyShallowRef', 'Date', 'Set', 'Map'):
                tag = value[0]
                if tag in ('EmptyRef', 'EmptyShallowRef'):
                    return None
                if tag == 'Date':
                    return value[1] if len(value) > 1 else None
                if tag == 'Set':
                    out = []
                    cache[index] = out
                    out.extend(walk(x, depth + 1) for x in value[1:])
                    return out
                if tag == 'Map':
                    out = {}
                    cache[index] = out
                    for k in range(1, len(value) - 1, 2):
                        out[str(walk(value[k], depth + 1))] = walk(value[k + 1], depth + 1)
                    return out
                cache[index] = walk(value[1], depth + 1) if len(value) > 1 else None
                return cache[index]
            out = []
            cache[index] = out
            out.extend(walk(x, depth + 1) for x in value)
            return out
        if isinstance(value, dict):
            out = {}
            cache[index] = out
            for k, x in value.items():
                out[k] = walk(x, depth + 1)
            return out
        return value

    return walk(0) or {}


def product_page(ctx, number):
    """(상품정보, 가격정보, 최종 URL). pdp-data → __NUXT_DATA__ 순."""
    url = BASE + '/product/' + number
    text = fetch(ctx, url).decode('utf-8-sig', 'replace')
    doc = BeautifulSoup(text, 'html.parser')
    node = doc.find('script', id='pdp-data')
    if node is not None:
        data = json.loads(node.get_text())
        info = data.get('productInfo') or {}
        prices = data.get('prductPriceInfo') or data.get('productPriceInfo') or info.get('productPrice') or {}
    else:
        nuxt = nuxt_data(text)
        store = nuxt.get('data') or {}
        info = store.get('productDetailData-' + number) or \
            (((nuxt.get('pinia') or {}).get('meta') or {}).get('prdDetailData') or {}).get('data') or {}
        prices = store.get('productPrice-' + number) or info.get('productPrice') or {}
    if not info:
        raise CollectionError('공개 상품 데이터 없음(페이지 형식 변경 가능): ' + url)
    if str(info.get('prdNo') or number) != number:
        raise CollectionError('상품 번호 불일치: ' + url)
    return info, prices or {}, url


def asset(raw):
    value = str(raw or '').strip()
    if not value:
        return ''
    url = urllib.parse.urljoin(CDN, value if value.startswith(('http', '//')) else value.lstrip('/'))
    if url.startswith('//'):
        url = 'https:' + url
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        return ''
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, urllib.parse.quote(urllib.parse.unquote(parts.path), safe='/%:@&=+$,~!*\'()-._'), '', ''))


def options(info, include_size):
    """[{'color_name','color_code', 'size'}] + 사이즈 목록. 색상 옵션 없으면 실패."""
    items = info.get('optionItem') or []
    color = next((k for k, item in enumerate(items) if re.search(r'색상|컬러|color', item.get('optItemNm') or '', re.I)), None)
    size = next((k for k, item in enumerate(items) if k != color and re.search(r'사이즈|size|치수', item.get('optItemNm') or '', re.I)), None)
    if color is None:
        raise CollectionError('공식 색상 옵션이 없어 색상 후보를 만들 수 없습니다(임의 색상 생성 안 함)')
    sizes_by_stock = {}
    if size is not None:
        for value in items[size].get('optValueList') or []:
            name = (value.get('optValueNm') or '').strip()
            if name:
                sizes_by_stock.setdefault(value.get('stockNo'), []).append(name)
    palette, all_sizes = [], []
    for value in items[color].get('optValueList') or []:
        name = (value.get('optValueNm') or '').strip()
        if not name:
            continue
        entry = next((c for c in palette if c['color_name'] == name), None)
        if entry is None:
            entry = dict(color_name=name, color_code='')
            palette.append(entry)
        for s in sizes_by_stock.get(value.get('stockNo'), []):
            if s not in all_sizes:
                all_sizes.append(s)
            if include_size:
                have = [x for x in entry.get('size', '').split(',') if x]
                if s not in have:
                    entry['size'] = ','.join(have + [s])
    if not palette:
        raise CollectionError('공식 색상 후보 없음')
    return palette, all_sizes


def positive(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def gallery_image(ctx, url, cache):
    """공개 접근·실제 형식·크기·비율(쿠팡 사전 필터) 통과 여부."""
    if url not in cache:
        cache[url] = check_public_image(url, timeout=10, retries=1, checkpoint=ctx.checkpoint)
    result = cache[url]
    if result['status'] != 'accessible':
        ctx.log('사진 제외(공개 접근 불가): ' + url + ' / ' + ', '.join(result['reasons']), 'WARNING')
        return False
    try:
        with Image.open(io.BytesIO(result['data'])) as image:
            width, height = image.size
            fmt = image.format
            animated = getattr(image, 'is_animated', False)
    except (UnidentifiedImageError, OSError, ValueError):
        ctx.log('사진 제외(해독 실패): ' + url, 'WARNING')
        return False
    reasons = []
    if fmt not in POLICY['formats']:
        reasons.append('형식 ' + str(fmt))
    if animated:
        reasons.append('움직이는 이미지')
    if min(width, height) < POLICY['min_side']:
        reasons.append('%dx%d 500px 미만' % (width, height))
    if max(width, height) / max(1, min(width, height)) > POLICY['max_aspect']:
        reasons.append('%dx%d 통짜·배너 의심 비율' % (width, height))
    if reasons:
        ctx.log('사진 제외: ' + url + ' / ' + ', '.join(reasons), 'WARNING')
        return False
    return True


def detail_html(ctx, info, gallery, cache):
    parts = []
    body = BeautifulSoup((info.get('productDesc') or {}).get('prdDescContClob') or '', 'html.parser')
    seen = set(gallery)
    for node in body.select('img'):
        ctx.checkpoint()
        url = asset(node.get('ec-data-src') or node.get('data-src') or node.get('data-original') or node.get('src'))
        if not url or url in seen or DETAIL_SKIP.search(url) or not re.search(r'\.(jpe?g|png|gif|webp)$', urllib.parse.urlsplit(url).path, re.I):
            continue
        seen.add(url)
        if url not in cache:
            cache[url] = check_public_image(url, timeout=10, retries=1, checkpoint=ctx.checkpoint)
        if cache[url]['status'] != 'accessible':
            ctx.log('상세 이미지 제외(공개 접근 불가): ' + url, 'WARNING')
            continue
        parts.append('<p><img src="' + html.escape(url, quote=True) + '" alt="상품 상세"></p>')
    notice = [(str(x.get('notiItemTitle') or '').strip(), re.sub(r'\s+', ' ', str(x.get('notiItemValue') or '')).strip())
              for x in info.get('notiItemMap') or [] if isinstance(x, dict) and str(x.get('notiItemTitle') or '').strip()]
    if notice:
        rows = ''.join('<tr><th>' + html.escape(k) + '</th><td>' + html.escape(v) + '</td></tr>' for k, v in notice)
        parts.append('<h3>상품정보고시</h3><table><tbody>' + rows + '</tbody></table>')
    else:
        ctx.log('상품정보제공고시 없음: 고시 없이 상세 완성', 'WARNING')
    return '<div>' + ''.join(parts) + '</div>' if parts else ''


def clean_name(name, code):
    text = re.sub(r'\s*[\(\[]?\s*' + re.escape(code) + r'[A-Z0-9]*\s*[\)\]]?\s*', ' ', name or '', flags=re.I)
    return re.sub(r'\s+', ' ', text).strip() or (name or '').strip()


def crawl(ctx):
    raw = str(ctx.product_code or '').strip()
    if not raw:
        ctx.fail_item('품번 또는 하프클럽 상품번호를 입력하세요.')
        raise CollectionError('입력 품번 없음')
    ctx.log('하프클럽 품번 코드 버전: ' + REVISION, 'INFO')
    number = direct_number(raw)
    code = '' if number else key(raw)
    try:
        ctx.stage('하프클럽 검색: ' + raw)
        numbers = [number] if number else search(ctx, code)
        if not numbers:
            raise CollectionError('정확히 일치하는 하프클럽 품번 상품 없음: ' + raw)
        cache, pending, errors, palette = {}, [], [], []
        for no in numbers:
            ctx.checkpoint()
            ctx.stage('하프클럽 상품 ' + no)
            try:
                info, prices, url = product_page(ctx, no)
                name = str(info.get('prdNm') or '').strip()
                product_code = key(str(info.get('prdCd') or '').split('_')[0])
                if not product_code or not re.search(r'[A-Z]', product_code):
                    found = re.search(r'(?<![A-Z0-9])(' + re.escape(code) + r'[A-Z0-9]*)', name.upper()) if code else None
                    product_code = found[1] if found else code
                if code and product_code != code and not token_in(code, name):
                    ctx.log('다른 품번 상품 제외: ' + no + ' / ' + (product_code or name), 'INFO')
                    continue
                if not product_code:
                    raise CollectionError('공식 상품코드 확인 불가')
                colors, sizes = options(info, ctx.enabled('size'))
                images = info.get('productImage') or {}
                gallery = []
                for k in ['basicExtNm'] + ['add%dExtNm' % i for i in range(1, 11)]:
                    candidate = asset(images.get(k))
                    if candidate and candidate not in gallery:
                        gallery.append(candidate)
                shots = [u for u in gallery if gallery_image(ctx, u, cache)]
                if not shots:
                    raise CollectionError('공개·규격 통과 상품 사진 없음')
                brand = str(info.get('brandMainNmKr') or info.get('brandNm') or '').strip()
                record = dict(product_code=code or product_code, product_name=clean_name(name, product_code), product_url=url)
                record['원본 상품명'] = name
                record['하프클럽 상품번호'] = no
                record['브랜드'] = brand
                record['원본 옵션'] = ' / '.join(c['color_name'] + (':' + c['size'] if c.get('size') else '') for c in colors)
                if ctx.enabled('size'):
                    record['size'] = ','.join(sizes)
                if ctx.enabled('price'):
                    record['price'] = positive(prices.get('price')) or positive(prices.get('selPrc'))
                if ctx.enabled('regular_price'):
                    record['regular_price'] = positive(prices.get('normPrc'))
                    if record['regular_price'] is None:
                        ctx.log('공식 정상가(normPrc) 없음/0: 빈칸, 나머지 수집 계속', 'WARNING')
                if ctx.enabled('category'):
                    category = info.get('dispCtgr') or {}
                    record['category'] = ' > '.join(str(category.get('dispCtgrNm%d' % i) or '').strip() for i in (1, 2, 3) if str(category.get('dispCtgrNm%d' % i) or '').strip())
                if ctx.enabled('detail_page'):
                    record['detail_page'] = detail_html(ctx, info, gallery, cache)
                for c in colors:
                    if not any(p['color_name'] == c['color_name'] for p in palette):
                        palette.append(dict(c))
                pending.append((record, shots, brand))
            except (CollectionError, ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(no + ': ' + str(exc))
                ctx.log(errors[-1], 'WARNING')
        if not pending:
            raise CollectionError('수집 가능한 하프클럽 상품 없음' + (' / ' + ' / '.join(errors) if errors else ''))
        if len(pending) > 1:
            ctx.log('같은 품번 판매 상품 %d개: %s' % (len(pending), ', '.join(r['하프클럽 상품번호'] + '(' + b + ')' for r, _, b in pending)), 'WARNING')
            for field in ('price', 'regular_price', 'category', 'size'):
                if ctx.enabled(field):
                    values = {r.get(field) for r, _, _ in pending}
                    common = values.pop() if len(values) == 1 else None
                    if common is None:
                        ctx.log('판매 상품 간 ' + field + ' 값이 달라 빈칸', 'WARNING')
                    for r, _, _ in pending:
                        r[field] = common if common is not None else ''
        shared = next((r.get('detail_page', '') for r, _, _ in pending if r.get('detail_page')), '')
        from detail_store import excel_detail
        if ctx.enabled('detail_page') and shared:
            shared, overflow = excel_detail(shared)
            if overflow:
                raise CollectionError('상세 HTML Excel 셀 한도 초과')
        emitted = set()
        for record, shots, _ in pending:
            if ctx.enabled('detail_page'):
                record['detail_page'] = shared
            for url in shots:
                if url in emitted:
                    continue
                emitted.add(url)
                ctx.emit_unmapped(dict(record), palette, url, shared_detail_html=shared)
        if errors:
            ctx.log('일부 상품 제외: ' + ' / '.join(errors), 'WARNING')
    except CollectionError as exc:
        ctx.fail_item(str(exc))
        raise
