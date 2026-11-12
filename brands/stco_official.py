PORTAL_API_VERSION = 1

import hashlib
import html
import http.client
import io
import json
import re
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin,urlsplit,urlencode,parse_qs,quote
from bs4 import BeautifulSoup
from PIL import Image,UnidentifiedImageError
from crawler_rules import validate_gallery_image,gallery_slots
SITE='https://www.stco.co.kr'
HOSTS=('www.stco.co.kr','stco.co.kr')
IMAGE_HOSTS=('image.codigallery.com','webimg.codigallery.com')
MAX_BYTES=15000000
REQUEST_SECONDS=45
SOCKET_SECONDS=15
REQUEST_ATTEMPTS=3
class CrawlError(RuntimeError):
    """Official source or collection failure."""
class MissingResource(CrawlError):
    """Source image was removed."""

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
                # This timeout also covers TLS after the TCP socket is returned.
                connection.settimeout(min(SOCKET_SECONDS, max(.01, deadline-time.monotonic())))
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
    if urlsplit(url).scheme not in ('http','https'):
        raise CrawlError('공개 HTTP/HTTPS 주소가 아님: '+url)
    deadline = time.monotonic() + REQUEST_SECONDS
    started = time.monotonic()
    ctx.log('공개 요청: ' + url, 'INFO')
    for attempt in range(REQUEST_ATTEMPTS):
        ctx.checkpoint()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 %s초 제한 초과' % REQUEST_SECONDS)
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            # Retry with a fresh connection after TLS/temporary network failures.
            opener = public_opener(ctx, deadline)
            with opener.open(request, timeout=min(SOCKET_SECONDS, remaining)) as response:
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise CrawlError('응답 크기 초과: ' + url)
                chunks, size = [], 0
                while True:
                    ctx.checkpoint()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('공개 응답 전체 %s초 제한 초과' % REQUEST_SECONDS)
                    block = response.read1(min(65536, limit + 1 - size))
                    if not block:
                        break
                    chunks.append(block)
                    size += len(block)
                    if size > limit:
                        raise CrawlError('응답 크기 초과: ' + url)
            ctx.checkpoint()
            ctx.log('공개 요청 완료 %.1f초: %s' % (time.monotonic()-started, url), 'INFO')
            return b''.join(chunks)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 404:
                raise MissingResource('HTTP 404: ' + url) from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == REQUEST_ATTEMPTS-1:
                raise CrawlError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt == REQUEST_ATTEMPTS-1 or time.monotonic() >= deadline:
                raise CrawlError('통신 실패: %s / %s' % (url, exc)) from exc
        ctx.log('일시적인 연결/응답 실패, 새 연결로 재시도 %s/%s: %s' % (attempt+2, REQUEST_ATTEMPTS, url),'WARNING')
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CrawlError('요청 실패: ' + url)




def parse(raw):
    return BeautifulSoup(raw,'html.parser')


def text(node):
    return node.get_text(' ',strip=True) if node else ''


def notice_pairs(doc):
    area=doc.select_one('#targetProdInfoNotice')
    if area is None:raise CrawlError('공식 상품정보 제공고시 영역 없음')
    pairs=[]
    for row in area.select('table tr'):
        key=row.find('th');value=row.find('td')
        if key is not None and value is not None:pairs.append((text(key),value.get_text('\n',strip=True)))
    if not pairs:raise CrawlError('공식 고시 표 구조 변경')
    return pairs


def field(doc,label):
    return next((v for k,v in notice_pairs(doc) if k==label),'')


def identity(doc,expected):
    box=doc.select_one('.prodOptionBox')
    if box is None:raise CrawlError('공식 상품 옵션 영역 없음')
    primary=doc.select_one('.prodOptionBox > input.prd_no_cate')
    sku=str(primary.get('value') or '').strip().upper() if primary else ''
    if sku!=expected or field(doc,'품번').strip().upper()!=expected:raise CrawlError('주상품 품번/고시 품번과 요청 상품 불일치: '+expected)
    name=text(box.select_one('.prodTitle'));color=field(doc,'색상').strip()
    if not name:raise CrawlError('공식 상품명 없음')
    if not re.search(r'[가-힣]',color):raise CrawlError('공식 한글 색상명 없음: '+sku)
    if len(sku)<4 or not re.fullmatch(r'[A-Z0-9]{3}',sku[-3:]):raise CrawlError('공식 3자리 색상코드 확인 불가: '+sku)
    return {'product_name':name,'color':color,'color_name':color,'color_code':sku[-3:]}


