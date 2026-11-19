PORTAL_API_VERSION = 1
"""엘칸토 엘칸토몰 공식 크롤러: 공식 옵션과 상품 상세 원본 수집."""
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

SITE='https://elcanto.co.kr'
HOSTS=('elcanto.co.kr','www.elcanto.co.kr')
REVISION='2026-11-19-elcanto-v1'
IMAGE_HOSTS=('elcanto.co.kr','www.elcanto.co.kr','cafe24.poxo.com','cafe24img.poxo.com')

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
    queue=[SITE+'/shop/shopbrand.html?'+urlencode({'search':code,'page':1})];seen=set();links={}
    while queue:
        url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise CrawlError('공식 검색30페이지 한도 초과')
        seen.add(url);doc=soup(fetch(ctx,url))
        for node in doc.select('a[href]'):
            link=urljoin(url,node['href']);parts=urlsplit(link);q=parse_qs(parts.query)
            if parts.hostname in HOSTS and parts.path=='/shop/shopdetail.html' and q.get('branduid'):
                uid=q['branduid'][0]
                if uid.isdigit():links[uid]=SITE+'/shop/shopdetail.html?'+urlencode({'branduid':uid})
        for num in re.findall(r'pagemove\((\d+)\)',str(doc.select_one('.paging') or '')):
            link=SITE+'/shop/shopbrand.html?'+urlencode({'search':code,'page':int(num)})
            if link not in seen and link not in queue:queue.append(link)
    return list(links.values())


def product_data(doc):
    for script in doc.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.get_text())
        except ValueError:continue
        for item in data if isinstance(data,list) else [data]:
            if isinstance(item,dict) and item.get('@type')=='Product' and item.get('mpn'):return item
    raise CrawlError('공식 Product/MPN 구조화 데이터 없음')


def identity(doc,code):
    product=product_data(doc)
    if str(product.get('mpn') or '').strip().upper()!=code:return None
    raw=str(product.get('name') or '')
    name=re.sub(r'\[[^\]]*\]', '',raw)
    for token in [code,'엘칸토','ELCANTO','인텐스','INTENSE','마쯔','MAZZ','by']:
        name=re.sub(r'(?<![A-Za-z0-9가-힣])'+re.escape(token)+r'(?![A-Za-z0-9가-힣])',' ',name,flags=re.I)
    name=re.sub(r'\s+',' ',name).strip(' -_/')
    if not name:raise CrawlError('정리한 상품명 없음')
    return name,raw


def stock_rows(source):
    # Parse only literal fields of option objects; never evaluate site JS.
    match=re.search(r'\bvar\s+optionJsonData\s*=\s*(.*?);',source,re.S)
    if not match:return []
    rows=[]
    for block in re.findall(r'\{([^{}]*)\}',match[1]):
        fields={}
        for item in re.finditer(r"(\w+)\s*:\s*('(?:\\.|[^'\\])*')",block):
            try:fields[item[1]]=ast.literal_eval(item[2])
            except (ValueError,SyntaxError):raise CrawlError('공식 옵션 literal 해석 실패')
        if fields.get('opt_values'):rows.append(fields)
    return rows


def color_label(label):
    match=re.fullmatch(r'(.+?)\s*\(([A-Z0-9]{2,4})\)',label,re.I)
    if match:return dict(color_name=match[1].strip(),color_code=match[2].upper())
    match=re.fullmatch(r'\(([A-Z0-9]{2,4})\)\s*(.+)',label,re.I)
    if match:return dict(color_name=match[2].strip(),color_code=match[1].upper())
    return dict(color_name=label,color_code='')

def palette(source):
    doc=soup(source);selects=doc.select('select.basic_option[opt_id]')
    labels={n['opt_id']:str(n.get('label') or '').strip() for n in selects}
    colors={};rows=stock_rows(source)
    for row in rows:
        ids=row.get('opt_ids','').split(',');values=row['opt_values'].split(',')
        if len(ids)!=len(values):raise CrawlError('공식 옵션 조합 ID/값 수 불일치')
        options={labels.get(k,''):v.strip() for k,v in zip(ids,values)}
        color=options.get('색상') or options.get('컬러');size=options.get('사이즈','')
        if not color:continue
        entry=colors.setdefault(color,dict(**color_label(color),sizes=[],prices=[]))
        if size and size not in entry['sizes']:entry['sizes'].append(size)
        try:delta=int(row.get('sto_price') or 0)
        except ValueError:raise CrawlError('공식 옵션 추가금 형식 확인 실패')
        base=(product_data(doc).get('offers') or {}).get('price')
        if isinstance(base,(int,float)):entry['prices'].append(base+delta)
    if not colors:
        # Fallback only to published select values, including sold-out options.
        vals=lambda n:[o.get('title') or o.get_text(' ',strip=True) for o in n.select('option[value]') if o.get('value')!='']
        color_select=next((n for n in selects if n.get('label') in ('색상','컬러')),None)
        size_select=next((n for n in selects if n.get('label')=='사이즈'),None)
        if color_select:
            for color in vals(color_select):colors[color]=dict(**color_label(color),sizes=vals(size_select) if size_select else [],prices=[(product_data(doc).get('offers') or {}).get('price')])
    if not colors:raise CrawlError('공식 색상 옵션 없음')
    return list(colors.values())


