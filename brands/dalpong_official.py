PORTAL_API_VERSION = 1
"""달퐁 공홈: 공식 Color/Size 옵션과 미연결 상품 사진 수동 매핑."""
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

SITE='https://cubynmom.com'
HOSTS=('dalpong.co.kr','www.dalpong.co.kr','cubynmom.com','www.cubynmom.com','dalpongcuby.cafe24.com')
REVISION='2026-11-27-dalpong-empty-slots-3'
IMAGE_HOSTS=HOSTS+('cafe24.poxo.com','cafe24img.poxo.com','cubyndalpong.speedgabia.com','dalpong.speedgabia.com','cubynmom.speedgabia.com')

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

def product_number(value):
    parsed=urlsplit(value)
    if parsed.hostname:
        if parsed.hostname not in HOSTS:raise CrawlError('달퐁 공식몰 상품 링크만 입력하세요.')
        query=parse_qs(parsed.query)
        number=(query.get('product_no') or [''])[0]
        match=re.search(r'/product/[^/]+/(\d+)(?:/|$)',parsed.path)
        return number if number.isdigit() else match[1] if match else ''
    return value if value.isdigit() and len(value)<=7 else ''


def search(ctx,code):
    number=product_number(code)
    if number:return [SITE+'/product/detail.html?product_no='+number]
    variants=list(dict.fromkeys([code,re.sub(r'[-_\s]','',code)]))
    queue=[SITE+'/product/search.html?'+urlencode({'keyword':value,'page':1}) for value in variants];seen=set();links=[]
    while queue:
        url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise CrawlError('공식 검색 30페이지 한도 초과')
        seen.add(url);doc=soup(fetch(ctx,url));area=doc.select_one('.xans-search-result')
        summary=doc.select_one('.searchResult,.prdCount');count=summary.get_text(' ',strip=True) if summary else ''
        if area is None and not re.search(r'\b0\s*(?:items|개)',count,re.I):raise CrawlError('공식 검색 화면 접근/구조 확인 실패 (검색 0건 아님)')
        for node in area.select('.name a[href]') if area else []:
            link=urljoin(SITE,node['href'])
            if product_number(link) and link not in links:links.append(link)
        for node in doc.select('.xans-search-paging a[href]'):
            if node['href'].startswith('#'):continue
            link=urljoin(url,node['href']);q=parse_qs(urlsplit(link).query);page=(q.get('page') or [''])[0]
            if urlsplit(link).hostname in HOSTS and page.isdigit() and int(page)>0 and q.get('keyword')==parse_qs(urlsplit(url).query).get('keyword'):
                link=SITE+'/product/search.html?'+urlencode({'keyword':q['keyword'][0],'page':int(page)})
                if link not in seen and link not in queue:queue.append(link)
    return links


