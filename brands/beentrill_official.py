PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

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

SITE = 'https://beentrill.co.kr'
CRAWLER_REVISION = '2026-11-08-beentrill-v5'
POLICY = {'checked': '2026-11-08', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP·비율 1.8은 프로젝트 정책. 전체 등록 승인은 미검증.'}
# 공식 .d_code의 기본 품번/색상코드와 색상별 페이지 갤러리를 연결한다.
# 상품명 끝 색상 괄호 제거. 고시/사이즈는 원본 상세 이미지 유지.
# BA251CD501 실제 공식몰 검증; 내용 검사는 규칙 기반이며 의미 인식은 아님.
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
    if urlsplit(final).hostname not in ('beentrill.co.kr', 'www.beentrill.co.kr'):
        raise CrawlError('공식몰 밖으로 이동: ' + final)
    return BeautifulSoup(data.decode('utf-8-sig'), 'html.parser'), final


def search(ctx, base):
    pending = [SITE + '/product/search.html?' + urlencode({'keyword': base})]
    visited, links, identities = set(), [], set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        doc, final = page(ctx, url)
        for anchor in doc.select('.xans-search-result .prdList .name a[href]'):
            link = urljoin(final, anchor['href'])
            parsed = urlsplit(link)
            identity = re.search(r'/product/[^/]+/(\d+)/', parsed.path)
            if parsed.hostname not in ('beentrill.co.kr', 'www.beentrill.co.kr') or not identity:
                continue
            if identity[1] not in identities:
                identities.add(identity[1])
                links.append(link)
        for anchor in doc.select('.xans-search-paging a[href]'):
            link = urljoin(final, anchor['href'])
            parsed = urlsplit(link)
            if parsed.hostname in ('beentrill.co.kr', 'www.beentrill.co.kr') and parse_qs(parsed.query).get('keyword') == [base] and link not in visited and link not in pending:
                pending.append(link)
    return links


def identity(doc, base):
    codes = {n.get_text(strip=True).upper() for n in doc.select('.d_code') if n.get_text(strip=True)}
    if len(codes) > 1:
        raise CrawlError('공식 .d_code 품번 필드가 중복됩니다.')
    if codes:
        full = codes.pop()
    else:
        # 단일 색상은 관련상품 모듈(.d_code) 자체가 없다.
        # 검색 키워드만 믿지 않고 상품별 공식 info 이미지의 폴더/파일 품번을 대조한다.
        official = set()
        for image in doc.select('#prdDetail img'):
            src = image.get('ec-data-src') or image.get('data-src') or image.get('src') or ''
            parsed = urlsplit(urljoin(SITE, src))
            if parsed.hostname not in ('wwbrands.net', 'www.wwbrands.net', 'beentrill.co.kr', 'www.beentrill.co.kr'):
                continue
            found = re.search(r'/web/upload/beentrill/detail/[^/]+/([A-Z0-9]+)/\1_([A-Z0-9]{2})_info(?:_?\d+)?\.(?:jpe?g|png|webp)$', unquote(parsed.path), re.I)
            if found:
                official.add(found[1].upper() + found[2].upper())
        if len(official) != 1:
            raise CrawlError('공식 품번 필드 부재 및 상품별 info 이미지의 고유 품번/색상 확인 불가')
        full = official.pop()
    match = re.fullmatch(r'([A-Z0-9]+)([A-Z0-9]{2})', full)
    if not match:
        raise CrawlError('공식 품번/색상코드 형식 변경: ' + full)
    if match[1] != base:
        return None
    heading = doc.select_one('.headingArea h1')
    title = heading.get_text(' ', strip=True) if heading else ''
    color = re.search(r'\(([^()]*)\)\s*$', title)
    if not title:
        raise CrawlError('공식 상품명 없음: ' + full)
    color_name = color[1].strip() if color else ''
    name = title[:color.start()].strip() if color else title
    for token in ('BEENTRILL', 'BEEN TRILL', '빈트릴', full, base):
        name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])', '', name, flags=re.I)
    name = re.sub(r'\s+', ' ', name).strip(' _-')
    if not name:
        raise CrawlError('정리 후 실제 상품명 없음')
    return dict(product_code=base, product_name=name, color=color_name or match[2], color_name=color_name, color_code=match[2])


def sizes(doc):
    selects = doc.select('select[option_title="size"], select[option_title="SIZE"], select[option_title="사이즈"]')
    result = []
    for select in selects:
        if select.get('product_type') == 'addproduct_option':
            continue
        for option in select.select('option'):
            if option.get('value', '') in ('', '*', '**'):
                continue
            value = option.get('value', '').strip()
            if value and value not in result:
                result.append(value)
    if not result:
        raise CrawlError('해당 색상의 공식 사이즈 옵션 없음')
    return ','.join(result)


def price(ctx, doc):
    # 최적할인가(쿠폰 적용시)는 쓰지 않는다. 기간 할인은 비회원 공개 판매가다.
    node = doc.select_one('#span_product_price_sale')
    if node is None:
        node = doc.select_one('#span_product_price_text')
    text = node.get_text(' ', strip=True) if node else ''
    match = re.search(r'(?:KRW|₩)\s*([\d,]+)', text)
    if not match:
        ctx.log('공개 KRW 판매가를 확인할 수 없어 가격을 비워 둡니다.', 'WARNING')
        return None
    return int(match[1].replace(',', ''))