def search(ctx,query):
    queue=[SITE+'/Search?'+urlencode({'searchWord':query})];seen=set();products={}
    while queue:
        ctx.checkpoint();url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise CrawlError('검색30페이지 한도 초과')
        seen.add(url);doc=parse(fetch(ctx,url,MAX_BYTES))
        for anchor in doc.select('.best_item_contents a[href]'):
            link=urljoin(SITE,anchor['href']);parts=urlsplit(link);m=re.fullmatch(r'/detail/index/([^/]+)',parts.path,re.I)
            if parts.hostname not in HOSTS or not m:continue
            sku=m[1].upper()
            if sku==query or (sku.startswith(query) and len(sku)==len(query)+3):products.setdefault(sku,link)
        for anchor in doc.select('.paging a[href],.pagination a[href]'):
            link=urljoin(url,anchor['href']);parts=urlsplit(link);params=parse_qs(parts.query)
            if parts.hostname in HOSTS and parts.path.lower()=='/search' and params.get('searchWord')==[query] and link not in seen and link not in queue:queue.append(link)
    if not products:
        # A direct full SKU can remain available after it leaves search results.
        products[query]=SITE+'/detail/index/'+quote(query,safe='')
    return list(products.items())


def sizes(doc,sku):
    values=[]
    for node in doc.select('.prodOptionBox .listOption input[data-name="사이즈"][data-prd_no]'):
        if node.get('data-prd_no')!=sku:continue
        value=str(node.get('value') or '').strip()
        if value and value not in values:values.append(value)
    if not values:raise CrawlError('주상품 공식 사이즈 옵션 없음')
    return ','.join(values)


def amount(value):
    match=re.fullmatch(r'\s*(?:₩)?\s*([0-9,]+)\s*원?\s*',str(value or ''))
    if not match:return None
    number=int(match[1].replace(',',''));return number or None


def regular_price(ctx,doc):
    try:value=amount(text(doc.select_one('.prodOptionBox .priceBox .priceOld')))
    except Exception as exc:ctx.log('정상가 확인 불가: '+str(exc),'WARNING');return None
    if value is None:ctx.log('독립 정상가 미표시/0/불명확: 빈칸으로 나머지 수집 계속','WARNING')
    return value


def image(ctx,url,cache):
    if url not in cache:
        raw=fetch(ctx,url,MAX_BYTES)
        try:
            with Image.open(io.BytesIO(raw)) as pic:
                if pic.format not in ('JPEG','PNG','WEBP','GIF') or pic.width*pic.height>80000000:raise CrawlError('이미지 형식/크기 확인 실패: '+url)
                pic.load();cache[url]={'raw':raw,'width':pic.width,'height':pic.height,'digest':hashlib.sha256(raw).hexdigest()}
        except (OSError,UnidentifiedImageError) as exc:raise CrawlError('공식 이미지 해독 실패: '+url) from exc
    return cache[url]


def gallery_urls(doc,link,sku):
    urls=[]
    # Coordinate products live in .coordOption, not in this primary swiper.
    for node in doc.select('.prodDetailTopWrap .swpMainVisual img'):
        url=urljoin(link,node.get('data-zoom-image') or node.get('data-src') or node.get('src') or '')
        parts=urlsplit(url);filename=parts.path.rsplit('/',1)[-1]
        if parts.hostname not in IMAGE_HOSTS or not filename.upper().startswith(sku+'_'):continue
        if url not in urls:urls.append(url)
    return urls


