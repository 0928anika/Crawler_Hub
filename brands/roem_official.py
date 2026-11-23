PORTAL_API_VERSION = 1
"""로엠 공홈: 공식 Color/Size 옵션과 미연결 상품 사진 수동 매핑."""
import ast
from pathlib import Path
import hashlib
import html
import json
import re
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qs, unquote
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from bs4 import BeautifulSoup
from crawler_rules import check_public_image

SITE='https://roem.com'
HOSTS=('roem.com','www.roem.com')
REVISION='2026-11-23-roem-v1'
IMAGE_HOSTS=('roem.com','www.roem.com','cafe24.poxo.com','cafe24img.poxo.com','item-imghub.eland.co.kr')

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
        scope=doc.select_one('.xans-search-result')
        if scope is None:
            summary=doc.select_one('.searchResult')
            count=doc.select_one('.prdCount .total_count')
            empty=doc.select_one('.noData')
            if (empty and ((summary and re.search(r'상품\s*검색\s*결과\s*0\b',summary.get_text(' ',strip=True)))
                           or (count and count.get_text(strip=True)=='0' and doc.select_one('.xans-search-form')))
                    and '검색결과가 없습니다' in empty.get_text(' ',strip=True)):
                continue
            raise CrawlError('공식 검색 화면 확인 실패: 접근/페이지 구조 오류 (검색 0건 아님)')
        for node in scope.select('.prdList .name a[href], .name a[href]'):
            link=urljoin(SITE,node['href']);p=urlsplit(link)
            if p.hostname in HOSTS and p.path.startswith('/product/') and link not in links:links.append(link)
        for node in doc.select('.xans-search-paging a[href]'):
            link=urljoin(url,node['href']);p=urlsplit(link);q=parse_qs(p.query)
            if p.hostname in HOSTS and q.get('keyword')==[code] and q.get('page'):
                page=q['page'][0]
                if not page.isdigit():continue
                link=SITE+'/product/search.html?'+urlencode({'keyword':code,'page':str(int(page))})
                if link not in seen and link not in queue:queue.append(link)
    return links

def identity(doc,code):
    heading=doc.select_one('.infoArea h1, .infoArea h2, .headingArea h1, .headingArea h2')
    raw=heading.get_text(' ',strip=True) if heading else field(doc,'상품명')
    if not re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',raw,re.I):return None
    name=re.sub(r'\[\s*(?:로엠|ROEM)\s*\]', '', raw,flags=re.I)
    for token in [code,'로엠','ROEM']:
        name=re.sub(r'(?<![A-Z0-9])'+re.escape(token)+r'(?![A-Z0-9])','',name,flags=re.I)
    name=re.sub(r'\s+',' ',name).strip(' -_/')
    if not name:raise CrawlError('정리한 상품명 없음')
    return name,raw


def bundle_page(source,doc):
    # Cafe24 coordinated SET pages have child-product options rather than the
    # standalone item's stock. Do not read or merge those child options.
    return bool(doc.select_one('.xans-product-setproduct') and
                re.search(r'\bvar\s+set_product_data\s*=',source))


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

def palette(source, fallback_color=''):
    colors={}
    for row in stock_rows(source):
        names=row.get('option_name_original');values=row.get('option_value_orginal') or row.get('option_value_original')
        if not isinstance(names,list) or not isinstance(values,list) or len(names)!=len(values):raise CrawlError('공식 옵션 이름/값 구조 변경')
        opts=dict(zip(names,values));color=str(opts.get('색상') or opts.get('컬러') or opts.get('Color') or opts.get('COLOR') or '').strip();size=str(opts.get('사이즈') or opts.get('Size') or opts.get('SIZE') or '').strip()
        color=color or fallback_color
        if not color:raise CrawlError('공식 색상명 확인 실패')
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
        if p.hostname in IMAGE_HOSTS and re.search(r'/web/product/(?:big/|extra/(?:small|big|medium)/)',p.path) and url not in urls:urls.append(url)
    # Prefer a declared big image over its same-file small rendition.
    urls.sort(key=lambda u: 0 if '/big/' in u else 1 if '/medium/' in u else 2)
    unique=[];seen=set()
    for url in urls:
        key=re.sub(r'/web/product/(?:extra/)?(?:big|medium|small)/','/web/product/',urlsplit(url).path)
        if key not in seen:seen.add(key);unique.append(url)
    return unique

