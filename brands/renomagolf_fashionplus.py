PORTAL_API_VERSION = 1

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
from PIL import Image, ImageChops, ImageFilter, ImageStat, UnidentifiedImageError

BASE = 'https://www.fashionplus.co.kr'
MAX_BYTES = 10_000_000
SEARCH_SELECTOR = '#goods_list_filter .goods-grid-pc'



class CollectionError(RuntimeError):
    """Expected transport, source-format or collection failure."""


class MissingResource(CollectionError):
    """Permanent HTTP 404; site itself removes these gallery slots on error."""


def public_opener(ctx, deadline):
    """Request-local dual-stack fallback; never change global socket settings.

    The observed Windows network publishes a nonresponsive DNS64 IPv6 address.
    Try DNS-provided IPv4 addresses first, retaining IPv6 as a fallback. A shared
    deadline covers address attempts, TLS, body reads and the optional retry.
    """
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
                connection.settimeout(min(5, max(.01, deadline-time.monotonic())))
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


def fetch(ctx, url, limit=4_000_000):
    """Anonymous GET. No cookies, credentials or Referer, including images."""
    deadline = time.monotonic() + 20
    started = time.monotonic()
    ctx.log('공개 요청: ' + url, 'INFO')
    opener = public_opener(ctx, deadline)
    for attempt in range(2):
        ctx.checkpoint()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 20초 제한 초과')
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with opener.open(request, timeout=min(5, remaining)) as response:
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise CollectionError('응답 크기 초과: ' + url)
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
            ctx.checkpoint()
            ctx.log('공개 요청 완료 %.1f초: %s' % (time.monotonic()-started, url), 'INFO')
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
            time.sleep(0.1)
    raise CollectionError('요청 실패: ' + url)


def soup_get(ctx, url):
    return BeautifulSoup(fetch(ctx, url).decode('utf-8-sig'), 'html.parser')


def source_url(node):
    if node is None:
        return ''
    for key in ('data-preload', 'data-lazyload'):
        match = re.search(r"['_\"]*src['\"]\s*:\s*['\"]([^'\"]+)",
                          html.unescape(node.get(key, '')))
        if match:
            return urllib.parse.urljoin(BASE, match.group(1))
    src = node.get('src', '')
    return urllib.parse.urljoin(BASE, src) if src and not src.startswith('data:') else ''


def exact_code(text, code):
    return bool(re.search(r'(?<![A-Z0-9])' + re.escape(code) + r'(?![A-Z0-9])',
                          text.upper()))


def studio_background(image):
    """Uniform perimeter color; no brand logo requirement or color guessing."""
    im=image.convert('RGB').resize((200,268))
    boxes=[(.04,.04,.14,.12),(.86,.04,.96,.12),(.04,.88,.14,.96),(.86,.88,.96,.96)]
    for y in (.20,.46,.72):boxes.extend([(.02,y,.10,y+.08),(.90,y,.98,y+.08)])
    patches=[]
    for x1,y1,x2,y2 in boxes:
        stat=ImageStat.Stat(im.crop((int(x1*im.width),int(y1*im.height),int(x2*im.width),int(y2*im.height))))
        patches.append((stat.mean,max(stat.stddev)))
    best=0
    for center,variance in patches:
        if variance>14:continue
        count=sum(spread<=14 and sum((a-b)**2 for a,b in zip(mean,center))<=35**2 for mean,spread in patches)
        best=max(best,count)
    foreground=ImageStat.Stat(im.crop((30,35,170,233)))
    accepted=best>=7 and max(foreground.stddev)>=6
    return accepted,'가장자리 단색 일치 패치 %s/%s (필요7)'%(best,len(patches))


def image_get(ctx, url):
    raw = fetch(ctx, url, MAX_BYTES)
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP'):
                raise CollectionError('지원하지 않는 실제 이미지 형식: ' + str(source.format))
            if getattr(source, 'is_animated', False):
                raise CollectionError('애니메이션 이미지')
            if source.width * source.height > 40_000_000:
                raise CollectionError('이미지 해독 크기 초과')
            source.load()
            return source.copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise CollectionError('손상 또는 이미지가 아닌 응답: ' + url) from exc


