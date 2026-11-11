PORTAL_API_VERSION = 1

import hashlib
import html
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit, parse_qs, urlencode
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup, NavigableString
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots, clean_product_name

SITE = 'https://www.byn.kr'
BRAND_PATH = 'blackyak'
REVISION = '2026-11-11-blackyak-2'
POLICY = dict(checked_on='2026-11-10',source='https://marketplace.coupang.com/mba/listing',min_side=500,max_bytes=10000000,formats=['JPEG','PNG','WEBP'],max_aspect=1.8)
# 실제 공식 STYLE 1BYGJFV002 + 색상 FB/DY의 별도 pno와 원본 갤러리 확인.
# 원본 상품 고시는 텍스트, 숨겨진 사이즈 가이드는 HTML 표로 이미 제공된다.


def parse(source):
    return BeautifulSoup(source,'html.parser')


def official(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.hostname in ('www.byn.kr','byn.kr') and not p.username and not p.password


def identity(doc,base):
    node=doc.select_one('.prd-desc__code-value')
    if not node:raise ValueError('공식 상품코드 누락: 언어 선택/오류 페이지 또는 형식 변경')
    sku=node.get_text(strip=True).upper();m=re.fullmatch(r'([A-Z0-9]{10})([A-Z0-9]{2})',sku)
    if not m:raise ValueError('공식 전체 품번 형식 확인 실패: '+sku)
    if m[1]!=base:return None
    canonical = doc.select_one('meta[property="og:url"]')
    if canonical:
        brand = parse_qs(urlsplit(canonical.get('content', '')).query).get('hta_brand')
        if brand and brand != [BRAND_PATH]:return None
    raw_node=doc.select_one('.prd-info__name');raw=raw_node.get_text(' ',strip=True) if raw_node else ''
    color_node=doc.select_one('.prd-info__color-name');color=color_node.get_text(' ',strip=True) if color_node else ''
    name=clean_product_name(raw,['블랙야크','BLACKYAK','BLACK YAK'],[base,sku])
    if not name:raise ValueError('공식 상품명 없음')
    return dict(product_code=base,product_name=name,color=color or m[2],color_name=color,color_code=m[2],**{'원본 상품명':raw,'공식 전체 품번':sku})


def linked_colors(doc,final):
    result=[]
    for n in doc.select('.prd-info__color-chip[href]'):
        url=urljoin(final,n['href'])
        if official(url) and urlsplit(url).path.endswith('/shop/detail.php') and parse_qs(urlsplit(url).query).get('pno') and url not in result:result.append(url)
    return result


def search(ctx,base):
    queue=[SITE+'/'+BRAND_PATH+'/shop/search_result.php?'+urlencode({'search_str':base})];seen=set();links=[]
    while queue:
        url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise ValueError('공식 검색 페이지 안전 한도 초과')
        seen.add(url);source,final=fetch(ctx,url);doc=parse(source)
        for n in doc.select('#prd_basic a[href]'):
            link=urljoin(final,n['href'])
            if official(link) and urlsplit(link).path.endswith('/shop/detail.php') and parse_qs(urlsplit(link).query).get('pno') and link not in links:links.append(link)
        for n in doc.select('#paging_box a[href]'):
            link=urljoin(final,n['href']);p=urlsplit(link)
            if official(link) and p.path.endswith('/shop/search_result.php') and parse_qs(p.query).get('search_str')==[base] and link not in seen and link not in queue:queue.append(link)
            elif 'getProListPage' in n.get('onclick','') or 'getProListPage' in n['href']:
                raise ValueError('추가 검색 페이지의 공개 링크 확인 필요: 누락 방지 중단')
    return links


def sizes(doc):
    result=[]
    for n in doc.select('.prd-info__size-chip'):
        text=n.get_text(' ',strip=True)
        if text and text not in result:result.append(text)
    if not result:raise ValueError('공식 사이즈 옵션 없음')
    return ','.join(result)


def price(ctx,doc,regular=False):
    selector='.prd-info__price-original .prd-info__price-num' if regular else '.prd-info__price-current .prd-info__price-num'
    label='정상가' if regular else '판매가';n=doc.select_one(selector);text=n.get_text(strip=True) if n else ''
    currency=re.search(r'\bvar\s+currency\s*=\s*[\'"]([^\'"]*)[\'"]',str(doc))
    match=re.fullmatch(r'(\d{1,3}(?:,\d{3})+|\d+)',text)
    if not currency or currency[1]!='원' or not match:
        ctx.log('공식 '+label+' KRW 값 확인 불가: 빈칸 / '+text,'WARNING');return None
    value=int(match[1].replace(',',''))
    if not value:ctx.log('공식 '+label+' 0: 빈칸','WARNING');return None
    return value


def category(doc):
    n=doc.select_one('input[name="toss_cate_name"]')
    return n.get('value','').strip() if n else ''


def gallery_urls(doc,final):
    urls=[]
    for n in doc.select('.prd-thumb__item[data-type="image"] img'):
        raw=n.get('data-src') or n.get('src') or '';url=urljoin(final,raw);p=urlsplit(url)
        if p.scheme not in ('http','https') or p.hostname not in ('picweb.blackyak.com','cdn.byn.kr') or p.username or p.password:continue
        # 공식 색상칩에도 쓰는 동일 picweb HTTPS 원본의 실제 GET/해독 확인.
        # 파일명/해상도는 바꾸지 않으며 HTTP의 간헐적 응답 지연을 피한다.
        if p.scheme=='http' and p.hostname=='picweb.blackyak.com':url='https:'+url[5:]
        if re.search(r'banner|coupon|review|ugc|size.?guide|size.?chart|measurement|collage|배너|쿠폰|배송|반품|사이즈|고시|콜라주',p.path+' '+n.get('alt',''),re.I):continue
        if url not in urls:urls.append(url)
    return urls


def gallery(ctx,urls,code,cache):
    accepted=[];seen=set()
    for url in urls:
        ctx.checkpoint();result=public_image(ctx,url,cache)
        if not result:continue
        validation=validate_gallery_image(result['data'],role='product_shot',mapping_verified=True,single_shot_verified=True,
            mapping_evidence='공식 전체 SKU 확인한 색상별 pno의 prd-thumb 갤러리 '+code,content_evidence='공식 개별 상품 전후면/디테일 컷. 배너·색상칩·리뷰·고시/가이드 별도 영역 제외')
        digest=hashlib.sha256(result['data']).hexdigest()
        if validation['passed'] and digest not in seen:
            seen.add(digest);accepted.append(dict(url=url,validation=validation))
        elif not validation['passed']:ctx.log('갤러리 제외: '+url+' / '+', '.join(validation['reasons']),'WARNING')
    return gallery_slots(accepted)


def notice_pairs(doc):
    for box in doc.select('.prd-notice__accordion'):
        title=box.select_one('.prd-notice__accordion-title')
        if not title or title.get_text(strip=True)!='상품정보제공고시':continue
        area=box.select_one('.prd-notice__dl')
        if not area:return []
        pairs=[]
        for dt in area.find_all('dt',recursive=False):
            dd=dt.find_next_sibling()
            if dd and dd.name=='dd':pairs.append((dt.get_text(' ',strip=True),dd.get_text(' ',strip=True)))
        return pairs
    return []


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


def detail(ctx,doc,urls,cache):
    parts=[]
    transient_failures = []
    for url in urls:
        ctx.checkpoint()
        if not public_image(ctx,url,cache):
            result = cache.get(url, {})
            if result.get('status') == 'unknown':
                transient_failures.append(url)
                continue
            if any(re.search(r'HTTP (404|410)\b',r) for r in result.get('reasons',[])):
                ctx.log('없는 상품컷 생략: '+url,'WARNING');continue
            raise ValueError('상세용 상품컷 조회 미완료: '+url)
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품컷" style="max-width:100%;height:auto"></p>')
    if transient_failures:
        if not parts:
            raise ValueError('상세용 상품컷 확인 실패: '+', '.join(transient_failures))
        ctx.log('재시도 후에도 응답하지 않은 상세 사진 생략: '+', '.join(transient_failures),'WARNING')
    pairs=notice_pairs(doc)
    if pairs:
        parts.append('<h3>상품정보제공고시</h3>')
        for label,value in pairs:
            if not value:ctx.log('고시 원문 값 비어 있음: '+label,'WARNING')
            parts.append('<p><strong>'+html.escape(label)+':</strong> '+html.escape(value)+'</p>')
    else:ctx.log('상품정보제공고시 원문 없음: 생성하지 않음','WARNING')
    guide=doc.select_one('.prd-modal-size__table')
    table=guide.find('table') if guide else None
    if table:
        parts.append('<h3>사이즈 가이드</h3>'+table_html(table))
        unit=guide.select_one('.prd-modal-size__table-desc')
        if unit:parts.append('<p>'+html.escape(unit.get_text(' ',strip=True))+'</p>')
    else:ctx.log('사이즈 가이드 표 없음: 생성하지 않음','WARNING')
    result='<div>'+''.join(parts)+'</div>' if parts else ''
    if len(result.encode('utf-16-le'))//2>32767:raise ValueError('상세 HTML Excel 셀 한도 초과')
    return result


def fetch(ctx, url):
    if not official(url):
        raise ValueError('블랙야크 공식 홈페이지 외부 URL')
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
        futures = {u: pool.submit(check_public_image, u, timeout=8, retries=0) for u in missing}
        for url, future in futures.items():
            while not future.done():
                ctx.checkpoint()
                time.sleep(.1)
            cache[url] = future.result()
            ctx.checkpoint()
    # 병렬 요청이 끝난 뒤 일시 실패한 원본만 한 장씩 다시 확인한다.
    for url in missing:
        if cache[url]['status'] == 'unknown':
            ctx.log('응답이 지연된 사진 개별 재조회: '+url, 'INFO')
            cache[url] = check_public_image(url, timeout=10, retries=1, checkpoint=ctx.checkpoint)

def crawl(ctx):
    raw=str(ctx.product_code).strip().upper();match=re.fullmatch(r'([A-Z0-9]{10})(?:[_-]?([A-Z0-9]{2}))?',raw)
    if not match:
        ctx.fail_item('기본 품번 10자리 또는 품번+색상코드 2자리로 입력하세요.')
        raise ValueError('입력 품번 형식 오류')
    base,requested=match[1],match[2];emitted=set();seen=set();cache={};failures=[]
    ctx.log('블랙야크 코드 버전: '+REVISION,'INFO')
    try:
        ctx.stage('블랙야크 공식 검색: '+base);queue=search(ctx,base+(requested or ''))
        while queue:
            ctx.checkpoint();url=queue.pop(0);params=parse_qs(urlsplit(url).query);key=params.get('pno',[''])[0]
            if key in seen:continue
            if len(seen)>=100:raise ValueError('상품 색상 페이지 안전 한도 초과')
            seen.add(key)
            try:
                source,final=fetch(ctx,url);doc=parse(source);record=identity(doc,base)
                if record is None:continue
                if not requested:queue.extend(u for u in linked_colors(doc,final) if parse_qs(urlsplit(u).query).get('pno',[''])[0] not in seen and u not in queue)
                code=record['color_code']
                if (requested and code!=requested) or code in emitted:continue
                record['product_url']=final;ctx.stage('블랙야크 색상 '+code)
                if ctx.enabled('size'):record['size']=sizes(doc)
                if ctx.enabled('price'):record['price']=price(ctx,doc)
                if ctx.enabled('regular_price'):record['regular_price']=price(ctx,doc,True)
                if ctx.enabled('category'):record['category']=category(doc)
                urls=gallery_urls(doc,final) if ctx.enabled('main_image') or ctx.enabled('additional_images') or ctx.enabled('detail_page') else []
                prefetch(ctx,urls,cache)
                if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                    main,extras,warning=gallery(ctx,urls,code,cache)
                    if warning:ctx.log(warning,'WARNING')
                    if ctx.enabled('main_image'):record['main_image']=main or ''
                    if ctx.enabled('additional_images'):record['additional_images']=','.join(extras)
                if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,urls,cache)
                ctx.emit_result(record);emitted.add(code)
            except Exception as exc:
                message=url+': '+str(exc);ctx.fail_item(message);failures.append(message)
        if not emitted and not failures:raise ValueError('정확한 품번/지정 색상 결과 없음: '+raw)
    except Exception as exc:
        if not failures:ctx.fail_item(str(exc))
        raise
    if failures:raise RuntimeError('일부 색상 미완료: '+' / '.join(failures))
