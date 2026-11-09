PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

import hashlib
import html
import io
import json
import re
import statistics
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

SITE = 'https://www.elandmall.co.kr'
BRAND_NO = '1690000175'
CRAWLER_REVISION = '2026-11-08-json-r2'
POLICY = {'checked': '2026-11-08', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP와 비율 1.8은 프로젝트 정책. 등록 승인 보장은 아님.'}
# 실제 조사: LALA25S01 전체 판매자 검색. 링크별 대표사진으로만 배경 판정.
# 상품 상세 통짜 안에 인쇄된 안내문은 이미지 자체의 일부로 유지된다.
# 실제 Hub 화면, 최종 Excel, WING 업로드는 별도 검증 대상이다.


class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


def fetch(ctx, url, limit=6000000):
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


def page(ctx, url):
    data, final, _ = fetch(ctx, url)
    if urlsplit(final).hostname != 'www.elandmall.co.kr':
        raise CrawlError('지정 쇼핑몰 밖으로 이동: ' + final)
    return BeautifulSoup(data.decode('utf-8-sig'), 'html.parser'), final


def search(ctx, code):
    links, seen, signatures = [], set(), set()
    page_no = 1
    while page_no <= 30:
        ctx.stage(f'전체 판매자 상품 검색 {page_no}페이지')
        soup, _ = page(ctx, SITE + '/s/srch?' + urlencode(
            {'q': code, 'size': 100, 'page': page_no, 'from': (page_no - 1) * 100}))
        total_tag = soup.select_one('[data-searchresult-total]')
        if total_tag is None or not re.fullmatch(r'[\d,]+', total_tag.get_text(strip=True)):
            raise CrawlError('검색 결과 건수 없음: 오류/인증 화면 또는 사이트 변경')
        total = int(total_tag.get_text(strip=True).replace(',', ''))
        current = []
        for a in soup.select('a.ui-item__link[href]'):
            u = urljoin(SITE, a['href'])
            p = urlsplit(u)
            q = parse_qs(p.query)
            if p.hostname != 'www.elandmall.co.kr' or p.path != '/i/item' or not q.get('itemNo'):
                continue
            key = (q['itemNo'][0], q.get('lowerVendNo', [''])[0])
            if key not in current:
                current.append(key)
            if u not in links:
                links.append(u)
        signature = tuple(current)
        if total and (not current or signature in signatures):
            raise CrawlError('검색 페이지가 반복되거나 비어 있음: 누락 방지를 위해 중단')
        signatures.add(signature)
        seen.update(current)
        if len(seen) >= total:
            return links
        page_no += 1
    raise CrawlError('검색 30페이지 초과')


def product(ctx, url, code):
    for attempt in range(2):
        soup, final = page(ctx, url)
        parsed = None
        reason = 'itemDetailJson 대입문 없음'
        for script in soup.find_all('script'):
            value = script.string or script.get_text()
            match = re.search(r'\bitemDetailJson\s*=\s*', value)
            if not match:
                continue
            try:
                candidate = json.JSONDecoder().raw_decode(value[match.end():].lstrip())[0]
                data = candidate['itemDetail']
                main = data['itemMain']
                if not isinstance(main, dict):
                    raise TypeError('itemMain이 객체가 아님')
                parsed = data, main
                break
            except (ValueError, KeyError, TypeError) as error:
                reason = '상품 JSON 구조/해석 오류: ' + str(error)
        if parsed is not None:
            data, main = parsed
            if str(main.get('brandNo')) != BRAND_NO:
                ctx.log('다른 브랜드 제외: ' + final, 'INFO')
                return None
            if str(main.get('modelNo', '')).strip().upper() != code:
                ctx.log('다른 공식 품번 제외: ' + str(main.get('modelNo')) + ': ' + final, 'INFO')
                return None
            query = parse_qs(urlsplit(final).query)
            if str(main.get('itemNo')) != query.get('itemNo', [''])[0]:
                raise CrawlError('조회 상품 ID와 응답 상품 ID 불일치')
            if query.get('lowerVendNo') and str(main.get('lowerVendNo')) != query['lowerVendNo'][0]:
                raise CrawlError('조회 링크와 응답 판매자 ID 불일치')
            if not main.get('itemName'):
                raise CrawlError('상품명 누락: ' + final)
            clean_name(main['itemName'], code)
            ctx.log('품번/판매자 확인: ' + final, 'SUCCESS')
            return data, final
        title = soup.title.get_text(' ', strip=True) if soup.title else '(제목 없음)'
        summary = f'{reason}; 응답 제목={title[:160]}; HTML={len(str(soup))}자; 최종 URL={final}'
        if not attempt:
            ctx.log('상품 JSON을 확인하지 못해 1회 재조회: ' + summary, 'WARNING')
            ctx.checkpoint()
            continue
        diagnostic = ''
        output_dir = getattr(ctx, 'output_dir', None)
        if output_dir is not None:
            item_id = parse_qs(urlsplit(url).query).get('itemNo', ['unknown'])[0]
            safe_id = re.sub(r'[^A-Za-z0-9_-]', '_', item_id)
            try:
                root = Path(output_dir)
                root.mkdir(parents=True, exist_ok=True)
                path = root / ('lanzi_debug_' + safe_id + '.html')
                path.write_text(str(soup), encoding='utf-8')
                diagnostic = '; 진단 HTML=' + str(path)
            except OSError as error:
                diagnostic = '; 진단 HTML 저장 실패=' + str(error)
        raise CrawlError('상품 응답에서 필수 JSON을 확인할 수 없습니다. ' + summary + diagnostic)


def options(ctx, main, selected=None):
    params = {'itemNo': main['itemNo'], 'lowerVendNo': main['lowerVendNo'], 'itemTcode': main['itemTcode']}
    if selected is not None:
        params['itemOptionNoList'] = str(selected)
    raw, final, _ = fetch(ctx, SITE + '/v1/item/option/api?' + urlencode(params))
    if urlsplit(final).hostname != 'www.elandmall.co.kr':
        raise CrawlError('옵션 API가 다른 호스트로 이동')
    try:
        data = json.loads(raw)
        if str(data['resultCode']) != '200':
            raise CrawlError('옵션 API 오류: ' + str(data.get('resultMessage')))
        rows = data['data']['itemOption']
        if not isinstance(rows, list) or not rows:
            raise CrawlError('공식 옵션 후보 없음')
        if any(str(o.get('lowerVendNo')) != str(main['lowerVendNo']) or str(o.get('itemNo')) != str(main['itemNo']) for o in rows):
            raise CrawlError('옵션 판매자/상품 ID 불일치')
        return rows
    except (ValueError, KeyError, TypeError) as e:
        raise CrawlError('옵션 JSON 구조 변경') from e


def color_rows(rows):
    result = []
    for o in rows:
        if str(o.get('itemOptionTcode')) != '10':
            raise CrawlError('첫 옵션이 색상인 상품만 지원: 사이트 옵션 구조 확인 필요')
        label = str(o.get('itemOptionName') or '').strip()
        if not label:
            raise CrawlError('공식 색상명 누락')
        # 공식 옵션의 00 WHITE 형식. DB의 itemOptionNo는 색상코드로 쓰지 않는다.
        m = re.fullmatch(r'([A-Z0-9]{2})\s+(.+)', label)
        result.append({'color_name': m[2] if m else label, 'color_code': m[1] if m else ''})
    return result


def clean_name(name, code):
    for token in ('란찌', 'LANZI', code):
        name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])', '', name, flags=re.I)
    name = re.sub(r'\[\s*\]|\(\s*\)', '', name)
    name = re.sub(r'_[^_]+점$', '', name.strip())
    name = re.sub(r'\s+', ' ', name).strip(' _-[]()')
    if not name:
        raise CrawlError('정리 후 실제 상품명이 없음')
    return name