def identity(doc,code,search_confirmed=False):
    heading=doc.select_one('.infoArea h1,.infoArea h2,.headingArea h1,.headingArea h2')
    raw=heading.get_text(' ',strip=True) if heading else field(doc,'상품명')
    if not raw:return None
    wanted=product_number(code)
    if wanted:
        published=[n.get('href') or n.get('content') or '' for n in doc.select('link[rel="canonical"],meta[property="og:url"]')]
        if not any(product_number(url)==wanted for url in published):return None
    else:
        normalize=lambda value:re.sub(r'[-_\s]','',value).upper()
        candidates=[field(doc,label) for label in ('자체상품코드','모델명','상품코드')]
        candidates+=re.findall(r'(?<![A-Za-z0-9])([A-Za-z]{1,8}[-_]?\d{2,8}[A-Za-z0-9]*)(?![A-Za-z0-9])',raw)
        for image in doc.select('#prdDetail img'):
            path=unquote(urlsplit(image.get('src') or image.get('data-src') or '').path)
            candidates+=re.findall(r'(?:^|/)([A-Za-z]{1,8}[-_]?\d{2,8}[A-Za-z0-9]*)(?![A-Za-z0-9])',path)
        if normalize(code) not in [normalize(c) for c in candidates if c] and not search_confirmed:return None
    name=re.sub(r'\[\s*(?:달퐁|DALPONG|큐비앤맘|CUBY\s*&?\s*MOM)\s*\]','',raw,flags=re.I)
    name=re.sub(r'^(?:달퐁|큐비앤맘)\s*','',name)
    if not wanted:name=re.sub(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])','',name,flags=re.I)
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
    colors={};doc=soup(source)
    spec={}
    for row in doc.select('#prdDetail .cm-spec tr'):
        cells=row.find_all(['th','td'],recursive=False)
        if len(cells)==2:spec[cells[0].get_text(strip=True)]=cells[1].get_text(' ',strip=True)
    fallback_color=fallback_color or spec.get('색상','')
    for row in stock_rows(source):
        names=row.get('option_name_original');values=row.get('option_value_orginal') or row.get('option_value_original')
        if not isinstance(names,list) or not isinstance(values,list) or len(names)!=len(values):raise CrawlError('공식 옵션 이름/값 구조 변경')
        # Deleted Cafe24 slots can remain in stock JSON with no option values.
        if all(not str(v or '').strip() for v in values) and not str(row.get('option_value') or '').strip():continue
        opts={str(k).strip().casefold():str(v).strip() for k,v in zip(names,values)}
        color=opts.get('색상') or opts.get('컬러') or opts.get('color') or '';size=opts.get('사이즈') or opts.get('size') or ''
        if not color and len(values)==1:
            match=re.fullmatch(r'(.+?)/\s*(\d{2,3}(?:\s*\([^)]*\))?|FREE|ONE\s*SIZE)',str(values[0]),re.I)
            if match:color,size=match[1].strip(),match[2].strip()
            else:
                reverse=re.fullmatch(r'(\d{2,3}(?:\s*\([^)]*\))?|FREE|ONE\s*SIZE)/(.+)',str(values[0]),re.I)
                if reverse:size,color=reverse[1].strip(),reverse[2].strip()
                else:
                    # Official combined labels also omit '/' (화이트90 / 화이트 90).
                    compact=re.fullmatch(r'([가-힣A-Za-z][가-힣A-Za-z\s]*(?:\([A-Z0-9]{2,4}\))?)\s*[-_]?\s*(\d{2,3}(?:\s*\([^)]*\))?)',str(values[0]),re.I)
                    if compact:color,size=compact[1].strip(),compact[2].strip()
                    else:
                        compact=re.fullmatch(r'(\d{2,3}(?:\s*\([^)]*\))?)\s*[-_]?\s*([가-힣A-Za-z][가-힣A-Za-z\s]*(?:\([A-Z0-9]{2,4}\))?)',str(values[0]),re.I)
                        if compact:size,color=compact[1].strip(),compact[2].strip()
                        elif re.fullmatch(r'블랙|화이트|아이보리|베이지|민트|핑크|블루|네이비|옐로우|그레이|브라운|레드|그린|퍼플|크림|black|white|ivory|beige|mint|pink|blue|navy|yellow|grey|gray|brown|red|green|purple|cream',str(values[0]),re.I):color=str(values[0]).strip()
            if not color and names[0].strip() in ('옵션','선택') and re.fullmatch(r'\d{2,3}(?:\s*\([^)]*\))?|FREE|ONE\s*SIZE',str(values[0]),re.I):size=str(values[0]).strip()
        color=color or fallback_color
        if not color:raise CrawlError('공식 색상명 확인 실패: 옵션과 제품정보에 색상 원문 없음')
        entry=colors.setdefault(color,dict(**color_label(color),sizes=[],prices=[]))
        if size and size not in entry['sizes']:entry['sizes'].append(size)
        price=row.get('option_price')
        if not isinstance(price,(int,float)) or price<=0:raise CrawlError('옵션 판매가 형식 확인 필요')
        entry['prices'].append(price)
    if not colors:raise CrawlError('유효한 공식 옵션 없음: 옵션 값이 전부 비어 있습니다.')
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
        if p.hostname in IMAGE_HOSTS and re.search(r'/web/product/(?:(?:big|medium|small)/|extra/(?:small|big|medium)/)',p.path) and url not in urls:urls.append(url)
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
        if re.search(r'banner|shipping|delivery|coupon|/morenvyimg/|/notice/|배송|교환|반품|쿠폰',path,re.I):continue
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
    area=doc.select_one('#prdDetail')
    if area:
        for table in area.select('table'):
            if re.search(r'배송비|교환배송|반품배송|쿠폰|적립금',table.get_text(' ',strip=True)):continue
            clean=BeautifulSoup(str(table),'html.parser')
            for node in clean.select('script,style,iframe,form'):node.decompose()
            for node in clean.find_all(True):node.attrs={k:v for k,v in node.attrs.items() if k in ('rowspan','colspan')}
            if clean.get_text(strip=True):parts.append(str(clean))
    if not parts:
        ctx.log('공식 상세 본문 없음: 확인된 상품컷으로 상세 구성','WARNING')
        parts=['<p><img src="'+html.escape(url,quote=True)+'" alt="상품 이미지"></p>' for url in photos]
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
        value=str(ctx.product_code).strip();code=value if urlsplit(value).hostname else value.upper()
        if not product_number(code) and not re.fullmatch(r'[A-Z0-9_-]+',code):raise CrawlError('품번·공식 상품번호·공식 상품 링크를 입력하세요.')
        ctx.allow_automatic_results=True;ctx.stage('달퐁 공식 품번 검색: '+code)
        links=search(ctx,code)
        if not links:raise CrawlError('달퐁 공식몰 검색 0건: '+code+' / 상품 없음과 접근 오류를 구분합니다. 공홈 검색에 노출되지 않는 품번은 공식 상품 링크를 입력하세요.')
        cache={};matched=0;seen=set();wants=ctx.enabled('main_image') or ctx.enabled('additional_images')
        for link in links:
            number=product_number(link)
            if number in seen:continue
            seen.add(number)
            source=fetch(ctx,link);doc=soup(source)
            info=identity(doc,code,search_confirmed=not bool(product_number(code)))
            if info is None:continue
            if bundle_page(source,doc):ctx.log('자식 상품 옵션이 별도인 코디 세트 페이지 제외: '+link,'WARNING');continue
            ctx.stage('달퐁 공식 옵션·사진 확인: '+link)
            colors=palette(source);record=dict(product_code=product_number(code) or code,product_name=info[0],product_url=link,**{'원본 상품명':info[1]})
            if not product_number(code):ctx.log('공식 품번 검색 결과 연결 확인: '+code+' → 상품번호 '+number,'INFO')
            if ctx.enabled('regular_price'):record['regular_price']=amount(field(doc,'소비자가'))
            if ctx.enabled('category'):record['category']=' > '.join(n.get_text(' ',strip=True) for n in doc.select('.xans-product-headcategory li') if n.get_text(' ',strip=True) not in ('홈','현재 위치'))
            photos=gallery(ctx,doc,link,cache) if wants else []
            body=detail(ctx,doc,link,code,cache,photos) if ctx.enabled('detail_page') else ''
            if ctx.enabled('detail_page'):record['detail_page']=body
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
                if ctx.enabled('price'):
                    prices={p for c in colors for p in c['prices']};record['price']=next(iter(prices)) if len(prices)==1 else None
                for url in photos:ctx.emit_unmapped(dict(record),choices,url,shared_detail_html=body)
            matched+=1
        if not matched:raise CrawlError('공식 검색 결과는 있으나 수집할 상품 페이지 확인 실패: '+code)
        ctx.log('달퐁 수집 완료: '+str(matched)+'개 공식 상품','SUCCESS')
    except Exception as exc:ctx.fail_item(str(exc));raise

if __name__=='__main__':
    raise SystemExit('Crawler Hub에서 실행하세요.')
