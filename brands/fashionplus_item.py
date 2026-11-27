PORTAL_API_VERSION = 1
import html,http.client,io,json,re,socket,time,hashlib,urllib.error,urllib.parse,urllib.request
from bs4 import BeautifulSoup,NavigableString,Comment
from PIL import Image,UnidentifiedImageError
BASE='https://www.fashionplus.co.kr'
REVISION='2026-11-27-fashionplus-item-8-category'
MAX_BYTES=10_000_000
class CollectionError(RuntimeError):pass
class MissingResource(CollectionError):pass

def public_opener(ctx, deadline):
    """Request-local dual-stack fallback; never change global socket settings.

    The observed Windows network publishes a nonresponsive DNS64 IPv6 address.
    Try DNS-provided IPv4 addresses first, retaining IPv6 as a fallback. A shared
    deadline covers address attempts, TLS, body reads and the optional retry.
    """
    def connect(address, timeout, source_address=None, **unused):
        host, port = address
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses.sort(key=lambda item: item[0] != socket.AF_INET)
        last_error = None
        for family, socktype, protocol, _, target in addresses:
            ctx.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 시간 제한 초과')
            connection = socket.socket(family, socktype, protocol)
            try:
                connection.settimeout(min(3, remaining))
                if source_address:
                    connection.bind(source_address)
                connection.connect(target)
                connection.settimeout(min(5, max(.01, deadline-time.monotonic())))
                return connection
            except OSError as exc:
                connection.close()
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError('공개 호스트 주소 없음: ' + host)

    def factory(tls):
        def create(host, **kwargs):
            cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
            connection = cls(host, **kwargs)
            connection._create_connection = connect
            return connection
        return create

    class HTTP(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(factory(False), request)

    class HTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(factory(True), request, context=self._context)

    return urllib.request.build_opener(HTTP(), HTTPS())

def fetch(ctx, url, limit=4_000_000):
    """Anonymous GET. No cookies, credentials or Referer, including images."""
    deadline = time.monotonic() + 20
    started = time.monotonic()
    ctx.log('공개 요청: ' + url, 'INFO')
    opener = public_opener(ctx, deadline)
    for attempt in range(2):
        ctx.checkpoint()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 20초 제한 초과')
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with opener.open(request, timeout=min(5, remaining)) as response:
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise CollectionError('응답 크기 초과: ' + url)
                chunks, size = [], 0
                while True:
                    ctx.checkpoint()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('공개 응답 전체 20초 제한 초과')
                    block = response.read1(min(65536, limit + 1 - size))
                    if not block:
                        break
                    chunks.append(block)
                    size += len(block)
                    if size > limit:
                        raise CollectionError('응답 크기 초과: ' + url)
            ctx.checkpoint()
            ctx.log('공개 요청 완료 %.1f초: %s' % (time.monotonic()-started, url), 'INFO')
            return b''.join(chunks)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise MissingResource('HTTP 404: ' + url) from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise CollectionError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt:
                raise CollectionError('통신 실패: %s / %s' % (url, exc)) from exc
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CollectionError('요청 실패: ' + url)

def soup_get(ctx, url):
    return BeautifulSoup(fetch(ctx, url).decode('utf-8-sig'), 'html.parser')

def source_url(node):
    if node is None:
        return ''
    for key in ('data-preload', 'data-lazyload'):
        match = re.search(r"['_\"]*src['\"]\s*:\s*['\"]([^'\"]+)",
                          html.unescape(node.get(key, '')))
        if match:
            return urllib.parse.urljoin(BASE, match.group(1))
    src = node.get('data-src') or node.get('ec-data-src') or node.get('src', '')
    return urllib.parse.urljoin(BASE, src) if src and not src.startswith('data:') else ''

def money(value):
    text = str(value or '').strip()
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)\s*원?', text):
        return None
    number = int(re.sub(r'\D', '', text))
    return number if number > 0 else None

def read_price(soup, regular=False):
    for row in soup.select('.discount-detail__row'):
        title, value = row.find('dt'), row.find('dd')
        if not title or not value:
            continue
        label = title.get_text(' ', strip=True)
        if ('희망소비자가' in label if regular else label.startswith('판매가')):
            return money(value.get_text(strip=True))
    return None

def color_parts(value):
    value = value.strip()
    match = re.fullmatch(r'\((\d+)\)\s*(.*)', value)
    if not match:
        match = re.fullmatch(r'(\d{2})([^\d].*)', value)
    if match:
        return {'color_name': match[2].strip(), 'color_code': match[1]}
    return {'color_name': value, 'color_code': ''}