def image_key(url):
    p = urlsplit(html.unescape(url))
    return (p.hostname or '').lower(), p.path.lower()


STUDIO_POLICY = {
    'min_channel': 210, 'min_average': 230, 'color_tolerance': 12,
    'min_border_match': 0.97, 'min_corner_match': 0.95,
}


def studio_background(image):
    """대표 한 장의 바깥 테두리와 모서리만 밝고 균일한지 판정한다."""
    if 'A' in image.getbands() and image.getchannel('A').getextrema()[0] < 255:
        return False, '투명 배경은 밝은 실제 배경으로 확정할 수 없음'
    image = image.convert('RGB')
    image.thumbnail((400, 400))
    width, height = image.size
    pixels = image.load()
    corner_width, corner_height = max(1, width // 20), max(1, height // 20)
    corners = []
    for left, top in ((0, 0), (width-corner_width, 0), (0, height-corner_height), (width-corner_width, height-corner_height)):
        corners.append([pixels[x, y] for x in range(left, left+corner_width) for y in range(top, top+corner_height)])
    samples = [pixel for corner in corners for pixel in corner]
    background = tuple(statistics.median(pixel[channel] for pixel in samples) for channel in range(3))
    if min(background) < STUDIO_POLICY['min_channel'] or sum(background)/3 < STUDIO_POLICY['min_average']:
        return False, f'밝기 부족: 배경 추정 RGB={background}'
    def matches(pixel):
        return max(abs(pixel[channel]-background[channel]) for channel in range(3)) <= STUDIO_POLICY['color_tolerance']
    ratios = [sum(matches(pixel) for pixel in corner)/len(corner) for corner in corners]
    border = [pixels[x, y] for x in range(width) for y in range(height)
              if x < width*.04 or x >= width*.96 or y < height*.04 or y >= height*.96]
    ratio = sum(matches(pixel) for pixel in border)/len(border)
    if ratio < STUDIO_POLICY['min_border_match'] or min(ratios) < STUDIO_POLICY['min_corner_match']:
        return False, f'배경 불균일: RGB={background}, 테두리 일치율={ratio:.3f}, 모서리={ratios}'
    foreground = sum(not matches(pixel) for pixel in image.getdata())/(width*height)
    if foreground < .01:
        return False, '상품이 없는 단색/공백 이미지 의심'
    return True, f'밝고 균일한 배경 RGB={background}, 테두리 일치율={ratio:.3f}'


def studio_gate(ctx, main, cache):
    representatives = [item for item in main.get('itemImage', []) if item.get('imageDcode') == '010' and item.get('imagePath')]
    if not representatives:
        raise CrawlError('링크 판정용 공식 대표이미지 010 없음: ' + str(main.get('itemNo')))
    url = urljoin('https://item.elandrs.com/', representatives[0]['imagePath'])
    gate_key = ('studio', url)
    if gate_key in cache:
        passed, reason = cache[gate_key]
        ctx.log(f"링크 {'허용' if passed else '제외'} itemNo={main['itemNo']}: 동일 대표사진 판정 재사용: {reason}", 'INFO')
        return passed
    try:
        data, final, content_type = fetch(ctx, url, POLICY['max_bytes'])
    except CrawlError as error:
        ctx.log(f"링크 제외 itemNo={main['itemNo']}: 대표 배경 확인 불가: {error}", 'WARNING')
        return False
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt, width, height = image.format, image.width, image.height
            animated = getattr(image, 'is_animated', False)
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            passed, reason = studio_background(image)
            signal = content_signal(image)
        if fmt not in POLICY['formats'] or animated or min(width, height) < 500 or max(width, height)/min(width, height) > 1.8 or signal:
            passed, reason = False, '대표이미지 형식/규격/개별 컷 기준 탈락: ' + signal
        cache[url] = (fmt, width, height, animated, hashlib.sha256(data).hexdigest(), signal)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise CrawlError('대표이미지 판정 실패: ' + str(error)) from error
    ctx.log(f"링크 {'허용' if passed else '제외'} itemNo={main['itemNo']}, seller={main['lowerVendNo']}: {reason}: {url}", 'SUCCESS' if passed else 'INFO')
    cache[gate_key] = (passed, reason)
    return passed


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


def gallery(ctx, main, cache):
    urls = []
    for item in main.get('itemImage', []):
        ctx.checkpoint()
        if str(item.get('imageDcode')) not in ('010', '030') or not item.get('imagePath'):
            continue
        url = urljoin('https://item.elandrs.com/', item['imagePath'])
        label = ' '.join(str(item.get(k) or '') for k in ('imageName', 'imageFileName'))
        if re.search(
            r'배너|콜라주|사이즈|측정|고시|스와치|로고|banner|collage|measurement|size.?chart|size.?guide|care_', label + ' ' + urlsplit(url).path, re.I):
            ctx.log('갤러리 제외 신호: ' + url, 'WARNING')
            continue
        info = image_info(ctx, url, cache)
        if not info:
            continue
        fmt, w, h, animated, digest, signal = info
        if signal:
            ctx.log('갤러리 제외: ' + signal + ': ' + url, 'WARNING')
            continue
        if fmt not in POLICY['formats'] or animated or min(w, h) < 500 or max(w, h) / min(w, h) > 1.8:
            ctx.log('갤러리 형식/픽셀/애니메이션/비율 기준 탈락: ' + url, 'WARNING')
            continue
        urls.append((url, digest))
    return urls


def detail(ctx, d, code, cache):
    desc = d.get('itemDesc', {})
    fragment = BeautifulSoup(html.unescape(desc.get('itemDescdocumentCon') or ''), 'html.parser')
    parts = []
    # Winstudio의 해당 품번 상품 이미지와 공식 SIZE 자료만 허용한다.
    # 공지/프로모션 itemAnnounceList 및 배송/케어 영역은 순회하지 않는다.
    # 사용자 요청: 이미지 안의 고시/실측 정보를 유지하며 별도 표는 만들지 않는다.
    for img in fragment.find_all('img'):
        ctx.checkpoint()
        url = urljoin(SITE, img.get('src') or '')
        p = urlsplit(url)
        filename = p.path.rsplit('/', 1)[-1]
        official = p.hostname == 'img-studio.elandmall.com' and p.path.startswith('/Winstudio/')
        product_photo = bool(re.fullmatch(re.escape(code) + r'(?:[_-][A-Za-z0-9]+)?\.(?:jpg|jpeg|png|webp)', filename, re.I))
        size_guide = bool(re.fullmatch(r'(?:LA|MA)[A-Za-z0-9_-]*SIZE[A-Za-z0-9_-]*\.(?:jpg|jpeg|png|webp)', filename, re.I))
        cdn_product = False  # UUID만으로 셀러 배너와 상품 설명을 구분할 수 없어 상세에서 제외
        negative = re.search(r'배송|교환|반품|쿠폰|혜택|케어|세탁|banner|coupon|shipping|care_', filename + ' ' + str(img.get('alt') or ''), re.I)
        if negative or not ((official and (product_photo or size_guide)) or cdn_product):
            ctx.log('지원 범위 밖 상세 이미지 생략: ' + url, 'WARNING')
            continue
        if p.scheme == 'http':
            url = 'https:' + url[5:]
        if image_info(ctx, url, cache):
            parts.append(('size' if size_guide else 'photo', '<img src="' + html.escape(url, quote=True) + '">'))
        else:
            raise CrawlError('선택한 상세 이미지 공개 접근/해독 실패: ' + url)
    if not parts:
        raise CrawlError('공식 상세에서 사용할 수 있는 상품 이미지가 없습니다.')
    if not any(kind == 'size' for kind, _ in parts):
        ctx.log('별도 사이즈 이미지 없음: 상품 상세 이미지 내부 정보는 원본 그대로 유지', 'INFO')
    return parts


def crawl(ctx):
    ctx.log('란찌 크롤러 버전 ' + CRAWLER_REVISION, 'INFO')
    code = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*', code):
        ctx.fail_item('색상 접미사 없는 기본 품번을 입력하세요.')
        raise CrawlError('기본 품번 형식 오류')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        ctx.fail_item('수동 매핑은 대표 또는 추가이미지 선택이 필요합니다.')
        raise CrawlError('매핑용 이미지 수집 미선택')
    errors, products, colors, candidates, sizes, prices, categories = [], [], [], [], [], [], []
    cache, pieces = {}, []
    def failed(message):
        errors.append(message)
        ctx.fail_item(message)
    try:
        links = search(ctx, code)
        if not links:
            raise CrawlError('이랜드몰 품번 검색 결과가 0건입니다: ' + code + '. 입력 품번을 확인하세요. 대표이미지 배경 판정 전 단계입니다.')
    except CrawlError as e:
        failed(str(e))
        raise
    for url in links:
        ctx.checkpoint()
        ctx.stage('공식 품번/판매자 및 옵션 확인')
        try:
            found = product(ctx, url, code)
            if found is None:
                continue
            d, final = found
            main = d['itemMain']
            if not studio_gate(ctx, main, cache):
                continue
            opts = options(ctx, main)
            cs = color_rows(opts)
            products.append((main, final))
            for c in cs:
                if c not in colors:
                    colors.append(c)
            candidates.extend(gallery(ctx, main, cache))
            if ctx.enabled('category'):
                categories.append(str(main.get('depth4StandardCategoryName') or ''))
            if ctx.enabled('price') or ctx.enabled('size'):
                for opt in opts:
                    ctx.checkpoint()
                    child = options(ctx, main, opt['itemOptionNo'])
                    if any(str(o.get('itemOptionTcode')) != '20' for o in child):
                        raise CrawlError('2단계 색상/사이즈 외 옵션 구조는 미지원')
                    if ctx.enabled('size'):
                        names = list(dict.fromkeys(str(o.get('itemOptionName') or '').strip() for o in child))
                        if not names or not all(names):
                            raise CrawlError('사이즈 옵션명 누락')
                        sizes.append(names)
                    if ctx.enabled('price'):
                        values = [o.get('sellPrice') for o in child]
                        if any(not isinstance(v, (int, float)) or v < 0 for v in values):
                            raise CrawlError('사이즈별 판매가 누락')
                        prices.extend(values)
            if ctx.enabled('detail_page'):
                pieces.extend(detail(ctx, d, code, cache))
        except CrawlError as e:
            failed(str(e) + ': ' + url)
    if not products or not colors:
        failed('정확한 품번과 밝고 균일한 대표 배경 조건을 만족하는 란찌 상품/색상 후보 없음')
    unique, hashes = [], set()
    for url, digest in candidates:
        ctx.checkpoint()
        if digest not in hashes:
            hashes.add(digest)
            unique.append(url)
    if not unique:
        failed('통과한 매핑용 개별 상품 사진이 0장입니다.')
    if products and colors and unique:
        main, final = products[0]
        record = {'product_code': code, 'product_name': clean_name(main['itemName'], code), 'product_url': final}
        regular_records = []
        for product_main, _ in products:
            value = {}
            collect_regular_price(ctx, value, product_main, 'eland')
            regular_records.append(value)
        merge_regular_prices(ctx, record, regular_records)
        if ctx.enabled('size'):
            same = bool(sizes) and all(v == sizes[0] for v in sizes)
            record['size'] = ','.join(sizes[0]) if same else ''
            if not same:
                ctx.log('색상/상품 링크별 사이즈가 달라 공통 사이즈는 빈 값', 'WARNING')
        if ctx.enabled('price'):
            record['price'] = prices[0] if prices else None
            if len(set(prices)) > 1:
                ctx.log(f'판매가 차이: 첫 지정 상품/옵션 판매가 {prices[0]}원 선택. 확인한 가격={sorted(set(prices))}. 가격 차이는 성공 처리.', 'WARNING')
        if ctx.enabled('category'):
            record['category'] = categories[0] if categories and len(set(categories)) == 1 else ''
        if ctx.enabled('detail_page'):
            try:
                body = list(dict.fromkeys(markup for _, markup in pieces))
                markup = ''.join(body)
                if not markup:
                    raise CrawlError('선택한 상세 이미지 수집 미완료')
                if len(markup.encode('utf-16-le')) // 2 > 32767:
                    raise CrawlError('상세 HTML이 Excel 셀 32767자 한도 초과')
                record['detail_page'] = markup
            except CrawlError as e:
                failed(str(e))
        ctx.stage(f'수동 매핑 후보 {len(unique)}장 전달')
        for url in unique:
            ctx.checkpoint()
            ctx.emit_unmapped(dict(record), colors, url, shared_detail_html=record.get('detail_page', ''))
    if errors:
        raise CrawlError(' / '.join(dict.fromkeys(errors)))
