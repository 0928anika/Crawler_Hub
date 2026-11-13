PORTAL_API_VERSION = 1

import hashlib
import html
import http.client
import io
import json
from decimal import Decimal,InvalidOperation
import re
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin,urlsplit,urlencode,parse_qs,quote,unquote
from bs4 import BeautifulSoup
from PIL import Image,UnidentifiedImageError
from crawler_rules import validate_gallery_image,gallery_slots

SITE='https://www.happylandmall.com'
HOSTS=('happylandmall.com','www.happylandmall.com')
IMAGE_HOSTS=('godomall-storage.cdn-nhncommerce.com','img.efe.co.kr')
STORE_PREFIX='/fa361ad2073a06ae52740c330ad8fb60/'
DETAIL_CDN='pub-020c219c6a054e3ab4d8a4c1f8bd4a5c.r2.dev'
MAX_BYTES=30000000
REQUEST_SECONDS=45
SOCKET_SECONDS=15
REQUEST_ATTEMPTS=3
BRANDS={'004':'압소바','002':'해피랜드'}
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


def fetch(ctx, url, limit=4_000_000, request_seconds=REQUEST_SECONDS, attempt_seconds=None):
    """Anonymous GET. No cookies, credentials or Referer, including images."""
    if urlsplit(url).scheme not in ('http','https'):
        raise CrawlError('공개 HTTP/HTTPS 주소가 아님: '+url)
    started = time.monotonic()
    total_deadline=started+request_seconds
    ctx.log('공개 요청: ' + url, 'INFO')
    for attempt in range(REQUEST_ATTEMPTS):
        ctx.checkpoint()
        deadline=min(total_deadline,time.monotonic()+(attempt_seconds or request_seconds))
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 %s초 제한 초과' % request_seconds)
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            # Retry with a fresh connection after TLS/temporary network failures.
            opener = public_opener(ctx, deadline)
            with opener.open(request, timeout=min(SOCKET_SECONDS, remaining)) as response:
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise CrawlError('응답 크기 초과: ' + url)
                chunks, size = [], 0
                last_progress=time.monotonic()
                while True:
                    ctx.checkpoint()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('공개 응답 다운로드 시도 %s초 제한 초과' % (attempt_seconds or request_seconds))
                    block = response.read1(min(65536, limit + 1 - size))
                    if not block:
                        break
                    chunks.append(block)
                    size += len(block)
                    if time.monotonic()-last_progress>=5:
                        ctx.log('원본 다운로드 진행: %sKB / %.0f초 / %s' % (size//1024,time.monotonic()-started,url),'INFO')
                        last_progress=time.monotonic()
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
            if attempt == REQUEST_ATTEMPTS-1 or time.monotonic() >= total_deadline:
                raise CrawlError('통신 실패: %s / %s' % (url, exc)) from exc
        ctx.log('일시적인 연결/응답 실패, 새 연결로 재시도 %s/%s: %s' % (attempt+2, REQUEST_ATTEMPTS, url),'WARNING')
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CrawlError('요청 실패: ' + url)


def parse(raw):return BeautifulSoup(raw,'html.parser')
def text(node):return node.get_text(' ',strip=True) if node else ''

def field(doc,label):
    for dl in doc.select('.item_detail_list dl'):
        if text(dl.find('dt'))==label:return text(dl.find('dd'))
    return ''

def amount(value):
    try:
        value=re.sub(r'\s|원|₩|,','',str(value or ''))
        if not re.fullmatch(r'\d+(?:\.0+)?',value):return None
        n=Decimal(value)
        return int(n) if n>0 else None
    except (InvalidOperation,ValueError):return None

def regular_price(ctx,doc):
    try:
        node=doc.select_one('#frmView input[name="set_goods_fixedPrice"]')
        value=amount(node.get('value') if node else field(doc,'정가'))
        if value is None:ctx.log('공식 독립 정가 미표시/0/형식 불명확: 빈칸으로 계속','WARNING')
        return value
    except Exception as exc:ctx.log('정상가 확인 불가: '+str(exc),'WARNING');return None

def identity(doc,code):
    model=field(doc,'모델명').upper()
    brand=doc.select_one('#frmView input[name="brandCd"]')
    title=text(doc.select_one('.item_detail_tit h3'))
    if model!=code:raise CrawlError('공식 모델명 불일치: 요청 '+code+' / 실제 '+model)
    brand_name=BRANDS.get(brand.get('value')) if brand else None
    if not brand_name or '['+brand_name+']' not in title:raise CrawlError('공식 해피랜드/압소바 브랜드 ID와 상품명 불일치')
    name=re.sub(r'^\s*\['+re.escape(brand_name)+r'\]\s*','',title)
    name=re.sub(r'\s*'+re.escape(model)+r'\s*$','',name).strip()
    if not name:raise CrawlError('정리 후 공식 상품명 없음')
    return dict(product_name=name,**{'원본 상품명':title})

def option_groups(doc):
    groups={}
    for node in doc.select('#frmView select[name="optionSnoInput"] option[value]'):
        value=node['value']
        if not value:continue
        label=str(node.get('alt') or text(node)).strip()
        parts=label.rsplit(':',1)
        if len(parts)!=2 or not all(p.strip() for p in parts):raise CrawlError('공식 색상:사이즈 옵션 구조 변경: '+label)
        color,size=(p.strip() for p in parts)
        data=value.split('^|^',1)[0].split('||')
        if len(data)<2:raise CrawlError('공식 옵션 가격 구조 확인 실패')
        try:delta=int(Decimal(data[1]))
        except (InvalidOperation,ValueError):raise CrawlError('공식 옵션 추가금 형식 오류')
        group=groups.setdefault(color,dict(sizes=[],deltas=set()))
        if size not in group['sizes']:group['sizes'].append(size)
        group['deltas'].add(delta)
    if not groups:raise CrawlError('공식 색상·사이즈 옵션 없음')
    return groups

def search(ctx,code):
    queue=[SITE+'/goods/goods_search.php?'+urlencode(dict(keyword=code))];seen=set();products={}
    while queue:
        link=queue.pop(0)
        if link in seen:continue
        if len(seen)>=50:raise CrawlError('공식 검색50페이지 한도 초과')
        seen.add(link);doc=parse(fetch(ctx,link,MAX_BYTES))
        # Empty/malformed/security pages must not masquerade as zero products.
        if not doc.select_one('#frmSearchTop,.goods_list_cont'):raise CrawlError('공식 검색 페이지 구조 확인 실패')
        for a in doc.select('.goods_list_cont a[href]'):
            url=urljoin(link,a['href']);p=urlsplit(url);number=parse_qs(p.query).get('goodsNo',[''])[0]
            if p.hostname in HOSTS and p.path=='/goods/goods_view.php' and number.isdigit():products.setdefault(number,url)
        for a in doc.select('.pagination a[href]'):
            url=urljoin(link,a['href']);p=urlsplit(url);params=parse_qs(p.query)
            if p.hostname in HOSTS and p.path=='/goods/goods_search.php' and params.get('keyword')==[code] and url not in seen and url not in queue:queue.append(url)
    return list(products.values())

def source_url(value,base=SITE):
    url=urljoin(base,value);p=urlsplit(url)
    allowed=(p.hostname=='godomall-storage.cdn-nhncommerce.com' and p.path.startswith(STORE_PREFIX)
             or p.hostname in ('img.efe.co.kr',DETAIL_CDN) and p.path.startswith(('/a/','/h/')))
    if p.scheme not in ('http','https') or not allowed or p.username or p.password:raise CrawlError('공식 압소바 이미지 출처 확인 실패: '+url)
    return url

def image(ctx,url,cache):
    source_url(url)
    if url not in cache:
        # Image transfers may stall temporarily. Keep document requests bounded
        # at45s, but give images a90s attempt and180s shared retry budget.
        raw=fetch(ctx,url,MAX_BYTES,request_seconds=180,attempt_seconds=90)
        try:
            with Image.open(io.BytesIO(raw)) as pic:
                if pic.width*pic.height>80000000:raise CrawlError('이미지 최대 픽셀 초과')
                pic.load();cache[url]=dict(raw=raw,width=pic.width,height=pic.height,digest=hashlib.sha256(raw).hexdigest())
        except (OSError,UnidentifiedImageError) as exc:raise CrawlError('공식 원본 이미지 해독 실패: '+url) from exc
    return cache[url]

def gallery_urls(doc,link):
    by_kind=dict(magnify=[],detail=[])
    for script in doc.select('script:not([src])'):
        for kind,quoted in re.findall(r'(magnify|detail)KeyID\[\d+\]\s*=\s*("(?:[^"\\]|\\.)*")',script.get_text()):
            try:fragment=parse(json.loads(quoted))
            except (ValueError,TypeError):continue
            for node in fragment.select('img[src]'):
                url=source_url(node['src'],link)
                if url not in by_kind[kind]:by_kind[kind].append(url)
    if by_kind['magnify']:return by_kind['magnify']
    if by_kind['detail']:return by_kind['detail']
    urls=[]
    nodes=doc.select('#testZoom img,#magnifyImage img') or doc.select('#mainImage img')
    for node in nodes:
        if not node.get('src'):continue
        url=source_url(node['src'],link)
        if url not in urls:urls.append(url)
    return urls

def gallery(ctx,doc,link,cache,mapped):
    photos=[];seen=set()
    for url in gallery_urls(doc,link):
        info=image(ctx,url,cache)
        if mapped:
            valid=validate_gallery_image(info['raw'],role='product_shot',mapping_verified=True,single_shot_verified=True,mapping_evidence='공식 모델명/브랜드 검증 주상품 갤러리 및 단일색상 옵션',content_evidence='공식 확대보기 magnifyKeyID 또는 mainImage 개별 상품컷, 본문/배너/추천 제외')
        else:
            # User confirmed these official shots show every color together;
            # validate the source file without claiming a single-color photo.
            with Image.open(io.BytesIO(info['raw'])) as pic:
                passed=pic.format in ('JPEG','PNG','WEBP') and not getattr(pic,'is_animated',False) and min(pic.size)>=500 and max(pic.size)/min(pic.size)<=1.8 and len(info['raw'])<=10000000
            valid=dict(passed=passed,reasons=[] if passed else ['공통 색상 이미지 파일/규격 불일치'])
        if valid['passed'] and info['digest'] not in seen:seen.add(info['digest']);photos.append(dict(url=url,validation=valid))
        elif not valid['passed']:ctx.log('갤러리 규격 제외: '+url+' / '+','.join(valid['reasons']),'WARNING')
    if not photos:raise CrawlError('검증된 공식 개별 상품컷 없음')
    return photos

def detail(ctx,doc,link,cache):
    area=doc.select_one('#detail .detail_explain_box')
    if area is None:raise CrawlError('공식 상세 본문 영역 없음')
    urls=[]
    for node in area.select('img'):
        src=node.get('data-src') or node.get('src')
        if not src:continue
        parsed=urlsplit(urljoin(link,src))
        if parsed.hostname==DETAIL_CDN and parsed.path=='/notice/notice.jpg':
            ctx.log('공통 빈 공지 자리표시자 제외: '+src,'INFO');continue
        url=source_url(src,link)
        if re.search(r'delivery|shipping|coupon|배송|교환|반품',url,re.I):continue
        if url not in urls:urls.append(url)
    if not urls:raise CrawlError('공식 상세 본문 이미지 없음')
    parts=[]
    for url in urls:
        info=image(ctx,url,cache)
        if min(info['width'],info['height'])<=1:ctx.log('1픽셀 빈 상세 제외: '+url,'WARNING');continue
        parts.append('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="max-width:100%;height:auto"><br>')
    if not parts:raise CrawlError('유효 상세 본문 이미지 없음')
    result='<div>'+''.join(parts)+'</div>'
    if len(result.encode('utf-16-le'))//2>32767:raise CrawlError('상세 HTML 셀 한도 초과')
    return result

def crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not code:raise CrawlError('품번이 비어 있습니다')
    rows=0;errors=[]
    try:links=search(ctx,code)
    except Exception as exc:ctx.fail_item(str(exc));raise
    for link in links:
        try:
            doc=parse(fetch(ctx,link,MAX_BYTES))
            if field(doc,'모델명').upper()!=code:continue
            record=identity(doc,code);groups=option_groups(doc)
            record.update(product_code=code,product_url=link)
            if ctx.enabled('category'):record['category']=' > '.join(text(n) for n in doc.select('.location_cont .location_tit'))
            if ctx.enabled('regular_price'):record['regular_price']=regular_price(ctx,doc)
            if ctx.enabled('price'):
                # Visible primary selling price; never coupon/member totals.
                base=amount(text(doc.select_one('.item_detail_tit .prd-price'))) or amount(field(doc,'판매가'))
                deltas=set(v for group in groups.values() for v in group['deltas'])
                record['price']=base if deltas=={0} else None
                if record['price'] is None:ctx.log('옵션별 추가금/공통 판매가 미확정: 빈칸','WARNING')
            cache={};photos=[]
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):photos=gallery(ctx,doc,link,cache,len(groups)==1)
            if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,link,cache)
            if photos:
                main,extras,warning=gallery_slots(photos)
                if warning:ctx.log(warning,'WARNING')
                if ctx.enabled('main_image'):record['main_image']=main
                if ctx.enabled('additional_images'):record['additional_images']=','.join(extras)
                if len(groups)>1:ctx.log('사용자 확인에 따라 전색상 공통 상품컷을 모든 공식 색상에 동일 사용','INFO')
            for color,group in groups.items():
                row=dict(record,color=color,color_name=color,color_code='')
                if ctx.enabled('size'):row['size']=','.join(group['sizes'])
                ctx.emit_result(row);rows+=1
        except Exception as exc:errors.append(str(exc));ctx.fail_item(str(exc))
    if not rows and not errors:errors.append('공식 해피랜드/압소바 일치 모델 검색 결과 없음: '+code);ctx.fail_item(errors[-1])
    if errors:raise CrawlError('수집 미완료: '+' / '.join(errors))
