PORTAL_API_VERSION = 1
import html,http.client,io,json,re,socket,time,hashlib,urllib.error,urllib.parse,urllib.request
import base64,subprocess,tempfile
from pathlib import Path
from bs4 import BeautifulSoup,NavigableString,Comment
from PIL import Image,UnidentifiedImageError
BASE='https://www.fashionplus.co.kr'
REVISION='2026-11-19-fashionplus-integrated-v1'
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


def product_info(doc,number):
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
            if product.get('mpn') is not None and str(product['mpn'])!=number:continue
            sku=product.get('sku')
            if isinstance(sku,(str,int)) and str(sku).strip():structured_skus.append(str(sku).strip())
    structured_skus=list(dict.fromkeys(structured_skus))
    if len(structured_skus)>1 or seller_number and structured_skus and seller_number!=structured_skus[0]:raise CollectionError('상품번호(SKU) 원문 불일치: '+number)
    sku=seller_number or (structured_skus[0] if structured_skus else '')
    record.update(product_code=sku,**{'상위품목ID':number})
    record.update({'판매자':seller,'판매자 상품번호':seller_number,'상품정보 원문':'\n'.join(k+': '+v for k,v in provided.items())})
    return record


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
    return bool(re.search(r'/mall/(?:event|md_image)/|/brand_info/|/(?:delivery|shipping|banner|coupon)/',path,re.I) or re.search(r'delivery|shipping|refund|copyright|(?:^|[/_-])banner|배송|교환.?반품|저작권',context,re.I))


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



SEARCH_SELECTOR = '#goods_list_filter .goods-grid-pc'

