PORTAL_API_VERSION = 1

import hashlib
import html
import json
import re
import sys
import time
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode, urldefrag
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image, ImageStat
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots, clean_product_name

SITE = 'https://www.giordano.co.kr'
HOSTS = ('www.giordano.co.kr', 'giordano.co.kr')
REVISION = '2026-11-14-giordano-4'
# 인명 표기 상품의 갤러리 전용 보수적 휴리스틱. 사람 신원/AI 여부 판별 아님.
# 양쪽 상단 12.5% 영역의 채널별 표준편차 <= 8, 평균색 차이 <= 32 (스튜디오 조명 그라데이션 허용).
BACKGROUND_MAX_NOISE = 8.0
BACKGROUND_MAX_DIFFERENCE = 32.0
POLICY = dict(checked_on='2026-11-10', source='https://marketplace.coupang.com/mba/listing',
              route='WING 웹/엑셀', min_side=500, max_bytes=10000000,
              formats=['JPEG', 'PNG', 'WEBP'], max_aspect=1.8)
# 공식 HTML/실제 색상 클릭 XHR로 확인: ppno + 색상 option ID로 getAddImgList,
# getOptionStockSKU의 응답 ID만 존재하지 않는 SKU. 품절과 구분한다.
# 샘플 05586901 실제 6색상 확인. 이미지 검사 통과는 WING 등록 완료를 뜻하지 않는다.
NEGATIVE = re.compile(r'review|ugc|banner|coupon|shipping|delivery|collage|측정|사이즈|고시|배송|반품|교환|쿠폰|콜라주', re.I)


def parse(source):
    return BeautifulSoup(source, 'html.parser')


def official(url):
    parts = urlsplit(url)
    return parts.scheme == 'https' and parts.hostname in HOSTS and not parts.username and not parts.password


def fetch(ctx, url):
    if not official(url):
        raise ValueError('지오다노 공식 홈페이지 외부 URL')
    last = None
    for attempt in range(2):
        ctx.checkpoint()
        try:
            with build_opener().open(Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}), timeout=20) as response:
                final = response.geturl()
                if not official(final):
                    raise ValueError('공식 홈페이지 외부 이동')
                data = response.read(8000001)
                if len(data) > 8000000:
                    raise ValueError('공식 응답 최대 용량 초과')
                return data.decode(response.headers.get_content_charset() or 'utf-8-sig'), final
        except Exception as exc:
            last = exc
            ctx.log('공식 조회 재시도: ' + str(exc), 'WARNING')
        finally:
            ctx.checkpoint()
    raise RuntimeError('공식 조회 실패: ' + str(last))


def ajax(ctx, action, **params):
    source, _ = fetch(ctx, SITE + '/main/exec.php?' + urlencode(dict(exec_file='shop/getAjaxData.php', exec=action, **params)))
    return source.strip()


def search(ctx, base):
    queue = [SITE + '/shop/search_result.php?' + urlencode({'search_str': base})]
    seen, links = set(), []
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        if len(seen) >= 30:
            raise ValueError('공식 검색 페이지 안전 한도 초과')
        seen.add(url)
        source, final = fetch(ctx, url)
        if urlsplit(url).path == '/main/exec.php':
            source = json.loads(source)['content']
        doc = parse(source)
        for node in doc.select('a[href]'):
            link = urljoin(final, node['href'])
            parts = urlsplit(link)
            if not official(link):
                continue
            if parts.path == '/shop/detail.php' and parse_qs(parts.query).get('pno') and link not in links:
                links.append(link)
        total = doc.select_one('.more_count .total')
        if total and total.get_text(strip=True).isdigit():
            count = int(total.get_text(strip=True))
            if count > 30:
                raise ValueError('공식 검색 페이지 안전 한도 초과')
            # 공식 common.js prdMore의 공개 더보기 요청과 동일한 파라미터.
            for number in range(2, count + 1):
                more = SITE + '/main/exec.php?' + urlencode(dict(exec_file='skin_module/skin_ajax.php',
                    obj_id='prd_basic', _tmp_file_name='shop/search_result.php', single_module='search_result_prd_list',
                    striplayout=1, module_page=number, document_url=final, search_str=base))
                if more not in seen and more not in queue:
                    queue.append(more)
    return links