def gallery_ok(image):
    w, h = image.size
    if min(w, h) < 500:
        return False, '가로·세로 최소 500px 미달: %sx%s' % (w, h)
    if max(w, h) / min(w, h) > 1.8:
        return False, '통짜/긴 이미지 비율: %sx%s' % (w, h)
    # Multiple full-width/full-height internal white gutters signal a collage.
    im = image.convert('RGB').resize((120, 160))
    runs = []
    for vertical in (False, True):
        blank = []
        length = im.width if vertical else im.height
        for pos in range(int(length * .18), int(length * .82)):
            line = im.crop((pos, 0, pos + 1, im.height) if vertical
                           else (0, pos, im.width, pos + 1))
            stat = ImageStat.Stat(line)
            blank.append(min(stat.mean) > 247 and max(stat.stddev) < 5)
        # 바깥 여백은 사진 분할선이 아니다. 띠의 양쪽에 실제 사진 영역이 있어야 한다.
        runs.append(sum(value and i > 0 and not blank[i - 1] and any(not v for v in blank[i+1:])
                        for i, value in enumerate(blank)))
    if max(runs) >= 2:
        return False, '복수 내부 구분선/콜라주 신호'
    return True, '실제 상품 갤러리 정지 이미지'


def search(ctx, code):
    """Use normal public search rendering; direct search JSON returns 403.

    A fresh isolated browser context is used. Never attach to the user's Chrome
    profile, import cookies, solve challenges or spoof browser automation flags.
    """
    try:
        from playwright.sync_api import sync_playwright, Error as BrowserError
    except ImportError as exc:
        raise CollectionError('playwright 설치가 필요합니다') from exc
    ctx.stage('패션플러스 공개 검색 목록 확인')
    results = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            try:
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(1500)
                page.route('**/*', lambda route: route.abort()
                           if route.request.resource_type in ('image', 'media', 'font')
                           else route.continue_())
                for page_no in range(1, 1001):
                    ctx.checkpoint()
                    url = BASE + '/search/goods/result?' + urllib.parse.urlencode(
                        {'searchWord': code, 'page': page_no, 'pageSize': 40})
                    response = page.goto(url, timeout=30000, wait_until='domcontentloaded')
                    if response is None or response.status >= 400:
                        raise CollectionError('검색 페이지 접근 실패: ' + url)
                    deadline = time.monotonic() + 35
                    markup = ''
                    while time.monotonic() < deadline:
                        ctx.checkpoint()
                        if (page.locator(SEARCH_SELECTOR + ' .product-item a[href]').count()
                                or page.locator(SEARCH_SELECTOR + ' .mm_text-none').count()):
                            markup = page.locator(SEARCH_SELECTOR).inner_html()
                            break
                        body = page.locator('body').inner_text(timeout=1500)
                        if '검색 결과가 없습니다' in body or '검색결과가 없습니다' in body:
                            return list(results.values())
                        page.wait_for_timeout(200)
                    if not markup:
                        raise CollectionError('검색 목록 로딩 실패/접근 제한: ' + url)
                    if len(markup.encode('utf-8')) > 4_000_000:
                        raise CollectionError('검색 DOM 크기 초과')
                    soup = BeautifulSoup(markup, 'html.parser')
                    count_node = soup.select_one('.goods-count')
                    count = int(re.sub(r'\D', '', count_node.get_text()) or '0')
                    cards = soup.select('.product-list > .product-item')
                    if count and not cards:
                        raise CollectionError('검색 개수는 있으나 상품 카드 파싱 실패')
                    for card in cards:
                        ctx.checkpoint()
                        name = card.select_one('.text-product')
                        brand = card.select_one('.text-brand')
                        anchor = card.select_one('a[href]')
                        thumb = source_url(card.select_one('.image-product'))
                        if (brand and brand.get_text(strip=True) == '레노마골프'
                                and name and exact_code(name.get_text(' ', strip=True), code)):
                            if not anchor or not thumb:
                                raise CollectionError('검색 상품 링크/대표사진 누락')
                            link = urllib.parse.urljoin(BASE, anchor['href'])
                            if not re.fullmatch(re.escape(BASE) + r'/goods/detail/\d+', link):
                                raise CollectionError('검색 상품 링크 형식 변경: ' + link)
                            results[link] = (link, name.get_text(' ', strip=True), thumb)
                    if page_no * 40 >= count:
                        return list(results.values())
                raise CollectionError('검색 페이지 안전 한도 초과: 미완료')
            finally:
                browser.close()
    except BrowserError as exc:
        raise CollectionError('공개 검색 브라우저 실패 (Chrome 설치 필요): ' + str(exc)) from exc


