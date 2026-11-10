PORTAL_API_VERSION = 1

import hashlib
import html
import json
import re
import sys
import time
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs, urlencode, quote
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots, clean_product_name

SITE = 'https://whoau.com'
REVISION = '2026-11-10-whoau-2'
DETAIL_MAX_BYTES = 64 * 1024 * 1024
POLICY = dict(checked_on='2026-11-10', source='https://marketplace.coupang.com/mba/listing', min_side=500, max_bytes=10000000, formats=['JPEG','PNG','WEBP'], max_aspect=1.8)
# 실제 WHMAG4V22U 검색 결과 5696/5692와 상세 이미지의 품번 확인.
# 색상 option link_image 연결만 사용. JSON-LD.image는 전 색상 공통이므로 사용하지 않음.

def parse(source):
    return BeautifulSoup(source, 'html.parser')

def official(url):
    p = urlsplit(url)
    return p.scheme == 'https' and p.hostname in ('whoau.com','www.whoau.com') and not p.username and not p.password

def image_url(raw, final=SITE):
    url = urljoin(final, raw)
    p = urlsplit(url)
    allowed = p.hostname == 'cafe24.poxo.com' and p.path.startswith('/ec01/whoaukr/') or p.hostname == 'item-imghub.eland.co.kr'
    if p.scheme != 'https' or not allowed or p.username or p.password:
        return ''
    return urlunsplit((p.scheme,p.netloc,quote(p.path,safe="/%:@+=$,;~!*'()"),p.query,''))

def field(doc, label):
    for row in doc.select('.infoArea tr'):
        th, td = row.find('th'), row.find('td')
        if th and td and th.get_text(' ',strip=True) == label:
            return td.get_text(' ',strip=True)
    return ''

def price(ctx, doc, regular=False):
    label = '소비자가' if regular else '판매가'
    text = field(doc,label)
    m = re.fullmatch(r'(\d{1,3}(?:,\d{3})+|\d+)\s*원?',text)
    value = int(m[1].replace(',','')) if m else 0
    if not value:
        ctx.log('공식 '+label+' 없음/형식 미확인: 빈칸','WARNING')
        return None
    return value

def variants(doc):
    result = []
    for script in doc.find_all('script',type='application/ld+json'):
        try:
            value=json.loads(script.get_text())
        except json.JSONDecodeError:
            continue
        for entry in value if isinstance(value,list) else [value]:
            if isinstance(entry,dict) and entry.get('@type')=='ProductGroup':
                result.extend(entry.get('hasVariant',[]))
    return result

def colors(doc, with_images=False):
    result=[]
    for node in doc.select('ul[option_title="Color"] li[option_value]'):
        label=node['option_value'];m=re.fullmatch(r'\(([A-Za-z0-9]{2})\)\s*(.+)',label)
        if not m:
            raise ValueError('공식 색상코드 형식 확인 실패: '+label)
        result.append(dict(label=label,color_code=m[1],color_name=m[2],color=m[2],image=image_url(node.get('link_image','')) if with_images else ''))
    if not result:
        raise ValueError('공식 색상 옵션 없음')
    return result

def sizes(doc,label):
    result=[]
    for variant in variants(doc):
        if variant.get('color')==label and variant.get('size') and variant['size'] not in result:
            result.append(str(variant['size']))
    if not result:
        raise ValueError('해당 색상의 공식 사이즈 조합 확인 실패')
    return ','.join(result)

def details(doc,final):
    urls=[]
    for node in doc.select('#prdDetailContent img'):
        raw=node.get('ec-data-src') or node.get('data-src') or node.get('src') or ''
        url=image_url(raw,final)
        if url and not re.search(r'banner|coupon|shipping|delivery|review|배송|반품|교환|쿠폰',url+' '+node.get('alt',''),re.I) and url not in urls:
            urls.append(url)
    return urls

def search(ctx,base):
    source,final=fetch(ctx,SITE+'/product/search.html?'+urlencode(dict(keyword=base)))
    doc=parse(source);urls=[]
    for node in doc.select('.prdList .name a[href]'):
        url=urljoin(final,node['href'])
        if official(url) and re.search(r'/product/[^/]+/\d+/',urlsplit(url).path) and url not in urls:
            urls.append(url)
    # 검색 결과 전체 범위 확인: 여러 페이지일 때는 실제 검색 페이지 링크를 따라간다.
    for node in doc.select('.xans-search-paging a[href]'):
        link=urljoin(final,node['href'])
        if 'page=' in link and parse_qs(urlsplit(link).query).get('keyword')==[base]:
            p=parse_qs(urlsplit(link).query).get('page',['1'])[0]
            if p!='1':
                extra,_=fetch(ctx,link)
                for a in parse(extra).select('.prdList .name a[href]'):
                    url=urljoin(link,a['href'])
                    if official(url) and url not in urls:urls.append(url)
    return sorted(urls,key=lambda u:int(re.search(r'/product/[^/]+/(\d+)/',urlsplit(u).path)[1]),reverse=True)

