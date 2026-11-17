"""TheAmall / PLASTIC ISLAND, portal_v1 manual colour mapping adapter."""
PORTAL_API_VERSION = 1
import io
import json
import re
import hashlib
from html import escape
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from bs4 import BeautifulSoup
from PIL import Image

SITE = 'https://www.theamall.com'
CDN = 'https://mall.theacdn.com'
REVISION = '2026-11-17-v3'
BAD = re.compile(r'세탁|취급|드라이|wash|laundry|care', re.I)


class CrawlError(Exception):
    pass


def fetch(ctx, url, form=None, limit=12_000_000):
    ctx.checkpoint()
    if urlsplit(url).hostname not in ('www.theamall.com', 'mall.theacdn.com'):
        raise CrawlError('공식 도메인이 아닌 주소: ' + url)
    request = Request(url, data=urlencode(form).encode() if form is not None else None,
                      headers={'User-Agent': 'Mozilla/5.0', 'Referer': SITE + '/'})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response:
                if urlsplit(response.geturl()).hostname not in ('www.theamall.com', 'mall.theacdn.com'):
                    raise CrawlError('공식몰 외부 이동')
                data = response.read(limit + 1)
            break
        except HTTPError as exc:
            if exc.code not in (500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        ctx.checkpoint()
        ctx.log('일시 통신 실패 재시도: ' + url, 'WARNING')
    ctx.checkpoint()
    if len(data) > limit:
        raise CrawlError('응답 용량 초과: ' + url)
    return data


def soup(data):
    return BeautifulSoup(data, 'html.parser')


def text(node):
    return ' '.join(node.stripped_strings) if node else ''


def model_from(data):
    source = data.decode('utf-8-sig') if isinstance(data, bytes) else data
    match = re.search(r'\bvar\s+model\s*=\s*', source)
    if not match:
        raise CrawlError('공식 상품 데이터 없음/구조 변경')
    model, _ = json.JSONDecoder().raw_decode(source[match.end():])
    item = model.get('contentsmap', {})
    if item.get('BRANDIDX') != 3 or item.get('BRANDNAME') != 'PLASTIC ISLAND':
        raise CrawlError('플라스틱 아일랜드 소속 확인 실패')
    return model


def clean_name(value, code):
    value = re.sub(r'\[[^\]]*\]', '', value)
    value = re.sub(r'(?<![A-Z0-9])' + re.escape(code) + r'(?![A-Z0-9])', '', value, flags=re.I)
    value = re.sub(r'\s+', ' ', value).strip(' -_/')
    if not value:
        raise CrawlError('상품명 없음')
    return value


def discover(ctx, code):
    page = fetch(ctx, SITE + '/goods/search?' + urlencode({'cate': 1, 'sword': code}))
    doc = soup(page)
    ids = []
    for node in doc.select('a[href]'):
        link = urlsplit(urljoin(SITE, node['href']))
        gno = parse_qs(link.query).get('gno', [''])[0]
        if link.hostname == 'www.theamall.com' and link.path == '/goods/detail' and gno.isdigit() and gno not in ids:
            ids.append(gno)
    match = re.search(r"let totalPage = '(\d+)'", page.decode('utf-8-sig'))
    if not match:
        raise CrawlError('검색 페이지 수 확인 실패')
    count = int(match[1])
    if count > 30:
        raise CrawlError('검색 30페이지 한도 초과')
    for number in range(2, count + 1):
        result = json.loads(fetch(ctx, SITE + '/goods/search_goods_ajax', dict(
            spage=number, psize=24, cate=1, addMonth='2 MONTH', psort='sort_asc',
            filterType='', list='', scate=0, sword=code)))
        for item in result.get('data', []):
            gno = str(item.get('NO', ''))
            if gno.isdigit() and gno not in ids:
                ids.append(gno)
    return ids


def option_call(ctx, gno, **kwargs):
    result = json.loads(fetch(ctx, SITE + '/common/ajax/optionItem_ajax',
                             dict(gno=gno, closeGoods=0, eventCode='', **kwargs)))
    if not isinstance(result.get('list'), list):
        raise CrawlError('공식 옵션 응답 구조 변경')
    return result['list']


def option_label(item):
    # The field after optionname is the actual option value. stockCode=101 is
    # an internal option key, not an official colour abbreviation.
    parts = str(item.get('value', '')).split(':')
    if len(parts) < 4:
        raise CrawlError('공식 옵션 값 해석 실패')
    return parts[3].strip()


def color_value(label):
    # Only explicit two-letter values or parenthesized codes; never BLACK -> BL.
    match = re.fullmatch(r'(.+?)\s*\(([A-Za-z]{2})\)', label)
    if match:
        return dict(color_name=match[1].strip(), color_code=match[2].upper())
    match = re.fullmatch(r'([A-Za-z]{2})\s*\((.+)\)', label)
    if match:
        return dict(color_name=match[2].strip(), color_code=match[1].upper())
    return dict(color_name=label, color_code=label.upper() if re.fullmatch('[A-Za-z]{2}', label) else '')


def palette(ctx, model, need_sizes, need_prices):
    gno = model['var']['goodsNo']
    options = model.get('goodsOptionList', [])
    if [o.get('optionname') for o in options] != ['색상', '사이즈']:
        raise CrawlError('색상/사이즈 이외 옵션 구조: 수집 중단')
    expected = options[0]['optionitems'].split(',')
    colors, prices = [], []
    rows = option_call(ctx, gno)
    if {option_label(row) for row in rows} != set(expected):
        raise CrawlError('공식 색상 목록과 옵션 응답 불일치(품절 누락 가능)')
    for row in rows:
        color = color_value(option_label(row))
        if need_sizes or need_prices:
            children = option_call(ctx, gno, coption=row['value'],
                                   prePrice=row['price'], stockCode=row['stockCode'])
            if not children:
                raise CrawlError('색상별 사이즈 응답 없음: ' + color['color_name'])
            if need_sizes:
                color['size'] = ','.join(dict.fromkeys(option_label(child) for child in children))
            for child in children:
                amount = child.get('price')
                if not isinstance(amount, (int, float)) or amount <= 0:
                    raise CrawlError('옵션 판매가 확인 실패')
                prices.append(amount)
        colors.append(color)
    return colors, prices


def table(headers, rows):
    return ('<table border="1" cellpadding="8" style="width:100%;border-collapse:collapse">'
            '<thead><tr>' + ''.join('<th>' + escape(str(v)) + '</th>' for v in headers)
            + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(str(v)) + '</td>'
            for v in row) + '</tr>' for row in rows) + '</tbody></table>')