def money(value):
    text = str(value or '').strip()
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)\s*원?', text):
        return None
    number = int(re.sub(r'\D', '', text))
    return number if number > 0 else None


def read_price(soup, regular=False):
    for row in soup.select('.discount-detail__row'):
        title, value = row.find('dt'), row.find('dd')
        if not title or not value:
            continue
        label = title.get_text(' ', strip=True)
        if ('희망소비자가' in label if regular else label.startswith('판매가')):
            return money(value.get_text(strip=True))
    return None


def clean_name(name, code):
    name=re.sub(r'^\s*\[(?:본사공식[^\]]*|레노마골프|레노마)\]\s*','',name)
    name=re.sub(r'(?<![A-Z0-9])'+re.escape(code)+r'(?:[-_][A-Z0-9]+)*(?![A-Z0-9])','',name,flags=re.I)
    name=re.sub(r'레노마골프|레노마|RENOMA(?:\s+GOLF)?','',name,flags=re.I)
    return re.sub(r'\s+',' ',name).strip(' _-[]()')


def color_parts(value):
    value = value.strip()
    match = re.fullmatch(r'\((\d+)\)\s*(.*)', value)
    if not match:
        match = re.fullmatch(r'(\d+)\s+(.+)', value)
    if match:
        return {'color_name': match[2].strip(), 'color_code': match[1]}
    return {'color_name': value, 'color_code': ''}


def options(ctx, link, include_size, doc=None, code="", raw_options=None):
    payload = json.loads(fetch(ctx, link + '/fetch-option-data').decode('utf-8-sig'))
    if not isinstance(payload, list) or not payload:
        raise CollectionError('옵션 JSON 형식 변경/비어 있음: ' + link)
    colors, size_groups = [], {}
    # Verified source is a flat list: "CAMEL FREE", "D/GREY FREE".
    # Unfamiliar nested option layouts fail explicitly instead of inventing colors.
    for product in payload:
        ctx.checkpoint()
        entries = product.get('options')
        if not isinstance(entries, list):
            raise CollectionError('미확인 중첩 옵션 구조: ' + link)
        for entry in entries:
            ctx.checkpoint()
            label = str(entry.get('_name') or '').strip()
            match = re.fullmatch(r'(.+?)[\s/]+(FREE|FR|F|ONE\s*SIZE|\d{2,3}|S|M|L|XL|XXL)',
                                 label, re.I)
            if not label:continue
            if raw_options is not None and label not in raw_options:raw_options.append(label)
            if not match:
                # 판매처가 색상만 쓰거나 구분자를 달리 써도 원본 후보를 보존한다.
                color_label=re.sub(r'^(?:색상|컬러|COLOR)\s*[:：]?\s*','',label,flags=re.I).strip() or label
                candidate=color_parts(color_label)
                if candidate not in colors:colors.append(candidate)
                ctx.log('옵션 원문 유지(사이즈 추측 안 함): '+label,'INFO')
                continue
            if match[1].strip() in ('사이즈','SIZE','size'):
                title=doc.select_one('.m__product-info .text_product') if doc else None
                value=title.get_text(' ',strip=True) if title else ''
                suffix=re.search(re.escape(code)+r'-([A-Z0-9]{3})(?![A-Z0-9])',value,re.I)
                colors.append({'color_name':'','color_code':suffix[1]} if suffix else color_parts(label))
            else:colors.append(color_parts(match[1]))
            if include_size:
                color_key = (colors[-1]['color_name'], colors[-1]['color_code'])
                size_groups.setdefault(color_key, set()).add(match[2])
    if not colors:
        raise CollectionError('실제 색상 후보 없음: ' + link)
    groups = list(size_groups.values())
    sizes = sorted(set().union(*groups)) if groups else []
    return colors, sizes


