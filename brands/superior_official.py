PORTAL_API_VERSION = 1
"""슈페리어 공식몰: 색상별 상품 페이지와 원본 상품컷을 자동 연결합니다."""
import ast,hashlib,html,json,re
from urllib.parse import quote,urlencode,urljoin,urlsplit,parse_qs,unquote
from urllib.request import Request,build_opener
from urllib.error import HTTPError,URLError
from bs4 import BeautifulSoup
from crawler_rules import check_public_image
SITE='https://theshops.co.kr'
HOSTS=('theshops.co.kr','www.theshops.co.kr')
IMAGE_HOSTS=HOSTS+('spholdings.diskn.com','cafe24.poxo.com','cafe24img.poxo.com')
REVISION='2026-11-27-superior-related-filter-5'
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

def stock_rows(source):
    match=re.search(r"\bvar\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*')",source)
    if not match:raise CrawlError('공식 원본 옵션 재고 데이터 없음')
    try:data=json.loads(ast.literal_eval(match[1]))
    except (ValueError,SyntaxError,TypeError) as exc:raise CrawlError('공식 옵션 JSON 해석 실패') from exc
    if not isinstance(data,dict) or not data:raise CrawlError('공식 옵션 후보 없음')
    return list(data.values())

def public_image_url(base,raw):
    return quote(urljoin(base,str(raw or '').strip()),safe=":/?=&%+;,@!~*'()-._")

def checked(ctx,url,cache,detail=False):
    key=(url,detail)
    if detail and key not in cache and (url,False) in cache:cache[key]=cache[(url,False)]
    if key not in cache:cache[key]=check_public_image(url,timeout=25,retries=2,max_bytes=64*1024*1024 if detail else 10_000_000,checkpoint=ctx.checkpoint)
    result=cache[key]
    if result['status']!='accessible':raise CrawlError('이미지 조회 미완료: '+url+' / '+', '.join(result.get('reasons',[])))
    if result.get('format') not in ('JPEG','PNG','WEBP'):raise CrawlError('지원하지 않는 이미지 형식: '+url)
    if not detail and (min(result['width'],result['height'])<500 or max(result['width'],result['height'])/min(result['width'],result['height'])>1.8):raise CrawlError('갤러리 이미지 크기/비율 기준 미달: '+url)
    return result

def amount(value):
    match=re.fullmatch(r'\s*([\d,]+)\s*(?:원)?\s*',value)
    return int(match[1].replace(',','')) if match else None

def product_number(url):
    parsed=urlsplit(url)
    if parsed.hostname not in HOSTS:return ''
    number=(parse_qs(parsed.query).get('product_no') or [''])[0]
    match=re.search(r'/product/[^/]+/(\d+)(?:/|$)',parsed.path)
    return number if number.isdigit() else match[1] if match else ''


def search(ctx,value):
    if urlsplit(value).hostname:
        number=product_number(value)
        if not number:raise CrawlError('슈페리어 공식 상품 링크를 입력하세요.')
        return [SITE+'/product/detail.html?product_no='+number]
    queue=[SITE+'/product/search.html?'+urlencode({'keyword':value,'page':1})];seen=set();links={}
    while queue:
        url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise CrawlError('공식 검색 30페이지 한도 초과')
        seen.add(url);doc=soup(fetch(ctx,url));area=doc.select_one('.xans-search-result')
        summary=doc.select_one('.searchResult,.prdCount');text=summary.get_text(' ',strip=True) if summary else ''
        empty=doc.select_one('.noData')
        count=summary.select_one('.record strong,.total_count') if summary else None
        zero=bool((count and count.get_text(strip=True)=='0') or re.search(r'상품\s*검색\s*결과\s*0(?:\s|$)|\b0\s*(?:items|개|건)',text,re.I))
        confirmed_empty=bool(summary and zero and empty and re.search(r'검색\s*결과가\s*없습니다',empty.get_text(' ',strip=True)))
        if area is None:
            if confirmed_empty:continue
            raise CrawlError('공식 검색 화면 접근/구조 확인 실패 (검색 0건 아님)')
        for node in area.select('.name a[href]') if area else []:
            link=urljoin(SITE,node['href']);number=product_number(link)
            if number:links[number]=SITE+'/product/detail.html?product_no='+number
        for node in doc.select('.xans-search-paging a[href]'):
            if node['href'].startswith('#'):continue
            link=urljoin(url,node['href']);q=parse_qs(urlsplit(link).query);page=(q.get('page') or [''])[0]
            if urlsplit(link).hostname in HOSTS and page.isdigit() and int(page)>0 and q.get('keyword')==[value]:
                canonical=SITE+'/product/search.html?'+urlencode({'keyword':value,'page':int(page)})
                if canonical not in seen and canonical not in queue:queue.append(canonical)
    return list(links.values())