def identity(doc, base):
    node = doc.select_one('.style_code_gio[data-clipboard-text]')
    if not node:
        raise ValueError('공식 STYLE 품번 필드 누락')
    if node['data-clipboard-text'].strip().upper() != base:
        return None
    title = doc.select_one('form[name="prdFrm"] input[name="product_name"]')
    raw = title.get('value', '').strip() if title else ''
    # 원본을 먼저 보존하고, 표준 상품명에서만 광고 표기와 인명 제거.
    title_text = re.sub(r'\[[가-힣]{2,4}\]', lambda m: '' if named_campaign(m[0]) else m[0], raw)
    title_text = re.sub(r'(?<!\w)NEW(?!\w)', '', title_text, flags=re.I)
    name = clean_product_name(title_text, ['지오다노', 'GIORDANO'], [base])
    if not name:
        raise ValueError('공식 상품명 없음')
    return dict(product_code=base, product_name=name, **{'원본 상품명': raw})


def colors(doc, base):
    result, seen = [], set()
    for node in doc.select('form[name="prdFrm"] .color_option a[data]'):
        image = node.find('img')
        label = image.get('alt', '').strip() if image else ''
        match = re.fullmatch(r'(\d{2})\s+(.+)', label)
        if not match or not image:
            raise ValueError('공식 색상 옵션 형식 확인 실패: ' + label)
        # 공식 colorchip_auto 파일의 전체 SKU가 STYLE + 숫자 색상코드와 일치.
        path = urlsplit(urljoin(SITE, image.get('src', ''))).path
        if path.rsplit('/', 1)[-1].split('.')[0] != base + match[1]:
            raise ValueError('공식 색상칩 SKU와 STYLE/색상코드 불일치: ' + label)
        code = match[1]
        if code not in seen:
            result.append(dict(color_code=code, color_name=match[2], color=match[2], option_id=node['data']))
            seen.add(code)
    if not result:
        raise ValueError('공식 색상 옵션 없음')
    return result


def sizes(ctx, doc, option_id):
    # StockSKU는 없는 조합만 제외. Stock(품절) 요청은 하지 않으므로 품절 사이즈 유지.
    response = ajax(ctx, 'getOptionStockSKU', item_no='@' + option_id)
    if not re.fullmatch(r'[\d@]*', response):
        raise ValueError('공식 사이즈 SKU 응답 형식 확인 실패')
    excluded = set(response.split('@'))
    result = []
    for node in doc.select('form[name="prdFrm"] a.optChipSet2[data]'):
        if node['data'] in excluded:
            continue
        text = re.sub(r'\s*[\[(](?:품절|일시품절|SOLD\s*OUT)[\])]\s*', '', node.get_text(' ', strip=True), flags=re.I).strip()
        if text and text not in result:
            result.append(text)
    if not result:
        raise ValueError('해당 색상의 공식 사이즈 옵션 없음')
    return ','.join(result)


def price(ctx, doc, regular=False):
    selector = '#cpn_list_normal_prc' if regular else '#cpn_list_sell_prc'
    label = '정상가(소비자가)' if regular else '판매가'
    currency = re.search(r'\bvar\s+currency\s*=\s*[\'"]([^\'"]*)[\'"]', str(doc))
    if not currency or currency[1] != '원':
        ctx.log('공식 ' + label + ' KRW 통화 미확인: 빈칸', 'WARNING')
        return None
    node = doc.select_one(selector)
    text = node.get_text(' ', strip=True) if node else ''
    if not text:
        # 공식 ready 스크립트가 이 data 속성에 currency(원)를 더해 위 요소에 표시.
        source = doc.select_one('#detail .wrap_prd div.info')
        text = (source.get('data-normal_prc' if regular else 'data-sell_prc', '') + '원') if source else ''
    match = re.fullmatch(r'(\d{1,3}(?:,\d{3})+|\d+)\s*원', text)
    value = int(match[1].replace(',', '')) if match else 0
    if value <= 0:
        ctx.log('공식 ' + label + ' 확인 불가: 빈칸 / ' + text, 'WARNING')
        return None
    return value


