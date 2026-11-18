PORTAL_API_VERSION = 1
"""Hush Puppies official Cafe24 store; original product detail, no generated tables."""
import ast
import hashlib
import html
import json
import re
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qs, unquote
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from bs4 import BeautifulSoup
from crawler_rules import check_public_image

SITE='https://hushpuppies.co.kr'
HOSTS=('hushpuppies.co.kr','www.hushpuppies.co.kr')
REVISION='2026-11-18-hushpuppies-v1'
EXCLUDED_FOOTER='/hpk1209/new/bot/bottom.jpg'

class CrawlError(ValueError):pass

def fetch(ctx,url):
    url=quote(url,safe=":/?=&%+;,@!~*'()-._")
    if urlsplit(url).hostname not in HOSTS:raise CrawlError('공식몰 외부 페이지 요청')
    for attempt in range(3):
        ctx.checkpoint()
        try:
            with build_opener().open(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as response:
                if urlsplit(response.geturl()).hostname not in HOSTS:raise CrawlError('공식몰 외부 이동')
                data=response.read(8_000_001)
            if len(data)>8_000_000:raise CrawlError('페이지 응답 용량 초과')
            return data.decode('utf-8-sig')
        except HTTPError as exc:
            if exc.code not in (500,502,503,504) or attempt==2:raise
        except (URLError,TimeoutError,ConnectionError):
            if attempt==2:raise
        ctx.log('공식 페이지 일시 오류 재시도: '+url,'WARNING')

def soup(source):return BeautifulSoup(source,'html.parser')

def field(doc,label):
    for row in doc.select('.infoArea tr, .xans-product-detaildesign tr'):
        key,value=row.find('th'),row.find('td')
        if key and value and key.get_text(' ',strip=True)==label and not value.find('select'):
            return value.get_text(' ',strip=True)
    return ''

def search(ctx,code):
    queue=[SITE+'/product/search.html?'+urlencode({'keyword':code})];seen=set();links=[]
    while queue:
        url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise CrawlError('검색 30페이지 한도 초과')
        seen.add(url);doc=soup(fetch(ctx,url))
        for node in doc.select('.prdList .name a[href]'):
            link=urljoin(SITE,node['href']);p=urlsplit(link)
            if p.hostname in HOSTS and p.path.startswith('/product/') and link not in links:links.append(link)
        for node in doc.select('.xans-search-paging a[href]'):
            link=urljoin(SITE,node['href']);p=urlsplit(link);q=parse_qs(p.query)
            if p.hostname in HOSTS and q.get('keyword')==[code] and q.get('page') and link not in seen and link not in queue:queue.append(link)
    return links

def identity(doc,code):
    actual=field(doc,'상품코드').strip().upper()
    if actual!=code:return None
    raw=field(doc,'상품명')
    if not raw:raise CrawlError('공식 상품명 누락')
    name=re.sub(r'\[[^\]]*\]','',raw)
    for token in [code,'허시파피','HUSH PUPPIES']:
        name=re.sub(r'(?<![A-Z0-9])'+re.escape(token)+r'(?![A-Z0-9])','',name,flags=re.I)
    name=re.sub(r'\s+',' ',name).strip(' -_/')
    if not name:raise CrawlError('정리한 상품명 없음')
    return name,raw

def stock_rows(source):
    match=re.search(r"\bvar\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*')",source)
    if not match:raise CrawlError('공식 원본 옵션 재고 데이터 없음')
    try:data=json.loads(ast.literal_eval(match[1]))
    except (ValueError,SyntaxError,TypeError) as exc:raise CrawlError('공식 옵션 JSON 해석 실패') from exc
    if not isinstance(data,dict) or not data:raise CrawlError('공식 옵션 후보 없음')
    return list(data.values())

def color_label(label):
    match=re.fullmatch(r'(.+?)\s*\(([A-Z0-9]{2,4})\)',label,re.I)
    if match:return dict(color_name=match[1].strip(),color_code=match[2].upper())
    match=re.fullmatch(r'\(([A-Z0-9]{2,4})\)\s*(.+)',label,re.I)
    if match:return dict(color_name=match[2].strip(),color_code=match[1].upper())
    return dict(color_name=label,color_code='')

def palette(source):
    colors={}
    for row in stock_rows(source):
        names=row.get('option_name_original');values=row.get('option_value_orginal') or row.get('option_value_original')
        if not isinstance(names,list) or not isinstance(values,list) or len(names)!=len(values):raise CrawlError('공식 옵션 이름/값 구조 변경')
        opts=dict(zip(names,values));color=str(opts.get('색상') or opts.get('컬러') or '').strip();size=str(opts.get('사이즈') or '').strip()
        if not color or not size:raise CrawlError('색상/사이즈 원본 옵션 누락')
        entry=colors.setdefault(color,dict(**color_label(color),sizes=[],prices=[]))
        if size not in entry['sizes']:entry['sizes'].append(size)
        price=row.get('option_price')
        if not isinstance(price,(int,float)) or price<=0:raise CrawlError('옵션 판매가 형식 확인 필요')
        entry['prices'].append(price)
    return list(colors.values())

def public_image_url(base,raw):
    return quote(urljoin(base,str(raw or '').strip()),safe=":/?=&%+;,@!~*'()-._")

def gallery_urls(doc,final):
    urls=[]
    for node in doc.select('.imgArea img, meta[property="og:image"]'):
        url=public_image_url(final,node.get('ec-data-src') or node.get('data-src') or node.get('src') or node.get('content'))
        p=urlsplit(url)
        # Only URLs explicitly supplied by the official gallery, no guessed
        # small->big substitutions. Validate real dimensions after downloading.
        if p.hostname in HOSTS and re.match(r'^/web/product/(?:big/|extra/(?:small|big|medium)/)',p.path) and url not in urls:urls.append(url)
    return urls

def detail_urls(doc,final,code):
    area=doc.select_one('#prdDetail')
    if area is None:raise CrawlError('공식 상품 상세 영역 없음')
    urls=[]
    for node in area.select('img'):
        url=public_image_url(final,node.get('ec-data-src') or node.get('data-src') or node.get('src'))
        p=urlsplit(url);path=unquote(p.path);filename=path.rsplit('/',1)[-1]
        # The attached caution/delivery/returns image is this common footer.
        # Exclude it and common notices before requesting any image bytes.
        if p.hostname!='gi.esmplus.com' or not path.startswith('/hpk1209/'):continue
        if path.lower()==EXCLUDED_FOOTER or '/notice/' in path.lower():continue
        if not re.fullmatch(re.escape(code)+r'(?:[_-][A-Z0-9가-힣]+)*\.(?:jpe?g|png|webp)',filename,re.I):continue
        if re.search(r'banner|shipping|delivery|교환|배송|반품',path+' '+str(node.get('alt') or ''),re.I):continue
        if url not in urls:urls.append(url)
    if not urls:raise CrawlError('공식 품번 상세 이미지 없음')
    return urls

def checked(ctx,url,cache,detail=False):
    key=(url,detail)
    if key not in cache:cache[key]=check_public_image(url,timeout=25,retries=2,max_bytes=64*1024*1024 if detail else 10_000_000,checkpoint=ctx.checkpoint)
    result=cache[key]
    if result['status']!='accessible':raise CrawlError('이미지 조회 미완료: '+url+' / '+', '.join(result.get('reasons',[])))
    if result.get('format') not in ('JPEG','PNG','WEBP'):raise CrawlError('지원하지 않는 이미지 형식: '+url)
    if not detail and (min(result['width'],result['height'])<500 or max(result['width'],result['height'])/min(result['width'],result['height'])>1.8):raise CrawlError('갤러리 이미지 크기/비율 기준 미달: '+url)
    return result

def gallery(ctx,doc,final,cache):
    photos=[];seen=set()
    for url in gallery_urls(doc,final):
        try:result=checked(ctx,url,cache)
        except CrawlError as exc:ctx.log(str(exc),'WARNING');continue
        digest=hashlib.sha256(result['data']).hexdigest()
        if digest not in seen:seen.add(digest);photos.append(url)
    if not photos:raise CrawlError('유효 공식 갤러리 이미지 없음')
    return photos

def detail(ctx,doc,final,code,cache):
    parts=[]
    for url in detail_urls(doc,final,code):
        checked(ctx,url,cache,True)
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세정보"></p>')
    markup='<div>'+''.join(parts)+'</div>'
    if len(markup.encode('utf-16-le'))//2>32767:raise CrawlError('상세 HTML 엑셀 셀 한도 초과')
    return markup

def extra_slots(photos):
    extras=list(photos[1:6] or photos[:1])
    while len(extras)<5:extras.append(extras[-1])
    return ','.join(extras)

def amount(value):
    match=re.fullmatch(r'\s*([\d,]+)\s*원\s*',value)
    return int(match[1].replace(',','')) if match else None

def _crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not code:raise CrawlError('품번이 비어 있습니다')
    ctx.allow_automatic_results=True;matched=0
    ctx.stage('허시파피 공식 품번 검색: '+code)
    for link in search(ctx,code):
        ctx.checkpoint();source=fetch(ctx,link);doc=soup(source);info=identity(doc,code)
        if info is None:continue
        matched+=1;colors=palette(source);cache={}
        record=dict(product_code=code,product_name=info[0],product_url=link,**{'원본 상품명':info[1]})
        if ctx.enabled('regular_price'):record['regular_price']=amount(field(doc,'소비자가'))
        if ctx.enabled('category'):record['category']=' > '.join(n.get_text(' ',strip=True) for n in doc.select('.xans-product-headcategory li') if n.get_text(' ',strip=True) not in ('홈','현재 위치'))
        if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,link,code,cache)
        wants=ctx.enabled('main_image') or ctx.enabled('additional_images');photos=gallery(ctx,doc,link,cache) if wants else []
        if len(colors)==1 or not wants:
            for color in colors:
                row=dict(record,color=color['color_name'],color_name=color['color_name'],color_code=color['color_code'])
                if ctx.enabled('size'):row['size']=','.join(color['sizes'])
                if ctx.enabled('price'):row['price']=color['prices'][0] if len(set(color['prices']))==1 else None
                if ctx.enabled('main_image'):row['main_image']=photos[0]
                if ctx.enabled('additional_images'):row['additional_images']=extra_slots(photos)
                ctx.emit_result(row)
        else:
            choices=[dict(color_name=c['color_name'],color_code=c['color_code'],**({'size':','.join(c['sizes'])} if ctx.enabled('size') else {})) for c in colors]
            if ctx.enabled('size'):
                sizes={','.join(c['sizes']) for c in colors};record['size']=next(iter(sizes)) if len(sizes)==1 else ''
            if ctx.enabled('price'):
                prices={p for c in colors for p in c['prices']};record['price']=next(iter(prices)) if len(prices)==1 else None
            for url in photos:ctx.emit_unmapped(dict(record),choices,url,shared_detail_html=record.get('detail_page',''))
        ctx.log(code+': '+str(len(colors))+'색상, '+str(len(photos))+'사진 수집 완료','SUCCESS')
    if not matched:raise CrawlError('공홈 검색 후 정확한 상품코드 일치 없음: '+code)

def crawl(ctx):
    ctx.log('허시파피 공식몰 '+REVISION,'INFO')
    try:_crawl(ctx)
    except Exception as exc:ctx.fail_item(str(exc));raise