def item_number(raw):
    raw=html.unescape(str(raw).strip())
    if raw.lower().startswith(('http://','https://')):
        p=urllib.parse.urlsplit(raw)
        match=re.fullmatch(r'/goods/detail/(\d+)/?',p.path)
        if p.hostname not in ('www.fashionplus.co.kr','fashionplus.co.kr') or p.username or p.password or not match:raise CollectionError('패션플러스 상품 링크만 입력할 수 있습니다.')
        return match[1]
    if not re.fullmatch(r'\d+',raw):raise CollectionError('패션플러스 품목 번호(숫자) 또는 상품 링크를 입력해주세요.')
    return raw


def product_info(doc,number,sku_reference=None):
    title=doc.select_one('.m__product-info .text_product')
    if not title or not title.get_text(strip=True):raise CollectionError('상품이 없거나 상세 응답이 올바르지 않습니다: '+number)
    canonical=doc.select_one('meta[property="og:url"]')
    if canonical and item_number(canonical.get('content',''))!=number:raise CollectionError('요청 품목 번호와 실제 상품이 다릅니다.')
    brand_node=doc.select_one('.m_prodetail-head .mm_breadcrumb a[href*="brand-shop"]')
    brand=brand_node.get_text(' ',strip=True).strip(' >') if brand_node else ''
    name=title.get_text(' ',strip=True)
    record=dict(product_code=number,product_name=name,product_url=BASE+'/goods/detail/'+number,**{'패션플러스 품목번호':number,'브랜드':brand})
    provided={};seller='';seller_number=''
    for table in doc.select('table'):
        section=table.find_parent('section');heading=section.find(['h4','h5']) if section else None
        is_notice=bool(heading and '제공고시' in heading.get_text().replace(' ',''))
        for row in table.select('tr'):
            th,td=row.find('th'),row.find('td')
            if not th or not td:continue
            key=th.get_text(' ',strip=True);value=td.get_text(' ',strip=True)
            if is_notice:provided[key]=value
            if key=='업체명' and not seller:seller=value
            if key=='상품번호' and not seller_number:seller_number=value
    structured_skus=[]
    def product_nodes(value):
        if isinstance(value,list):
            for entry in value:yield from product_nodes(entry)
        elif isinstance(value,dict):
            kind=value.get('@type',[]);kind=[kind] if isinstance(kind,str) else kind
            if 'Product' in kind:yield value
            if '@graph' in value:yield from product_nodes(value['@graph'])
    for script in doc.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError):continue
        for product in product_nodes(data):
            if product.get('mpn') is not None and str(product['mpn'])!=str(sku_reference or number):continue
            sku=product.get('sku')
            if isinstance(sku,(str,int)) and str(sku).strip():structured_skus.append(str(sku).strip())
    structured_skus=list(dict.fromkeys(structured_skus))
    if len(structured_skus)>1 or seller_number and structured_skus and seller_number!=structured_skus[0]:raise CollectionError('상품번호(SKU) 원문 불일치: '+number)
    sku=seller_number or (structured_skus[0] if structured_skus else '')
    if not sku:raise CollectionError('상품번호(SKU) 미표시: '+number+' / 상품명에서 추정하지 않습니다.')
    record.update(product_code=sku,**{'상위품목ID':number})
    record.update({'판매자':seller,'판매자 상품번호':seller_number,'상품정보 원문':'\n'.join(k+': '+v for k,v in provided.items())})
    return record


def resolve_product(ctx,number):
    """Resolve child pages through their own declared option endpoint and SKU."""
    link=BASE+'/goods/detail/'+number
    doc=soup_get(ctx,link)
    declared=set()
    for node in doc.select('.m__product-option[data-product]'):
        declared.update(re.findall(r'/goods/detail/([0-9]+)/fetch-option-data',node.get('data-product','')))
    if len(declared)>1:raise CollectionError('상위 상품 연결 정보가 여러 개입니다: '+number)
    parent=next(iter(declared),number)
    if parent==number:
        return doc,link,product_info(doc,number),None
    child=product_info(doc,number,sku_reference=parent)
    parent_link=BASE+'/goods/detail/'+parent
    parent_doc=soup_get(ctx,parent_link);record=product_info(parent_doc,parent)
    if child['product_code']!=record['product_code']:
        raise CollectionError('하위 품목과 상위 상품의 SKU 불일치: '+number)
    record['하위품목ID']=number
    record['입력품목ID']=number
    ctx.log('하위품목 '+number+' → 상위품목 '+parent+' / 공식 SKU 일치 확인','SUCCESS')
    return parent_doc,parent_link,record,number