def gallery_urls(ctx, doc, option_id):
    form = doc.select_one('form[name="prdFrm"]')
    pno, ppno = form.select_one('input[name="pno"]'), form.select_one('input[name="ppno"]')
    key = ppno.get('value') if ppno and ppno.get('value') else pno.get('value') if pno else ''
    if not key:
        raise ValueError('공식 갤러리 상품 식별자 누락')
    payload = json.loads(ajax(ctx, 'getAddImgList', hash=key, opt_no=1, ino=option_id, product_cut='', custom_head_img_gio=''))
    doc = parse(payload.get('html', ''))
    result = []
    for node in doc.select('.slides img[src]'):
        url = urldefrag(urljoin(SITE, node['src']))[0]
        if official(url) and urlsplit(url).path.startswith('/_data/attach/') and not NEGATIVE.search(str(node)) and url not in result:
            result.append(url)
    return result


def detail_urls(doc, final):
    result = []
    # 상세 본문 + 실측 이미지 + 상품정보고시 이미지만. 별도 소재정보 ch는 제외.
    nodes = list(doc.select('#detail .detail_info .wing-detail-more-contents img'))
    nodes.extend(doc.select('#det_size_info .size_img img'))
    nodes.extend(n for n in doc.select('.prd_detail_content_gio img') if '상품정보고시' in n.get('data-layer_title', ''))
    for node in nodes:
        url = urljoin(final, node.get('data-src') or node.get('src') or '')
        if not official(url) or not urlsplit(url).path.startswith(('/_gio_on/', '/_data/')):
            continue
        if re.search(r'review|shipping|delivery|banner|coupon|care|배송|교환|반품|쿠폰', url + ' ' + node.get('alt', ''), re.I):
            continue
        if url not in result:
            result.append(url)
    return result


def public_image(ctx, url, cache):
    if url not in cache:
        cache[url] = check_public_image(url, timeout=10, retries=1, checkpoint=ctx.checkpoint)
    result = cache[url]
    if result['status'] != 'accessible':
        ctx.log('이미지 확인 불가: ' + url + ' / ' + ', '.join(result['reasons']), 'WARNING')
        return None
    return result


def prefetch(ctx, urls, cache):
    missing = [u for u in dict.fromkeys(urls) if u not in cache]
    if len(missing) < 2 or getattr(sys.modules.get('__main__'), 'EMIT_LOCK', None) is None:
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {u: pool.submit(check_public_image, u, timeout=10, retries=1) for u in missing}
        for url, future in futures.items():
            while not future.done():
                ctx.checkpoint()
                time.sleep(.1)
            cache[url] = future.result()
            ctx.checkpoint()


def named_campaign(raw_name):
    # 실제 공식 제목 [박현지], [변우석], [문가영]과 같은 인명형 표기.
    # 상품 제목의 다른 대괄호 분류는 인명으로 취급하지 않는다.
    labels = re.findall(r'\[([가-힣]{2,4})\]', raw_name)
    return any(label not in {'남성', '여성', '공용', '키즈', '주니어', '온라인', '신상품', '남성용', '여성용', '한정', '단독'} for label in labels)


