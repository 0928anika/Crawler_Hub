PORTAL_API_VERSION = 1

import hashlib
import ast
import html
import json
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlencode, parse_qs

from bs4 import BeautifulSoup
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots, clean_product_name

SITE='https://jeepwear.co.kr'
HOSTS=('jeepwear.co.kr','www.jeepwear.co.kr')
DETAIL_MAX_BYTES=64*1024*1024
REVISION='2026-11-17-jeepkids-setup-color-detail'
# Official exact searches for both component codes lead to this same set-up
# product, whose official photos include both top and trousers. Keep set name
# and set price; do not present the shared page as a separately sold component.
SETUP_MODELS={'KP4TPU098':'KP4TZU098'}
POLICY={'checked_on':'2026-11-10','source':'https://marketplace.coupang.com/mba/listing',
        'route':'WING 웹/엑셀','min_side':500,'max_bytes':10000000,
        'formats':['JPEG','PNG','WEBP'],'max_aspect':1.8}
# 공식 5317/5306/5210/5211의 상품명 full SKU와 KIDS 분류,
# ProductGroup.hasVariant.image의 big 원본 및 상세 이미지 연결 확인.
# small URL을 임의 big URL로 바꾸거나 색상 코드로 색상명을 추측하지 않는다.
NEGATIVE=re.compile(r'review|ugc|shipping|delivery|coupon|banner|collage|notice|로고|배송|교환|반품|쿠폰|콜라주|실측|사이즈|고시',re.I)


def parse(source):
    return BeautifulSoup(source,'html.parser')


def field(doc,label):
    for row in doc.select('.infoArea tr'):
        head,value=row.find('th'),row.find('td')
        if head and value and head.get_text(' ',strip=True)==label:
            return value.get_text(' ',strip=True)
    return ''


def categories(doc):
    return [a.get_text(' ',strip=True) for a in doc.select('.xans-product-headcategory ol li a') if a.get_text(' ',strip=True) not in ('','홈','현재 위치')]


def product_sku(doc):
    raw=field(doc,'상품명')
    match=re.search(r'\(([A-Z0-9]{9})([A-Z0-9]{2})\)\s*$',raw,re.I)
    if match:return match[1].upper(),match[2].upper(),raw[:match.start()].strip()
    suffix=re.search(r'_([A-Z]{2})\s*$',raw,re.I)
    if suffix:
        pairs=set()
        for node in doc.select('#prdDetail .cont img'):
            url=urlsplit(node.get('ec-data-src') or node.get('data-src') or node.get('src') or '')
            if url.hostname!='hollhaus.speedgabia.com' or not re.match(r'^/jeep(?:kids)?/detail/',url.path):continue
            photo=re.fullmatch(r'(?:\d+[_-])?([A-Z0-9]{9})[_-]([A-Z]{2})(?:[_-]\d+)?\.(?:jpe?g|png|webp)',url.path.rsplit('/',1)[-1],re.I)
            if photo:pairs.add((photo[1].upper(),photo[2].upper()))
        if len(pairs)==1 and next(iter(pairs))[1]==suffix[1].upper():
            model,color=next(iter(pairs));return model,color,raw[:suffix.start()].strip()
    raise ValueError('공식 상품명/상품 상세 이미지에서 품번·색상 확인 실패: '+raw)


def color_name(doc,code):
    for row in doc.select('.infoArea tr'):
        head,value=row.find('th'),row.find('td')
        if not head or not value or head.get_text(' ',strip=True) not in ('색상','컬러'):continue
        options=[]
        for option in value.select('select option[value]'):
            if option['value'] in ('','*','**'):continue
            label=re.sub(r'\s*\[(?:품절|일시품절|SOLD\s*OUT)\]\s*','',option.get_text(' ',strip=True),flags=re.I).strip()
            if label:options.append(label)
        # Static labelled information is independent from the option widget.
        static=parse(str(value))
        for node in static.select('select,script,style'):node.decompose()
        label=static.get_text(' ',strip=True)
        if not label:
            matches=[v for v in options if v.upper()==code or re.search(r'\('+re.escape(code)+r'\)',v,re.I)]
            label=matches[0] if len(matches)==1 else ''
        if re.search(r'옵션.*선택|\[필수\]|^-+$',label):return ''
        return '' if label.upper()==code else label
    return ''