def exact_code(text,code):return bool(re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',str(text or '').upper()))

def search(ctx, code):
    """Use normal public search rendering; direct search JSON returns 403.

    A fresh isolated browser context is used. Never attach to the user's Chrome
    profile, import cookies, solve challenges or spoof browser automation flags.
    """
    try:
        from playwright.sync_api import sync_playwright, Error as BrowserError
    except ImportError as exc:
        raise CollectionError('playwright 설치가 필요합니다') from exc
    ctx.stage('패션플러스 공개 검색 목록 확인')
    results = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            try:
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(1500)
                page.route('**/*', lambda route: route.abort()
                           if route.request.resource_type in ('image', 'media', 'font')
                           else route.continue_())
                for page_no in range(1, 1001):
                    ctx.checkpoint()
                    url = BASE + '/search/goods/result?' + urllib.parse.urlencode(
                        {'searchWord': code, 'page': page_no, 'pageSize': 40})
                    response = page.goto(url, timeout=30000, wait_until='domcontentloaded')
                    if response is None or response.status >= 400:
                        raise CollectionError('검색 페이지 접근 실패: ' + url)
                    deadline = time.monotonic() + 35
                    markup = ''
                    while time.monotonic() < deadline:
                        ctx.checkpoint()
                        if (page.locator(SEARCH_SELECTOR + ' .product-item a[href]').count()
                                or (page.locator(SEARCH_SELECTOR + ' .mm_text-none:visible').count()
                                    and not page.locator(SEARCH_SELECTOR + ' .skeleton').count())):
                            markup = page.locator(SEARCH_SELECTOR).inner_html()
                            break
                        body = page.locator('body').inner_text(timeout=1500)
                        if (not page.locator(SEARCH_SELECTOR + ' .skeleton').count()
                                and ('검색 결과가 없습니다' in body or '검색결과가 없습니다' in body)):
                            return list(results.values())
                        page.wait_for_timeout(200)
                    if not markup:
                        raise CollectionError('검색 목록 로딩 실패/접근 제한: ' + url)
                    if len(markup.encode('utf-8')) > 4_000_000:
                        raise CollectionError('검색 DOM 크기 초과')
                    soup = BeautifulSoup(markup, 'html.parser')
                    count_node = soup.select_one('.goods-count')
                    count = int(re.sub(r'\D', '', count_node.get_text()) or '0')
                    cards = soup.select('.product-list > .product-item')
                    if count and not cards:
                        raise CollectionError('검색 개수는 있으나 상품 카드 파싱 실패')
                    for card in cards:
                        ctx.checkpoint()
                        name = card.select_one('.text-product')
                        brand = card.select_one('.text-brand')
                        anchor = card.select_one('a[href]')
                        thumb = source_url(card.select_one('.image-product'))
                        if name:
                            if not anchor:
                                raise CollectionError('검색 상품 링크/대표사진 누락')
                            link = urllib.parse.urljoin(BASE, anchor['href'])
                            if not re.fullmatch(re.escape(BASE) + r'/goods/detail/\d+', link):
                                raise CollectionError('검색 상품 링크 형식 변경: ' + link)
                            results[link] = (link, name.get_text(' ', strip=True), thumb)
                    if page_no * 40 >= count:
                        return list(results.values())
                raise CollectionError('검색 페이지 안전 한도 초과: 미완료')
            finally:
                browser.close()
    except BrowserError as exc:
        raise CollectionError('공개 검색 브라우저 실패 (Chrome 설치 필요): ' + str(exc)) from exc


OCR_SCRIPT = r"""[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)

$ProgressPreference='SilentlyContinue'
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1})[0]
function Await($Op,$Type) { $task=$asTask.MakeGenericMethod($Type).Invoke($null,@($Op));$task.Wait();$task.Result }

$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko-KR'))
if ($null -eq $engine) { throw 'Korean OCR language is not installed' }
foreach($ImagePath in $ImagePaths) {
try {
$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
[pscustomobject]@{text=$result.Text} | ConvertTo-Json -Compress
} catch { [pscustomobject]@{error=$_.Exception.Message} | ConvertTo-Json -Compress } finally { if ($bitmap) {$bitmap.Dispose();$bitmap=$null}; if ($stream) {$stream.Dispose();$stream=$null} }

}
"""

def ocr_many(ctx, pictures):
    """One PowerShell/OCR engine for a bounded group; retain every image tile."""
    if not pictures:return []
    with tempfile.TemporaryDirectory(prefix='cheek-eland-ocr-') as folder:
        paths=[];owners=[]
        for owner,picture in enumerate(pictures):
            width=min(1600,max(picture.width,1200))
            scaled=picture.convert('RGB').resize((width,round(picture.height*width/picture.width)))
            for y in range(0,scaled.height,2100):
                ctx.checkpoint()
                path=Path(folder)/(str(len(paths))+'.png')
                scaled.crop((0,y,width,min(y+2300,scaled.height))).save(path)
                paths.append(str(path.resolve()));owners.append(owner)
        script=OCR_SCRIPT
        manifest=Path(folder)/'tiles.json'
        manifest.write_text(json.dumps(paths,ensure_ascii=False),encoding='utf-8')
        script="$ImagePaths=Get-Content -LiteralPath '"+str(manifest.resolve()).replace("'","''")+"' -Raw -Encoding UTF8 | ConvertFrom-Json\n"+script
        command=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        process=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+min(120,20+len(paths)*10)
        try:
            while True:
                ctx.checkpoint()
                if time.monotonic()>deadline:raise CollectionError('상세 이미지 OCR 검사 시간 초과')
                try:
                    output,error=process.communicate(timeout=.2);break
                except subprocess.TimeoutExpired:pass
            if process.returncode:raise CollectionError('Windows 한국어 OCR 검사 실행 실패')
        finally:
            if process.poll() is None:process.kill();process.communicate()
    try:
        tiles=[json.loads(line) for line in output.splitlines() if line.strip()]
        if len(tiles)!=len(owners):raise ValueError('OCR tile count mismatch')
        results=['' for _ in pictures]
        for owner,tile in zip(owners,tiles):
            if 'error' in tile:results[owner]=CollectionError('OCR 이미지 검사 실패: '+tile['error'])
            elif not isinstance(results[owner],Exception):results[owner]+=tile['text']+'\n'
        return results
    except (ValueError,KeyError,TypeError) as exc:
        raise CollectionError('OCR 결과 형식 확인 실패') from exc


BANNER_TEXT=re.compile(r'판매자안내|매장안내|배송안내|배송정책|출고안내|교환반품|교환및반품|반품안내|무료배송이벤트|쿠폰|구매전필독|택배안내',re.I)


def all_candidates(ctx,doc,link,cache):
    nodes=list(doc.select('.m_prodetail-head-thumbnail .btn_thumbnail [data-preload],.m_prodetail-head-thumbnail .btn_thumbnail img'))
    area=detail_document(ctx,link,cache)
    nodes+=list(area.select('img'))
    photos=[];assets=set();digests=set();batches=[]
    for node in nodes:
        url=source_url(node)
        if not url or common_banner(url,node) or photo_asset(url) in assets:continue
        assets.add(photo_asset(url))
        try:info=image_info(ctx,url,cache,True)
        except MissingResource:ctx.log('없는 원본 이미지 제외: '+url,'WARNING');continue
        if min(info['width'],info['height'])<=1 or info['format'] not in ('JPEG','PNG','WEBP'):continue
        with Image.open(io.BytesIO(info['data'])) as im:
            if getattr(im,'is_animated',False) or im.width*im.height>80000000:continue
        digest=hashlib.sha256(info['data']).hexdigest()
        if digest in digests:continue
        digests.add(digest);batches.append((url,info))
    for offset in range(0,len(batches),4):
        batch=batches[offset:offset+4];pictures=[]
        for url,info in batch:
            with Image.open(io.BytesIO(info['data'])) as im:pictures.append(im.copy())
        try:texts=ocr_many(ctx,pictures)
        except (CollectionError,OSError,subprocess.SubprocessError) as exc:
            ctx.log('이미지 배너 검사 미완료: 해당 후보 제외 / '+str(exc),'WARNING');continue
        for (url,info),text in zip(batch,texts):
            if isinstance(text,Exception) or BANNER_TEXT.search(re.sub(r'\s+','',text)):
                ctx.log('판매자·배송·쿠폰 이미지 제외: '+url,'INFO');continue
            photos.append(url)
    return photos,area


def crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not code:raise CollectionError('품번을 입력하세요.')
    if not all(ctx.enabled(k) for k in ('main_image','additional_images','detail_page')):raise CollectionError('이 크롤러는 대표·추가이미지와 상세페이지를 함께 수집합니다.')
    ctx.log('패션플러스 통합 '+REVISION,'INFO');links=search(ctx,code);matched=0;emitted=0;seen=set();errors=[]
    for link,search_name,thumbnail in links:
        ctx.checkpoint()
        try:
            number=item_number(link);doc=soup_get(ctx,link);record=product_info(doc,number)
            if not (exact_code(record.get('판매자 상품번호',''),code) or exact_code(record['product_name'],code)):
                ctx.log('다른 품번 제외: '+record['product_name']+' / '+record.get('판매자 상품번호',''),'INFO');continue
            matched+=1;record['product_code']=code;record['상위품목ID']=number
            palette,sizes,options=read_options(ctx,link)
            if ctx.enabled('size'):record['size']=','.join(sizes) or None
            if ctx.enabled('price'):record['price']=read_price(doc)
            if ctx.enabled('regular_price'):record['regular_price']=read_price(doc,True)
            if ctx.enabled('category'):record['category']=' > '.join(n.get_text(' ',strip=True) for n in doc.select('.mm_breadcrumb a[href*="/goods/category/"]')) or None
            preserve_text(record,'원본 옵션',json.dumps(options,ensure_ascii=False))
            cache={};photos,area=all_candidates(ctx,doc,link,cache)
            preserve_text(record,'원본 상세 텍스트',area.get_text(' ',strip=True))
            preserve_text(record,'원본 상세 데이터',str(area))
            record['전체 상품사진']='\n'.join(photos)
            for url in photos:
                digest=hashlib.sha256(cache[(url,True)]['data']).hexdigest()
                # Retain each seller's option/information record; the mapping UI deduplicates photos by URL.
                ctx.emit_unmapped(dict(record),palette,url);emitted+=1
        except (CollectionError,ValueError,KeyError) as exc:
            message=link+' / '+str(exc);errors.append(message);ctx.fail_item(message)
    if not emitted:
        message='품번 검색 결과 없음: '+code if not links else '정확한 품번 일치 상품 없음: '+code if not matched else '판매자·배송 안내 제외 후 사용 가능한 사진 없음: '+code
        ctx.fail_item(message);raise CollectionError(message)
    if errors:raise CollectionError('일부 상품 수집 미완료: '+' / '.join(errors))
    ctx.log(code+': '+str(matched)+'개 상품 / '+str(emitted)+'장 후보. 상세페이지와 색상 사진을 각각 선택하세요.','SUCCESS')