def uniform_background(data):
    """Check visible upper edge background; fail closed when obscured/textured.

    Two wide patches prevent a tiny plain corner of an outdoor wall from passing.
    Subject close-ups touching these patches can be conservatively excluded too.
    Verified on official 05376918 / 01076501 sample galleries, not a face classifier.
    """
    with Image.open(BytesIO(data)) as source:
        image = source.convert('RGB').resize((160, 200), Image.Resampling.BILINEAR)
    left = ImageStat.Stat(image.crop((0, 0, 20, 25)))
    right = ImageStat.Stat(image.crop((140, 0, 160, 25)))
    noise = max(left.stddev + right.stddev)
    difference = max(abs(a - b) for a, b in zip(left.mean, right.mean))
    passed = noise <= BACKGROUND_MAX_NOISE and difference <= 10
    if not passed and noise <= BACKGROUND_MAX_NOISE and difference <= BACKGROUND_MAX_DIFFERENCE:
        # A lighting gradient is safe only when both side backgrounds remain smooth.
        side_noise=max(max(ImageStat.Stat(image.crop((x,y,x+20,y+20))).stddev)
                       for x in (0,140) for y in (25,50,75,100))
        passed=side_noise<=BACKGROUND_MAX_NOISE
    return passed, f'가장자리 배경 질감={noise:.1f}/8, 좌우 색차={difference:.1f}/32'


def gallery(ctx, urls, code, cache, filter_background=False):
    accepted, seen = [], set()
    for url in urls:
        ctx.checkpoint()
        result = public_image(ctx, url, cache)
        if not result:
            continue
        if filter_background:
            passed, reason = uniform_background(result['data'])
            if not passed:
                ctx.log('인명 표기 상품의 비균일/불명확 배경 컷 제외: ' + reason + ' / ' + url, 'WARNING')
                continue
            ctx.log('균일 배경 확인: ' + reason + ' / ' + url, 'INFO')
        validation = validate_gallery_image(result['data'], role='product_shot', mapping_verified=True,
            single_shot_verified=True, mapping_evidence='STYLE 검증 상품의 공식 색상 option ID 갤러리: ' + code,
            content_evidence='공식 상품갤러리 개별 컷, 본문/리뷰/색상칩 제외; 부정 신호 없음')
        digest = hashlib.sha256(result['data']).hexdigest()
        if validation['passed'] and digest not in seen:
            seen.add(digest)
            accepted.append(dict(url=url, validation=validation))
        elif not validation['passed']:
            ctx.log('갤러리 제외: ' + url + ' / ' + ', '.join(validation['reasons']), 'WARNING')
    return gallery_slots(accepted)


def detail(ctx, urls, cache):
    parts = []
    for url in urls:
        ctx.checkpoint()
        if not public_image(ctx, url, cache):
            # Some official size links point to JPG while the same asset is PNG.
            # Only try the exact same official size/notice basename; validate bytes.
            replacement=None
            if re.search(r'/_gio_on/.*(?:size|gosi)\.(?:jpg|jpeg|png)$',url,re.I) and cache.get(url,{}).get('status')=='inaccessible':
                for extension in ('.png','.jpg','.jpeg'):
                    candidate=re.sub(r'\.(?:jpg|jpeg|png)$',extension,url,flags=re.I)
                    if candidate!=url and public_image(ctx,candidate,cache):
                        replacement=candidate;break
            if replacement:
                ctx.log('공식 상세 이미지 확장자 보정: '+url+' → '+replacement,'WARNING')
                parts.append('<p><img src="'+html.escape(replacement,quote=True)+'" alt="상품 상세정보"></p>');continue
            if any(re.search(r'HTTP (404|410)\b', r) for r in cache.get(url, {}).get('reasons', [])):
                ctx.log('없는 상세 원본 이미지 생략: ' + url, 'WARNING')
                continue
            raise ValueError('상세 이미지 조회 미완료: ' + url)
        parts.append('<p><img src="' + html.escape(url, quote=True) + '" alt="상품 상세정보"></p>')
    if not parts:
        ctx.log('상품 상세 이미지 없음: 고시/사이즈표를 임의 생성하지 않음', 'WARNING')
        return ''
    result = '<div>' + ''.join(parts) + '</div>'
    if len(result.encode('utf-16-le')) // 2 > 32767:
        raise ValueError('상세 HTML Excel 셀 한도 초과')
    return result


