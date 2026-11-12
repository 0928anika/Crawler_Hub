PORTAL_API_VERSION = 1

import hashlib
import html
import json
import re
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from crawler_rules import check_public_image, clean_product_name, gallery_slots, validate_gallery_image

SITE = 'https://jambangee.co.kr'
HOSTS = {'jambangee.co.kr', 'www.jambangee.co.kr'}
POLICY = dict(checked_on='2026-11-12', source='https://marketplace.coupang.com/mba/listing',
              min_side=500, max_bytes=10000000, max_aspect=1.8, formats=['JPEG','PNG','WEBP'],
              note='정지 WebP는 사용자 등록 환경 정책. 비율은 프로젝트 보조 기준.')
BUNDLE = re.compile(r'1\s*[+＋]\s*1|원\s*플러스\s*원|원\s*플\s*원|원플원|일\s*플러스\s*일', re.I)
NEGATIVE = re.compile(r'chart|size|measur|guide|collage|banner|logo|coupon|notice|shipping|review|고시|측정|실측|사이즈|배너|배송|쿠폰|리뷰', re.I)
# 실제 검색 AP3DNE655: 9311(1+1) 제외, 8669/BK 및 8668/GY.
# AP3DDJ643: 8681/DB. ProductGroup과 상품명 끝의 공식 SKU, 단일 색상 옵션을 대조.
# 본문 #prdAdd 이미지에 고시/실측표가 포함되어 별도 표를 생성하지 않는다.


def fetch(ctx, url):
    url = quote(url, safe=':/?&=%+#')
    if urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in HOSTS:
        raise ValueError('잠뱅이 공식 주소 확인 실패')
    for attempt in range(2):
        ctx.checkpoint()
        try:
            with urlopen(Request(url, headers={'User-Agent':'Mozilla/5.0'}), timeout=20) as response:
                final = response.geturl()
                if urlsplit(final).hostname not in HOSTS:
                    raise ValueError('공식몰 외부로 이동했습니다.')
                body = response.read(6000001)
                if len(body) > 6000000:
                    raise ValueError('공식 HTML 크기 한도 초과')
                return BeautifulSoup(body, 'html.parser'), final
        except HTTPError as exc:
            if exc.code in (400,401,403,404,410,429) or attempt:
                raise
        except OSError:
            if attempt:
                raise
        finally:
            ctx.checkpoint()


def search(ctx, base, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    found, excluded = set(), set()
    pending = [SITE + '/product/search.html?' + urlencode({'keyword':base})]
    visited, products = set(), {}
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        if len(visited) >= 30:
            raise ValueError('공식 검색 페이지 한도 초과')
        visited.add(url)
        doc, final = fetch(ctx, url)
        empty = doc.select_one('.xans-search-form .empty')
        explicit_empty = empty and '검색결과가 없습니다' in empty.get_text(' ',strip=True) and not doc.select('.name a[href]')
        if not doc.select_one('.xans-search-result') and not explicit_empty:
            raise ValueError('검색 결과 영역 확인 실패: 상품 없음으로 판정하지 않습니다.')
        for node in doc.select('.name a[href]'):
            title = node.get_text(' ', strip=True)
            target = urljoin(final,node['href'])
            found.add(target)
            if BUNDLE.search(title):
                excluded.add(target)
                ctx.log('1+1 상품 제외: ' + title, 'INFO')
                continue
            parsed = urlsplit(urljoin(final, node['href']))
            number = parse_qs(parsed.query).get('product_no',[''])[0]
            if parsed.hostname in HOSTS and parsed.path == '/product/detail.html' and number.isdecimal():
                products[number] = urljoin(final, node['href'])
        for node in doc.select('.xans-search-paging a[href]'):
            url = urljoin(final, node['href'])
            parsed = urlsplit(url)
            if parsed.hostname in HOSTS and parsed.path == '/product/search.html' and parse_qs(parsed.query).get('keyword') == [base]:
                pending.append(url)
    diagnostics.update(found=len(found), bundles=len(excluded), candidates=len(products))
    return products


class ProductMismatch(ValueError):
    """검색되었으나 공식 식별 정보가 입력 품번과 다름."""


class ColorMappingError(ValueError):
    """상품은 조회되었으나 색상 연결이 불명확함."""


class ImageLookupError(ValueError):
    """이미지 통신을 끝내지 못한 경우. 상품 없음과 구분."""


def groups(doc, number):
    result = []
    for node in doc.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.get_text())
        except (ValueError, TypeError):
            continue
        for entry in data if isinstance(data,list) else [data]:
            if isinstance(entry,dict) and entry.get('@type') == 'ProductGroup' and entry.get('productGroupID') == 'cafe24_smocbrand_1_' + number:
                result.append(entry)
            elif isinstance(entry,dict) and entry.get('@type') == 'Product' and entry.get('sku') == 'cafe24_smocbrand_1_' + number:
                result.append(entry)
    return result


