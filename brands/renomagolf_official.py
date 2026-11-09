PORTAL_API_VERSION = 1

import hashlib
import html
import io
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, quote, unquote
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

SITE = 'https://hansungmall.net'
CRAWLER_REVISION = '2026-11-09-renomagolf-v1'
POLICY = {'checked': '2026-11-09', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP·비율 1.8은 프로젝트 정책. 전체 등록 승인은 미검증.'}
# 공식 품번코드(기본품번-색상코드)와 단일색상 옵션, 색상별 페이지를 연결한다.
# 상품 본문 /renoma/detail/만 유지. 시즌 화보/배송/교환 안내 /renoma/img/는 요청 전 제외.
# 고시와 사이즈는 원본 상세이미지 유지. 별도 표는 생성하지 않는다.
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
                ctx.checkpoint()
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
    if small_count >= 150 and 0 < total_dark < w*h*.16 and small_area > total_dark*.35:
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


def image_info(ctx, url, cache):
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
            signal = content_signal(im)
        ctx.log(f'이미지 {fmt} {w}x{h}, {len(data)}bytes, animated={animated}, HTTP={content_type}: {url}', 'INFO')
        info = (fmt, w, h, animated, hashlib.sha256(data).hexdigest(), signal)
    except (CrawlError, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        ctx.log(f'이미지 제외: {url}: {e}', 'WARNING')
        info = None
    cache[url] = info
    return info


def page(ctx, url):
    data, final, _ = fetch(ctx, url)
    if urlsplit(final).hostname not in ('hansungmall.net','www.hansungmall.net'):
        raise CrawlError('지정 공식몰 밖으로 이동: ' + final)
    return BeautifulSoup(data.decode('utf-8-sig'),'html.parser'), final


def search(ctx, base):
    pending = [SITE + '/product/search.html?' + urlencode({'keyword':base})]
    visited, identities, links = set(), set(), []
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        doc, final = page(ctx, url)
        for anchor in doc.select('.name a[href]'):
            link = urljoin(final, anchor['href'])
            parsed = urlsplit(link)
            match = re.search(r'/product/[^/]+/(\d+)/', parsed.path)
            if parsed.hostname in ('hansungmall.net','www.hansungmall.net') and match and match[1] not in identities:
                identities.add(match[1])
                links.append(link)
        for anchor in doc.select('.xans-search-paging a[href]'):
            link = urljoin(final, anchor['href'])
            parsed = urlsplit(link)
            if parsed.hostname in ('hansungmall.net','www.hansungmall.net') and parse_qs(parsed.query).get('keyword') == [base] and link not in visited and link not in pending:
                pending.append(link)
    return links


def field(doc, label):
    node = doc.select_one('li[rel="' + label + '"]')
    if node is None:
        return ''
    value = node.get_text(' ',strip=True)
    return re.sub(r'^' + re.escape(label) + r'\s*','',value).strip()


def identity(doc, base):
    brand = field(doc,'브랜드')
    if re.sub(r'\s+','',brand).casefold() not in ('레노마골프','renomagolf'):
        raise CrawlError('공식 브랜드 정보가 레노마골프가 아닙니다: ' + brand)
    full = field(doc,'품번코드').upper()
    match = re.fullmatch(r'([A-Z0-9]+)-([A-Z0-9]+)',full)
    if not match:
        raise CrawlError('공식 품번코드를 확인할 수 없습니다: ' + full)
    if match[1] != base:
        return None
    color_selects = doc.select('select[option_title="색상"]')
    colors = list(dict.fromkeys(o.get('value','').strip() for s in color_selects for o in s.select('option[value]') if o.get('value') not in ('','*','**')))
    if len(colors) != 1:
        raise CrawlError('색상별 페이지의 단일 공식 색상 옵션을 확인할 수 없습니다: ' + full)
    title = doc.select_one('h1.efd-prd-name')
    if title is None:
        raise CrawlError('공식 상품명 없음')
    name = title.get_text(' ',strip=True)
    # 뒤쪽 상품코드와 그 앞의 실제 옵션 색상명만 제거한다.
    name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(full) + r'(?![A-Za-z0-9])','',name,flags=re.I).strip()
    name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(colors[0]) + r'\s*$','',name,flags=re.I).strip()
    for token in ('레노마골프','레노마 골프','RENOMA GOLF','RENOMAGOLF',base):
        name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])','',name,flags=re.I)
    name = re.sub(r'\[\s*\]|\(\s*\)','',name)
    name = re.sub(r'\s+',' ',name).strip(' _-')
    if not name:
        raise CrawlError('정리 후 실제 상품명 없음')
    return dict(product_code=base,product_name=name,color=colors[0],color_name=colors[0],color_code=match[2])


def sizes(doc):
    result = []
    for select in doc.select('select[option_title="사이즈"]'):
        if select.get('product_type') == 'addproduct_option':
            continue
        for option in select.select('option[value]'):
            value = option.get('value','').strip()
            if value not in ('','*','**') and value not in result:
                result.append(value)
    if not result:
        raise CrawlError('공식 사이즈 옵션 없음')
    return ','.join(result)


def price(ctx, doc):
    node = doc.select_one('#span_product_price_sale')
    if node is None:
        node = doc.select_one('#span_product_price_text')
    value = node.get_text(' ',strip=True) if node else ''
    match = re.search(r'([\d,]+)\s*원',value)
    if not match:
        ctx.log('공개 KRW 판매가를 확인할 수 없어 가격은 빈칸입니다.','WARNING')
        return None
    return int(match[1].replace(',',''))