def product_notice(doc):
    tables=[]
    for table in doc.select('#prdDetail table'):
        if table.find_parent('table'):continue
        text=table.get_text(' ',strip=True)
        if re.search(r'제품 소재|제조자|제조국|품질보증',text):tables.append(table)
    return tables


def identity(doc,wanted):
    # Extract source metadata without brand or fixed-length gates.
    # requested_product separately excludes recommendations using actual codes.
    heading=doc.select_one('.infoArea h1,.infoArea h2,.headingArea h1,.headingArea h2')
    raw=heading.get_text(' ',strip=True) if heading else field(doc,'상품명')
    if not raw:raise CrawlError('공식 상품 페이지 로딩 확인 실패: 상품명 영역 없음')
    tokens=re.findall(r'\(([A-Z0-9]+)\)',raw,re.I)
    sku=next((token.upper() for token in reversed(tokens) if re.search(r'\d',token)),'')
    color='';warnings=[]
    for table in product_notice(doc):
        for row in table.select('tr'):
            cells=row.find_all(['th','td'],recursive=False)
            if len(cells)!=2 or cells[0].get_text(strip=True)!='색상':continue
            value=cells[1].get_text(' ',strip=True);evidence=re.fullmatch(r'(.+?)\.\s*([A-Z0-9]+)',value,re.I)
            if evidence:
                if sku and sku!=evidence[2].upper():warnings.append('상품명/고시 품번이 달라 실제 상품명 품번을 우선 사용합니다.')
                sku=sku or evidence[2].upper();color=evidence[1].strip()
            elif value:color=value
    paths=[unquote(urlsplit(node.get('ec-data-src') or node.get('data-src') or node.get('src') or '').path) for node in doc.select('#prdDetail img')]
    if not sku:
        candidates=list(dict.fromkeys(match[1].upper() for path in paths if (match:=re.search(r'/([A-Z0-9]+)_(?:detail\d+|page\d+|SIZE|CO)\.',path,re.I))))
        if len(candidates)==1:sku=candidates[0]
    guide_base=next((match[1].upper() for path in paths if (match:=re.search(r'/SZIF_([A-Z0-9]+)\.',path,re.I)) and sku.startswith(match[1].upper())),'')
    # No fixed model length. E.g. QW0EVT30149 and QW0FTTE30136 both work.
    base=wanted if wanted and wanted!=sku else guide_base or (sku[:-2] if re.search(r'\d{2}$',sku) else sku)
    base=base or wanted
    suffix=sku[len(base):] if base and sku.startswith(base) else ''
    color_code=suffix if re.fullmatch(r'\d{2}',suffix) else ''
    name=re.sub(r'\(\s*'+re.escape(sku)+r'\s*\)','',raw,flags=re.I).strip() if sku else raw
    if color and name.endswith(color):name=name[:-len(color)].strip()
    if not color:
        tail=re.search(r'[_\s]([A-Z]/[^\s]+|[^_\s]+)\s*$',name,re.I) if '_' in name else re.search(r'\s+([A-Z]/[^\s]+)\s*$',name,re.I)
        if tail:color=tail[1];name=name[:tail.start()].strip()
    color=color or field(doc,'색상')
    name=re.sub(r'(?<![A-Za-z0-9가-힣])(?:슈페리어|SUPERIOR)(?![A-Za-z0-9가-힣])','',name,flags=re.I)
    name=re.sub(r'\s+',' ',name).strip(' -_/') or raw
    return dict(sku=sku,base=base,guide_base=guide_base,color_name=color,color_code=color_code,name=name,raw_name=raw,warnings=warnings)


