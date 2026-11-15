PORTAL_API_VERSION = 1
import ast
import hashlib
import html
import json
import re
import time
from io import BytesIO
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qs,urlencode,quote,unquote
from urllib.request import Request,build_opener
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image,clean_product_name

SITE='https://www.soupmall.co.kr'
DETAIL_MAX_BYTES=64*1024*1024
REVISION='2026-11-15-soup-1'

def official(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.hostname in ('www.soupmall.co.kr','soupmall.co.kr') and not p.username and not p.password

def image_url(raw,final=SITE):
    if not raw:return ''
    url=urljoin(final,raw);p=urlsplit(url)
    allowed=(p.hostname in ('www.soupmall.co.kr','soupmall.co.kr') and p.path.startswith('/web/') or p.hostname=='images.dcollect.co.kr' or p.hostname=='dcollec.img13.kr' or p.hostname=='ecimg.cafe24img.com' and '/dcollec' in p.path or p.hostname=='cafe24.poxo.com' and p.path.startswith('/ec01/dcollec/'))
    if p.scheme not in ('http','https') or not allowed or p.username or p.password:return ''
    return urlunsplit(('https',p.netloc,quote(p.path,safe="/%:@+=$,;~!*'()"),p.query,''))

def parse(source):
    return BeautifulSoup(source, 'html.parser')

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

def fetch(ctx, url):
    url=quote(url,safe=":/?#[]@!$&'()*+,;=%")
    if not official(url):
        raise ValueError('숲 공식 홈페이지 외부 URL')
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

def excluded_detail(url,context=''):
    path=unquote(urlsplit(url).path).lower()
    return ('/images/banner/' in path or '/banner/' in path or
            bool(re.search(r'(?:goods_detail_brand_banner|detail_bottom|copyright|shipping|delivery|coupon|notice)',path,re.I)) or
            bool(re.search(r'배송|교환|반품|쿠폰|저작권',context)))


def search(ctx,code):
    queue=[SITE+'/product/search.html?'+urlencode(dict(keyword=code))];seen=set();products={}
    while queue:
        link=queue.pop(0)
        if link in seen:continue
        if len(seen)>=30:raise ValueError('숲 검색 페이지 안전 한도 초과')
        seen.add(link);source,final=fetch(ctx,link);doc=parse(source)
        for node in doc.select('.xans-search-result .name a[href],.xans-search-result .thumbnail a[href]'):
            url=urljoin(final,node['href']);p=urlsplit(url)
            number=(parse_qs(p.query).get('product_no') or [''])[0]
            match=re.search(r'/product/[^/]+/(\d+)(?:/|$)',p.path)
            if match:number=match[1]
            if official(url) and number.isdigit():products.setdefault(number,SITE+'/product/detail.html?'+urlencode(dict(product_no=number)))
        for node in doc.select('.xans-search-paging a[href]'):
            url=urljoin(final,node['href']);params=parse_qs(urlsplit(url).query)
            if official(url) and params.get('keyword')==[code] and url not in seen and url not in queue:queue.append(url)
    return list(products.values())


def identity(doc,code):
    raw=field(doc,'상품명')
    if not re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',raw.upper()):return None
    brand=field(doc,'브랜드').strip().upper()
    if brand!='SOUP':return None
    name=clean_product_name(raw,['SOUP','숲'],[code])
    if not name:raise ValueError('공식 상품명 확인 실패')
    return dict(product_code=code,product_name=name,**{'원본 상품명':raw})


def palette(doc,final):
    colors=[]
    for group in doc.select('[option_title="색상"],[option_title="Color"]'):
        for node in group.select('li[option_value],option[value]'):
            label=node.get('option_value') or node.get('value') or ''
            if label in ('','*','**'):continue
            match=re.fullmatch(r'(.*?)\s*\(([A-Za-z0-9]{1,4})\)',label)
            name,code=(match[1].strip(),match[2].upper()) if match else (label.strip(),'')
            image=image_url(node.get('link_image') or '',final)
            color=dict(label=label,color_name=name,color_code=code,image=image)
            if not any(c['label']==label for c in colors):colors.append(color)
    if not colors:raise ValueError('공식 색상 옵션 없음')
    return colors


def sizes(doc,label):
    values=[]
    for variant in variants(doc):
        value=variant.get('size')
        if variant.get('color')==label and value and str(value) not in values:values.append(str(value))
    if not values:
        for script in doc.find_all('script'):
            match=re.search(r"var\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*')",script.get_text())
            if not match:continue
            try:stock=json.loads(ast.literal_eval(match[1]))
            except (ValueError,TypeError,SyntaxError):continue
            if not isinstance(stock,dict):continue
            for row in stock.values():
                if not isinstance(row,dict):continue
                names=row.get('option_name_original',[]);original=row.get('option_value_orginal',[])
                if not isinstance(names,list) or not isinstance(original,list) or len(names)!=len(original):continue
                pair=dict(zip(names,original));color=pair.get('색상',pair.get('Color'));value=pair.get('사이즈',pair.get('Size'))
                if color==label and value and value not in values:values.append(value)
    if not values and len(palette(doc,SITE))==1:
        for node in doc.select('[option_title="사이즈"] li[option_value],[option_title="Size"] li[option_value]'):
            value=node.get('option_value','')
            if value and value not in ('*','**') and value not in values:values.append(value)
    if not values:raise ValueError('색상별 공식 사이즈 조합 확인 실패: '+label)
    return ','.join(values)


def detail_urls(doc,final):
    area=doc.select_one('#prdDetail .cont') or doc.select_one('#prdDetailContent')
    if area is None:raise ValueError('공식 상세 본문 영역 없음')
    result=[]
    for node in area.select('img'):
        raw=node.get('ec-data-src') or node.get('data-src') or node.get('src') or ''
        url=image_url(raw,final)
        context=' '.join(str(node.get(k) or '') for k in ('alt','title'))
        if url and not excluded_detail(url,context) and url not in result:result.append(url)
    if not result:raise ValueError('배송/저작권 안내 제외 후 상세 본문 이미지 없음')
    return result


def build_detail(ctx,urls,cache):
    result=[]
    for url in urls:
        if not detail_image(ctx,url,cache):raise ValueError('상세 원본 이미지 조회 미완료: '+url)
        result.append('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세">')
    return ''.join(result)


def gallery_urls(doc,final):
    result=[]
    for node in doc.select('.imgArea img'):
        url=image_url(node.get('ec-data-src') or node.get('data-src') or node.get('src') or '',final)
        if url and re.search(r'/web/product/(?:big|extra/big)/',urlsplit(url).path) and url not in result:result.append(url)
    return result


def explicit_connections(doc,colors,final):
    by_url={};evidence={}
    for color in colors:
        if color['image']:
            by_url.setdefault(color['image'],set()).add(color['label']);evidence[color['image']]='공식 색상 옵션 link_image'
    variant_urls={}
    for variant in variants(doc):
        label=variant.get('color')
        if label not in {c['label'] for c in colors}:continue
        images=variant.get('image') or []
        if isinstance(images,str):images=[images]
        for raw in images:
            if not isinstance(raw,str):continue
            url=image_url(raw,final)
            if url:variant_urls.setdefault(url,set()).add(label)
    # Cafe24 repeats the shared cover in every variant: never map that default.
    for url,labels in variant_urls.items():
        if len(labels)==1 and url not in by_url:by_url[url]=labels;evidence[url]='공식 색상별 독립 variant 이미지'
    return {url:(next(iter(labels)),evidence[url]) for url,labels in by_url.items() if len(labels)==1}


def body_photo_sections(urls,code,colors):
    known={c['color_code']:c['label'] for c in colors if c['color_code']};active=None;result={}
    for url in urls:
        name=unquote(urlsplit(url).path.rsplit('/',1)[-1])
        header=re.fullmatch(re.escape(code)+r'_([A-Za-z0-9]{1,4})_img\.(?:jpe?g|png|webp)',name,re.I)
        if header:
            active=known.get(header[1].upper());continue
        if re.fullmatch(re.escape(code)+r'_\d+\.(?:jpe?g|png|webp)',name,re.I):
            if active:result[url]=(active,'공식 본문 색상코드 이미지 구간 '+active)
        else:active=None
    return result


def photo(ctx,url,cache):
    image=public_image(ctx,url,cache)
    if not image:return None
    if image['format'] not in ('JPEG','PNG','WEBP') or min(image['width'],image['height'])<300 or max(image['width'],image['height'])/min(image['width'],image['height'])>1.8:return None
    with Image.open(BytesIO(image['data'])) as im:
        if getattr(im,'is_animated',False):return None
    return image


def crawl_impl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not code:raise ValueError('품번이 비어 있습니다')
    ctx.allow_automatic_results=True;cache={};pages=[]
    ctx.stage('숲 공식 품번 검색: '+code)
    for link in search(ctx,code):
        source,final=fetch(ctx,link);doc=parse(source);record=identity(doc,code)
        if record is not None:pages.append(dict(doc=doc,url=final,record=record,colors=palette(doc,final)))
    if not pages:raise ValueError('숲 공식 동일 품번 검색 결과 없음: '+code)
    colors=[]
    for page in pages:
        for color in page['colors']:
            if not any(c['label']==color['label'] for c in colors):colors.append(color)
    first=pages[0];base=dict(first['record'],product_url=first['url'])
    if ctx.enabled('size'):base['size']=','.join(dict.fromkeys(v for p in pages for c in p['colors'] for v in sizes(p['doc'],c['label']).split(',')))
    for key,regular in [('price',False),('regular_price',True)]:
        if ctx.enabled(key):
            values=[price(ctx,p['doc'],regular) for p in pages];base[key]=values[0] if len(set(values))==1 else None
    if ctx.enabled('category'):base['category']=' > '.join(n.get_text(' ',strip=True) for n in first['doc'].select('.xans-product-headcategory ol li a') if n.get_text(strip=True).upper() not in ('HOME','홈'))
    urls=detail_urls(first['doc'],first['url']) if ctx.enabled('detail_page') or ctx.enabled('main_image') or ctx.enabled('additional_images') else []
    if ctx.enabled('detail_page'):base['detail_page']=build_detail(ctx,urls,cache)
    wants=ctx.enabled('main_image') or ctx.enabled('additional_images')
    if not wants:
        for color in colors:
            row=dict(base,color=color['label'],color_name=color['color_name'],color_code=color['color_code'])
            if ctx.enabled('size'):
                source=next(p for p in pages if any(c['label']==color['label'] for c in p['colors']));row['size']=sizes(source['doc'],color['label'])
            ctx.emit_result(row)
        return
    connected={};candidates=[]
    for page in pages:
        mapping=explicit_connections(page['doc'],page['colors'],page['url'])
        for url,evidence in mapping.items():connected.setdefault(url,evidence)
        for url in gallery_urls(page['doc'],page['url'])+list(mapping):
            if url not in candidates:candidates.append(url)
    # Color panels remain in the body, never in the gallery candidates.
    sections=body_photo_sections(urls,code,colors)
    for url in urls:
        name=unquote(urlsplit(url).path.rsplit('/',1)[-1])
        if re.fullmatch(re.escape(code)+r'_\d+\.(?:jpe?g|png|webp)',name,re.I):
            if url not in candidates:candidates.append(url)
    connected.update(sections)
    accepted=[];seen={}
    for url in candidates:
        ctx.checkpoint();image=photo(ctx,url,cache)
        if not image:continue
        digest=hashlib.sha256(image['data']).hexdigest()
        if digest in seen:
            prior=seen[digest]
            if url in connected and prior not in connected:connected[prior]=connected[url]
            continue
        seen[digest]=url;accepted.append(url)
    if not accepted:raise ValueError('수집 가능한 공식 상품 사진 없음')
    palette_values=[dict(color_name=c['color_name'],color_code=c['color_code']) for c in colors]
    for url in accepted:
        connection=connected.get(url)
        if len(colors)==1:connection=(colors[0]['label'],'공식 단일 색상 상품')
        if connection:
            label,evidence=connection;color=next(c for c in colors if c['label']==label)
            record=dict(base,color=label,color_name=color['color_name'],color_code=color['color_code'])
            if ctx.enabled('size'):
                source=next(p for p in pages if any(c['label']==label for c in p['colors']));record['size']=sizes(source['doc'],label)
            if ctx.enabled('main_image'):record['main_image']=url
            if ctx.enabled('additional_images'):record['additional_images']=','.join([url]*5)
            ctx.emit_result(record);ctx.log('공식 사전 매핑: '+label+' / '+evidence+' / '+url,'INFO')
        else:ctx.emit_unmapped(dict(base),palette_values,url,shared_detail_html=base.get('detail_page',''))
    ctx.log(code+': '+str(len(colors))+'색상 / '+str(len(accepted))+'장 / 본문 '+str(len(urls))+'장','SUCCESS')


def crawl(ctx):
    try:return crawl_impl(ctx)
    except (ValueError,RuntimeError,OSError) as exc:
        ctx.fail_item(str(exc));raise