def collect(ctx, doc, final, base, requested, emitted, cache):
    common = identity(doc, base)
    if common is None:
        return []
    options = [c for c in colors(doc, base) if not requested or c['color_code'] == requested]
    # 선택된 데이터만 파싱/요청. 공통 상세는 품번당 한 번 확인한다.
    details = detail_urls(doc, final) if ctx.enabled('detail_page') else []
    if ctx.enabled('detail_page'):
        if not doc.select('#det_size_info .size_img img'):
            ctx.log('공식 실측 사이즈 이미지 없음: 별도 표 생성 없이 진행', 'WARNING')
        if not any('상품정보고시' in n.get('data-layer_title', '') for n in doc.select('.prd_detail_content_gio img')):
            ctx.log('공식 상품정보고시 이미지 없음: 별도 표 생성 없이 진행', 'WARNING')
    failures = [];detail_html=None
    for option in options:
        code = option['color_code']
        if code in emitted:
            continue
        try:
            ctx.stage('지오다노 색상 ' + code + ' ' + option['color_name'])
            record = dict(common, product_url=final, **{k: v for k, v in option.items() if k != 'option_id'})
            if ctx.enabled('size'):
                record['size'] = sizes(ctx, doc, option['option_id'])
            if ctx.enabled('price'):
                record['price'] = price(ctx, doc)
            if ctx.enabled('regular_price'):
                record['regular_price'] = price(ctx, doc, regular=True)
            if ctx.enabled('category'):
                record['category'] = ' > '.join(n.get_text(' ', strip=True) for n in doc.select('.navi a[class^="prdpath"]') if '/shop/big_section.php' in n.get('href', ''))
            photos = gallery_urls(ctx, doc, option['option_id']) if ctx.enabled('main_image') or ctx.enabled('additional_images') else []
            prefetch(ctx, photos + details, cache)
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main, extras, warning = gallery(ctx, photos, code, cache, filter_background=named_campaign(common['원본 상품명']))
                if warning:
                    ctx.log(warning, 'WARNING')
                if ctx.enabled('main_image'):
                    record['main_image'] = main or ''
                if ctx.enabled('additional_images'):
                    record['additional_images'] = ','.join(extras)
            if ctx.enabled('detail_page'):
                if detail_html is None:detail_html=detail(ctx, details, cache)
                record['detail_page'] = detail_html
            ctx.emit_result(record)
            emitted.add(code)
        except Exception as exc:
            message = base + '_' + code + ': ' + str(exc)
            failures.append(message)
            ctx.fail_item(message)
    return failures


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'(\d{8})(?:[_-](\d{2}))?', raw)
    if not match:
        ctx.fail_item('품번 8자리 또는 품번_색상코드 / 품번-색상코드 2자리로 입력하세요. 앞자리 0을 보존하세요.')
        raise ValueError('입력 품번 형식 오류')
    base, requested = match[1], match[2]
    ctx.log('지오다노 코드 버전: ' + REVISION, 'INFO')
    emitted, cache, failures, seen = set(), {}, [], set()
    try:
        ctx.stage('지오다노 공식 검색: ' + base)
        for link in search(ctx, base):
            key = parse_qs(urlsplit(link).query)['pno'][0]
            if key in seen:
                continue
            seen.add(key)
            source, final = fetch(ctx, link)
            doc = parse(source)
            failures.extend(collect(ctx, doc, final, base, requested, emitted, cache))
        if not emitted:
            raise ValueError('정확한 STYLE 품번/지정 색상 결과 없음: ' + raw)
    except Exception as exc:
        if not failures:
            ctx.fail_item(str(exc))
        raise
    if failures:
        raise RuntimeError('일부 색상 미완료: ' + ' / '.join(failures))