def identity(doc,base):
    raw=field(doc,'상품명')
    model,code,title=product_sku(doc)
    if model!=base and not ('셋업' in title and SETUP_MODELS.get(base)==model):return None
    kids_category=any(c.upper()=='KIDS' or '키즈' in c for c in categories(doc))
    kids_title=bool(re.search(r'\bJEEPKIDS\b|\bJEEP\s+KIDS\b|\[KIDS\]|지프\s*키즈',raw,re.I))
    if not (kids_category or kids_title):
        raise ValueError('공식 KIDS 카테고리 상품인지 확인할 수 없습니다.')
    name=clean_product_name(title,['지프키즈','지프 키즈','JEEP KIDS','JEEPKIDS','[KIDS]','Jeep®'],[base])
    if not name:raise ValueError('정리 후 상품명 없음')
    color=color_name(doc,code)
    return dict(product_code=base,product_name=name,color=color or code,color_name=color,color_code=code,원본상품명=raw)


def product_groups(doc,raw):
    result=[]
    for script in doc.find_all('script',type='application/ld+json'):
        try:data=json.loads(script.get_text())
        except json.JSONDecodeError:continue
        entries=data if isinstance(data,list) else [data]
        for entry in entries:
            if isinstance(entry,dict) and entry.get('@type')=='ProductGroup' and entry.get('name')==raw:
                result.append(entry)
    return result


def gallery_urls(doc,final,raw):
    urls=[]
    # 실제 공식 JSON-LD는 큰 원본의 별도 해시를 제공한다.
    groups=product_groups(doc,raw)
    for group in groups:
        for variant in group.get('hasVariant',[]):
            if not isinstance(variant,dict) or not str(variant.get('name','')).startswith(raw+' '):continue
            images=variant.get('image',[])
            for image in images if isinstance(images,list) else [images]:
                url=urljoin(final,image) if isinstance(image,str) else ''
                parts=urlsplit(url)
                if parts.hostname in HOSTS and re.match(r'/web/product/(?:big/|extra/big/)',parts.path) and url not in urls:
                    urls.append(url)
    # 품절 상품은 hasVariant가 비어도 해당 상품 페이지의 og:image에는
    # 같은 색상의 실제 big 원본이 남는다. 동일 상품명 ProductGroup이
    # 확인된 페이지에서만 사용하며 다른 상품/사이트의 메타 이미지는 제외.
    if groups and field(doc,'상품명')==raw:
        for node in doc.find_all('meta',property='og:image'):
            url=urljoin(final,node.get('content',''));parts=urlsplit(url)
            if parts.hostname in HOSTS and re.match(r'/web/product/(?:big/|extra/big/)',parts.path) and url not in urls:
                urls.append(url)
    for node in doc.select('.imgArea img'):
        url=urljoin(final,node.get('ec-data-src') or node.get('data-src') or node.get('src') or '')
        parts=urlsplit(url)
        if parts.hostname in HOSTS and re.match(r'/web/product/(?:big/|extra/big/)',parts.path) and url not in urls:urls.append(url)
    return [url for url in urls if not NEGATIVE.search(urlsplit(url).path)]


def detail_urls(doc,final):
    area=doc.select_one('#prdDetail .cont')
    if area is None:raise ValueError('공식 상품 상세 영역 누락')
    urls=[]
    for node in area.find_all('img'):
        url=urljoin(final,node.get('ec-data-src') or node.get('data-src') or node.get('src') or '')
        parts=urlsplit(url)
        # 상품 본문 안 공식 업로드 이미지에는 고시/사이즈표도 포함된다.
        # 갤러리용 사이즈/비율 필터를 상세에 적용하지 않는다.
        local=parts.hostname in HOSTS and parts.path.startswith('/web/upload/NNEditor/')
        external=False
        if parts.hostname=='hollhaus.speedgabia.com' and re.match(r'^/jeep(?:kids)?/detail/',parts.path):
            model,color,_=product_sku(doc)
            filename=parts.path.rsplit('/',1)[-1]
            external=bool(re.fullmatch(r'(?:\d+[_-])?'+re.escape(model)+r'[_-](?:'+re.escape(color)+r'|size|gosi|spec|detail)(?:[_-]\d+)?\.(?:jpe?g|png|webp)',filename,re.I))
            # One official size-chart filename literally has an extra "p".
            # Preserve the source URL; never invent a corrected file path.
            external=external or bool(re.fullmatch(r'p'+re.escape(model)+r'[_-]size\.(?:jpe?g|png|webp)',filename,re.I))
        if not (local or external):continue
        if re.search(r'banner|shipping|delivery|coupon|review|배송|교환|반품|쿠폰',str(node.get('alt',''))+' '+parts.path,re.I):continue
        if url not in urls:urls.append(url)
    return urls


def public_image(ctx,url,cache,detail=False):
    key=('detail',url) if detail else url
    if key not in cache:
        prior=cache.get(url,{})
        cache[key]=prior if detail and prior.get('status')=='accessible' else check_public_image(url,timeout=15 if detail else 10,max_bytes=DETAIL_MAX_BYTES if detail else 10000000,retries=1,checkpoint=ctx.checkpoint)
    result=cache[key]
    if result['status']!='accessible':
        ctx.log('이미지 확인 불가: '+url+' / '+', '.join(result['reasons']),'WARNING')
        return None
    return result


