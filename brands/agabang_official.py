PORTAL_API_VERSION = 1

import hashlib
import html
import json
import math
import re
import uuid
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlencode, parse_qs, unquote

from bs4 import BeautifulSoup
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots

SITE = 'https://agabangmall.com'
HOSTS = ('agabangmall.com', 'www.agabangmall.com')
REVISION = '2026-11-09-agabang-2'
POLICY = {'checked_on':'2026-11-09', 'source':'https://marketplace.coupang.com/mba/listing',
          'route':'WING 웹/엑셀', 'min_side':500, 'max_bytes':10000000,
          'formats':['JPEG','PNG','WEBP'], 'max_aspect':1.8}
# 공식 상품 55628/55656의 모델, 상품명 끝 전체 코드, 단일 색상 옵션,
# 색상별 독립 갤러리 및 상세 이미지 파일명을 대조했다.
# 고시/사이즈표는 상세 이미지에 포함되어 있어 재작성하지 않는다.
NEGATIVE = re.compile(r'review|ugc|banner|event_bn|_brand_|_bottom_|shipping|delivery|coupon|care_|collage|사이즈|실측|측정|고시|리뷰|배송|이벤트|브랜드 설명|하단 공통',re.I)


def soup(source):
    return BeautifulSoup(source,'html.parser')


def field(doc, label):
    for row in doc.select('.infoArea tr'):
        head, value = row.find('th'), row.find('td')
        if head and value and head.get_text(' ',strip=True)==label:
            return value.get_text(' ',strip=True)
    return ''


def identity(doc, base):
    model=field(doc,'모델').upper()
    if model!=base:
        return None
    if field(doc,'브랜드')!='아가방':
        raise ValueError('브랜드 필드가 아가방이 아닙니다.')
    raw=field(doc,'상품명')
    match=re.search(r'_('+re.escape(base)+r')(\d{2})\s*$',raw,re.I)
    if not match:
        raise ValueError('공식 상품명에서 모델+숫자 색상코드를 확인하지 못했습니다: '+raw)
    names=[]
    for select in doc.select('select[option_title="색상"][product_type="product_option"]'):
        for option in select.select('option[value]'):
            if option['value'] not in ('','*','**'):
                name=option.get_text(' ',strip=True)
                if name not in names:names.append(name)
    if len(names)!=1:
        raise ValueError('단일 색상 상품과 갤러리 연결을 확인할 수 없습니다.')
    color=names[0]
    title=re.sub(r'_'+re.escape(base)+r'\d{2}\s*$','',raw,flags=re.I)
    if not re.search(r'\('+re.escape(color)+r'\)\s*$',title,re.I):
        raise ValueError('공식 상품명과 색상 옵션 불일치')
    title=re.sub(r'\('+re.escape(color)+r'\)\s*$','',title,flags=re.I)
    title=re.sub(r'\[[^\]]*아가방[^\]]*\]','',title)
    title=re.sub(r'(?<![A-Za-z0-9])(?:아가방|AGABANG)(?![A-Za-z0-9])','',title,flags=re.I)
    title=re.sub(r'\s+',' ',title).strip(' _-')
    if not title:raise ValueError('정리 후 상품명 누락')
    return dict(product_code=base,product_name=title,color=color,color_name=color,
                color_code=match[2],원본상품명=raw)


def stocks(doc):
    for script in doc.find_all('script',src=False):
        match=re.search(r"var\s+option_stock_data\s*=\s*'((?:\\.|[^'\\])*)'",script.get_text())
        if match:
            value=json.loads(json.loads('"'+match[1]+'"'))
            if not isinstance(value,dict) or not value:raise ValueError('공식 옵션 데이터 비어 있음')
            return list(value.values())
    raise ValueError('공식 옵션 데이터 확인 실패')


def matching_stocks(doc,color):
    result=[v for v in stocks(doc) if isinstance(v.get('option_value_orginal'),list) and len(v['option_value_orginal'])==2 and str(v['option_value_orginal'][0])==color]
    if not result:raise ValueError('색상별 사이즈/가격 옵션 확인 실패: '+color)
    return result


def numeric_price(value):
    if isinstance(value,bool):return None
    if isinstance(value,(int,float)):
        return value if math.isfinite(value) and value>0 else None
    match=re.fullmatch(r'\s*([\d,]+)\s*원?\s*',str(value or ''))
    if not match:return None
    number=int(match[1].replace(',',''))
    return number if number>0 else None


def selling_price(ctx, variants):
    values=[numeric_price(v.get('option_price')) for v in variants]
    if not values or None in values or len(set(values))!=1:
        ctx.log('판매가 누락/형식 오류/사이즈별 가격 상이: 빈칸','WARNING')
        return None
    return values[0]


def regular_price(ctx,doc):
    try:
        result=numeric_price(field(doc,'소비자가'))
        if result is None:ctx.log('독립 소비자가 없음/0/형식 오류: 정상가 빈칸','WARNING')
        return result
    except Exception as exc:
        ctx.log('정상가 확인 불가: '+str(exc)+' / 나머지 수집 계속','WARNING')
        return None