def gallery(ctx,urls,code,cache):
    accepted=[];seen=set()
    for url in urls:
        ctx.checkpoint();result=public_image(ctx,url,cache)
        if not result:continue
        validation=validate_gallery_image(result['data'],role='product_shot',mapping_verified=True,single_shot_verified=True,
            mapping_evidence='공식 품번 검색 결과의 Color '+code+' option link_image',content_evidence='공식 색상 옵션의 개별 상품 원본; 본문/리뷰/작은썸네일 제외')
        digest=hashlib.sha256(result['data']).hexdigest()
        if validation['passed'] and digest not in seen:
            seen.add(digest);accepted.append(dict(url=url,validation=validation))
        elif not validation['passed']:ctx.log('갤러리 제외: '+', '.join(validation['reasons'])+' / '+url,'WARNING')
    return gallery_slots(accepted)


def gallery_urls(doc,final):
    urls=[]
    for node in doc.select('.imgArea .detail_image img'):
        url=image_url(node.get('ec-data-src') or node.get('data-src') or node.get('src') or '',final)
        if url and re.search(r'/web/product/(?:big/|extra/big/)',urlsplit(url).path) and not re.search(r'review|banner|coupon|shipping|사이즈|고시|배송|쿠폰|콜라주',url+' '+node.get('alt',''),re.I) and url not in urls:
            urls.append(url)
    return urls


def manual_photos(ctx,urls,linked,cache):
    # 색상은 미정이므로 자동 매핑 검사를 통과했다고 표시하지 않는다.
    # 개별 공식 갤러리의 실제 파일/규격만 검사하여 수동 후보로 전달한다.
    linked_hashes={hashlib.sha256(cache[u]['data']).hexdigest() for u in linked if cache.get(u,{}).get('status')=='accessible'}
    result=[];seen=set(linked_hashes)
    for url in urls:
        if url in linked:continue
        ctx.checkpoint();image=public_image(ctx,url,cache)
        if not image:continue
        data=image['data'];digest=hashlib.sha256(data).hexdigest()
        if digest in seen:continue
        with Image.open(BytesIO(data)) as im:
            animated=getattr(im,'is_animated',False)
        if image['format'] not in ('JPEG','PNG','WEBP') or animated or len(data)>10000000 or min(image['width'],image['height'])<500 or max(image['width'],image['height'])/min(image['width'],image['height'])>1.8:
            ctx.log('수동 후보 규격 제외: '+url,'WARNING');continue
        seen.add(digest);result.append(url)
    return result


def detail_image(ctx,url,cache):
    key=('detail',url)
    if key not in cache:
        prior=cache.get(url,{})
        cache[key]=prior if prior.get('status')=='accessible' else check_public_image(url,timeout=20,max_bytes=DETAIL_MAX_BYTES,retries=1,checkpoint=ctx.checkpoint)
    result=cache[key]
    if result['status']!='accessible':
        ctx.log('상세 원본 확인 불가: '+url+' / '+', '.join(result['reasons']),'WARNING')
        return None
    return result


def fetch(ctx, url):
    if not official(url):
        raise ValueError('후아유 공식 홈페이지 외부 URL')
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
        futures = {u: pool.submit(check_public_image, u, timeout=10, retries=1) for u in missing}
        for url, future in futures.items():
            while not future.done():
                ctx.checkpoint()
                time.sleep(.1)
            cache[url] = future.result()
            ctx.checkpoint()

def detail(ctx, urls, cache):
    parts = []
    for url in urls:
        ctx.checkpoint()
        if not detail_image(ctx, url, cache):
            if any(re.search(r'HTTP (404|410)\b', r) for r in cache.get(('detail',url), {}).get('reasons', [])):
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

