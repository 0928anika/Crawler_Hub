PORTAL_API_VERSION = 1

import hashlib
import html
import json
import re
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, NavigableString
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots, clean_product_name

SITE = 'https://www.goodwearmall.com'
IMAGE_SITE = 'https://img.goodwearmall.com'


def fetch(ctx, path, params=None):
    url = urljoin(SITE, path)
    if params:
        url += '?' + urlencode(params)
    if urlsplit(url).hostname != 'www.goodwearmall.com':
        raise ValueError('굿웨어몰 공식 주소 확인 실패')
    for attempt in range(2):
        ctx.checkpoint()
        try:
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
                final = response.geturl()
                if urlsplit(final).hostname != 'www.goodwearmall.com':
                    raise ValueError('공식몰 외부로 이동했습니다.')
                data = response.read(6000001)
                if len(data) > 6000000:
                    raise ValueError('공식 응답 크기 초과')
                return data.decode(response.headers.get_content_charset() or 'utf-8-sig'), final
        except OSError:
            if attempt:
                raise


def api(ctx, path, params):
    source, _ = fetch(ctx, path, params)
    return json.loads(source)


def search(ctx, base):
    result = []
    page = 1
    while page <= 100:
        data = api(ctx, '/search/api/getTotalSearch.json', {'searchWord': base, 'pageNoGod': page})
        if not isinstance(data.get('godList'), list):
            raise ValueError('공식 검색 응답 형식 확인 실패')
        for row in data['godList']:
            sku = row.get('GOD_NO')
            if row.get('BRND_ID') == 'PBR' and sku and sku not in result:
                result.append(sku)
        paging = data.get('godPaging') or {}
        if page >= int(paging.get('totalPage') or 1):
            return result
        page += 1
    raise ValueError('검색 페이지 한도 초과')


def image_url(raw):
    if not raw:
        return ''
    url = urljoin(IMAGE_SITE, raw)
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'img.goodwearmall.com', 'static.goodwearmall.com'}:
        return ''
    return url


def gallery_urls(goods):
    return list(dict.fromkeys(image_url(row['imgUrl']) for row in goods.get('godImgList', [])
                              if row.get('imgUrl') and (row.get('imgUseYn') == 'Y' or row.get('imgTurn') == 100)
                              and image_url(row['imgUrl'])))


def money(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(',', ''))
    except (ValueError, TypeError):
        return None
    return int(number) if number > 0 and number.is_integer() else None


def sizes(goods):
    values = [str(row.get('optValNm1') or row.get('itmNm') or '') for row in goods.get('cnsgnSingleGoditmList', [])]
    if not values:
        values = [str(row.get('optValNm') or row.get('itmNm') or '') for row in goods.get('godItmExList', [])]
    return ','.join(dict.fromkeys(value for value in values if value))


def body_images(goods):
    body = goods['god'].get('godDetailCont') or ''
    if goods.get('godCaUseYn') == 'Y':
        for entry in goods.get('godDetailList', []):
            if entry.get('godDetailTpCd') == 'GOD_DETAIL_ASSTN' and entry.get('godDetailCont'):
                body = entry['godDetailCont']
    doc = BeautifulSoup(body, 'html.parser')
    urls = []
    for node in doc.select('img'):
        url = image_url(node.get('src') or node.get('data-src'))
        if url and not re.search(r'banner|coupon|shipping|delivery|care|배송|교환|반품|쿠폰', url, re.I) and url not in urls:
            urls.append(url)
    # 공식 상세 템플릿의 상품 개별 컷. 후기·착용 비교·관리 아이콘은 읽지 않는다.
    for row in goods.get('godImgList', []):
        if 101 <= int(row.get('imgTurn') or 0) <= 215:
            url = image_url(row.get('imgUrl'))
            if url and url not in urls:
                urls.append(url)
    return urls


