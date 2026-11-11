PORTAL_API_VERSION = 1

import html
import json
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from crawler_rules import check_public_image, gallery_slots, validate_gallery_image

SITE = 'https://www.dlm1947.com'
SHOP_HOSTS = {'www.dlm1947.com', 'dlm1947.com'}
IMAGE_HOSTS = SHOP_HOSTS | {'ellegotr8125.cdn-nhncommerce.com'}


def fetch(ctx, url, data=None):
    if urlsplit(url).hostname not in SHOP_HOSTS or urlsplit(url).scheme != 'https':
        raise ValueError('PAT 공식몰 주소 확인 실패')
    headers = {'User-Agent': 'Mozilla/5.0'}
    if data is not None:
        headers.update({'X-Requested-With': 'XMLHttpRequest', 'Referer': SITE + '/goods/goods_view.php'})
    for attempt in range(2):
        ctx.checkpoint()
        try:
            with urlopen(Request(url, data=data, headers=headers), timeout=15) as response:
                final = response.geturl()
                if urlsplit(final).hostname not in SHOP_HOSTS:
                    raise ValueError('PAT 공식몰 외부로 이동했습니다.')
                body = response.read(5000001)
                if len(body) > 5000000:
                    raise ValueError('공식 응답 크기 초과')
                return body.decode(response.headers.get_content_charset() or 'utf-8-sig'), final
        except OSError:
            if attempt:
                raise


def search(ctx, code):
    first = SITE + '/goods/goods_search.php?' + urlencode({'keyword': code})
    pending, seen, products = [first], set(), {}
    while pending:
        url = pending.pop(0)
        if url in seen:
            continue
        if len(seen) >= 30:
            raise ValueError('공식 검색 페이지 한도 초과')
        seen.add(url)
        source, final = fetch(ctx, url)
        doc = BeautifulSoup(source, 'html.parser')
        for link in doc.select('.goods_list_cont .item_cont a[href]'):
            target = urlsplit(urljoin(final, link['href']))
            number = parse_qs(target.query).get('goodsNo', [''])[0]
            if target.hostname in SHOP_HOSTS and target.path == '/goods/goods_view.php' and number.isdigit():
                products[number] = SITE + target.path + '?' + urlencode({'goodsNo': number})
        for link in doc.select('.pagination a[href]'):
            target = urljoin(final, link['href'])
            parsed = urlsplit(target)
            if parsed.hostname in SHOP_HOSTS and parsed.path == '/goods/goods_search.php' and parse_qs(parsed.query).get('keyword') == [code]:
                pending.append(target)
    return products


def product_images(doc, selector, final):
    for node in doc.select(selector):
        raw = node.get('data-src') or node.get('src')
        if raw:
            url = urljoin(final, raw)
            if urlsplit(url).hostname in IMAGE_HOSTS and urlsplit(url).scheme == 'https':
                yield url


def color_options(doc):
    for option in doc.select('.prd-option-wrap dl'):
        label = option.find('dt')
        if label and label.get_text(strip=True) == '색상':
            return [node for node in option.select('select option[value]') if node['value'].strip()]
    return []


def size_options(ctx, doc, number, color):
    for group in doc.select('.prd-option-wrap dl'):
        label = group.find('dt')
        if not label or label.get_text(strip=True) != '사이즈':
            continue
        values = [n['value'].strip() for n in group.select('select option[value]') if n['value'].strip()]
        if values:
            return ','.join(dict.fromkeys(values))
        if not color:
            return ''
        payload = urlencode({'mode': 'option_select', 'optionVal[]': color, 'optionKey': '0',
                             'goodsNo': number, 'mileageFl': ' c'}).encode()
        source, _ = fetch(ctx, SITE + '/goods/goods_ps.php', payload)
        result = json.loads(source)
        if str(result.get('nextKey')) != '1' or not isinstance(result.get('nextOption'), list):
            raise ValueError('공식 색상별 사이즈 응답 확인 실패')
        flags = result.get('optionViewFl') or []
        return ','.join(dict.fromkeys(str(v) for i, v in enumerate(result['nextOption'])
                                      if v and (i >= len(flags) or flags[i] == 'y')))
    return ''


def price(doc, selector):
    node = doc.select_one(selector)
    value = node.get_text(strip=True).replace(',', '') if node else ''
    return int(value) if value.isdecimal() and int(value) else None