def public_image_url(base,raw):
    return quote(urljoin(base,str(raw or '').strip()),safe=":/?=&%+;,@!~*'()-._")

def gallery_urls(doc,final):
    urls=[]
    for node in doc.select('.thumb-info .originImgArea img, .thumb-info .multi_list img'):
        url=public_image_url(final,node.get('src'));p=urlsplit(url)
        if p.path.lower().endswith('.gif'):continue
        if p.hostname not in ('www.elcanto.co.kr','elcanto.co.kr','cdn4-aka.makeshop.co.kr') and not re.fullmatch(r'elcanto[0-9]*\.speedgabia\.com',p.hostname or ''):continue
        if url not in urls:urls.append(url)
    return urls


def detail_urls(doc,final,code):
    area=doc.select_one('.prd-detail')
    if area is None:raise CrawlError('공식 상세 본문 없음')
    urls=[]
    for node in area.select('img'):
        url=public_image_url(final,node.get('data-src') or node.get('src'));p=urlsplit(url);path=unquote(p.path)
        if path.lower().endswith('.gif'):continue
        if not re.fullmatch(r'elcanto[0-9]*\.speedgabia\.com',p.hostname or '') or '/'+code.upper()+'/' not in path.upper():continue
        if re.search(r'banner|notice|shipping|delivery|coupon|교환|배송|반품',path,re.I):continue
        if url not in urls:urls.append(url)
    if not urls:raise CrawlError('품번 상세 원본 이미지 없음')
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

def detail(ctx,doc,final,code,cache,photos=()):
    parts=[]
    for url in detail_urls(doc,final,code):
        checked(ctx,url,cache,True)
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세정보"></p>')
    table=doc.select_one('#productWrap table')
    if table:
        rows=[]
        for tr in table.select('tr'):
            cells=tr.find_all(['td','th'],recursive=False)
            if len(cells)==2:
                key,value=(cell.get_text(' ',strip=True) for cell in cells)
                if key and value:rows.append('<tr><th>'+html.escape(key)+'</th><td>'+html.escape(value)+'</td></tr>')
        if rows:parts.append('<table style="width:100%;border-collapse:collapse"><tbody>'+''.join(rows)+'</tbody></table>')
    markup='<div>'+''.join(parts)+'</div>'
    if len(markup.encode('utf-16-le'))//2>32767:raise CrawlError('상세 HTML 엑셀 셀 한도 초과')
    return markup

def extra_slots(photos):
    extras=list(photos[1:10] or photos[:1])
    while len(extras)<5:extras.append(extras[-1])
    return ','.join(extras)

def amount(value):
    match=re.fullmatch(r'\s*([\d,]+)\s*(?:원)?\s*',value)
    return int(match[1].replace(',','')) if match else None

def _crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not code:raise CrawlError('품번이 비어 있습니다')
    ctx.allow_automatic_results=True;matched=0
    ctx.stage('엘칸토 공식 품번 검색: '+code)
    for link in search(ctx,code):
        ctx.checkpoint();source=fetch(ctx,link);doc=soup(source);info=identity(doc,code)
        if info is None:continue
        matched+=1;colors=palette(source);cache={}
        record=dict(product_code=code,product_name=info[0],product_url=link,**{'원본 상품명':info[1]})
        if ctx.enabled('regular_price'):record['regular_price']=amount((doc.select_one('.price_wrap strike').get_text(' ',strip=True) if doc.select_one('.price_wrap strike') else ''))
        if ctx.enabled('category'):record['category']=str(product_data(doc).get('category') or '')
        wants=ctx.enabled('main_image') or ctx.enabled('additional_images')
        photos=gallery(ctx,doc,link,cache) if wants or ctx.enabled('detail_page') else []
        if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,link,code,cache,photos)
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
    ctx.log('엘칸토 공식몰 '+REVISION,'INFO')
    try:_crawl(ctx)
    except Exception as exc:ctx.fail_item(str(exc));raise