def measurement(goods):
    table = goods.get('gdSizeLktb') or ''
    if not table:
        for entry in goods.get('godDetailList', []):
            if entry.get('godDetailTpCd') in ('SIZE_LKTB', 'MOBILE_SIZE_LKTB') and entry.get('godDetailCont'):
                table = entry['godDetailCont']
                break
    doc = BeautifulSoup(table, 'html.parser')
    tables = ''.join(table_html(node) for node in doc.find_all('table'))
    diagram = image_url(goods.get('acmsrImgUrl'))
    return diagram, tables


def checked(ctx, url, cache, detail=False):
    key = (url, detail)
    if key not in cache:
        normal = cache.get((url, False))
        if detail and normal and normal['status'] == 'accessible':
            cache[key] = normal
        else:
            cache[key] = check_public_image(url, timeout=10, retries=1,
                                            max_bytes=64*1024*1024 if detail else 10000000,
                                            checkpoint=ctx.checkpoint)
    result = cache[key]
    if result['status'] != 'accessible':
        ctx.log('원본 이미지 확인 실패: ' + url + ' / ' + ', '.join(result['reasons']), 'WARNING')
        return None
    return result


def gallery(ctx, urls, sku, cache):
    accepted, digests = [], set()
    for url in urls:
        result = checked(ctx, url, cache)
        if not result:
            continue
        validation = validate_gallery_image(
            result['data'], role='product_shot', mapping_verified=True, single_shot_verified=True,
            mapping_evidence='공식 색상별 godNo의 godImgList ' + sku,
            content_evidence='공식 개별 상품컷 영역. 상품 본문·측정 그림·색상칩·리뷰 제외')
        digest = hashlib.sha256(result['data']).hexdigest()
        if validation['passed'] and digest not in digests:
            digests.add(digest)
            accepted.append(dict(url=url, validation=validation))
        elif not validation['passed']:
            ctx.log('갤러리 제외: ' + url + ' / ' + ', '.join(validation['reasons']), 'WARNING')
    return gallery_slots(accepted)


