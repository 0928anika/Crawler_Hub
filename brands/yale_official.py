PORTAL_API_VERSION = 1
import hashlib, html, json, re
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup
from crawler_rules import check_public_image, clean_product_name, gallery_slots, validate_gallery_image
SITE = 'https://yaleapparel.co.kr'
HOSTS = {'yaleapparel.co.kr','www.yaleapparel.co.kr'}
NEGATIVE = re.compile(r'chart|size|guide|banner|coupon|notice|shipping|review|고시|실측|사이즈|배너|배송|쿠폰|리뷰',re.I)


def fetch(ctx, url):
    url = quote(url, safe=':/?&=%+#')
    if urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in HOSTS:
        raise ValueError('예일 공식 주소 확인 실패')
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


class ProductMismatch(ValueError):
    """검색되었으나 공식 식별 정보가 입력 품번과 다름."""


class ColorMappingError(ValueError):
    """상품은 조회되었으나 색상 연결이 불명확함."""


class ImageLookupError(ValueError):
    """이미지 통신을 끝내지 못한 경우. 상품 없음과 구분."""


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
            return ' > '.join(str(v['category_name']) for _,v in sorted(((k,v) for k,v in entries.items() if str(k).isdigit() and isinstance(v,dict)),key=lambda pair:int(pair[0])) if v.get('category_name'))
    ctx.log('공식 상품 카테고리 정보 없음','WARNING')
    return ''


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
            mapping_evidence='공식 상품번호 '+number+' / 정확한 본문 품번 경로와 상품명 색상 '+record['color_code']+' / 공식 상품 갤러리',
            content_evidence='상품 사진 필드와 .imgArea에서 추출. 경로 부정 신호 제외. 실제 형식·픽셀·용량·비율 검사. 상세/추천상품 영역 분리.')
        digest = hashlib.sha256(result['data']).hexdigest()
        if valid['passed'] and digest not in hashes:
            accepted.append(dict(url=url,validation=valid));hashes.add(digest)
        elif not valid['passed']:
            ctx.log('갤러리 규격 제외: '+url+' / '+', '.join(valid['reasons']),'WARNING')
    return gallery_slots(accepted)


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r'([A-Z0-9]+?)(?:[_-]([A-Z0-9]{2,4}))?',raw)
    if not match:
        raise ValueError('예일 기본 품번 또는 기본품번_공식색상코드를 입력하세요.')
    base, requested = match.groups()
    ctx.stage('예일 공식 검색: '+base)
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
            ctx.stage('예일 색상 수집: '+record['color'])
            record['product_url'] = final
            if ctx.enabled('size'):
                record['size'] = ','.join(options(doc,'사이즈',number))
            if ctx.enabled('price'):
                record['price'] = price(ctx,doc)
            if ctx.enabled('regular_price'):
                record['regular_price'] = regular_price(ctx,doc)
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

def search(ctx,base,diagnostics=None):
    found={};summaries={};pending=[SITE+'/product/search.html?'+urlencode({'keyword':base})];seen=set()
    while pending:
        url=pending.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise ValueError('검색 페이지 한도 초과')
        seen.add(url);doc,final=fetch(ctx,url)
        if not doc.select_one('.xans-search-result, .xans-search-form, .xans-product-searchdata'):
            raise ValueError('검색 영역 접근 실패: 검색 0건으로 판정하지 않습니다.')
        cards=doc.select('.name a[href]')
        if not cards and not doc.find(string=lambda t:t and '검색 결과가 없습니다' in t):
            raise ValueError('검색 결과와 명시적 0건 안내 모두 확인 불가')
        for a in cards:
            target=urljoin(final,a['href']);p=urlsplit(target)
            number=parse_qs(p.query).get('product_no',[''])[0]
            if not number:
                match=re.match(r'/product/[^/]+/(\d+)(?:/|$)',unquote(p.path))
                number=match[1] if match else ''
            if p.hostname in HOSTS and number.isdecimal():
                title=a.get_text(' ',strip=True)
                if re.search(r'\b\d+\s*Colors?\s*$',title,re.I):summaries[number]=target
                else:found[number]=SITE+'/product/detail.html?product_no='+number
        for a in doc.select('.xans-search-paging a[href]'):
            target=urljoin(final,a['href']);p=urlsplit(target)
            if p.hostname in HOSTS and p.path=='/product/search.html' and parse_qs(p.query).get('keyword')==[base]:pending.append(target)
    if summaries:
        ctx.log('다색상 요약 페이지 '+str(len(summaries))+'개 대신 검색된 개별 색상 페이지 '+str(len(found))+'개 수집','INFO')
        if not found:raise ColorMappingError('다색상 요약 상품만 조회되어 개별 색상 연결 확인 필요')
    if diagnostics is not None:diagnostics.update(found=len(found),bundles=0,candidates=len(found))
    return found