SIZE_TOKEN=r'(?:FREE|FR|F|ONE\s*SIZE|XS|S|M|L|XL|XXL|XXXL|[2-6]XL|\d{2,3}[A-H]|\d{2,3}(?:MM|CM)?|[CJM]\d+(?:W\d+)?)'

def read_options(ctx,link):
    try:payload=json.loads(fetch(ctx,link+'/fetch-option-data').decode('utf-8-sig'))
    except (ValueError,UnicodeError) as exc:raise CollectionError('옵션 JSON 응답 해석 실패') from exc
    if not isinstance(payload,list):raise CollectionError('공식 옵션 응답 형식 변경')
    leaves=[]
    def visit(node,parents):
        if not isinstance(node,dict):raise CollectionError('옵션 항목 구조 변경')
        children=node.get('options');name=str(node.get('_name') or '').strip()
        if children is not None and not isinstance(children,list):raise CollectionError('옵션 하위 구조 변경')
        if children:
            for child in children:visit(child,parents+([name] if name else []))
        elif name:leaves.append(dict(label=' / '.join(parents+[name]),price=node.get('_price'),stock=node.get('_stock'),option_id=node.get('_id')))
    for group in payload:
        if not isinstance(group,dict):raise CollectionError('상품 옵션 그룹 구조 변경')
        if 'options' not in group:raise CollectionError('공식 옵션 목록 필드 누락')
        for option in group.get('options') or []:visit(option,[])
    return palette_for_leaves(leaves)


def palette_for_leaves(leaves):
    palette=[];sizes=[]
    for row in leaves:
        label=row['label'];match=re.fullmatch(r'(.+?)[\s/]+('+SIZE_TOKEN+r')',label,re.I)
        if match:
            color_label=match[1].strip();size=match[2].strip()
            if size not in sizes:sizes.append(size)
            if color_label.upper() in ('사이즈','SIZE'):color_label='공통'
        elif re.fullmatch(SIZE_TOKEN,label,re.I):
            color_label='공통'
            if label not in sizes:sizes.append(label)
        else:color_label=label
        candidate=color_parts(color_label)
        if candidate not in palette:palette.append(candidate)
    if not palette:palette=[dict(color_name='옵션 미표시',color_code='')]
    return palette,sizes,leaves


def image_info(ctx,url,cache,detail=False):
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:raise CollectionError('공개 이미지 URL 확인 실패: '+url)
    key=(url,detail)
    if key not in cache:
        prior=cache.get((url,not detail))
        if isinstance(prior,MissingResource):raise prior
        if isinstance(prior,dict) and (detail or len(prior['data'])<=10000000):
            cache[key]=prior;return prior
        try:data=fetch(ctx,url,limit=64*1024*1024 if detail else 10000000)
        except MissingResource as exc:cache[key]=exc;raise
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt=im.format;width,height=im.size;im.verify()
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError) as exc:raise CollectionError('상품 이미지 해독 실패: '+url) from exc
        cache[key]=dict(data=data,width=width,height=height,format=fmt)
    if isinstance(cache[key],MissingResource):raise cache[key]
    return cache[key]


def gallery(ctx,doc,cache):
    result=[]
    for node in doc.select('.m_prodetail-head-thumbnail .btn_thumbnail [data-preload],.m_prodetail-head-thumbnail .btn_thumbnail img'):
        url=source_url(node)
        if not url or url in result:continue
        try:image=image_info(ctx,url,cache)
        except MissingResource:
            ctx.log('공식 원본 404 갤러리 파일만 제외: '+url,'WARNING');continue
        if min(image['width'],image['height'])<=1:continue
        result.append(url)
    return result


def common_banner(url,node):
    path=urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    context=path+' '+str(node.get('alt') or '')+' '+str(node.get('title') or '')
    return bool(re.search(r'/mall/(?:event|md_image)/|/brand_info/|/(?:notice|delivery|shipping|banner|coupon)/',path,re.I) or re.search(r'delivery|shipping|refund|copyright|(?:^|[/_-])banner|배송|교환.?반품|저작권',context,re.I))