def requested_product(info,wanted):
    """Reject recommendations using actual source codes, never derived input base."""
    if not wanted:return True  # User explicitly supplied this official product URL.
    sku=str(info.get('sku') or '').upper();wanted=wanted.upper()
    return bool(sku and (sku==wanted or re.fullmatch(re.escape(wanted)+r'\d{2}',sku)
                        or wanted==str(info.get('guide_base') or '').upper()))


def body_urls(doc,final,info):
    area=doc.select_one('#prdDetail')
    if area is None:return []
    urls=[]
    for image in area.select('img'):
        raw=image.get('ec-data-src') or image.get('data-src') or image.get('src')
        if not raw or str(raw).startswith('data:'):continue
        url=public_image_url(final,raw);parsed=urlsplit(url);path=unquote(parsed.path)
        if parsed.hostname not in IMAGE_HOSTS or not re.search(r'\.(?:jpe?g|png|webp)$',path,re.I):continue
        # Product-identified body and exact family size guide only. Excludes IP/ banners.
        product=bool(re.search(r'/'+re.escape(info['sku'])+r'(?:[_\.]|$)',path,re.I))
        size=bool(re.search(r'/SZIF_'+re.escape(info.get('guide_base') or info['base'])+r'\.',path,re.I))
        if not (product or size) or re.search(r'banner|delivery|shipping|coupon|brand_top|IP_INFO|배송|반품|교환',path,re.I):continue
        if url not in urls:urls.append(url)
    return urls


def gallery_urls(doc,final,info):
    urls=[]
    for node in doc.select('.imgArea img,meta[property="og:image"]'):
        raw=node.get('ec-data-src') or node.get('data-src') or node.get('src') or node.get('content')
        if not raw:continue
        url=public_image_url(final,raw);p=urlsplit(url)
        if p.hostname in IMAGE_HOSTS and re.search(r'/web/product/(?:big|extra/(?:big|medium))/',p.path) and url not in urls:urls.append(url)
    # The separate color page explicitly publishes individual product cuts in /P/.
    for url in body_urls(doc,final,info):
        if re.search(r'/P/'+re.escape(info['sku'])+r'_\d+\.',urlsplit(url).path,re.I) and url not in urls:urls.append(url)
    return urls


def gallery(ctx,doc,final,info,cache):
    photos=[];seen=set()
    for url in gallery_urls(doc,final,info):
        try:result=checked(ctx,url,cache)
        except CrawlError as exc:ctx.log(str(exc),'WARNING');continue
        digest=hashlib.sha256(result['data']).hexdigest()
        if digest not in seen:seen.add(digest);photos.append(url)
    if not photos:raise CrawlError('확인된 공식 색상 상품컷 없음: '+info['sku'])
    return photos


def detail(ctx,doc,final,info,cache,photos):
    parts=[]
    for url in body_urls(doc,final,info):
        try:checked(ctx,url,cache,True)
        except CrawlError as exc:
            if not re.search(r'HTTP (?:404|410)\b',str(exc)):raise
            ctx.log('공식 상세 이미지 삭제/누락: '+str(exc),'WARNING');continue
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세"></p>')
    if not parts:
        fallback=photos or gallery(ctx,doc,final,info,cache)
        parts=['<p><img src="'+html.escape(url,quote=True)+'" alt="공식 상품컷"></p>' for url in fallback]
        ctx.log('별도 본문 없음: 검증된 상품컷으로 상세 구성','WARNING')
    for table in product_notice(doc):
        clean=soup(str(table))
        for node in clean.select('script,style,iframe,object,form'):node.decompose()
        for node in clean.find_all(True):node.attrs={k:v for k,v in node.attrs.items() if k in ('rowspan','colspan')}
        parts.append(str(clean))
    return '<div>'+''.join(parts)+'</div>'


