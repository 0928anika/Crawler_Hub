PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

import hashlib
import html
import io
import json
import re
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import (
    check_public_image, clean_product_name, gallery_slots,
    resolve_input, validate_gallery_image,
)

SITE = 'https://www.lynxmall.co.kr'
POLICY = {
    'checked': '2026-11-05',
    'source': 'https://marketplace.coupang.com/mba/listing',
    'route': 'WING 웹/엑셀',
    'min_side': 500, 'max_bytes': 10000000, 'max_aspect': 1.8,
    'formats': ['JPEG', 'PNG', 'WEBP'],
    'note': '정지 WebP는 사용자 등록 환경 정책. 비율 1.8은 프로젝트 정책.',
}
# 실제 확인: L21F3JP152_IV/BK 상세, 검색, 갤러리 및 긴 이미지, 고시 9행.
# L11F3PT301_KH/MG의 공식 HTML로 색상:사이즈 통합 옵션도 검증.
# 상품명 기준 추출: L21F3TI351_BK/WH 포함. MPN/옵션 색상 비교 제거.
# Hub 실제 실행/최종 Excel/WING 등록 및 실제 품절 상품은 미검증.


def text(node):
    return node.get_text(' ', strip=True) if node else ''


def fetch(ctx, url):
    for attempt in range(2):
        ctx.checkpoint()
        try:
            request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with build_opener().open(request, timeout=20) as response:
                final = response.geturl()
                if urlsplit(final).hostname != 'www.lynxmall.co.kr':
                    raise RuntimeError('지정 공식몰 밖으로 이동: ' + final)
                data = response.read(4000001)
                if len(data) > 4000000:
                    raise RuntimeError('HTML 응답 크기 제한 초과')
            soup = BeautifulSoup(data, 'html.parser')
            if soup.select_one('input[type="password"]') and not soup.select_one('.goodsList, .tit-prd'):
                raise RuntimeError('인증 화면 응답')
            return soup, final
        except Exception as error:
            if attempt:
                raise RuntimeError(f'페이지 조회 실패: {url}: {error}') from error
            ctx.log(f'페이지 재시도: {url}: {error}', 'WARNING')
        finally:
            ctx.checkpoint()


def find_links(ctx, query):
    links = []
    page = 1
    while True:
        soup, _ = fetch(ctx, SITE + '/shop/shopbrand.html?' + urlencode({'search': query, 'page': page}))
        for a in soup.select('.goodsList a[href]'):
            url = urljoin(SITE, a['href'])
            parsed = urlsplit(url)
            if parsed.hostname == 'www.lynxmall.co.kr' and parsed.path == '/shop/shopdetail.html' and parse_qs(parsed.query).get('branduid'):
                if url not in links:
                    links.append(url)
        pages = [int(n) for n in re.findall(r'pagemove\((\d+)\)', str(soup.select_one('.paging') or ''))]
        if page >= max(pages, default=1):
            return links
        page += 1
        if page > 30:
            raise RuntimeError('검색 30페이지 초과: 누락 방지를 위해 실패 처리')