def prefetch(ctx,urls,cache):
    missing=[u for u in dict.fromkeys(urls) if u not in cache]
    # 이전 Hub의 잠금 없는 네트워크 로그 출력과도 호환된다.
    if len(missing)<2 or getattr(sys.modules.get('__main__'),'EMIT_LOCK',None) is None:return
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={u:pool.submit(check_public_image,u,timeout=10,retries=1) for u in missing}
        for url,future in futures.items():
            while not future.done():ctx.checkpoint();time.sleep(.1)
            cache[url]=future.result();ctx.checkpoint()


def gallery(ctx,urls,code,cache):
    accepted=[];seen=set()
    for url in urls:
        ctx.checkpoint();result=public_image(ctx,url,cache)
        if not result:continue
        evidence=f'공식 색상별 상품의 ProductGroup/동일 페이지 og:image big 원본; 제외 신호 없음; {result["width"]}x{result["height"]}'
        validation=validate_gallery_image(result['data'],role='product_shot',mapping_verified=True,single_shot_verified=True,
            mapping_evidence='상품명 전체 품번과 색상 '+code+' / 같은 ProductGroup의 사진',content_evidence=evidence)
        digest=hashlib.sha256(result['data']).hexdigest()
        if validation['passed'] and digest not in seen:
            seen.add(digest);accepted.append({'url':url,'validation':validation})
            ctx.log('갤러리 채택: '+evidence+' / '+url,'INFO')
        elif not validation['passed']:ctx.log('갤러리 제외: '+url+' / '+', '.join(validation['reasons']),'WARNING')
    return gallery_slots(accepted)


def detail(ctx,urls,cache):
    if not urls:
        raise ValueError('상세페이지 수집 실패: 공식 상품/사이즈 이미지 없음')
    parts=[]
    for url in urls:
        ctx.checkpoint()
        if not public_image(ctx,url,cache,detail=True):
            if any(re.search(r'HTTP (404|410)\b',r) for r in cache.get(('detail',url),cache.get(url,{})).get('reasons',[])):
                ctx.log('공식 상세 원본 404/410: 해당 파일 생략','WARNING');continue
            raise ValueError('상세 이미지 조회 미완료: '+url)
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세정보"></p>')
    if not parts:raise ValueError('유효한 상세 원본 이미지 없음')
    result='<div>'+''.join(parts)+'</div>'
    if len(result.encode('utf-16-le'))//2>32767:raise ValueError('상세 HTML Excel 셀 한도 초과')
    return result


def sizes(doc):
    result=[]
    # Dependent/sold-out selects may be empty; the original combinations remain.
    _,color,_=product_sku(doc)
    for script in doc.find_all('script'):
        stock=re.search(r"var\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*')",script.get_text())
        if not stock:continue
        try:rows=json.loads(ast.literal_eval(stock[1]))
        except (ValueError,SyntaxError,TypeError):continue
        if not isinstance(rows,dict):continue
        for row in rows.values():
            if not isinstance(row,dict):continue
            names=row.get('option_name_original');values=row.get('option_value_orginal')
            if not isinstance(names,list) or not isinstance(values,list) or len(names)!=len(values):continue
            options=dict(zip(names,values));selected=options.get('색상',options.get('Color'))
            if color and selected and str(selected).upper()!=color:continue
            size=options.get('사이즈',options.get('Size'))
            if isinstance(size,str) and size.strip() and size.strip() not in result:result.append(size.strip())
    if result:return ','.join(result)
    for select in doc.select('select[product_type="product_option"][option_title="사이즈"]'):
        for option in select.select('option[value]'):
            value=option.get_text(' ',strip=True)
            value=re.sub(r'\s*\[(?:품절|일시품절|SOLD\s*OUT)\]\s*','',value,flags=re.I).strip()
            if option['value'] not in ('','*','**') and value and value not in result:result.append(value)
    if not result:raise ValueError('공식 사이즈 옵션 확인 실패')
    return ','.join(result)


def price(ctx,doc):
    text=field(doc,'판매가')
    match=re.fullmatch(r'\s*(?:₩|KRW)?\s*([\d,]+)\s*원?\s*',text)
    if not match:
        ctx.log('공식 KRW 판매가 형식 확인 불가: 빈칸','WARNING');return None
    value=int(match[1].replace(',',''))
    if not value:ctx.log('공식 판매가 0: 빈칸','WARNING');return None
    return value