def crawl(ctx):
    raw=str(ctx.product_code).strip().upper()
    match=re.fullmatch(r'([A-Z0-9]{10})(?:[_-]([A-Z0-9]{2}))?',raw)
    if not match:
        ctx.fail_item('품번 10자리 또는 품번_영숫자 색상코드 2자리로 입력하세요.')
        raise ValueError('입력 품번 형식 오류')
    base,requested=match[1],match[2];ctx.log('후아유 코드 버전: '+REVISION,'INFO')
    ctx.allow_automatic_results=True
    wants_images=ctx.enabled('main_image') or ctx.enabled('additional_images')
    cache={};pages=[];candidates={};failures=[];emitted=set();shots=[];all_linked=set()
    try:
        ctx.stage('후아유 공식 품번 검색: '+base)
        for link in search(ctx,base):
            source,final=fetch(ctx,link);doc=parse(source);name=field(doc,'상품명')
            if not name:raise ValueError('공식 상품명 없음: '+final)
            page=dict(doc=doc,final=final,name=name);pages.append(page)
            if wants_images:
                shots.extend(u for u in gallery_urls(doc,final) if u not in shots)
            for color in colors(doc,wants_images):
                if color['image']:all_linked.add(color['image'])
                if requested and color['color_code']!=requested:continue
                # link_image 없는 색상은 다른 검색 결과 페이지에서 보완한다.
                key=color['color_code'];item=candidates.setdefault(key,dict(color=color,page=page,urls=[]))
                if color['image']:
                    if not item['urls']:item['page']=page
                    if color['image'] not in item['urls']:item['urls'].append(color['image'])
        if not candidates:raise ValueError('공식 품번 검색/색상 결과 없음: '+raw)
        detail_urls=details(pages[0]['doc'],pages[0]['final']) if ctx.enabled('detail_page') else []
        shared_detail=detail(ctx,detail_urls,cache) if ctx.enabled('detail_page') else ''
        if wants_images:
            prefetch(ctx,shots+list(all_linked),cache)
        for code,item in candidates.items():
            try:
                if wants_images and not item['urls']:continue
                ctx.checkpoint();ctx.stage('후아유 색상 '+code)
                page=item['page'];doc=page['doc'];color=item['color']
                record=dict(product_code=base,product_name=clean_product_name(page['name'],['후아유','WHO.A.U','WHOAU'],[base]),product_url=page['final'],color=color['color'],color_name=color['color_name'],color_code=code,**{'원본 상품명':page['name']})
                if ctx.enabled('size'):record['size']=sizes(doc,color['label'])
                if ctx.enabled('price'):record['price']=price(ctx,doc)
                if ctx.enabled('regular_price'):record['regular_price']=price(ctx,doc,True)
                if ctx.enabled('category'):record['category']=' > '.join(n.get_text(' ',strip=True) for n in doc.select('.xans-product-headcategory ol li a') if n.get_text(strip=True) not in ('홈','Home'))
                photos=item['urls'] if ctx.enabled('main_image') or ctx.enabled('additional_images') else []
                if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                    main,extras,warning=gallery(ctx,photos,code,cache)
                    if warning:ctx.log(warning,'WARNING')
                    if ctx.enabled('main_image'):record['main_image']=main or ''
                    if ctx.enabled('additional_images'):record['additional_images']=','.join(extras)
                if ctx.enabled('detail_page'):record['detail_page']=shared_detail
                ctx.emit_result(record);emitted.add(code)
            except Exception as exc:
                message=base+'_'+code+': '+str(exc);ctx.fail_item(message);failures.append(message)
        if wants_images:
            unknown=manual_photos(ctx,shots,all_linked,cache)
            if unknown:
                palette=[{k:item['color'][k] for k in ('color_name','color_code')} for item in candidates.values()]
                first=pages[0];doc=first['doc']
                common=dict(product_code=base,product_name=clean_product_name(first['name'],['후아유','WHO.A.U','WHOAU'],[base]),product_url=first['final'],**{'원본 상품명':first['name']})
                if ctx.enabled('size'):
                    values=[sizes(item['page']['doc'],item['color']['label']) for item in candidates.values()]
                    common['size']=values[0]
                    if len(set(values))>1:ctx.log('수동 후보 사이즈는 첫 색상 공식 옵션으로 통일: '+values[0],'WARNING')
                for key,regular in [('price',False),('regular_price',True)]:
                    if ctx.enabled(key):
                        values=[price(ctx,page['doc'],regular) for page in pages]
                        common[key]=values[0] if len(set(values))==1 else None
                        if common[key] is None:ctx.log('수동 후보 공통 '+key+' 미확정: 빈칸','WARNING')
                if ctx.enabled('category'):common['category']=' > '.join(n.get_text(' ',strip=True) for n in doc.select('.xans-product-headcategory ol li a') if n.get_text(strip=True) not in ('홈','Home'))
                if ctx.enabled('detail_page'):common['detail_page']=shared_detail
                for url in unknown:
                    ctx.emit_unmapped(dict(common),palette,url,shared_detail_html=shared_detail)
                ctx.log('공식 연결 사진은 자동 수집, 미연결 사진 '+str(len(unknown))+'장만 수동 매핑으로 전달','SUCCESS')
                emitted.add('manual')
        if not emitted:raise ValueError('완성된 색상 결과 없음')
    except Exception as exc:
        if not failures:ctx.fail_item(str(exc))
        raise
    if failures:raise RuntimeError('일부 색상 미완료: '+' / '.join(failures))