def image_tag(url):
    return '<p><img src="' + escape(url, quote=True) + '" style="max-width:100%;height:auto" /></p>'


def detail(ctx, model, doc, code):
    item = model['contentsmap']
    parts = ['<div>']
    content = soup(item.get('CONTENT', ''))
    seen = set()
    for node in content.select('img[src]'):
        url = urljoin(CDN, node['src'].strip())
        path = urlsplit(url).path
        # CONTENT belongs to the independently verified product. Reissued items
        # can officially reuse an older model's /external/pla/ photo directory.
        # Do not require that directory to match the current GOODSCODE.
        product_path = re.search(r'/(?:data/)?external/pla/[^/]+/[^/]+/', path, re.I)
        editor_path = re.fullmatch(r'/data/editor/\d{6}/[^/]+\.(?:jpe?g|png|webp)', path, re.I)
        if not (product_path or editor_path) or BAD.search(path + ' ' + node.get('alt', '')):
            continue
        if re.search(r'(?:size|notice|notify)[_-]', path, re.I):
            ctx.log('표 재구성과 중복되는 상세 이미지 제외: ' + url, 'INFO')
            continue
        if urlsplit(url).hostname not in ('mall.theacdn.com', 'www.theamall.com'):
            continue
        if url not in seen:
            parts.append(image_tag(url))
            seen.add(url)
    if not seen:
        raise CrawlError('공식 상품 CONTENT에서 지원하는 상세 이미지 없음/구조 변경')
    source_sizes = model.get('goodsSizeList', [])
    # Accessories can have wash-only placeholders: FF, SIZE1..8=0,
    # SIZEGROUP='', and no measurement titles. Those are not a size chart.
    sizes = [row for row in source_sizes
             if row.get('SIZETITLENAMELIST') or row.get('TITLENAME', '').strip()]
    if source_sizes and not sizes:
        ctx.log('공식 실측 항목명 없음: 실측표 생략, 구매 사이즈 옵션과 상품고시는 유지', 'WARNING')
    area = doc.select_one('.js-detailer-size-area')
    if sizes:
        parts.append('<h3>실측사이즈</h3>')
        # The actual DOM image is used; do not fabricate a SIZEGROUP URL.
        if area:
            for node in area.select('img[src]'):
                if node['src'].startswith('/img/sizeChart/'):
                    parts.append(image_tag(urljoin(SITE, node['src'])))
        headers = sizes[0].get('SIZETITLENAMELIST') or sizes[0].get('TITLENAME', '').split(',')
        if not headers or not all(headers):
            raise CrawlError('실측 항목명 누락')
        rows = []
        for size in sizes:
            current = size.get('SIZETITLENAMELIST') or size.get('TITLENAME', '').split(',')
            if current != headers:
                raise CrawlError('사이즈별 실측 열 불일치')
            rows.append([size['SIZETITLE']] + [size.get('SIZE' + str(i), '') for i in range(1, len(headers) + 1)])
        parts.append(table(['사이즈(cm)'] + headers, rows))
    if area:
        for node in area.select('p'):
            value = text(node)
            if value and not BAD.search(value):
                parts.append('<p>' + escape(value) + '</p>')
        for extra in area.select('table')[1:]:
            headers = [text(n) for n in extra.select('tr:first-child th')]
            rows = [[text(n) for n in row.select('td')] for row in extra.select('tr') if row.select('td')]
            if headers and rows and not BAD.search(text(extra)):
                parts.extend(['<h3>추가정보</h3>', table(headers, rows)])
    notices = [(n.get('notifyname', ''), text(soup(n.get('notifydata') or '')))
               for n in model.get('goodsNotifyList', []) if not BAD.search(n.get('notifyname', ''))]
    if notices:
        parts.extend(['<h3>상품정보고시</h3>', table(['항목', '내용'], notices)])
    parts.append('</div>')
    markup = ''.join(parts)
    if len(markup.encode('utf-16-le')) // 2 > 32767:
        raise CrawlError('상세 HTML 엑셀 셀 한도 초과')
    return markup


