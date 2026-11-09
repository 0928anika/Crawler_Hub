PORTAL_API_VERSION = 1

import hashlib
import html
import io
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, quote
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

SITE = 'https://benjefe.com'
POLICY = {'checked': '2026-11-08', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP·비율 1.8은 프로젝트 정책. 전체 등록 승인은 미검증.'}
# 색상 연결은 COLOR 옵션 li의 link_image로 확인한다.
# 구조화 데이터의 전 색상 공통 image 배열은 색상 사진 근거로 사용하지 않는다.
# 상세는 원본 상품 이미지로 조립하며 고시/사이즈 표를 따로 생성하지 않는다.

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
    if urlsplit(final).hostname not in ('benjefe.com', 'www.benjefe.com'):
        raise CrawlError('공식몰 밖으로 이동: ' + final)
    return BeautifulSoup(data.decode('utf-8-sig'), 'html.parser'), final


def search(ctx, code):
    queue = [SITE + '/product/search.html?' + urlencode({'keyword': code})]
    visited, products = set(), []
    while queue:
        ctx.checkpoint()
        url = queue.pop(0)
        if url in visited:
            continue
        if len(visited) >= 30:
            raise CrawlError('검색 30페이지 초과')
        visited.add(url)
        doc, final = page(ctx, url)
        for anchor in doc.select('.prdList .name a[href]'):
            link = urljoin(final, anchor['href'])
            if urlsplit(link).hostname in ('benjefe.com', 'www.benjefe.com') and link not in products:
                products.append(link)
        for anchor in doc.select('.xans-search-paging a[href]'):
            link = urljoin(final, anchor['href'])
            parsed = urlsplit(link)
            params = parse_qs(parsed.query)
            if parsed.hostname in ('benjefe.com', 'www.benjefe.com') and params.get('keyword') == [code] and link not in visited and link not in queue:
                queue.append(link)
    return products


def colors(doc):
    result = []
    for option in doc.select('ul[option_title="COLOR"] li[option_value]'):
        label = option['option_value'].strip()
        match = re.fullmatch(r'(.+?)\s*\(([A-Za-z0-9]+)\)', label)
        if not match:
            raise CrawlError('공식 색상명/코드 형식 변경: ' + label)
        color = {'label': label, 'name': match[1].strip(), 'code': match[2].upper(), 'image': option.get('link_image', '')}
        if color not in result:
            result.append(color)
    if not result:
        raise CrawlError('공식 COLOR 옵션이 없습니다.')
    return result


def variants(doc):
    for script in doc.find_all('script', src=False):
        match = re.search(r"var\s+option_stock_data\s*=\s*'([^']*)'", script.string or script.get_text())
        if match:
            try:
                value = json.loads(json.loads('"' + match[1] + '"'))
            except (ValueError, TypeError) as error:
                raise CrawlError('옵션 재고 JSON 해석 실패') from error
            if isinstance(value, dict) and value:
                return list(value.values())
    raise CrawlError('사이즈/가격 조회용 공식 옵션 정보 없음')


def product_name(doc, code):
    title = doc.select_one('.infoArea .headingArea h2')
    if title is None:
        for script in doc.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or script.get_text())
            except ValueError as error:
                raise CrawlError('상품 구조화 데이터 오류') from error
            if isinstance(data, dict) and data.get('@type') == 'ProductGroup':
                name = str(data.get('name') or '')
                break
        else:
            raise CrawlError('공식 상품명 없음')
    else:
        name = title.get_text(' ', strip=True)
    for token in ('벤제프', 'BENJEFE', code):
        name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])', '', name, flags=re.I)
    name = re.sub(r'\[\s*\]|\(\s*\)', '', name)
    name = re.sub(r'\s+', ' ', name).strip(' _-[]()')
    if not name:
        raise CrawlError('정리 후 실제 상품명이 없습니다.')
    return name


def gallery(ctx, color, page_url, cache):
    url = urljoin(page_url, color['image']) if color['image'] else ''
    if not url:
        raise CrawlError('색상 link_image 연결 없음: ' + color['label'])
    info = image_info(ctx, url, cache)
    if not info:
        return '', ''
    fmt, width, height, animated, digest, signal = info
    if fmt not in POLICY['formats'] or animated or min(width, height) < 500 or max(width, height)/min(width, height) > 1.8 or signal:
        ctx.log('색상 이미지 기준 탈락: ' + (signal or '형식/픽셀/비율') + ': ' + url, 'WARNING')
        return '', ''
    if ctx.enabled('additional_images'):
        ctx.log('공식 색상 연결 개별 컷이 1장이므로 해당 컷을 추가 5슬롯에 재사용: ' + color['code'], 'WARNING')
    return url, ','.join([url] * 5)