def gallery(ctx, doc, final, cache):
    urls = []
    original = doc.select_one('.imgArea input.first_img[value]')
    if original:
        urls.append(urljoin(final, original['value']))
    for image in doc.select('.imgArea #zoom_image_list img, .imgArea .listImg img'):
        source = image.get('ec-data-src') or image.get('data-src') or image.get('src')
        if source and urljoin(final, source) not in urls:
            urls.append(urljoin(final, source))
    accepted, hashes = [], set()
    for url in urls:
        ctx.checkpoint()
        info = image_info(ctx, url, cache)
        if not info:
            continue
        fmt, width, height, animated, digest, signal = info
        if fmt not in POLICY['formats'] or animated or min(width, height) < 500 or max(width, height)/min(width, height) > 1.8 or signal:
            ctx.log('갤러리 제외: ' + (signal or '파일 형식/픽셀/비율') + ': ' + url, 'WARNING')
            continue
        if digest in hashes:
            continue
        hashes.add(digest)
        accepted.append(url)
        # 대표 1장+추가 5장의 슬롯이 채워지면 불필요한 뒤쪽 요청은 하지 않는다.
        if len(accepted) == 6:
            break
    if not accepted:
        ctx.log('통과한 대표/추가이미지가 없어 이미지 필드는 빈칸으로 보존합니다.', 'WARNING')
        return '', ''
    extras = accepted[1:]
    if not extras:
        extras = accepted[:]
        ctx.log('유효한 사진 1장만 있어 추가 5슬롯에 재사용합니다.', 'WARNING')
    return accepted[0], ','.join(extras[i % len(extras)] for i in range(5))


def detail(ctx, doc, final, base, cache):
    sections = doc.select('#prdDetail')
    if not sections:
        raise CrawlError('원본 상품 상세 영역 없음')
    urls = []
    for section in sections:
        for image in section.select('img'):
            source = image.get('ec-data-src') or image.get('data-src') or image.get('src')
            if not source:
                continue
            url = urljoin(final, source)
            parsed = urlsplit(url)
            # 상품별 폴더만 허용: 시즌 intro, 공통 notice/배송 배너는 요청 전 제외.
            if parsed.hostname not in ('wwbrands.net', 'www.wwbrands.net', 'beentrill.co.kr'):
                continue
            folder = re.search(r'/web/upload/beentrill/detail/[^/]+/([A-Z0-9]+)/([^/]+)$', unquote(parsed.path), re.I)
            if not folder:
                continue
            if not re.fullmatch(re.escape(folder[1]) + r'_(?:[A-Z0-9]+_)?(?:info|model|detail)(?:_?\d+)?\.(?:jpe?g|png|webp)', folder[2], re.I):
                continue
            if url not in urls:
                if folder[1].upper() != base:
                    ctx.log('공식 상품 페이지가 연결한 재사용 상세 원본 유지: ' + url, 'INFO')
                urls.append(url)
    if not urls:
        raise CrawlError('상품별 원본 상세 이미지 없음')
    markup = []
    for url in urls:
        ctx.checkpoint()
        if not image_info(ctx, url, cache):
            raise CrawlError('상세 이미지 접근/해독 실패: ' + url)
        markup.append('<img src="' + html.escape(url, quote=True) + '" style="max-width:100%;height:auto;">')
    result = ''.join(markup)
    if len(result.encode('utf-16-le'))//2 > 32767:
        raise CrawlError('상세 HTML Excel 셀 한도 초과. 원본 자료 임의 절단하지 않음.')
    return result


def crawl(ctx):
    ctx.log('빈트릴 크롤러 ' + CRAWLER_REVISION, 'INFO')
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'([A-Z0-9]+)(?:[_-]([A-Z0-9]{2}))?', raw)
    if not match:
        message = '영문·숫자로 된 기본 품번 또는 기본품번_색상코드를 입력하세요. 예: BA252CPC02, BA251CD501_LP'
        if re.fullmatch(r'[A-Z]{2}\d{3}[A-Z]1\d{3}(?:[_-][A-Z0-9]{2})?', raw):
            message += ' S1처럼 보이는 부분이 영문 SI인지 확인하세요. 숫자 1과 영문 I는 다른 품번입니다.'
        ctx.fail_item(message)
        raise CrawlError(message)
    base, requested = match[1], match[2]
    errors, emitted = [], set()
    ctx.stage('공식 품번 검색')
    try:
        links = search(ctx, base)
    except CrawlError as error:
        ctx.fail_item(str(error))
        raise
    for link in links:
        ctx.checkpoint()
        final = link
        try:
            doc, final = page(ctx, link)
            record = identity(doc, base)
            if record is None:
                ctx.log('다른 공식 품번 제외: ' + final, 'INFO')
                continue
            code = record['color_code']
            if (requested and requested != code) or code in emitted:
                continue
            ctx.stage('색상 수집: ' + record['color'])
            record['product_url'] = final
            if ctx.enabled('size'):
                record['size'] = sizes(doc)
            if ctx.enabled('price'):
                record['price'] = price(ctx, doc)
            if ctx.enabled('category'):
                record['category'] = ' > '.join(a.get_text(' ', strip=True) for a in doc.select('.xans-product-headcategory ol li a') if a.get_text(strip=True))
            cache = {}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main, extras = gallery(ctx, doc, final, cache)
                if ctx.enabled('main_image'):
                    record['main_image'] = main
                if ctx.enabled('additional_images'):
                    record['additional_images'] = extras
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail(ctx, doc, final, base, cache)
            collect_regular_price(ctx, record, doc, 'beentrill')
            ctx.emit_result(record)
            emitted.add(code)
        except CrawlError as error:
            message = final + ': ' + str(error)
            errors.append(message)
            ctx.fail_item(message)
    if not emitted and not errors:
        errors.append('일치하는 공식 품번/지정 색상 상품이 없습니다: ' + raw)
        ctx.fail_item(errors[-1])
    if errors:
        raise CrawlError(' / '.join(errors))