def detail_body(ctx, doc, final, code):
    urls = []
    for url in product_images(doc, '#detail .detail_explain_box .txt-manual img', final):
        path = PurePosixPath(urlsplit(url).path)
        if path.parent.name.lower() == 'product' and re.fullmatch(
                re.escape(code) + r'(?:[_-][A-Za-z0-9_-]+)?', path.stem, re.I):
            if url not in urls:
                urls.append(url)
    if not urls:
        ctx.log('품번이 확인되는 상세 본문 이미지가 없습니다.', 'WARNING')
    return ''.join('<p><img src="' + html.escape(url, quote=True)
                   + '" alt="상품 상세" style="max-width:100%;height:auto"></p>' for url in urls)


def color_gallery(ctx, urls, evidence):
    accepted = []
    for url in urls:
        result = check_public_image(url, timeout=8, retries=1, checkpoint=ctx.checkpoint)
        if result['status'] != 'accessible':
            ctx.log('상품 사진 조회 실패: ' + url + ' / ' + ', '.join(result['reasons']), 'WARNING')
            continue
        validation = validate_gallery_image(
            result['data'], role='product_shot', mapping_verified=True, single_shot_verified=True,
            mapping_evidence=evidence, content_evidence='공식 상품 사진 영역의 개별 컷')
        if validation['passed']:
            accepted.append(dict(url=url, validation=validation))
        else:
            ctx.log('갤러리 규격 제외: ' + url + ' / ' + ', '.join(validation['reasons']), 'WARNING')
    return gallery_slots(accepted)


def crawl(ctx):
    code = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]+', code):
        raise ValueError('PAT 기본 품번을 입력하세요.')
    ctx.stage('PAT 공식 품번 검색: ' + code)
    emitted = set()
    failures = []
    for number, url in search(ctx, code).items():
        ctx.checkpoint()
        source, final = fetch(ctx, url)
        doc = BeautifulSoup(source, 'html.parser')
        item_code = doc.select_one('.item_detail_tit .prd-goodscd')
        brand = doc.select_one('.item_detail_tit .prd-brand')
        title = doc.select_one('.item_detail_tit h3')
        if not item_code or item_code.get_text(strip=True).upper() != code:
            continue
        if not brand or brand.get_text(strip=True).upper() != 'PAT' or not title:
            continue
        common = dict(product_code=code, product_name=title.get_text(' ', strip=True), product_url=final)
        if ctx.enabled('price'):
            common['price'] = price(doc, '.price-box .prd-price strong')
        if ctx.enabled('regular_price'):
            common['regular_price'] = price(doc, '.price-box .prd-price-custom del span')
        if ctx.enabled('category'):
            common['category'] = ' > '.join(n.get_text(' ', strip=True) for n in doc.select('.location_wrap .location_tit a') if n.get_text(strip=True))
        if ctx.enabled('detail_page'):
            common['detail_page'] = detail_body(ctx, doc, final, code)
        wants_images = ctx.enabled('main_image') or ctx.enabled('additional_images')
        photos = list(dict.fromkeys(product_images(doc, '.item_photo_info_sec #mainImage img, .item_photo_info_sec .slider_goods_nav img', final))) if wants_images else []
        for option in color_options(doc):
            value = option['value'].strip()
            match = re.fullmatch(r'(.*?)\s*\(([0-9]+)\)', value)
            color, color_code = (match[1].strip(), match[2]) if match else (value, '')
            if value in emitted:
                continue
            try:
                ctx.stage('PAT 색상 수집: ' + value)
                record = dict(common, color=color, color_name=color, color_code=color_code)
                if ctx.enabled('size'):
                    record['size'] = size_options(ctx, doc, number, value)
                if wants_images:
                    thumb = PurePosixPath(urlsplit(option.get('data-img-src', '')).path).name
                    mapped = [photo for photo in photos if thumb and PurePosixPath(urlsplit(photo).path).name == thumb]
                    main, extras, warning = color_gallery(ctx, mapped, '공식 색상 옵션 썸네일과 동일 파일명: ' + value)
                    if warning:
                        ctx.log(warning, 'WARNING')
                    if ctx.enabled('main_image'):
                        record['main_image'] = main or ''
                    if ctx.enabled('additional_images'):
                        record['additional_images'] = ','.join(extras)
                ctx.emit_result(record)
                emitted.add(value)
            except (ValueError, OSError) as exc:
                message = value + ' / ' + str(exc)
                failures.append(message)
                ctx.fail_item(message)
    if failures:
        raise RuntimeError('일부 색상 미완료: ' + ' / '.join(failures))
    if not emitted:
        message = '공식 PAT 품번과 색상 옵션을 확인할 수 없습니다: ' + code
        ctx.fail_item(message)
        raise ValueError(message)