def detail(ctx, goods, cache):
    diagram, tables = measurement(goods)
    parts = []
    urls = body_images(goods)
    if diagram and diagram not in urls:
        urls.append(diagram)
    for url in urls:
        result = checked(ctx, url, cache, detail=True)
        if result:
            parts.append('<p><img src="' + html.escape(url, quote=True) + '" alt="상품 상세" style="max-width:100%;height:auto"></p>')
        elif cache[(url, True)]['status'] == 'unknown':
            raise ValueError('상세 원본 이미지 조회 미완료: ' + url)
    if tables:
        parts.append('<h3>실측사이즈</h3>' + tables)
    else:
        ctx.log('공식 실측사이즈 표가 없어 해당 부분을 생략합니다.', 'WARNING')
    result = '<div>' + ''.join(parts) + '</div>' if parts else ''
    if len(result.encode('utf-16-le')) // 2 > 32767:
        raise ValueError('상세 HTML Excel 셀 한도 초과')
    return result


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'([A-Z0-9]{10})(?:[_-]?([A-Z0-9]{2,3}))?', raw)
    if not match:
        ctx.fail_item('기본 품번 10자리 또는 품번+공식 색상코드를 입력하세요.')
        raise ValueError('입력 품번 형식 오류')
    base, requested = match.groups()
    pending = search(ctx, base)
    seen, failures, completed = set(), [], set()
    cache = {}
    while pending:
        sku = pending.pop(0)
        if sku in seen:
            continue
        seen.add(sku)
        if len(seen) > 100:
            raise ValueError('상품 색상 조회 한도 초과')
        try:
            data = api(ctx, '/product/api/view.json', {'godNo': sku})
            if data.get('result') != 'Y':
                raise ValueError('공식 상품 응답 실패')
            goods = data['goods']
            god = goods['god']
            if god.get('dsgnGrpNo') != base or god.get('brndGrpId') != 'PBR' or god.get('godNo') != sku:
                continue
            for row in goods.get('colorList', []):
                other = row.get('god', {})
                if other.get('dsgnGrpNo') == base and other.get('godNo') and other['godNo'] not in seen:
                    pending.append(other['godNo'])
            color_code = str(god.get('colorCd') or '')
            if requested and color_code != requested:
                continue
            if color_code in completed:
                continue
            source, final = fetch(ctx, '/product/' + sku + '/detail')
            doc = BeautifulSoup(source, 'html.parser')
            canonical = doc.select_one('meta[property="og:url"]')
            expected_path = '/product/' + sku + '/detail'
            # 판매종료 색상은 상세 HTML의 og:url이 /main으로 바뀌지만,
            # 위에서 검증한 공식 API에는 해당 SKU의 원본 자료가 남아 있다.
            ended = (urlsplit(final).path == expected_path
                     and '판매종료된 상품입니다.' in source
                     and god.get('godSaleSectCd') == 'SLDOUT')
            if not ended and (not canonical or urlsplit(canonical.get('content', '')).path != expected_path):
                raise ValueError('상품 상세 링크 확인 실패')
            if ended:
                ctx.log('판매종료 색상: 공식 API에 남아 있는 상품 정보와 원본 이미지로 수집합니다. ' + sku, 'WARNING')
            ctx.stage('폴햄 색상 수집: ' + color_code)
            record = dict(product_code=base, product_name=clean_product_name(god['godNm'], ['폴햄', 'POLHAM'], [base, sku]),
                          product_url=final, color=god.get('colorNm') or color_code,
                          color_name=god.get('colorNm') or '', color_code=color_code)
            if ctx.enabled('size'):
                record['size'] = sizes(goods)
            if ctx.enabled('price'):
                record['price'] = money((goods.get('dspGodPrc') or {}).get('salePrc'))
            if ctx.enabled('regular_price'):
                record['regular_price'] = money(god.get('cvrPrc'))
                if record['regular_price'] is None:
                    ctx.log('공식 독립 정가가 없어 빈칸으로 저장합니다.', 'WARNING')
            if ctx.enabled('category'):
                names = re.findall(r"var\s+key\s*=\s*'stdCtgryNm_(\d+)'\s*;\s*var\s+value\s*=\s*'([^']*)'", source)
                record['category'] = ' > '.join(value for level, value in sorted(names, key=lambda item: int(item[0])) if int(level) > 1 and value)
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main, extras, warning = gallery(ctx, gallery_urls(goods), sku, cache)
                if warning:
                    ctx.log(warning, 'WARNING')
                if ctx.enabled('main_image'):
                    record['main_image'] = main or ''
                if ctx.enabled('additional_images'):
                    record['additional_images'] = ','.join(extras)
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail(ctx, goods, cache)
            ctx.emit_result(record)
            completed.add(color_code)
        except (OSError, ValueError, KeyError) as exc:
            message = sku + ' / ' + str(exc)
            ctx.fail_item(message)
            failures.append(message)
    if failures:
        raise RuntimeError('일부 색상 수집 미완료: ' + ' / '.join(failures))
    if not completed:
        ctx.fail_item('정확한 폴햄 품번 또는 색상이 없습니다: ' + raw)
        raise ValueError('공식 폴햄 상품 확인 실패')


def table_html(node):
    # 재구성 방식으로 중첩 트리 제거/unwrap 오류와 이벤트 속성 유입을 피한다.
    if isinstance(node,NavigableString):return html.escape(str(node))
    if node.name in ('script','style','iframe','object','embed','form'):return ''
    if node.name=='br':return '<br>'
    children=''.join(table_html(n) for n in node.children)
    if node.name not in ('table','thead','tbody','tfoot','tr','th','td','caption'):return children
    attrs=''
    if node.name in ('th','td'):
        attrs=' style="border:1px solid #ddd;padding:6px"'
        for k in ('rowspan','colspan'):
            value=str(node.get(k,''))
            if value.isdigit() and 0<int(value)<=100:attrs+=' '+k+'="'+value+'"'
    if node.name=='table':attrs=' style="border-collapse:collapse;width:100%"'
    return '<'+node.name+attrs+'>'+children+'</'+node.name+'>'