def image_url(raw,final):
    if not raw:return ''
    u=urljoin(final,raw);p=urlsplit(u)
    allowed=(p.hostname in HOSTS and p.path.startswith('/web/')) or (p.hostname=='yalerecords.phyps.co.kr' and p.path.startswith('/yale/'))
    return quote(u,safe=':/?&=%+#') if p.scheme=='https' and allowed else ''


def identity(doc,number,base,search_verified=False):
    node=doc.select_one('meta[property="product:productId"]')
    if not node or node.get('content')!=number:raise ProductMismatch('공식 상품번호 불일치')
    node=doc.select_one('.xans-product-detaildesign [rel="상품명"] td')
    title=node.get_text(' ',strip=True) if node else ''
    if '_' not in title:raise ColorMappingError('상품명에 공식 단일 색상 표기 없음: '+title)
    name,color=title.rsplit('_',1);color=color.strip()
    codes=set();other=set()
    for node in doc.select('#prdDetailContent img'):
        u=image_url(node.get('ec-data-src') or node.get('src'),SITE)
        if not u:continue
        for folder in unquote(urlsplit(u).path).upper().split('/'):
            if re.fullmatch(re.escape(base)+r'[A-Z0-9]{2,4}',folder):codes.add(folder[len(base):])
            elif re.fullmatch(r'YE[A-Z0-9]{8,16}',folder):other.add(folder)
    if len(codes)>1:raise ColorMappingError('본문에 여러 색상 코드: '+str(sorted(codes)))
    if not codes and other:raise ProductMismatch('본문 품번이 입력과 다름: '+str(sorted(other)))
    if not codes and not search_verified:raise ProductMismatch('공식 품번 확인 불가')
    for select in doc.select('select[option_title]'):
        if select.get('option_title') not in ('사이즈','SIZE','Size'):raise ColorMappingError('추가 색상 옵션 구조 확인 필요')
    name=clean_product_name(name,['YALE','예일'],[base])
    if not name or not color:raise ValueError('상품명/색상 누락')
    return dict(product_code=base,product_name=name,color=color,color_name=color,color_code=next(iter(codes),''))


def gallery_urls(doc,number,final):
    urls=[]
    for node in doc.select('meta[property="og:image"], .xans-product-addimage img'):
        raw=node.get('content') or node.get('ec-data-src') or node.get('src')
        # small 경로의 첫 이미지는 og:image 원본과 같은 대표컷의 썸네일이므로 제외.
        if raw and '/web/product/small/' in raw:continue
        u=image_url(raw,final)
        if u and u not in urls:urls.append(u)
    return urls


def regular_price(ctx,doc):
    node=doc.select_one('#span_product_price_custom')
    value=money(node.get_text(' ',strip=True)) if node else None
    if value is None:ctx.log('공식 소비자가 없음: 정상가 빈칸','WARNING')
    return value


def detail(ctx,doc,final,cache):
    parts=[];seen=set()
    for node in doc.select('#prdDetailContent img'):
        u=image_url(node.get('ec-data-src') or node.get('data-src') or node.get('src'),final)
        if not u or u in seen:continue
        seen.add(u)
        if re.search(r'banner|coupon|shipping|delivery|review|/intro/|배송|교환|반품|쿠폰',unquote(urlsplit(u).path),re.I):continue
        try:
            available=checked(ctx,u,cache,detail=True)
        except ImageLookupError:
            ctx.log('본문 이미지 서버 연결 확인 미완료: 공식 원본 주소 보존 / '+u,'WARNING')
            available=True
        if available:parts.append('<p><img src="'+html.escape(u,quote=True)+'" alt="상품 상세" style="max-width:100%;height:auto"></p>')
    if not parts:ctx.log('본문 이미지 없음: 상세 생략','WARNING')
    return '<div>'+''.join(parts)+'</div>' if parts else ''