def extra_slots(photos):
    extra=list(photos[1:6] or photos[:1])
    while len(extra)<5:extra.append(extra[-1])
    return ','.join(extra)


def crawl(ctx):
    try:
        value=str(ctx.product_code).strip();wanted='' if urlsplit(value).hostname else value.upper()
        if wanted and not re.fullmatch(r'[A-Z0-9]+',wanted):raise CrawlError('슈페리어 기본 품번 또는 전체 색상 품번을 입력하세요.')
        ctx.stage('슈페리어 공식 검색: '+value);links=search(ctx,wanted or value)
        if not links:raise CrawlError('슈페리어 공식 검색 0건: '+value)
        count=0;cache={};seen=set();related=[];unconfirmed=[]
        for link in links:
            source=fetch(ctx,link);doc=soup(source);info=identity(doc,wanted)
            if not requested_product(info,wanted):
                if info['sku']:
                    related.append(info['sku']);ctx.log('연관 상품 제외: 입력 '+wanted+' / 실제 '+info['sku']+' / '+link,'INFO')
                else:
                    unconfirmed.append(link);ctx.log('실제 품번 근거가 없는 검색 상품 제외: '+link,'WARNING')
                continue
            source_key=product_number(link)
            if source_key in seen:continue
            seen.add(source_key);info['base']=info['base'] or wanted or product_number(link)
            for warning in info.get('warnings',[]):ctx.log(warning,'WARNING')
            if not info['color_name']:
                ctx.allow_empty_color=True;ctx.log('공식 상품에 색상명 없음: 공백으로 보존하고 상품은 수집합니다.','WARNING')
            ctx.stage('슈페리어 공식 검색 상품 수집: '+source_key)
            record=dict(product_code=info['base'],product_name=info['name'],color=info['color_name'],color_name=info['color_name'],color_code=info['color_code'],product_url=link,**{'원본 상품명':info['raw_name']})
            if ctx.enabled('size'):
                sizes=[]
                try:rows=stock_rows(source)
                except CrawlError:
                    if not re.search(r"\bvar\s+has_option\s*=\s*'F'",source):raise
                    rows=[]
                for row in rows:
                    names=row.get('option_name_original') or [];values=row.get('option_value_orginal') or row.get('option_value_original') or []
                    if len(names)!=len(values):raise CrawlError('공식 사이즈 조합 구조 오류')
                    for label,size in zip(names,values):
                        if re.search(r'사이즈|규격|size',str(label),re.I) and str(size).strip() and str(size).strip() not in sizes:sizes.append(str(size).strip())
                record['size']=','.join(sizes)
                if not sizes:ctx.log('공식 사이즈 옵션 없음: '+info['sku'],'WARNING')
            if ctx.enabled('price'):record['price']=amount(field(doc,'판매가'))
            if ctx.enabled('regular_price'):record['regular_price']=amount(field(doc,'소비자가'))
            if ctx.enabled('category'):record['category']=' > '.join(n.get_text(' ',strip=True) for n in doc.select('.xans-product-headcategory li') if n.get_text(' ',strip=True) not in ('홈','현재 위치'))
            wants=ctx.enabled('main_image') or ctx.enabled('additional_images')
            photos=gallery(ctx,doc,link,info,cache) if wants else []
            if ctx.enabled('main_image'):record['main_image']=photos[0]
            if ctx.enabled('additional_images'):record['additional_images']=extra_slots(photos)
            if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,link,info,cache,photos)
            ctx.emit_result(record);count+=1
        if not count:
            if related and not unconfirmed:raise CrawlError('입력 품번 상품 없음: 공홈 검색에 연관 상품만 노출됩니다. 입력='+value+' / 제외 품번='+', '.join(dict.fromkeys(related)))
            if unconfirmed:raise CrawlError('검색 상품의 실제 품번 미확인: '+value+' / '+', '.join(unconfirmed))
            raise CrawlError('공식 검색 상품 수집 결과 없음: '+value)
        ctx.log('슈페리어 수집 완료: '+str(count)+'색상','SUCCESS')
    except Exception as exc:ctx.fail_item(str(exc));raise