def options(doc, title, number):
    values = []
    for select in doc.select('[option_title="' + title + '"]'):
        if select.get('option_product_no') != number:
            raise ValueError('공식 옵션과 상품번호 불일치')
        for node in select.select('option, li[option_value]'):
            value = node.get('option_value',node.get('value',''))
            if value in ('','*','**'):
                continue
            label = re.sub(r'\s*[\[(]품절[\])]\s*$', '', node.get_text(' ',strip=True)).strip()
            if label and label not in values:
                values.append(label)
    return values


def shared_image_identity(filename):
    match = re.fullmatch(r'([A-Z0-9]+(?:&[A-Z0-9]+)*)-([A-Z0-9]{2,4})\.(?:jpe?g|png|webp)',filename,re.I)
    if not match:
        return set(), ''
    tokens = match[1].upper().split('&')
    bases = {tokens[0]}
    suffix = re.fullmatch(r'(.*?)(\d+)',tokens[0])
    for token in tokens[1:]:
        if token.isdecimal():
            # 원문이 명시한 같은 길이의 숫자 끝자리만 확장한다. 임의 품번 추측 없음.
            if suffix and len(token) == len(suffix[2]):
                bases.add(suffix[1]+token)
        else:
            bases.add(token)
    return bases, match[2].upper()


def identity(doc, number, base, search_verified=False):
    node = doc.select_one('.infoArea .prdName')
    title = node.get_text(' ',strip=True) if node else ''
    if BUNDLE.search(title):
        return None
    match = re.search(r'(?<![A-Z0-9])([A-Z0-9]+(?:&[A-Z0-9]+)*)-([A-Z0-9]{2,4})\s*$',title,re.I)
    if match:
        title_bases, _ = shared_image_identity(match[0].strip()+'.jpg')
        if base not in title_bases:
            raise ProductMismatch('입력 '+base+' / 공식 상품명 품번 '+match[1].upper())
        code = match[2].upper()
        name_text = title[:match.start()].strip()
    else:
        # 제목에 SKU가 없는 상품은 실제 본문 원본의 품번/색상 파일명을 대조한다.
        # AP3DLG634&684-MB.jpg는 원문에 명시된 634/684 두 품번의 공통 자료.
        codes, identified_bases = set(), set()
        for node in doc.select('#prdAdd img'):
            parsed = urlsplit(node.get('ec-data-src') or node.get('src') or '')
            if parsed.hostname != 'thisclose.godohosting.com' or not parsed.path.startswith('/jambangee/page/'):
                continue
            bases, image_code = shared_image_identity(unquote(parsed.path).rsplit('/',1)[-1])
            identified_bases.update(bases)
            if base in bases:
                codes.add(image_code)
        if len(codes) > 1:
            raise ColorMappingError('공식 본문에 여러 색상코드가 있어 단일 색상 연결 확인 필요')
        if codes:
            code = codes.pop()
        elif search_verified:
            if identified_bases and base not in identified_bases:
                raise ProductMismatch('입력 '+base+' / 본문 공식 품번 '+', '.join(sorted(identified_bases)))
            code = ''
        else:
            return None
        name_text = title
    confirmed = groups(doc,number)
    product_id = doc.select_one('meta[property="product:productId"]')
    if (product_id and product_id.get('content') != number) or (not product_id and not confirmed):
        raise ValueError('공식 상품번호 연결 확인 실패')
    colors = options(doc,'색상',number)
    if len(colors) != 1:
        raise ColorMappingError('개별 상품의 단일 색상 옵션 확인 실패: ' + str(colors))
    name = clean_product_name(name_text,['잠뱅이','JAMBANGEE'],[base,base+'-'+code])
    # 원문 TG(L) 및 닫는 괄호가 빠진 TG(L 모두 상품명 끝의 사이즈만 제거.
    name = re.sub(r'\s*[\[(]\s*(?:[2-6]?X{0,3}[SML]|FREE|F|ONE\s*SIZE)\s*[\])]?\s*$', '', name, flags=re.I).strip()
    if not name:
        raise ValueError('정리 후 상품명 없음')
    return dict(product_code=base,product_name=name,color=colors[0],color_name=colors[0],color_code=code)


def money(raw):
    match = re.fullmatch(r'\s*(\d+(?:,\d{3})*)\s*원?\s*',str(raw or ''))
    return (int(match[1].replace(',','')) or None) if match else None