def detail_images(ctx, link, code):
    doc = soup_get(ctx, link + '/description-iframe')
    area = doc.select_one('.m_prodetail-desc .mm_pinchzoom-wrapper')
    if area is None:raise CollectionError('공식 상세 본문 영역 형식 변경: '+link)
    urls=[]
    for node in area.select('img'):
        url=source_url(node)
        if not url:continue
        path=urllib.parse.unquote(urllib.parse.urlsplit(url).path)
        # Actual seller-independent banner paths: Moda shop/shipping notices,
        # platform brand bar, Hansung common seasonal top/bottom banners.
        context=path+' '+str(node.get('alt') or '')+' '+str(node.get('title') or '')
        if (re.search(r'/mall/(?:event|md_image)/|/brand_info/|/renoma/img/|/(?:notice|delivery|shipping|banner|coupon)/',path,re.I)
                or re.search(r'delivery|shipping|return|refund|copyright|(?:^|[/_-])banner|브랜드\s*매장|매장\s*전경|배송|교환|반품|저작권',context,re.I)):
            ctx.log('공통 매장/행사/배송 안내 이미지 제외(요청 안 함): '+url,'INFO');continue
        if urllib.parse.urlsplit(url).scheme not in ('http','https'):raise CollectionError('비공개 본문 이미지 주소: '+url)
        if url in urls:continue
        try:picture=image_get(ctx,url)
        except MissingResource:
            ctx.log('공식 원본에 없는 상세 이미지404 제외: '+url,'WARNING');continue
        if min(picture.size)<=1:ctx.log('1픽셀 빈 상세 이미지 제외: '+url,'WARNING');continue
        urls.append(url)
    if not urls:raise CollectionError('유효 상품 상세 본문 이미지 없음: '+link)
    return urls


def common(values):
    return values[0] if values and all(v == values[0] and v is not None for v in values) else None