def detail(ctx, doc, page_url, cache):
    section = doc.select_one('#prdDetail')
    if section is None:
        raise CrawlError('원본 상품 상세 영역 없음')
    urls = []
    for image in section.select('img'):
        ctx.checkpoint()
        src = image.get('src') or image.get('data-src')
        if not src:
            continue
        url = urljoin(page_url, src)
        if re.search(r'banner|shipping|delivery|coupon|care_|배송|교환|반품|혜택', url + ' ' + str(image.get('alt') or ''), re.I):
            continue
        if url not in urls:
            urls.append(url)
    if not urls:
        raise CrawlError('공식 상세 이미지 없음')
    markup = []
    for url in urls:
        ctx.checkpoint()
        if not image_info(ctx, url, cache):
            raise CrawlError('원본 상세 이미지 공개 접근/해독 실패: ' + url)
        markup.append('<img src="' + html.escape(url, quote=True) + '">')
    result = ''.join(markup)
    if len(result.encode('utf-16-le')) // 2 > 32767:
        raise CrawlError('상세 HTML Excel 셀 한도 초과')
    return result


def regular_price(ctx, doc):
    # 공식 소비자가 필드. 판매가/쿠폰가를 정상가의 대체값으로 쓰지 않는다.
    node = doc.select_one('#span_product_price_custom')
    value = node.get_text(' ', strip=True) if node else ''
    if not value or value in ('-', '0원', '0'):
        ctx.log('공식 소비자가(정상가) 없음: 정상가 칼럼은 빈 값', 'WARNING')
        return None
    match = re.fullmatch(r'([0-9][0-9,]*)\s*원', value)
    if not match:
        raise CrawlError('공식 소비자가(정상가) 해석 실패: ' + value)
    return int(match[1].replace(',', ''))


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'([A-Z0-9]{10})(?:[_-]([A-Z0-9]+))?', raw)
    if not match:
        ctx.fail_item('기본 품번 10자리 또는 기본품번_색상코드 형식으로 입력하세요.')
        raise CrawlError('품번 형식 오류')
    base, requested = match[1], match[2]
    failures, emitted = [], set()
    def failed(message):
        failures.append(message)
        ctx.fail_item(message)
    ctx.stage('공식 품번 검색')
    try:
        links = search(ctx, base)
    except CrawlError as error:
        failed(str(error))
        raise
    for link in links:
        ctx.checkpoint()
        try:
            doc, final = page(ctx, link)
            model = doc.select_one('.infoArea .model_no')
            if model is None:
                raise CrawlError('공식 모델명 필드 누락: ' + final)
            if model.get_text(strip=True).upper() != base:
                ctx.log('다른 모델명 제외: ' + final, 'INFO')
                continue
            name = product_name(doc, base)
            palette = colors(doc)
            original_price = regular_price(ctx, doc)
            stock = variants(doc) if ctx.enabled('size') or ctx.enabled('price') else None
            shared_detail, cache = None, {}
            for color in palette:
                ctx.checkpoint()
                if requested and color['code'] != requested:
                    continue
                if color['code'] in emitted:
                    continue
                try:
                    ctx.stage('색상 수집: ' + color['label'])
                    record = {'product_code': base, 'product_name': name, 'color': color['name'], 'color_name': color['name'], 'color_code': color['code'], 'product_url': final}
                    if stock is not None:
                        matched = [v for v in stock if v.get('option_value_orginal', [None])[0] == color['label']]
                        if not matched:
                            raise CrawlError('해당 색상 공식 사이즈/가격 옵션 없음')
                    if ctx.enabled('size'):
                        sizes = []
                        for item in matched:
                            values = item.get('option_value_orginal')
                            if not isinstance(values, list) or len(values) != 2:
                                raise CrawlError('색상/사이즈 옵션 구조 변경')
                            if str(values[1]) not in sizes:
                                sizes.append(str(values[1]))
                        record['size'] = ','.join(sizes)
                    if ctx.enabled('price'):
                        prices = [v.get('option_price') for v in matched]
                        if any(not isinstance(p, (int, float)) for p in prices):
                            raise CrawlError('공식 옵션 판매가 누락')
                        record['price'] = prices[0]
                        if len(set(prices)) > 1:
                            ctx.log(f'사이즈별 가격 차이. 첫 옵션 가격 {prices[0]}원 선택: {prices}', 'WARNING')
                    if ctx.enabled('category'):
                        record['category'] = ' > '.join(a.get_text(' ', strip=True) for a in doc.select('.xans-product-headcategory ol li:not(:first-child) a') if a.get_text(strip=True))
                    if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                        main, extra = gallery(ctx, color, final, cache)
                        if ctx.enabled('main_image'):
                            record['main_image'] = main
                        if ctx.enabled('additional_images'):
                            record['additional_images'] = extra
                    if ctx.enabled('detail_page'):
                        if shared_detail is None:
                            shared_detail = detail(ctx, doc, final, cache)
                        record['detail_page'] = shared_detail
                    record['정상가'] = original_price
                    ctx.emit_result(record)
                    emitted.add(color['code'])
                except CrawlError as error:
                    failed(base + '_' + color['code'] + ': ' + str(error))
        except CrawlError as error:
            failed(str(error))
    if not emitted:
        failed('정확한 품번/지정 색상에 해당하는 공식 상품이 없습니다: ' + raw)
    if failures:
        raise CrawlError(' / '.join(failures))