def detail_document(ctx,link,cache):
    key=('detail-document',link)
    if key not in cache:cache[key]=soup_get(ctx,link+'/description-iframe')
    area=cache[key].select_one('.m_prodetail-desc .mm_pinchzoom-wrapper')
    if area is None:raise CollectionError('상품 상세 본문 영역 확인 실패')
    return area


def detail_candidates(ctx,link,cache):
    area=detail_document(ctx,link,cache);photos=[]
    for node in area.select('img'):
        url=source_url(node)
        if not url or url in photos or common_banner(url,node):continue
        context=urllib.parse.unquote(urllib.parse.urlsplit(url).path)+' '+str(node.get('alt') or '')+' '+str(node.get('title') or '')
        if re.search(r'(?:^|[/_\s-])(?:size|chart|measure|gosi|spec|guide)(?:[._\s-]|$)|사이즈|고시|실측|측정|관리방법',context,re.I):continue
        try:image=image_info(ctx,url,cache,True)
        except MissingResource:continue
        w,h=image['width'],image['height']
        # Keep individual portrait/product cuts, not long composite descriptions.
        if min(w,h)<300 or max(w,h)/min(w,h)>2 or len(image['data'])>10000000 or image['format'] not in ('JPEG','PNG','WEBP'):continue
        with Image.open(io.BytesIO(image['data'])) as im:
            if getattr(im,'is_animated',False):continue
        photos.append(url)
    return photos


def photo_asset(url):
    p=urllib.parse.urlsplit(url)
    query=urllib.parse.parse_qsl(p.query,keep_blank_values=True)
    if p.hostname=='img.fashionplus.co.kr':query=[(k,v) for k,v in query if k.upper() not in ('RS','AR')]
    return (p.netloc.lower(),p.path,tuple(query))


def merge_photos(gallery_photos,body_photos,cache):
    result=[];assets=set();digests=set()
    for url in gallery_photos+body_photos:
        asset=photo_asset(url);info=cache.get((url,False)) or cache.get((url,True))
        digest=hashlib.sha256(info['data']).hexdigest() if isinstance(info,dict) and info.get('data') else None
        if asset in assets or digest and digest in digests:continue
        assets.add(asset)
        if digest:digests.add(digest)
        result.append(url)
    return result


def detail(ctx,link,cache):
    area=detail_document(ctx,link,cache)
    seen=set()
    allowed={'div','p','span','br','strong','b','em','i','h1','h2','h3','h4','h5','ul','ol','li','table','thead','tbody','tfoot','tr','th','td','caption','a'}
    blocked={'script','style','iframe','object','embed','form','input','button','link','meta','noscript'}
    def render(node):
        if isinstance(node,Comment):return ''
        if isinstance(node,NavigableString):return html.escape(str(node))
        if node.name in blocked:return ''
        if node.name=='img':
            url=source_url(node)
            if not url or common_banner(url,node) or url in seen:return ''
            seen.add(url)
            try:image=image_info(ctx,url,cache,True)
            except MissingResource:
                ctx.log('공식 상세 원본 404 파일 제외: '+url,'WARNING');return ''
            if min(image['width'],image['height'])<=1:return ''
            return '<img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="display:block;width:100%;height:auto;margin:0 auto">'
        children=''.join(render(child) for child in node.children)
        if node.name not in allowed or node.name in ('a','span','div'):return children
        if node.name=='br':return '<br>'
        # Source image galleries use ul/li with site-only list-reset CSS.
        # Export blocks instead so WING cannot reintroduce bullet markers.
        if node.name=='ul':return '<div style="margin:0;padding:0">'+children+'</div>' if children.strip() else ''
        if node.name=='li':
            if not children.strip():return ''
            if not node.parent or node.parent.name!='ol':return '<div style="margin:0;padding:0">'+children+'</div>'
        attrs=''
        if node.name=='table':attrs=' style="width:100%;border-collapse:collapse"'
        if node.name in ('td','th'):
            attrs=' style="border:1px solid #ddd;padding:6px"'
            for key in ('rowspan','colspan'):
                value=str(node.get(key) or '')
                if value.isdigit() and 0<int(value)<=100:attrs+=' '+key+'="'+value+'"'
        return '<'+node.name+attrs+'>'+children+'</'+node.name+'>'
    markup=''.join(render(n) for n in area.children)
    check=BeautifulSoup(markup,'html.parser')
    if not check.select('img') and not check.get_text(strip=True):raise CollectionError('안내 배너 제외 후 상품 상세 본문 없음')
    return '<div style="width:100%;max-width:780px;margin:0 auto;text-align:center">'+markup+'</div>'