def crawl(ctx):
    code = str(ctx.product_code).strip().upper()
    if not code:raise CollectionError('검색 품번이 비어 있습니다')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        ctx.fail_item('수동 매핑은 대표/추가이미지 중 하나 이상 선택해야 합니다')
        raise CollectionError('사진 수집 미선택')
    try:
        candidates = search(ctx, code)
    except CollectionError as exc:
        ctx.fail_item(str(exc))
        raise
    errors, products = [], []
    for link, search_name, thumb in candidates:
        ctx.checkpoint()
        try:
            ctx.stage('대표사진 스튜디오 배경 검사: ' + link)
            accepted, reason = studio_background(image_get(ctx, thumb))
            if not accepted:
                ctx.log('셀러 촬영/불확실 대표사진 제외 (상세 미진입): %s / %s' % (reason, link), 'INFO')
                continue
            ctx.log('대표사진 통과: %s / %s' % (reason, link), 'INFO')
            soup = soup_get(ctx, link)
            title = soup.select_one('.m__product-info .text_product')
            name = title.get_text(' ', strip=True) if title else ''
            # Product title is the observed model-code source. Never require a
            # brand substring in the title, nor use platform goods ID as a model.
            if not exact_code(name, code):
                raise CollectionError('상품 상세 품번 불일치/확인 불가: ' + link)
            name = clean_name(name, code)
            if not name:
                raise CollectionError('필수 상품명 없음: ' + link)
            raw_options=[]
            colors, sizes = options(ctx, link, ctx.enabled('size'),doc=soup,code=code,raw_options=raw_options)
            record = {'product_code': code, 'product_name': name, 'product_url': link}
            if raw_options:record['원본 옵션']='\n'.join(raw_options)
            if ctx.enabled('size'):
                record['size'] = ','.join(sizes) if sizes else None
                if not sizes:
                    ctx.log('사이즈 분리 불가: 원본 옵션은 그대로 보존 / ' + link, 'INFO')
            if ctx.enabled('price'):
                record['price'] = read_price(soup)
                if record['price'] is None:
                    ctx.log('판매가 확인 불가: ' + link, 'WARNING')
            if ctx.enabled('regular_price'):
                ctx.checkpoint()
                try:record['regular_price'] = read_price(soup, True)
                except Exception as exc:
                    record['regular_price']=None
                    ctx.log('정상가 파싱 확인 불가: '+str(exc),'WARNING')
                if record['regular_price'] is None:
                    ctx.log('정상가 누락/0/형식 불명확: 빈칸으로 나머지 수집 계속 / ' + link, 'WARNING')
                ctx.checkpoint()
            photos = []
            for node in soup.select('.m_prodetail-head-thumbnail .btn_thumbnail [data-preload]'):
                ctx.checkpoint()
                url = source_url(node)
                if not url or url in photos:
                    continue
                try:
                    image = image_get(ctx, url)
                    valid, why = gallery_ok(image)
                    if valid:
                        photos.append(url)
                    else:
                        ctx.log('사진 제외: %s / %s' % (why, url), 'INFO')
                except MissingResource as exc:
                    ctx.log('사이트 원본 갤러리의 없는 사진 제외 (재시도 안 함): ' + str(exc), 'WARNING')
                except CollectionError as exc:
                    ctx.log('사진 확인 실패: ' + str(exc), 'WARNING')
                    raise
            if not photos:
                raise CollectionError('유효 개별 상품 사진 0장: ' + link)
            products.append((record, colors, photos))
        except (CollectionError, json.JSONDecodeError, UnicodeError) as exc:
            message = '%s / %s' % (link, exc)
            errors.append(message)
            ctx.fail_item(message)
    if not products:
        message = '정확한 품번의 스튜디오 대표사진 상품/유효 사진 없음: ' + code
        ctx.fail_item(message)
        raise CollectionError(message + (' / ' + '; '.join(errors) if errors else ''))
    # Merge all metadata BEFORE deduplicating image URLs.
    colors, photos, sources = [], [], {}
    for record, options_list, photo_list in products:
        for option in options_list:
            if option not in colors:
                colors.append(option)
        photos.extend(u for u in photo_list if u not in photos)
        for url in photo_list:
            sources.setdefault(url, record['product_url'])
    record = {'product_code': code, 'product_name': products[0][0]['product_name']}
    raw_options=[]
    for product in products:
        for label in str(product[0].get('원본 옵션') or '').splitlines():
            if label and label not in raw_options:raw_options.append(label)
    if raw_options:record['원본 옵션']='\n'.join(raw_options)
    for field in ('size', 'price', 'regular_price'):
        if ctx.enabled(field):
            if field=='size':
                values=[]
                for product in products:
                    for value in str(product[0].get('size') or '').split(','):
                        if value and value not in values:values.append(value)
                record[field]=','.join(values)
            else:record[field] = common([p[0].get(field) for p in products])
            if record[field] is None:
                ctx.log({'size': '사이즈', 'price': '판매가', 'regular_price': '정상가'}[field] +
                        ' 공통값 미확정: 빈칸으로 수집 계속', 'WARNING')
    if ctx.enabled('detail_page'):
        detail_link=products[0][0]['product_url']
        ctx.log('공통 상세 기준: 첫 번째 채택 상품 링크 하나만 사용 / '+detail_link,'INFO')
        try:details=detail_images(ctx,detail_link,code)
        except (CollectionError,json.JSONDecodeError,UnicodeError) as exc:
            ctx.fail_item(detail_link+' / '+str(exc));raise
        markup = ''.join('<img src="%s" alt="상품 상세" style="max-width:100%%;height:auto">' %
                         html.escape(url, quote=True) for url in details)
        if len(markup.encode('utf-16-le'))//2 > 32767:
            ctx.fail_item('공통 상세 HTML Excel 셀 한도 초과')
            raise CollectionError('공통 상세 HTML Excel 셀 한도 초과')
        record['detail_page'] = markup
    for url in photos:
        ctx.checkpoint()
        ctx.emit_unmapped(dict(record, product_url=products[0][0]['product_url']), colors, url,
                          shared_detail_html=record.get('detail_page', ''))
    if errors:
        raise CollectionError('일부 링크 수집 실패. 성공 후보 보존, 입력 행 미완료: ' + '; '.join(errors))
    ctx.log('%s: %s개 링크, %s개 색상 후보, %s장 사진 수집' %
            (code, len(products), len(colors), len(photos)), 'SUCCESS')