def gallery(ctx, model):
    item = model['contentsmap']
    urls = [CDN + '/data/goods/' + item['IMGB']]
    urls += [CDN + node['imgpath'] + node['imgfname'] for node in model.get('goodsImageList', [])]
    result, hashes = [], set()
    for url in dict.fromkeys(urls):
        try:
            data = fetch(ctx, url, limit=10_000_000)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ('JPEG', 'PNG', 'WEBP') or getattr(image, 'is_animated', False):
                    raise CrawlError('지원하지 않는 이미지 형식')
                width, height = image.size
                if min(width, height) < 500 or max(width, height) / min(width, height) > 1.8:
                    raise CrawlError('갤러리 크기/비율 기준 미달')
                image.verify()
            digest = hashlib.sha256(data).hexdigest()
            if digest not in hashes:
                hashes.add(digest)
                result.append(url)
        except Exception as exc:
            ctx.log('갤러리 제외: ' + url + ': ' + str(exc), 'WARNING')
    if not result:
        raise CrawlError('유효 개별 상품 사진 없음')
    return result


def _crawl(ctx):
    code = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z]{2}\d[A-Z]{2}\d{3}', code):
        raise CrawlError('기본 품번 8자리 입력 예: PY6WZ062')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        raise CrawlError('수동 매핑에는 이미지 항목 선택이 필요합니다')
    ctx.stage('플라스틱 아일랜드 공식 품번 검색')
    matches = []
    product_ids = discover(ctx, code)
    if not product_ids:
        raise CrawlError('공홈 검색 결과 0건: ' + code + ' (현재 공개 검색에서 상품을 찾을 수 없습니다)')
    for gno in product_ids:
        url = SITE + '/goods/detail?' + urlencode({'gno': gno, 'cate': 164})
        data = fetch(ctx, url)
        try:
            model = model_from(data)
        except CrawlError:
            continue
        if model['contentsmap'].get('GOODSCODE', '').upper() == code:
            matches.append((url, model, soup(data)))
    if len(matches) != 1:
        raise CrawlError('정확한 공식 품번 상품이 1개가 아님: ' + code + ' (' + str(len(matches)) + '개)')
    url, model, doc = matches[0]
    ctx.stage(code + ' 옵션/상세/사진 수집')
    item = model['contentsmap']
    colors, prices = palette(ctx, model, ctx.enabled('size'), ctx.enabled('price'))
    record = dict(product_code=code, product_name=clean_name(item['GOODSNAME'], code), product_url=url)
    if ctx.enabled('size'):
        values = {c['size'] for c in colors}
        if len(values) == 1:
            record['size'] = next(iter(values))
        else:
            ctx.log('색상별 사이즈가 달라 매핑 후보별 사이즈만 전달합니다', 'INFO')
    if ctx.enabled('price'):
        if len(set(prices)) == 1:
            record['price'] = prices[0]
        else:
            ctx.log('색상/사이즈별 판매가 상이: 공통 판매가 빈칸', 'WARNING')
    if ctx.enabled('regular_price'):
        value = model.get('goodsPrice', {}).get('RETAILPRICE')
        if isinstance(value, (int, float)) and value > 0:
            record['regular_price'] = value
    if ctx.enabled('category'):
        # Brand navigation is not a product category.
        ctx.log('상품 분류의 독립 필드 미확인: 카테고리 빈칸', 'WARNING')
    if ctx.enabled('detail_page'):
        record['detail_page'] = detail(ctx, model, doc, code)
    images = gallery(ctx, model)
    for url in images:
        ctx.checkpoint()
        ctx.emit_unmapped(dict(record), [dict(c) for c in colors], url,
                          shared_detail_html=record.get('detail_page', ''))
    ctx.log(f'{code}: 색상 {len(colors)}개, 사진 {len(images)}장 / 수동 매핑', 'SUCCESS')


def crawl(ctx):
    try:
        _crawl(ctx)
    except Exception as exc:
        # Send a structured failure so the queue shows the actual cause rather
        # than only the process exit code. The original exception still fails.
        ctx.fail_item(str(exc))
        raise