FULL_CATEGORY=re.compile(r"fullCategory\s*:\s*'([^'\n]*)'")
LEVEL_CATEGORY=re.compile(r"'([123])(?:st|nd|rd)_category_name'\s*:\s*'([^'\n]*)'")


def read_category(doc):
    """상품 분류: 페이지 스크립트의 공식 fullCategory('아웃도어/레저>등산잡화>장갑') → 1~3차 category_name → 분류 링크.
    값이 없으면 None(브랜드를 분류로 대체하지 않음). Oberon 2026-11-20 수정(0dbe706)을 develop 버전에 다시 적용."""
    source=str(doc)
    found=FULL_CATEGORY.search(source)
    parts=[p.strip() for p in html.unescape(found[1]).split('>') if p.strip()] if found else []
    if not parts:
        levels={}
        for level,name in LEVEL_CATEGORY.findall(source):
            if name.strip() and level not in levels:levels[level]=html.unescape(name.strip())
        parts=[levels[k] for k in ('1','2','3') if k in levels]
    if not parts:
        parts=[n.get_text(' ',strip=True) for n in doc.select('.mm_breadcrumb a[href*="/goods/category/"]')]
    return ' > '.join(parts) or None


def preserve_text(record,label,value):
    # Excel cells hold 32,767 UTF-16 units. Preserve long raw options across
    # additional columns instead of letting openpyxl silently truncate them.
    chunks=[];part=[];units=0
    for char in value:
        width=2 if ord(char)>0xffff else 1
        if units+width>28000:
            chunks.append(''.join(part));part=[];units=0
        part.append(char);units+=width
    chunks.append(''.join(part))
    for index,text in enumerate(chunks,1):record[label if index==1 else label+' ('+str(index)+')']=text


def crawl(ctx):
    try:
        number=item_number(ctx.product_code);link=BASE+'/goods/detail/'+number;cache={};ctx.allow_automatic_results=True
        ctx.stage('패션플러스 품목번호 직접 조회: '+number)
        doc,link,record,child=resolve_product(ctx,number)
        record.setdefault('입력품목ID',number)
        palette,sizes,raw_options=read_options(ctx,link)
        if child:
            matching=[row for row in raw_options if str(row.get('option_id'))==child]
            if not matching:ctx.log('입력 하위품목은 현재 옵션 목록에 없습니다: '+child+' / 공식 상위 연결·SKU 일치가 확인되어 상위 상품의 현재 전체 옵션을 수집합니다.','WARNING')
        preserve_text(record,'원본 옵션',json.dumps(raw_options,ensure_ascii=False))
        if ctx.enabled('size'):record['size']=','.join(sizes) or None
        if ctx.enabled('price'):
            record['price']=read_price(doc)
        if ctx.enabled('regular_price'):record['regular_price']=read_price(doc,True)
        if ctx.enabled('category'):
            record['category']=read_category(doc)
            if not record['category']:ctx.log('공식 상품 분류(fullCategory) 없음: 카테고리 빈칸, 나머지 수집 계속','WARNING')
        if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,link,cache)
        wants=ctx.enabled('main_image') or ctx.enabled('additional_images')
        photos=merge_photos(gallery(ctx,doc,cache),detail_candidates(ctx,link,cache),cache) if wants else []
        if wants and not photos:raise CollectionError('갤러리·상세페이지 상품컷 수집 결과 없음')
        if wants:record['전체 상품사진']='\n'.join(photos)
        if not wants:
            for color in palette:ctx.emit_result(dict(record,color=color['color_name'] or color['color_code'],**color))
        elif len(palette)>1:
            for url in photos:ctx.emit_unmapped(dict(record),palette,url,shared_detail_html=record.get('detail_page',''))
        else:
            color=palette[0];row=dict(record,color=color['color_name'] or color['color_code'],**color)
            if ctx.enabled('main_image'):row['main_image']=photos[0]
            if ctx.enabled('additional_images'):
                extras=photos[1:] or photos[:1];row['additional_images']=','.join(extras[i%len(extras)] for i in range(max(5,len(extras))))
            ctx.emit_result(row)
        ctx.log(number+': 브랜드 '+record['브랜드']+' / 상품 사진 '+str(len(photos))+'장 / 옵션 '+str(len(raw_options))+'개','SUCCESS')
    except (CollectionError,ValueError,KeyError) as exc:ctx.fail_item(str(exc));raise