def visit(ctx,page,url):
    if urlsplit(url).hostname not in HOSTS:raise ValueError('공식몰 외부 URL')
    last=None
    for attempt in range(2):
        ctx.checkpoint()
        try:
            response=page.goto(url,wait_until='domcontentloaded',timeout=45000)
            if response and response.status>=400:raise ValueError('HTTP '+str(response.status))
            if urlsplit(page.url).hostname not in HOSTS:raise ValueError('공식몰 밖으로 이동')
            return soup(page.content()),page.url
        except Exception as exc:
            last=exc
            ctx.log('공식 페이지 조회 재시도: '+str(exc),'WARNING')
        finally:ctx.checkpoint()
    raise RuntimeError('공식 페이지 조회 실패: '+str(last))


def search(ctx,page,base):
    queue=[SITE+'/product/search.html?'+urlencode({'keyword':base})]
    visited,links=set(),[]
    while queue:
        ctx.checkpoint()
        url=queue.pop(0)
        key=(parse_qs(urlsplit(url).query).get('keyword',[base])[0],parse_qs(urlsplit(url).query).get('page',['1'])[0])
        if key in visited:continue
        if len(visited)>=30:raise ValueError('공식 검색 30페이지 초과')
        visited.add(key)
        doc,final=visit(ctx,page,url)
        for a in doc.select('.prdList .name a[href]'):
            link=urljoin(final,a['href'])
            if urlsplit(link).hostname in HOSTS and link not in links:links.append(link)
        for a in doc.select('.xans-search-paging a[href]'):
            if a['href'].startswith('#'):continue
            link=urljoin(final,a['href']);parts=urlsplit(link)
            next_key=(parse_qs(parts.query).get('keyword',[''])[0],parse_qs(parts.query).get('page',['1'])[0])
            if parts.hostname in HOSTS and parse_qs(parts.query).get('keyword')==[base] and next_key not in visited and link not in queue:
                queue.append(link)
    return links


def public_image(ctx,url,cache):
    if url not in cache:
        ctx.checkpoint()
        cache[url]=check_public_image(url,timeout=10,retries=1,checkpoint=ctx.checkpoint)
        ctx.checkpoint()
    result=cache[url]
    if result['status']!='accessible':
        ctx.log('이미지 접근/해독 확인 불가: '+url+' / '+', '.join(result['reasons']),'WARNING')
        return None
    return result


def gallery_urls(doc,final):
    urls=[]
    for image in doc.select('.imgArea img'):
        src=image.get('ec-data-src') or image.get('data-src') or image.get('src') or ''
        url=urljoin(final,src);path=urlsplit(url).path
        if urlsplit(url).hostname!='ecimg.cafe24img.com' or not re.search(r'/web/product/(?:big/|extra/big/)',path):continue
        if NEGATIVE.search(path+' '+str(image.get('alt',''))):continue
        if url not in urls:urls.append(url)
    return urls


def prefetch_images(ctx,doc,final,base,code,cache):
    # Parallel network hooks are safe only on workers with serialized stdout.
    # Older Hub runtimes keep the existing sequential path.
    if getattr(sys.modules.get('__main__'),'EMIT_LOCK',None) is None:return
    urls=[]
    if ctx.enabled('main_image') or ctx.enabled('additional_images'):
        urls.extend(gallery_urls(doc,final))
    if ctx.enabled('detail_page'):
        urls.extend(detail_urls(doc,final,base,code))
    missing=[u for u in dict.fromkeys(urls) if u not in cache]
    if len(missing)<2:return
    started=time.monotonic()
    ctx.stage('아가방 선택 이미지 병렬 확인: '+str(len(missing))+'개')
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending={u:pool.submit(check_public_image,u,timeout=10,retries=1) for u in missing}
        for url,future in pending.items():
            while not future.done():
                ctx.checkpoint();time.sleep(.1)
            cache[url]=future.result()
            ctx.checkpoint()
    ctx.log(f'선택 이미지 {len(missing)}개 확인: {time.monotonic()-started:.1f}초','INFO')


def gallery(ctx,doc,final,cache):
    accepted,seen=[],set()
    for url in gallery_urls(doc,final):
        ctx.checkpoint()
        result=public_image(ctx,url,cache)
        if not result:continue
        evidence=f'공식 단일 색상 상품 .imgArea big 갤러리; 제외 신호 없음; {result["width"]}x{result["height"]}'
        validation=validate_gallery_image(result['data'],role='product_shot',mapping_verified=True,single_shot_verified=True,
            mapping_evidence='모델+전체 코드와 단일 색상 옵션이 일치하는 독립 상품 페이지',content_evidence=evidence)
        digest=hashlib.sha256(result['data']).hexdigest()
        if validation['passed'] and digest not in seen:
            seen.add(digest);accepted.append({'url':url,'validation':validation})
            ctx.log('상품 갤러리 채택: '+evidence+' / '+url,'INFO')
        elif not validation['passed']:
            ctx.log('상품 갤러리 제외: '+', '.join(validation['reasons'])+' / '+url,'WARNING')
    return gallery_slots(accepted)


