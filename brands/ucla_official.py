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

SITE = 'https://uclakorea.co.kr'
CRAWLER_REVISION = '2026-11-08-hybrid-r4'
POLICY = {'checked': '2026-11-08', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP·비율 1.8은 프로젝트 정책. 전체 등록 승인은 미검증.'}
# 색상별 독립 상품 페이지의 공식 모델명과 색상 옵션을 대조한다.
# 검색의 모든 상품 링크를 조회하고 해당 단일 색상 페이지 갤러리만 사용한다.
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
    if urlsplit(final).hostname not in ('uclakorea.co.kr', 'www.uclakorea.co.kr'):
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
            if urlsplit(link).hostname in ('uclakorea.co.kr', 'www.uclakorea.co.kr') and link not in products:
                products.append(link)
        for anchor in doc.select('.xans-search-paging a[href]'):
            link = urljoin(final, anchor['href'])
            parsed = urlsplit(link)
            params = parse_qs(parsed.query)
            if parsed.hostname in ('uclakorea.co.kr', 'www.uclakorea.co.kr') and params.get('keyword') == [code] and link not in visited and link not in queue:
                queue.append(link)
    return products


def colors(doc):
    model = official_model(doc)
    title = doc.select_one('.infoArea h1').get_text(' ', strip=True)
    match = re.search(r'\[([^\[\]]+)\]\s*\(?\s*' + re.escape(model) + r'(?![A-Za-z0-9])', title, re.I)
    color_name = match[1].strip() if match else ''
    color_code = model.split('_', 1)[1]
    label = color_name + '(' + color_code + ')' if color_name else color_code
    return [{'label': label, 'name': color_name, 'code': color_code}]


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


def product_name(doc, code, palette=None):
    title = doc.select_one('.infoArea h1')
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
    for color in colors(doc) if palette is None else palette:
        name = re.sub(r'\[\s*' + re.escape(color['name']) + r'\s*\]', '', name, flags=re.I)
        name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(code + '_' + color['code']) + r'(?![A-Za-z0-9])', '', name, flags=re.I)
    for token in ('UCLA', '유씨엘에이', code):
        name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])', '', name, flags=re.I)
    name = re.sub(r'\[\s*\]|\(\s*\)', '', name)
    name = re.sub(r'\s+', ' ', name).strip(' _-[]()')
    if not name:
        raise CrawlError('정리 후 실제 상품명이 없습니다.')
    return name


def gallery_candidates(ctx, urls, cache):
    accepted, hashes = [], set()
    for url in urls:
        ctx.checkpoint()
        info = image_info(ctx, url, cache)
        if not info:
            continue
        fmt, width, height, animated, digest, signal = info
        if fmt not in POLICY['formats'] or animated or min(width, height) < 500 or max(width, height)/min(width, height) > 1.8 or signal:
            ctx.log('색상 이미지 기준 탈락: ' + (signal or '형식/픽셀/비율') + ': ' + url, 'WARNING')
            continue
        if digest not in hashes:
            hashes.add(digest)
            accepted.append((url, digest))
    return accepted


def gallery(ctx, color, page_url, cache):
    accepted = [url for url, digest in gallery_candidates(ctx, color['images'], cache)]
    if not accepted:
        ctx.log('통과한 색상 갤러리 사진 없음', 'WARNING')
        return '', ''
    extras = accepted[1:]
    if not extras:
        extras = accepted
        if ctx.enabled('additional_images'):
            ctx.log('색상 사진 1장만 통과하여 추가 5슬롯에도 재사용', 'WARNING')
    return accepted[0], ','.join(extras[i % len(extras)] for i in range(5))


def detail(ctx, doc, page_url, cache):
    section = doc.select_one('#detail1 .cont')
    if section is None:
        raise CrawlError('원본 상품 상세 영역 없음')
    urls = []
    for image in section.select('img'):
        ctx.checkpoint()
        src = image.get('ec-data-src') or image.get('data-src') or image.get('src')
        if not src:
            continue
        url = urljoin(page_url, src)
        parsed = urlsplit(url)
        # 원본 상품 이미지/INFO만 허용. 브랜드·하단 공통 안내는 상품 이미지와 별도 경로다.
        if parsed.hostname != 'images.dcollect.co.kr' or not parsed.path.startswith('/GdsImages/'):
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


def official_model(doc):
    heading = doc.select_one('.infoArea h1')
    if heading is not None:
        codes = set(re.findall(r'(?<![A-Z0-9])([A-Z0-9]{7}_[A-Z0-9]+)(?![A-Z0-9])', heading.get_text(' ', strip=True).upper()))
        if len(codes) == 1:
            return codes.pop()
    raise CrawlError('공식 상품명에서 고유한 품번_색상코드를 확인할 수 없습니다.')


def crawl(ctx):
    ctx.log('UCLA 크롤러 버전 ' + CRAWLER_REVISION, 'INFO')
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'([A-Z0-9]{7})(?:[_-]([A-Z0-9]+))?', raw)
    if not match:
        ctx.fail_item('기본 품번 7자리 또는 기본품번_색상코드 형식으로 입력하세요.')
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
            model = official_model(doc)
            if not re.fullmatch(re.escape(base) + r'_[A-Z0-9]+', model):
                ctx.log('다른 모델명 제외: ' + final, 'INFO')
                continue
            name = product_name(doc, base)
            palette = colors(doc)
            ctx.log('상품명 기준 색상 확인: ' + palette[0]['label'] + ': ' + final, 'SUCCESS')
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                gallery_urls = []
                for image in doc.select('.imgArea img.bigImage, .imgArea .viewer img'):
                    src = image.get('src') or image.get('data-src')
                    if src:
                        url = urljoin(final, src)
                        if url not in gallery_urls:
                            gallery_urls.append(url)
                palette[0]['images'] = gallery_urls
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
                        matched = []
                        for variant in stock:
                            values = variant.get('option_value_orginal')
                            if not isinstance(values, list) or not values:
                                continue
                            option_color = re.search(r'\(([A-Za-z0-9]+)\)\s*$', str(values[0]))
                            if option_color and option_color[1].upper() == color['code']:
                                matched.append(variant)
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