def product_data(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        data = json.loads(script.string or script.get_text())
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get('@type') == 'Product':
                return item
    raise RuntimeError('공식 Product 구조화 데이터 없음')


def option_values(select):
    return [
        (o.get('title') or text(o)).strip()
        for o in select.select('option[value]')
        if o.get('value') != ''
    ]


def combined_options(soup):
    # L11F3PT301은 화면 label="옵션"이며 공식 JS의 opt_name이 색상:사이즈다.
    # opt_id로 JS 정의와 실제 select를 연결한다. 임의 콜론 옵션을 추측하지 않는다.
    combined_ids = set()
    for script in soup.find_all('script'):
        source = script.string or script.get_text()
        if 'optionJsonData' not in source:
            continue
        for block in re.findall(r'\{([^{}]+)\}', source):
            opt_id = re.search(r"\bopt_id\s*:\s*'([^']+)'", block)
            opt_name = re.search(r"\bopt_name\s*:\s*'([^']+)'", block)
            if opt_id and opt_name and re.sub(r'\s+', '', opt_name[1]) == '색상:사이즈':
                combined_ids.add(opt_id[1])
    for select in soup.select('select[label]'):
        if re.sub(r'\s+', '', select['label']) != '색상:사이즈' and select.get('opt_id') not in combined_ids:
            continue
        for value in option_values(select):
            parts = value.split(':')
            if len(parts) != 2 or not all(p.strip() for p in parts):
                raise RuntimeError('색상:사이즈 통합 옵션 형식 오류: ' + value)
            yield parts[0].strip().upper(), parts[1].strip()


def product_sizes(soup):
    sizes = []
    for select in soup.select('select[label="사이즈"]'):
        sizes.extend(option_values(select))
    sizes.extend(size for _, size in combined_options(soup))
    return list(dict.fromkeys(sizes))


def identity(soup):
    # 품번과 색상은 공개 상품명만 사용한다. MPN/옵션명과 대조하지 않는다.
    title = text(soup.select_one('.detailRightBox .tit-prd'))
    if not title:
        raise RuntimeError('공식 상품명 영역 누락')
    matches = re.findall(
        r'(?<![A-Z0-9_-])([A-Z0-9]+(?:-[A-Z0-9]+)*)_([A-Z0-9]+)(?![A-Z0-9_-])',
        title.upper()
    )
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1:
        raise RuntimeError('상품명에서 단일 품번_색상코드를 확인할 수 없음: ' + title)
    base, color = matches[0]
    return base, color, {'name': title, 'title_code': base + '_' + color}


def notice_rows(soup):
    section = soup.select_one('.prd-detail #productWrap')
    if section is None:
        return []
    rows = []
    for tr in section.select('table tr'):
        cells = tr.find_all(['th', 'td'], recursive=False)
        if len(cells) != 2:
            raise RuntimeError('상품고시 행 구조 변경: 정확한 항목/값 대응 불가')
        label, value = (text(c) for c in cells)
        if not label:
            raise RuntimeError('상품고시 항목명 누락')
        rows.append((label, value))
    if not rows or not any(v for _, v in rows):
        raise RuntimeError('원본 상품고시 영역은 있으나 항목/값 추출 실패')
    return rows


def table_html(rows):
    if not rows:
        return ''
    body = ''.join('<tr><th scope="row">' + html.escape(k) + '</th><td>' + html.escape(v) + '</td></tr>' for k, v in rows)
    return '<h3>상품정보고시</h3><table border="1" cellpadding="8" cellspacing="0" width="100%"><tbody>' + body + '</tbody></table>'


def image_data(ctx, url, cache):
    if url not in cache:
        ctx.checkpoint()
        cache[url] = check_public_image(url, timeout=15, max_bytes=POLICY['max_bytes'], retries=1, checkpoint=ctx.checkpoint)
        ctx.checkpoint()
    result = cache[url]
    for warning in result.get('warnings', []):
        ctx.log(url + ': ' + warning, 'WARNING')
    if result.get('status') == 'unknown':
        raise RuntimeError('이미지 공개 접근 확인 미완료: ' + url + ': ' + '; '.join(result.get('reasons', [])))
    if result.get('status') != 'accessible':
        ctx.log('이미지 제외: ' + url + ': ' + '; '.join(result.get('reasons', [])), 'WARNING')
        return None
    return result['data']


def detail_html(ctx, soup, product, cache):
    section = soup.select_one('.prd-detail')
    if section is None:
        raise RuntimeError('상세 본문 .prd-detail 없음: 사이트 구조 확인 필요')
    title_code = product['title_code']
    images = []
    # URL을 조립하지 않는다. 실제 본문의 상품명 품번_색상코드 파일만 선택한다.
    # brandtop/notice/wash 등 공통 이미지는 다운로드/공개 검사도 하지 않는다.
    for image in section.select('img[src]'):
        ctx.checkpoint()
        url = urljoin(SITE, image['src'])
        path = urlsplit(url)
        filename = unquote(path.path.rsplit('/', 1)[-1])
        if path.hostname != 'lynx.speedgabia.com' or path.path.rsplit('/', 1)[0] != '/LYNX':
            continue
        if filename.rsplit('.', 1)[0].upper() != title_code or url in images:
            continue
        raw = image_data(ctx, url, cache)
        if raw is None:
            continue
        with Image.open(io.BytesIO(raw)) as im:
            width, height = im.size
            im.verify()
        if height <= width * 1.8:
            ctx.log('상세용 긴 이미지가 아니므로 제외: ' + url, 'WARNING')
            continue
        images.append(url)
        ctx.log(f'상세 긴 이미지: {width}x{height}: {url}', 'INFO')
    if not images:
        ctx.log('품번이 일치하는 공개 긴 상세 이미지 없음', 'WARNING')
    rows = notice_rows(soup)
    if not rows:
        ctx.log('상품정보고시 없음', 'WARNING')
    # 사이즈표/측정 그림은 긴 원본 안에 있으므로 별도 추출·요청하지 않는다.
    result = '<div>' + ''.join('<p><img src="' + html.escape(url, quote=True) + '" alt="상품 상세" style="max-width:100%;height:auto"></p>' for url in images) + table_html(rows) + '</div>'
    if len(result) > 32767:
        raise RuntimeError('상세 HTML이 Excel 셀 한도 초과: 임의 절단하지 않음')
    return result


def gallery(ctx, soup, base, color, cache):
    candidates, hashes = [], set()
    for image in soup.select('.thumb-info .thumb-wrap img.detail_image[src]'):
        ctx.checkpoint()
        url = urljoin(SITE, image['src'])
        role_text = ' '.join([url, image.get('alt', ''), image.get('title', '')])
        if re.search(r'collage|banner|notice|brandtop|wash|size|measure|사이즈|측정|고시|배너|콜라주', role_text, re.I):
            ctx.log('갤러리 제외 역할 신호: ' + url, 'WARNING')
            continue
        raw = image_data(ctx, url, cache)
        if raw is None:
            continue
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            width, height = im.size
            animated = getattr(im, 'is_animated', False)
            digest = hashlib.sha256(str(im.size).encode() + im.convert('RGB').tobytes()).hexdigest()
            fmt = im.format
        ctx.log(f'갤러리 실제 형식={fmt}, {width}x{height}, bytes={len(raw)}, animated={animated}: {url}', 'INFO')
        if digest in hashes:
            continue
        hashes.add(digest)
        role_ok = urlsplit(url).hostname == 'www.lynxmall.co.kr' and urlsplit(url).path.startswith('/shopimages/lynxgnc/') and image.get('alt') == '상품이미지'
        content = f'공식 색상별 상품 썸네일 영역, alt=상품이미지, 부정 역할 신호 없음, {width}x{height}'
        validation = validate_gallery_image(raw, role='product_shot', mapping_verified=True,
            single_shot_verified=role_ok and not animated and min(width, height) >= 500 and max(width, height) / min(width, height) <= 1.8,
            mapping_evidence=f'공식 상품명 품번_색상코드={base}_{color}, 동일 색상별 상품 페이지 갤러리',
            content_evidence=content, evidence=content,
            min_side=500, max_bytes=10000000, max_aspect=1.8)
        if not validation.get('passed'):
            ctx.log('갤러리 제외: ' + url + ': ' + '; '.join(validation.get('reasons', [])), 'WARNING')
        candidates.append({'url': url, 'validation': validation})
    main, extras, warning = gallery_slots(candidates)
    if warning:
        ctx.log(str(warning), 'WARNING')
    return main or '', ','.join(extras)


def selected_price(ctx, soup, product):
    offer = product.get('offers')
    if not isinstance(offer, dict) or offer.get('priceCurrency') != 'KRW':
        ctx.log('원화 단일 판매가를 확인할 수 없음', 'WARNING')
        return None
    prices = [o.get('price', '0') for o in soup.select('select.basic_option option[value]') if o.get('value') != '']
    if any(float(p) != 0 for p in prices):
        ctx.log('옵션별 추가금 존재: 단일 판매가로 표현하지 않음', 'WARNING')
        return None
    value = offer.get('price')
    return float(value) if value is not None else None


def crawl(ctx):
    value = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]+(?:[_-][A-Z0-9]+)*', value):
        message = '품번 입력 형식 오류: ' + value
        ctx.fail_item(message)
        raise RuntimeError(message)
    ctx.stage('공식 품번 검색 및 색상 확인')
    failures, pages, seen = [], [], set()
    try:
        queries = [value]
        if '_' in value:
            queries = [value.rsplit('_', 1)[0]]
        elif '-' in value:
            queries.append(value.rsplit('-', 1)[0])
        for query in dict.fromkeys(queries):
            for link in find_links(ctx, query):
                ctx.checkpoint()
                uid = parse_qs(urlsplit(link).query)['branduid'][0]
                if uid in seen:
                    continue
                seen.add(uid)
                try:
                    soup, final = fetch(ctx, link)
                    base, color, product = identity(soup)
                except Exception as error:
                    message = f'검색 상품 확인 실패: {link}: {error}'
                    ctx.fail_item(message)
                    failures.append(message)
                    continue
                if value not in (base, base + '_' + color, base + '-' + color):
                    continue
                pages.append((soup, final, base, color, product))
        if not pages:
            raise RuntimeError('정확히 일치하는 공식 품번/색상 상품 없음: ' + value)
        base, requested = resolve_input(value, [(p[2], p[3]) for p in pages], has_color_codes=True, hyphen_alias=True)
    except Exception as error:
        ctx.fail_item(str(error))
        raise
    for soup, final, found_base, color, product in pages:
        if found_base != base or (requested and color != requested):
            continue
        ctx.checkpoint()
        try:
            ctx.stage('색상 수집: ' + color)
            name = clean_product_name(product['name'], brand_aliases=['링스골프', '링스 골프', '링스', 'LYNX'], product_codes=[product['title_code'], base])
            if not name:
                raise RuntimeError('상품명 누락')
            record = dict(product_code=base, product_name=name, color=color,
                color_code=color, color_name='', product_url=final)
            if ctx.enabled('size'):
                sizes = product_sizes(soup)
                record['size'] = ','.join(sizes)
                if not sizes:
                    ctx.log('공식 사이즈 옵션 없음', 'WARNING')
            if ctx.enabled('price'):
                record['price'] = selected_price(ctx, soup, product_data(soup))
            if ctx.enabled('category'):
                record['category'] = str(product_data(soup).get('category') or '')
            cache = {}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main, extra = gallery(ctx, soup, base, color, cache)
                if ctx.enabled('main_image'):
                    record['main_image'] = main
                if ctx.enabled('additional_images'):
                    record['additional_images'] = extra
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail_html(ctx, soup, product, cache)
            collect_regular_price(ctx, record, soup, 'lynx')
            ctx.emit_result(record)
        except Exception as error:
            message = f'{base}_{color}: {error}'
            ctx.fail_item(message)
            failures.append(message)
    if failures:
        raise RuntimeError('; '.join(failures))