def detail_urls(doc,final,base,code):
    area=doc.select_one('#prdDetail')
    if area is None:raise ValueError('공식 상세 영역 확인 실패')
    result=[]
    for image in area.find_all('img'):
        src=image.get('ec-data-src') or image.get('data-src') or image.get('src') or ''
        url=urljoin(final,src)
        if urlsplit(url).scheme not in ('http','https'):continue
        label=unquote(urlsplit(url).path)+' '+str(image.get('alt',''))
        if NEGATIVE.search(label):continue
        filename=unquote(urlsplit(url).path).rsplit('/',1)[-1]
        # 실제 상품 코드 이미지. 공통 이벤트/브랜드/배송 이미지는 요청하지 않는다.
        if not re.search(re.escape(base)+r'(?:'+re.escape(code)+r')?(?:[_-]\d+)?\.(?:jpe?g|png|webp)$',filename,re.I):continue
        if url not in result:result.append(url)
    return result


def detail(ctx,doc,final,base,code,cache):
    urls=detail_urls(doc,final,base,code)
    if not urls:
        ctx.log('상품 코드에 연결된 상세 이미지 없음: 사이즈표/고시를 임의로 생성하지 않음','WARNING')
        return ''
    parts=[]
    for url in urls:
        if not public_image(ctx,url,cache):raise ValueError('선택한 상세 이미지 조회 미완료: '+url)
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세정보"></p>')
    result='<div>'+''.join(parts)+'</div>'
    if len(result.encode('utf-16-le'))//2>32767:raise ValueError('상세 HTML 엑셀 셀 한도 초과')
    return result


def crawl(ctx):
    from playwright.sync_api import sync_playwright
    raw=str(ctx.product_code).strip().upper()
    match=re.fullmatch(r'(\d{2}[A-Z]\d{6})(?:[_-]?(\d{2}))?',raw)
    if not match:
        ctx.fail_item('품번 형식 오류: 01T721012 또는 01T72101293 / 01T721012_93 / 01T721012-93')
        raise ValueError('품번 형식 오류')
    base,requested=match[1],match[2]
    ctx.log('아가방 코드 버전: '+REVISION,'INFO')
    failures=[];emitted=set()
    try:
        with sync_playwright() as p:
            folder=Path(ctx.output_dir)/'agabang-browser'/uuid.uuid4().hex
            folder.mkdir(parents=True,exist_ok=True)
            browser=p.chromium.launch_persistent_context(str(folder),channel='chrome',headless=True,locale='ko-KR')
            try:
                browser.route('**/*',lambda route: route.abort() if route.request.resource_type in ('image','media','font') else route.continue_())
                page=browser.new_page()
                ctx.stage('아가방 공식 품번 검색: '+base)
                links=search(ctx,page,base)
                for link in links:
                    ctx.checkpoint()
                    try:
                        doc,final=visit(ctx,page,link)
                        record=identity(doc,base)
                        if record is None:continue
                        code=record['color_code']
                        if requested and code!=requested:continue
                        if code in emitted:continue
                        record['원본 상품명']=record.pop('원본상품명')
                        record['product_url']=final
                        ctx.stage('아가방 색상 '+code+': '+record['color'])
                        variants=matching_stocks(doc,record['color']) if ctx.enabled('size') or ctx.enabled('price') else []
                        if ctx.enabled('size'):
                            record['size']=','.join(dict.fromkeys(str(v['option_value_orginal'][1]).strip() for v in variants))
                            if not record['size']:raise ValueError('공식 사이즈 옵션 없음')
                        if ctx.enabled('price'):record['price']=selling_price(ctx,variants)
                        if ctx.enabled('regular_price'):record['regular_price']=regular_price(ctx,doc)
                        if ctx.enabled('category'):
                            record['category']=' > '.join(a.get_text(' ',strip=True) for a in doc.select('.xans-product-headcategory ol li a') if a.get_text(strip=True) not in ('홈','현재 위치'))
                        cache={}
                        prefetch_images(ctx,doc,final,base,code,cache)
                        if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                            main,extra,warning=gallery(ctx,doc,final,cache)
                            if warning:ctx.log(warning,'WARNING')
                            if ctx.enabled('main_image'):record['main_image']=main or ''
                            if ctx.enabled('additional_images'):record['additional_images']=','.join(extra)
                        if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,final,base,code,cache)
                        ctx.emit_result(record);emitted.add(code)
                    except Exception as exc:
                        message=link+': '+str(exc)
                        failures.append(message);ctx.fail_item(message)
                if not emitted:
                    raise ValueError('정확한 공식 품번/지정 색상 상품을 찾지 못했습니다: '+raw)
            finally:browser.close()
    except Exception as exc:
        if not failures:ctx.fail_item(str(exc))
        raise
    if failures:raise RuntimeError('일부 색상 수집 미완료: '+' / '.join(failures))