def detail_urls(doc,final,code):
    area=doc.select_one('#prdDetail .cont') or doc.select_one('#prdDetail')
    if area is None:raise CrawlError('공식 상품 상세 영역 없음')
    urls=[]
    for node in area.select('img'):
        raw=node.get('ec-data-src') or node.get('data-src') or node.get('src')
        if not raw or str(raw).startswith('data:'):continue
        url=public_image_url(final,raw);parsed=urlsplit(url);path=unquote(parsed.path)
        if parsed.hostname not in IMAGE_HOSTS:continue
        if not re.search(r'\.(?:jpe?g|png|webp)$',path,re.I):continue
        if re.search(r'banner|shipping|delivery|coupon|/morenvyimg/|배송|교환|반품|쿠폰',path,re.I):continue
        if url not in urls:urls.append(url)
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
    table=doc.select_one('#prdDetail #desctable')
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

def crawl(ctx):
    try:
        code=str(ctx.product_code).strip().upper()
        if not re.fullmatch(r'[A-Z0-9]+',code):raise CrawlError('영문·숫자 기본 품번을 입력하세요.')
        ctx.allow_automatic_results=True;ctx.stage('로엠 공식 품번 검색: '+code)
        links=search(ctx,code)
        def order(url):
            query=parse_qs(urlsplit(url).query);match=re.search(r'/product/[^/]+/([0-9]+)/',urlsplit(url).path)
            return int(query.get('product_no',[match[1] if match else '999999999'])[0])
        links.sort(key=order)
        if not links:raise CrawlError('로엠 공식몰 검색 0건: '+code)
        cache={};photos=[];digests=set();colors={};record=None;body=''
        wants=ctx.enabled('main_image') or ctx.enabled('additional_images')
        for link in links:
            ctx.checkpoint();source=fetch(ctx,link);doc=soup(source);info=identity(doc,code)
            if info is None:continue
            if bundle_page(source,doc):
                ctx.log('코디 SET 상품 제외: '+link,'INFO');continue
            ctx.stage('로엠 공식 옵션·사진 확인: '+link)
            local=palette(source)
            if record is None:
                record=dict(product_code=code,product_name=info[0],product_url=link,**{'원본 상품명':info[1]})
                if ctx.enabled('regular_price'):record['regular_price']=amount(field(doc,'소비자가'))
                if ctx.enabled('category'):record['category']=' > '.join(n.get_text(' ',strip=True) for n in doc.select('.xans-product-headcategory li') if n.get_text(' ',strip=True) not in ('홈','현재 위치'))
            for color in local:
                value=colors.setdefault(color['color_code'] or color['color_name'],dict(color_name=color['color_name'],color_code=color['color_code'],sizes=[],prices=[]))
                for key in ('sizes','prices'):
                    for item in color[key]:
                        if item not in value[key]:value[key].append(item)
            if wants:
                for url in gallery(ctx,doc,link,cache):
                    digest=hashlib.sha256(checked(ctx,url,cache)['data']).hexdigest()
                    if digest not in digests:digests.add(digest);photos.append(url)
            if ctx.enabled('detail_page') and not body:
                body=detail(ctx,doc,link,code,cache)
        if not record or not colors:raise CrawlError('검색 결과는 있으나 정확한 품번의 공식 단품 없음: '+code)
        values=list(colors.values())
        if ctx.enabled('detail_page'):record['detail_page']=body
        if ctx.enabled('price'):
            prices={price for color in values for price in color['prices']}
            record['price']=next(iter(prices)) if len(prices)==1 else None
            if len(prices)>1:ctx.log('공식 옵션별 판매가가 달라 공통 판매가는 빈칸으로 보존합니다.','WARNING')
        if ctx.enabled('size'):record['size']=','.join(dict.fromkeys(size for color in values for size in color['sizes']))
        if wants:
            if not photos:raise CrawlError('유효한 로엠 공식 상품 사진 없음')
            palette_values=[dict(color_name=v['color_name'],color_code=v['color_code'],**({'size':','.join(v['sizes'])} if ctx.enabled('size') else {})) for v in values]
            # Empty option link_image is not evidence for guessing a photo color.
            for url in photos:ctx.emit_unmapped(dict(record),palette_values,url,shared_detail_html=body)
        else:
            for color in values:
                row=dict(record,color=color['color_name'],color_name=color['color_name'],color_code=color['color_code'])
                if ctx.enabled('size'):row['size']=','.join(color['sizes'])
                if ctx.enabled('price'):row['price']=color['prices'][0] if len(color['prices'])==1 else None
                ctx.emit_result(row)
        ctx.log('로엠 수집 완료: '+str(len(values))+'색상 / '+str(len(photos))+'사진','SUCCESS')
    except Exception as exc:ctx.fail_item(str(exc));raise