def visit(ctx,page,url):
    if urlsplit(url).hostname not in HOSTS:raise ValueError('지프웨어 공식몰 외부 URL')
    last=None
    for attempt in range(2):
        ctx.checkpoint()
        try:
            response=page.goto(url,wait_until='domcontentloaded',timeout=45000)
            if response is None or response.status>=400:raise ValueError('공식 페이지 HTTP 실패')
            if urlsplit(page.url).hostname not in HOSTS:raise ValueError('공식몰 외부 이동')
            return parse(page.content()),page.url
        except Exception as exc:last=exc;ctx.log('공식 조회 재시도: '+str(exc),'WARNING')
        finally:ctx.checkpoint()
    raise RuntimeError('공식 페이지 조회 실패: '+str(last))


def search(ctx,page,base):
    queue=[SITE+'/product/search.html?'+urlencode({'keyword':base})];seen=set();links=[]
    while queue:
        ctx.checkpoint();url=queue.pop(0);params=parse_qs(urlsplit(url).query)
        key=(params.get('keyword',[base])[0],params.get('page',['1'])[0])
        if key in seen:continue
        if len(seen)>=30:raise ValueError('검색 페이지 안전 한도 초과')
        seen.add(key);doc,final=visit(ctx,page,url)
        for node in doc.select('.prdList .name a[href]'):
            link=urljoin(final,node['href'])
            if urlsplit(link).hostname in HOSTS and link not in links:links.append(link)
        for node in doc.select('.xans-search-paging a[href]'):
            if node['href'].startswith('#'):continue
            link=urljoin(final,node['href']);parts=urlsplit(link);p=parse_qs(parts.query)
            if parts.hostname in HOSTS and p.get('keyword')==[base] and (base,p.get('page',['1'])[0]) not in seen and link not in queue:queue.append(link)
    return links


def crawl(ctx):
    from playwright.sync_api import sync_playwright
    raw=str(ctx.product_code).strip().upper()
    match=re.fullmatch(r'([A-Z0-9]{9})(?:[_-]?([A-Z0-9]{2}))?',raw)
    if not match:
        ctx.fail_item('기본 품번 9자리 또는 품번+색상코드 2자리 형식으로 입력하세요.')
        raise ValueError('입력 품번 형식 오류')
    base,requested=match[1],match[2];failures=[];emitted=set()
    ctx.log('지프키즈 코드 버전: '+REVISION,'INFO')
    try:
        with sync_playwright() as p:
            folder=Path(ctx.output_dir)/'jeepkids-browser'/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=True)
            browser=p.chromium.launch_persistent_context(str(folder),channel='chrome',headless=True,locale='ko-KR')
            try:
                browser.route('**/*',lambda r:r.abort() if r.request.resource_type in ('image','media','font') else r.continue_())
                page=browser.new_page();ctx.stage('지프키즈 공식 검색: '+base)
                for link in search(ctx,page,base):
                    ctx.checkpoint()
                    try:
                        doc,final=visit(ctx,page,link);record=identity(doc,base)
                        if record is None:continue
                        code=record['color_code']
                        if requested and requested!=code:continue
                        if code in emitted:continue
                        record['product_url']=final;raw_name=record.pop('원본상품명');record['원본 상품명']=raw_name
                        ctx.stage('지프키즈 색상 '+code)
                        if ctx.enabled('size'):record['size']=sizes(doc)
                        if ctx.enabled('price'):record['price']=price(ctx,doc)
                        if ctx.enabled('regular_price'):
                            record['regular_price']=None;ctx.log('공식 독립 정상가 출처 미확인: 빈칸','WARNING')
                        if ctx.enabled('category'):record['category']=' > '.join(categories(doc))
                        photos=gallery_urls(doc,final,raw_name) if ctx.enabled('main_image') or ctx.enabled('additional_images') else []
                        details=detail_urls(doc,final) if ctx.enabled('detail_page') else []
                        cache={};prefetch(ctx,photos,cache)
                        if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                            main,extras,warning=gallery(ctx,photos,code,cache)
                            if warning:ctx.log(warning,'WARNING')
                            if ctx.enabled('main_image'):record['main_image']=main or ''
                            if ctx.enabled('additional_images'):record['additional_images']=','.join(extras)
                        if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,details,cache)
                        ctx.emit_result(record);emitted.add(code)
                    except Exception as exc:
                        msg=link+': '+str(exc);failures.append(msg);ctx.fail_item(msg)
                if not emitted:raise ValueError('정확한 품번/지정 색상의 KIDS 상품 없음: '+raw)
            finally:browser.close()
    except Exception as exc:
        if not failures:ctx.fail_item(str(exc))
        raise
    if failures:raise RuntimeError('일부 색상 미완료: '+' / '.join(failures))