def gallery(ctx,doc,link,sku,cache):
    shots=[];seen=set()
    for url in gallery_urls(doc,link,sku):
        info=image(ctx,url,cache)
        validation=validate_gallery_image(info['raw'],role='product_shot',mapping_verified=True,single_shot_verified=True,mapping_evidence='주상품 .swpMainVisual의 전체 품번 파일명 + 주상품/고시 품번 대조',content_evidence='공식 주상품 갤러리 개별 사진; 코디 옵션 영역 제외')
        if validation['passed'] and info['digest'] not in seen:
            seen.add(info['digest']);shots.append({'url':url,'validation':validation})
        else:ctx.log('개별 상품컷 기준 제외: '+url,'WARNING')
    if not shots:raise CrawlError('주상품의 유효한 원본 상품컷 없음')
    main,extra,warning=gallery_slots(shots)
    if warning:ctx.log(warning,'WARNING')
    return main,','.join(extra)


def body_urls(doc,link):
    area=doc.select_one('#prodInfo')
    if area is None:raise CrawlError('공식 상세 본문 영역 없음')
    urls=[]
    for node in area.find_all('img'):
        url=urljoin(link,node.get('data-original') or node.get('data-src') or node.get('src') or '')
        parts=urlsplit(url)
        if parts.hostname not in IMAGE_HOSTS:raise CrawlError('미확인 본문 이미지 출처: '+url)
        if not parts.path or parts.path.endswith('/'):
            continue  # Empty filename: host root or /promotion/ banner placeholder.
        if url not in urls:urls.append(url)
    if not urls:raise CrawlError('실제 상세 본문 이미지 없음')
    return urls


def notice_html(doc):
    def escaped(value):return html.escape(value).replace('\n','<br>')
    rows=''.join('<tr><th>'+escaped(k)+'</th><td>'+escaped(v)+'</td></tr>' for k,v in notice_pairs(doc))
    result='<h3>상품정보 제공고시</h3><table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">'+rows+'</table>'
    area=parse(str(doc.select_one('#targetProdInfoNotice')))
    for node in area.select('table,script,style,h1,h2,h3,h4'):node.decompose()
    remaining=area.get_text('\n',strip=True)
    remaining=remaining.replace('상품정보 제공고시','').strip()
    if remaining:result+='<p>'+escaped(remaining)+'</p>'
    return result


def detail(ctx,doc,link,cache):
    parts=[]
    for url in body_urls(doc,link):
        image(ctx,url,cache)  # Every real body source is checked; no truncation.
        parts.append('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세 본문" style="max-width:100%;height:auto"><br>')
    result='<div>'+''.join(parts)+notice_html(doc)+'</div>'
    if len(result.encode('utf-16-le'))//2>32767:raise CrawlError('상세 HTML 엑셀 셀 한도 초과: 원문을 임의 절단하지 않습니다')
    return result


def crawl(ctx):
    query=str(ctx.product_code).strip().upper()
    if not query:raise CrawlError('검색 품번이 비어 있습니다')
    # Support explicit base_COLOR as a full-SKU spelling; no length whitelist.
    lookup=re.sub(r'[_-](?=[A-Z0-9]{3}$)','',query)
    ctx.stage('에스티코 공식 검색: '+lookup);rows=0;errors=[]
    try:products=search(ctx,lookup)
    except Exception as exc:ctx.fail_item(str(exc));raise
    for sku,link in products:
        try:
            doc=parse(fetch(ctx,link,MAX_BYTES));record=identity(doc,sku)
            record.update(product_code=query,product_url=link)
            if ctx.enabled('size'):record['size']=sizes(doc,sku)
            if ctx.enabled('price'):record['price']=amount(text(doc.select_one('.prodOptionBox .priceBox .priceNew')))
            if ctx.enabled('regular_price'):record['regular_price']=regular_price(ctx,doc)
            if ctx.enabled('category'):
                node=doc.select_one('.prodOptionBox > input.prd_no_cate')
                record['category']=' > '.join(v for v in str(node.get('data-category','') if node else '').split('>') if v and v.lower()!='all')
            cache={}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main,extras=gallery(ctx,doc,link,sku,cache)
                if ctx.enabled('main_image'):record['main_image']=main
                if ctx.enabled('additional_images'):record['additional_images']=extras
            if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,link,cache)
            ctx.emit_result(record);rows+=1
        except Exception as exc:errors.append(sku+' / '+str(exc));ctx.fail_item(errors[-1])
    if not rows and not errors:errors.append('공식 상품 검색 결과 없음: '+query);ctx.fail_item(errors[-1])
    if errors:raise CrawlError('일부 수집 미완료: '+' / '.join(errors))