def price(ctx, doc):
    currency = doc.select_one('meta[property="product:price:currency"]')
    if not currency or currency.get('content') != 'KRW':
        ctx.log('KRW 판매가를 확인할 수 없어 빈칸으로 저장합니다.','WARNING')
        return None
    # 공식 공개 할인 판매가. 회원/쿠폰 혜택가를 요청하지 않는다.
    sale = doc.select_one('meta[property="product:sale_price:amount"]')
    node = sale if sale else doc.select_one('meta[property="product:price:amount"]')
    value = money(node.get('content')) if node else None
    if value is None:
        ctx.log('공식 판매가가 없거나 형식이 불명확하여 빈칸으로 저장합니다.','WARNING')
    return value


def category(ctx,doc):
    for script in doc.find_all('script',src=False):
        source = script.get_text()
        match = re.search(r'CAFE24\.FRONT_JS_CONFIG_SHOP\s*=\s*',source)
        if match:
            data,_ = json.JSONDecoder().raw_decode(source[match.end():])
            entries = data.get('oCategoryInfo',{})
            return ' > '.join(str(v['category_name']) for _,v in sorted(entries.items(),key=lambda pair:int(pair[0])) if v.get('category_name'))
    ctx.log('공식 상품 카테고리 정보 없음','WARNING')
    return ''


def image_url(raw, final):
    if not raw:
        return ''
    url = urljoin(final,raw)
    parsed = urlsplit(url)
    allowed = (parsed.hostname in HOSTS and parsed.path.startswith('/web/')) or (
        parsed.hostname == 'thisclose.godohosting.com' and parsed.path.startswith('/jambangee/'))
    return quote(url,safe=':/?&=%+#') if parsed.scheme == 'https' and allowed else ''


def gallery_urls(doc,number,final):
    urls = []
    for group in groups(doc,number):
        for variant in [group] if group.get('@type') == 'Product' else group.get('hasVariant',[]):
            sku = str(variant.get('sku',''))
            if sku != 'cafe24_smocbrand_1_' + number and not sku.startswith('cafe24_smocbrand_1_' + number + '_'):
                continue
            values = variant.get('image',[])
            for raw in values if isinstance(values,list) else [values]:
                url = image_url(raw,final)
                if url and url not in urls:
                    urls.append(url)
    for node in doc.select('meta[property="og:image"], .imgArea img'):
        url = image_url(node.get('content') or node.get('ec-data-src') or node.get('src'),final)
        if url and url not in urls:
            urls.append(url)
    return urls


def checked(ctx,url,cache,detail=False):
    key = (url,detail)
    if key not in cache:
        previous = cache.get((url,False))
        cache[key] = previous if detail and previous and previous['status']=='accessible' else check_public_image(
            url,timeout=12,retries=1,max_bytes=64*1024*1024 if detail else 10000000,checkpoint=ctx.checkpoint)
    result = cache[key]
    if result['status'] == 'unknown':
        raise ImageLookupError('원본 이미지 통신 확인 미완료: ' + url + ' / ' + ', '.join(result['reasons']))
    if result['status'] != 'accessible':
        ctx.log('이미지 제외: ' + url + ' / ' + ', '.join(result['reasons']),'WARNING')
        return None
    return result


def gallery(ctx,doc,number,final,record,cache):
    accepted, hashes = [], set()
    for url in gallery_urls(doc,number,final):
        if NEGATIVE.search(unquote(urlsplit(url).path)):
            ctx.log('갤러리 제외 경로: ' + url,'WARNING')
            continue
        result = checked(ctx,url,cache)
        if not result:
            continue
        valid = validate_gallery_image(result['data'],role='product_shot',mapping_verified=True,single_shot_verified=True,
            mapping_evidence='공식 상품번호 '+number+' / 상품명 SKU와 단일 색상 옵션 '+record['color_code']+' / ProductGroup.image 및 상품 갤러리',
            content_evidence='상품 사진 필드와 .imgArea에서 추출. 경로 부정 신호 제외. 실제 형식·픽셀·용량·비율 검사. 상세/추천상품 영역 분리.')
        digest = hashlib.sha256(result['data']).hexdigest()
        if valid['passed'] and digest not in hashes:
            accepted.append(dict(url=url,validation=valid));hashes.add(digest)
        elif not valid['passed']:
            ctx.log('갤러리 규격 제외: '+url+' / '+', '.join(valid['reasons']),'WARNING')
    return gallery_slots(accepted)