def gallery(ctx, doc, final, cache):
    urls = []
    # HTML이 제공하는 실제 big 원본만 사용. small URL의 경로를 임의 변조하지 않는다.
    for image in doc.select('.imgArea img'):
        src = image.get('ec-data-src') or image.get('data-src') or image.get('src')
        if not src:
            continue
        url = urljoin(final,src)
        parsed = urlsplit(url)
        if parsed.hostname not in ('hansungmall.net','www.hansungmall.net') or not re.match(r'/web/product/(?:big/|extra/big/)',parsed.path):
            continue
        if url not in urls:
            urls.append(url)
    accepted, hashes = [], set()
    for url in urls:
        ctx.checkpoint()
        info = image_info(ctx,url,cache)
        if not info:
            continue
        fmt,w,h,animated,digest,signal = info
        if fmt not in POLICY['formats'] or animated or min(w,h)<500 or max(w,h)/min(w,h)>1.8 or signal:
            ctx.log('갤러리 제외: ' + (signal or '파일 형식/픽셀/비율') + ': ' + url,'WARNING')
            continue
        if re.search(r'size|chart|measure|banner|notice|collage|사이즈|고시|측정',urlsplit(url).path,re.I):
            ctx.log('갤러리 자료 역할 제외: ' + url,'WARNING')
            continue
        if digest not in hashes:
            hashes.add(digest)
            accepted.append(url)
            ctx.log('채택: 공식 단일색상 상품의 big 갤러리, 실제 규격/역할/부정 신호 검사 통과: ' + url,'INFO')
        if len(accepted)==6:
            break
    if not accepted:
        ctx.log('통과한 갤러리 사진 0장: 이미지 빈칸, 필수 상품정보 유지','WARNING')
        return '', ''
    extras = accepted[1:] or accepted[:1]
    if len(accepted)==1:
        ctx.log('유효한 사진 1장만 있어 추가에도 재사용합니다.','WARNING')
    return accepted[0], ','.join(extras[i%len(extras)] for i in range(5))


def detail(ctx, doc, final, base, color, cache):
    urls = []
    for image in doc.select('#prdDetail2 img'):
        src = image.get('ec-data-src') or image.get('data-src') or image.get('src')
        if not src:
            continue
        url = urljoin(final,src)
        parsed = urlsplit(url)
        if parsed.hostname != 'hansungimall.openhost.cafe24.com':
            continue
        # /renoma/img/26fwtop.jpg, bottom.jpg 등 공통배너는 다운로드하지 않는다.
        if not re.fullmatch(r'/renoma/detail/' + re.escape(base) + '-' + re.escape(color) + r'(?:[_-]\d+)?\.(?:jpe?g|png|webp)',parsed.path,re.I):
            continue
        if url not in urls:
            urls.append(url)
    if not urls:
        raise CrawlError('해당 품번/색상 원본 상세이미지 없음')
    markup = []
    for url in urls:
        ctx.checkpoint()
        if not image_info(ctx,url,cache):
            raise CrawlError('원본 상세이미지 공개 접근/해독 실패: ' + url)
        markup.append('<img src="' + html.escape(url,quote=True) + '" style="max-width:100%;height:auto;">')
    value = ''.join(markup)
    if len(value.encode('utf-16-le'))//2>32767:
        raise CrawlError('상세 HTML Excel 셀 한도 초과. 원본을 임의 절단하지 않습니다.')
    return value


def crawl(ctx):
    ctx.log('레노마골프 크롤러 ' + CRAWLER_REVISION,'INFO')
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'([A-Z0-9]+)(?:[_-]([A-Z0-9]+))?',raw)
    if not match:
        ctx.fail_item('기본 품번 또는 기본품번_색상코드 형식으로 입력하세요.')
        raise CrawlError('품번 형식 오류')
    base, requested = match[1],match[2]
    ctx.stage('공식 품번 검색')
    try:
        links = search(ctx,base)
    except CrawlError as error:
        ctx.fail_item(str(error))
        raise
    errors, emitted = [],set()
    for link in links:
        ctx.checkpoint()
        final = link
        try:
            doc, final = page(ctx,link)
            record = identity(doc,base)
            if record is None:
                ctx.log('다른 품번 검색 결과 제외: ' + final,'INFO')
                continue
            color = record['color_code']
            if color in emitted or (requested and requested!=color):
                continue
            ctx.stage('색상 수집: ' + record['color_name'] + '(' + color + ')')
            record['product_url'] = final
            if ctx.enabled('size'):
                record['size'] = sizes(doc)
            if ctx.enabled('price'):
                record['price'] = price(ctx,doc)
            if ctx.enabled('category'):
                record['category'] = ' > '.join(a.get_text(' ',strip=True) for a in doc.select('.xans-product-headcategory li a') if a.get_text(strip=True) and a.get_text(strip=True) not in ('홈','Home'))
            cache = {}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main,extras = gallery(ctx,doc,final,cache)
                if ctx.enabled('main_image'):
                    record['main_image'] = main
                if ctx.enabled('additional_images'):
                    record['additional_images'] = extras
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail(ctx,doc,final,base,color,cache)
            ctx.emit_result(record)
            emitted.add(color)
        except CrawlError as error:
            message = final + ': ' + str(error)
            errors.append(message)
            ctx.fail_item(message)
    if not emitted and not errors:
        errors.append('일치하는 공식 품번/지정 색상 상품 없음: ' + raw)
        ctx.fail_item(errors[-1])
    if errors:
        raise CrawlError(' / '.join(errors))
