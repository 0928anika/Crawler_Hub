PORTAL_API_VERSION = 1

import hashlib
import html
import http.client
import io
import re
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin,urlsplit,urlencode,parse_qs,quote,unquote
from bs4 import BeautifulSoup
from PIL import Image,UnidentifiedImageError
from crawler_rules import validate_gallery_image,gallery_slots

SITE='https://intercrewkorea.co.kr'
HOSTS=('intercrewkorea.co.kr','www.intercrewkorea.co.kr')
IMAGE_HOSTS=HOSTS+('intercrewkk.mycafe24.com',)
MAX_BYTES=30000000
REQUEST_SECONDS=45
SOCKET_SECONDS=15
REQUEST_ATTEMPTS=3
class CrawlError(RuntimeError):
    """Official collection could not be completed."""
class MissingResource(CrawlError):
    """The official image URL returns 404."""

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


def parse(raw):return BeautifulSoup(raw,'html.parser')
def text(node):return node.get_text(' ',strip=True) if node else ''

def amount(value):
    value=re.sub(r'\s|원|₩','',str(value or ''))
    if not re.fullmatch(r'\d[\d,]*',value):return None
    return int(value.replace(',','')) or None

def info(doc):
    return {text(row.find('th')).lower():text(row.find('td')) for row in doc.select('.infoArea .prd_info tr') if row.find('th') and row.find('td')}

def options(doc,title):
    return [n for n in doc.select('.infoArea select[option_title="'+title+'"] option') if n.get('value') not in ('*','**','',None)]

def identity(doc,query):
    fields=info(doc);sku=fields.get('product no.','').upper()
    match=re.fullmatch(r'([A-Z0-9]{9})([A-Z0-9]{2})',sku)
    if not match or query not in (sku,match[1]):raise CrawlError('공식 상품코드와 요청 품번 불일치: '+sku)
    if fields.get('brand','').upper()!='INTERCREW':raise CrawlError('공식 인터크루 브랜드 확인 실패')
    colors=options(doc,'색상')
    if len(colors)!=1:raise CrawlError('색상별 개별 상품 페이지 확인 필요: '+sku)
    color=str(colors[0]['value']).strip();name=fields.get('상품명','')
    if not color or not name:raise CrawlError('필수 상품명/색상 없음')
    name=re.sub(r'\s*'+re.escape(sku)+r'\s*$','',name,flags=re.I)
    name=re.sub(r'\s+'+re.escape(color)+r'\s*$','',name).strip()
    return dict(product_name=name,color=color,color_name=color,color_code=match[2]),sku

def search(ctx,query):
    queue=[SITE+'/product/search.html?'+urlencode({'keyword':query})];seen=set();found={}
    while queue:
        url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise CrawlError('공식 검색 30페이지 제한 초과')
        seen.add(url);doc=parse(fetch(ctx,url,MAX_BYTES))
        for card in doc.select('.prdList > li'):
            for anchor in card.select('a[href]'):
                link=urljoin(url,anchor['href']);p=urlsplit(link)
                if p.hostname not in HOSTS:continue
                match=re.search(r'/product/[^/]+/(\d+)(?:/|$)',p.path)
                number=match[1] if match else parse_qs(p.query).get('product_no',[''])[0]
                # Search cards, not page recommendations; verify exact code in detail.
                if number.isdigit():found.setdefault(number,link);break
        for a in doc.select('.xans-search-paging a[href]'):
            if a['href'].startswith('#'):continue
            link=urljoin(url,a['href']);p=urlsplit(link);params=parse_qs(p.query)
            if p.hostname in HOSTS and p.path=='/product/search.html' and params.get('keyword')==[query] and link not in seen and link not in queue:queue.append(link)
    return list(found.values())

def image(ctx,url,cache):
    if urlsplit(url).hostname not in IMAGE_HOSTS:raise CrawlError('미확인 이미지 출처: '+url)
    if url not in cache:
        raw=fetch(ctx,url,MAX_BYTES)
        try:
            with Image.open(io.BytesIO(raw)) as pic:
                if pic.width*pic.height>80000000:raise CrawlError('이미지 픽셀 한도 초과')
                pic.load();cache[url]=dict(raw=raw,width=pic.width,height=pic.height,digest=hashlib.sha256(raw).hexdigest())
        except (OSError,UnidentifiedImageError) as exc:raise CrawlError('이미지 해독 실패: '+url) from exc
    return cache[url]