def detail(ctx,doc,final,cache):
    urls, parts = [], []
    for node in doc.select('#prdAdd img'):
        url = image_url(node.get('ec-data-src') or node.get('data-src') or node.get('src'),final)
        if url and not re.search(r'banner|coupon|shipping|delivery|review|배송|교환|반품|쿠폰',urlsplit(url).path+' '+node.get('alt',''),re.I) and url not in urls:
            urls.append(url)
    for url in urls:
        if checked(ctx,url,cache,detail=True):
            parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="max-width:100%;height:auto"></p>')
    if not parts:
        ctx.log('공식 본문 이미지가 없어 상세를 생략합니다. 고시/사이즈표를 별도로 만들지 않습니다.','WARNING')
    value = '<div>'+''.join(parts)+'</div>' if parts else ''
    if len(value.encode('utf-16-le'))//2 > 32767:
        raise ValueError('상세 HTML Excel 셀 한도 초과')
    return value


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'([A-Z0-9]+?)(?:[_-]([A-Z0-9]{2,4}))?',raw)
    if not match:
        raise ValueError('잠뱅이 기본 품번 또는 기본품번_공식색상코드를 입력하세요.')
    base, requested = match.groups()
    ctx.stage('잠뱅이 공식 검색: '+base)
    diagnostics = {}
    try:
        products = search(ctx,base,diagnostics)
    except (OSError,ValueError) as exc:
        ctx.fail_item('[검색 조회 실패] '+base+' / '+str(exc))
        raise
    if not products:
        if not diagnostics['found']:
            message = '[검색 결과 없음] '+base+' / 공식 품번 검색 0건'
        elif diagnostics['bundles'] == diagnostics['found']:
            message = '[1+1 상품 제외] '+base+' / 검색 '+str(diagnostics['found'])+'건 모두 1+1 상품이며 개별 상품 없음'
        else:
            message = '[검색 링크 확인 실패] '+base+' / 검색 결과는 있으나 공식 상품 링크 추출 불가'
        ctx.fail_item(message)
        raise ValueError(message)
    completed, failures, available = set(), [], set()
    bundle_pages = 0
    for number,url in products.items():
        try:
            doc,final = fetch(ctx,url)
            record = identity(doc,number,base,search_verified=True)
            if not record:
                bundle_pages += 1
                ctx.log('[1+1 상품 제외] '+final,'INFO')
                continue
            code = record['color_code']
            available.add(code or ('코드 미확인('+record['color']+')'))
            color_key = code or record['color']
            if (requested and code != requested) or color_key in completed:
                continue
            ctx.stage('잠뱅이 색상 수집: '+record['color'])
            record['product_url'] = final
            if ctx.enabled('size'):
                record['size'] = ','.join(options(doc,'사이즈',number))
            if ctx.enabled('price'):
                record['price'] = price(ctx,doc)
            if ctx.enabled('regular_price'):
                record['regular_price'] = None
                ctx.log('공식 독립 정상가/소비자가 필드가 확인되지 않아 빈칸으로 저장합니다.','WARNING')
            if ctx.enabled('category'):
                record['category'] = category(ctx,doc)
            cache = {}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main,extras,warning = gallery(ctx,doc,number,final,record,cache)
                if warning:ctx.log(warning,'WARNING')
                if ctx.enabled('main_image'):record['main_image'] = main or ''
                if ctx.enabled('additional_images'):record['additional_images'] = ','.join(extras)
            if ctx.enabled('detail_page'):
                record['detail_page'] = detail(ctx,doc,final,cache)
            ctx.emit_result(record)
            completed.add(color_key)
        except (OSError,ValueError,KeyError) as exc:
            reason = ('품번 불일치' if isinstance(exc,ProductMismatch) else
                      '색상 매핑 불가' if isinstance(exc,ColorMappingError) else
                      '이미지 조회 실패' if isinstance(exc,ImageLookupError) else
                      '통신 오류' if isinstance(exc,OSError) else '상품정보 확인 실패')
            message = '['+reason+'] 상품 '+number+' / '+str(exc)
            ctx.fail_item(message);failures.append(message)
    if failures:
        raise RuntimeError('일부 색상 수집 실패: '+' / '.join(failures))
    if not completed:
        if requested and available:
            message = '[지정 색상 없음] '+raw+' / 조회된 공식 색상: '+', '.join(sorted(available))
        elif bundle_pages:
            message = '[1+1 상품 제외] '+raw+' / 검색된 상품 상세가 1+1 상품으로 확인됨'
        else:
            message = '[상품정보 확인 실패] '+raw+' / 검색 결과는 있으나 수집 가능한 개별 상품 정보를 확정하지 못함'
        ctx.fail_item(message)
        raise ValueError(message)