def gallery(ctx,doc,link,cache):
    shots=[];seen=set()
    for node in doc.select('.keyImg img.BigImage,.listImg img'):
        src=node.get('ec-data-src') or node.get('data-src') or node.get('src')
        if not src:continue
        url=urljoin(link,src);im=image(ctx,url,cache)
        valid=validate_gallery_image(im['raw'],role='product_shot',mapping_verified=True,single_shot_verified=True,mapping_evidence='공식 상품코드+단일 색상 옵션이 일치하는 주상품 갤러리',content_evidence='공식 .keyImg/.listImg 개별 상품 사진, 상세/사이즈/배송 영역 제외')
        if valid['passed'] and im['digest'] not in seen:seen.add(im['digest']);shots.append(dict(url=url,validation=valid))
        elif not valid['passed']:ctx.log('갤러리 규격 제외: '+url+' / '+','.join(valid['reasons']),'WARNING')
    if not shots:raise CrawlError('유효한 공식 원본 상품컷 없음')
    main,extras,warning=gallery_slots(shots)
    if warning:ctx.log(warning,'WARNING')
    return main,','.join(extras)

def detail_urls(doc,link):
    area=doc.select_one('#prdDetail .cont')
    if area is None:raise CrawlError('공식 상세 본문 영역 없음')
    urls=[]
    for node in area.select('img'):
        src=node.get('ec-data-src') or node.get('data-original') or node.get('data-src') or node.get('src')
        if not src:continue
        url=urljoin(link,src);path=unquote(urlsplit(url).path).lower()
        if re.search(r'delivery|shipping|exchange|return|배송|교환|반품',path):continue
        if url not in urls:urls.append(url)
    if not urls:raise CrawlError('실제 상세 본문 이미지 없음')
    return urls

def detail(ctx,doc,link,cache):
    parts=[];has_body=False
    for url in detail_urls(doc,link):
        try:im=image(ctx,url,cache)
        except MissingResource:ctx.log('공식 원본 404 상세 이미지 제외: '+url,'WARNING');continue
        if min(im['width'],im['height'])<=1:ctx.log('1픽셀 빈 이미지 제외: '+url,'WARNING');continue
        if not re.search(r'info_size|model_\d',url,re.I):has_body=True
        parts.append('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="max-width:100%;height:auto"><br>')
    if not has_body:raise CrawlError('사용 가능한 상품 상세 본문 이미지 없음')
    result='<div>'+''.join(parts)+'</div>'
    if len(result.encode('utf-16-le'))//2>32767:raise CrawlError('상세 HTML 셀 한도 초과; 원문 임의 절단 안 함')
    return result

def regular_price(ctx,doc):
    try:
        value=amount(text(doc.select_one('.infoArea #span_product_price_custom')))
        if value is None:ctx.log('독립 정상가 미표시/0/형식 불명확: 빈칸으로 계속','WARNING')
        return value
    except Exception as exc:ctx.log('정상가 확인 불가: '+str(exc),'WARNING');return None

def crawl(ctx):
    query=str(ctx.product_code).strip().upper();query=re.sub(r'[_-](?=[A-Z0-9]{2}$)','',query)
    if not query:raise CrawlError('검색 품번이 비어 있습니다')
    ctx.stage('인터크루 공식 검색: '+query);rows=0;errors=[]
    try:links=search(ctx,query)
    except Exception as exc:ctx.fail_item(str(exc));raise
    for link in links:
        try:
            doc=parse(fetch(ctx,quote(link,safe=':/?&=%+#,;@!$*()~[]'),MAX_BYTES))
            fields=info(doc);sku=fields.get('product no.','').upper()
            if not (sku==query or (sku.startswith(query) and len(sku)==len(query)+2)):continue
            record,sku=identity(doc,query);record.update(product_code=str(ctx.product_code).strip().upper(),product_url=link)
            if ctx.enabled('size'):
                values=list(dict.fromkeys(str(n['value']).strip() for n in options(doc,'사이즈')))
                if not values:raise CrawlError('공식 사이즈 옵션 없음')
                record['size']=','.join(values)
            if ctx.enabled('price'):
                record['price']=amount(text(doc.select_one('.infoArea #span_product_price_sale')) or text(doc.select_one('.infoArea #span_product_price_text')))
            if ctx.enabled('regular_price'):record['regular_price']=regular_price(ctx,doc)
            if ctx.enabled('category'):
                record['category']=' > '.join(text(n) for n in doc.select('.path ol li')[1:] if text(n) and 'displaynone' not in n.get('class',[]))
            cache={}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main,extras=gallery(ctx,doc,link,cache)
                if ctx.enabled('main_image'):record['main_image']=main
                if ctx.enabled('additional_images'):record['additional_images']=extras
            if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,link,cache)
            ctx.emit_result(record);rows+=1
        except Exception as exc:errors.append(str(exc));ctx.fail_item(str(exc))
    if not rows and not errors:errors.append('공식 일치 상품 검색 결과 없음: '+query);ctx.fail_item(errors[-1])
    if errors:raise CrawlError('수집 미완료: '+' / '.join(errors))
