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
from urllib.parse import quote, urlencode, urljoin, urlsplit
from bs4 import BeautifulSoup
from PIL import Image, ImageStat

SITE = 'https://m.boribori.co.kr'
CDN = 'https://cdn2.boribori.co.kr/'
MAX_BYTES = 10_000_000
class CollectionError(RuntimeError):
    """Expected transport, source-format or collection failure."""


class MissingResource(CollectionError):
    """Permanent HTTP 404; site itself removes these gallery slots on error."""


class AccessDenied(CollectionError):
    """Official public source returned HTTP 403."""


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
                connection.settimeout(min(15, max(.01, deadline-time.monotonic())))
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
        raise CollectionError('공개 HTTP/HTTPS 주소가 아님: '+url)
    url = image_url(url)
    deadline = time.monotonic() + 60
    started = time.monotonic()
    ctx.log('공개 요청: ' + url, 'INFO')
    opener = public_opener(ctx, deadline)
    for attempt in range(3):
        ctx.checkpoint()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 60초 제한 초과')
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with opener.open(request, timeout=min(15, remaining)) as response:
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise CollectionError('응답 크기 초과: ' + url)
                chunks, size = [], 0
                while True:
                    ctx.checkpoint()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('공개 응답 전체 60초 제한 초과')
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
            if exc.code == 403:
                raise AccessDenied('HTTP 403: ' + url) from exc
            if exc.code == 404:
                raise MissingResource('HTTP 404: ' + url) from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise CollectionError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt == 2:
                raise CollectionError('통신 실패: %s / %s' % (url, exc)) from exc
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CollectionError('요청 실패: ' + url)


def studio_background(image):
    """Uniform perimeter color; no brand logo requirement or color guessing."""
    im=image.convert('RGB').resize((200,268))
    boxes=[(.04,.04,.14,.12),(.86,.04,.96,.12),(.04,.88,.14,.96),(.86,.88,.96,.96)]
    for y in (.20,.46,.72):boxes.extend([(.02,y,.10,y+.08),(.90,y,.98,y+.08)])
    patches=[]
    for x1,y1,x2,y2 in boxes:
        stat=ImageStat.Stat(im.crop((int(x1*im.width),int(y1*im.height),int(x2*im.width),int(y2*im.height))))
        patches.append((stat.mean,max(stat.stddev)))
    best=0
    for center,variance in patches:
        if variance>14:continue
        count=sum(spread<=14 and sum((a-b)**2 for a,b in zip(mean,center))<=35**2 for mean,spread in patches)
        best=max(best,count)
    # Large packshots may overlap the broad inset patches. Confirm the actual
    # outer border independently, requiring matching background on every side.
    border=[]
    for side in range(4):
        for pos in (.20,.46,.72):
            box=((pos,0,pos+.08,.02) if side==0 else
                 (pos,.98,pos+.08,1) if side==1 else
                 (0,pos,.02,pos+.08) if side==2 else (.98,pos,1,pos+.08))
            x1,y1,x2,y2=box
            stat=ImageStat.Stat(im.crop((int(x1*im.width),int(y1*im.height),int(x2*im.width),int(y2*im.height))))
            border.append((side,stat.mean,max(stat.stddev)))
    border_best=0
    border_ok=False
    for _,center,variance in border:
        if variance>14:continue
        matched=[side for side,mean,spread in border
                 if spread<=14 and sum((a-b)**2 for a,b in zip(mean,center))<=35**2]
        border_best=max(border_best,len(matched))
        if len(matched)>=10 and all(matched.count(side)>=2 for side in range(4)):
            border_ok=True
    foreground=ImageStat.Stat(im.crop((30,35,170,233)))
    # Pale knitwear has low contrast even though its perimeter is a clean studio backdrop.
    contrast=max(sum((a-b)**2 for a,b in zip(foreground.mean,mean)) for _,mean,spread in border if spread<=14) if any(spread<=14 for _,_,spread in border) else 0
    visible=max(foreground.stddev)>=6 or (max(foreground.stddev)>=1.5 and contrast>=25)
    accepted=(best>=7 or border_ok) and visible
    return accepted,'배경 패치 %s/10, 외곽 테두리 %s/12(4방향 확인)'%(best,border_best)



def image_url(url):
    return quote(url,safe=":/?#[]@!$&'()*+,;=%")


def decode_nuxt(soup):
    tag=soup.find('script',id='__NUXT_DATA__')
    if not tag:raise CollectionError('상품 공개 데이터 없음: 사이트 응답을 확인하세요.')
    values=json.loads(tag.string or tag.get_text());cache={}
    def decode(index):
        if index<0:return None
        if index in cache:return cache[index]
        value=values[index]
        if isinstance(value,dict):
            result={};cache[index]=result
            result.update({k:decode(v) for k,v in value.items()});return result
        if isinstance(value,list):
            if value and isinstance(value[0],str):
                return decode(value[1]) if len(value)>1 else None
            result=[];cache[index]=result;result.extend(decode(v) for v in value);return result
        return value
    return decode(0)


def product(ctx,number):
    link=SITE+'/product/'+str(number)
    root=decode_nuxt(BeautifulSoup(fetch(ctx,link,8_000_000).decode('utf-8-sig'),'html.parser'))
    meta=root.get('pinia',{}).get('meta',{})
    data=meta.get('prdDetailData',{}).get('data')
    if not isinstance(data,dict) or str(data.get('prdNo'))!=str(number):raise CollectionError('상품 상세 데이터 확인 실패: '+link)
    prices=meta.get('prdPriceDetailData') or {}
    prices=prices.get('data',prices)
    return data,prices,link


def search(ctx,code):
    found=[];seen=set()
    for offset in range(0,4000,100):
        ctx.stage('보리보리 품번 검색 '+str(offset//100+1))
        url='https://apix.boribori.co.kr/searches/prdList/?'+urlencode(dict(siteCd=2,keyword=code,device='mc',limit=str(offset)+',100',sortSeq=12))
        response=json.loads(fetch(ctx,url,8_000_000).decode('utf-8-sig'))
        result=(response.get('data') or {}).get('result') or {}
        if result.get('timed_out') or result.get('_shards',{}).get('failed',0):raise CollectionError('검색 응답 불완전: 다시 조회해주세요.')
        hits=result.get('hits')
        if not isinstance(hits,dict) or not isinstance(hits.get('hits'),list):raise CollectionError('검색 응답 형식 확인 실패')
        total=hits.get('total',{});total=total.get('value') if isinstance(total,dict) else total
        if not isinstance(total,int):raise CollectionError('검색 건수 확인 실패')
        rows=hits['hits']
        for row in rows:
            source=row.get('_source') or {};number=str(source.get('prdNo') or row.get('_id') or '')
            if number.isdigit() and number not in seen:seen.add(number);found.append(number)
        if offset+len(rows)>=total:return found,total
        if not rows:raise CollectionError('검색 페이지 누락')
    raise CollectionError('검색 결과가 너무 많습니다. 정확한 품번을 입력하세요.')


def palette(data):
    colors=[];sizes=[];raw=[]
    for option in data.get('optionItem') or []:
        name=option.get('optItemNm','')
        entries=list(dict.fromkeys(str(v.get('optValueNm') or '').strip() for v in option.get('optValueList',[]) if v.get('optValueNm')))
        raw.extend(name+': '+value for value in entries)
        if '색상' in name or '컬러' in name:
            for value in entries:
                if any(key in name for key in ('치수','사이즈','SIZE')):
                    parts=re.split(r'[/／|]',value)
                    if len(parts)!=2 or not all(part.strip() for part in parts):
                        raise CollectionError('색상/사이즈 복합 옵션 형식 확인 필요: '+value)
                    value,size=(part.strip() for part in parts);sizes.append(size)
                match=re.fullmatch(r'\s*(.*?)\s*\(([A-Za-z0-9]+)\)\s*',value)
                color=dict(color_name=match[1].strip() if match else value,color_code=match[2] if match else '')
                if color not in colors:colors.append(color)
        elif any(key in name for key in ('치수','사이즈','SIZE')):sizes.extend(entries)
    if not colors:raise CollectionError('실제 색상 옵션을 확인할 수 없습니다.')
    return colors,list(dict.fromkeys(sizes)),raw


def excluded_image(url):
    path=urlsplit(url).path.lower()
    # These are the exact two supplied banners, plus shared seasonal branding.
    return ('/popup_img/' in path or '/image/header/' in path or '/image/footer/' in path or bool(re.search(r'/(?:avantop|avanbottom)\.',path)) or bool(re.search(r'/(?:notice|banner|delivery|shipping|return|exchange|info)[^/]*\.(?:jpe?g|png|gif)',path))
            or bool(re.search(r'(?:_title|_notice|_banner)\.(?:jpe?g|png|gif)$',path)))


def body_images(data):
    markup=(data.get('productDesc') or {}).get('prdDescContClob') or ''
    soup=BeautifulSoup(markup,'html.parser');urls=[]
    for image in soup.find_all('img'):
        src=image.get('data-src') or image.get('src') or ''
        url=urljoin(SITE,src)
        if not src or urlsplit(url).scheme not in ('http','https') or excluded_image(url):continue
        if url.startswith('http://cmcrftp.godohosting.com/'):url='https://'+url[7:]
        if url not in urls:urls.append(url)
    if not urls:raise CollectionError('배송/공통 배너를 제외한 상세 본문 이미지 없음')
    return urls


def checked_photo(ctx,url):
    try:
        payload=fetch(ctx,url,MAX_BYTES)
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if min(image.size)<100:return False
            accepted,reason=studio_background(image)
    except (ValueError,OSError) as exc:raise CollectionError('상품 이미지 확인 실패: '+url) from exc
    ctx.log(('스튜디오컷 통과: ' if accepted else '스튜디오컷 제외: ')+reason+' / '+url,'INFO')
    return accepted


def _crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    numbers,total=search(ctx,code)
    if not numbers:raise CollectionError('보리보리 검색 결과 없음: '+code)
    products=[];other=0
    for number in numbers:
        ctx.checkpoint();data,price,link=product(ctx,number)
        if '페리미츠' not in str(data.get('brandMainNmKr','')):continue
        model=str(data.get('prdCd') or '').strip().upper()
        title=str(data.get('prdNm') or '')
        if model!=code and not re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',title.upper()):continue
        gallery=[];mixed=False
        for field,value in (data.get('productImage') or {}).items():
            if re.fullmatch(r'(?:basic|add\d+)ExtNm',field) and value:
                url=urljoin(CDN,str(value))
                if url in gallery:continue
                if checked_photo(ctx,url):gallery.append(url)
                else:mixed=True
        if mixed or not gallery:
            ctx.log('셀러컷이 섞인 상품 링크 전체 제외: '+link,'INFO');other+=1;continue
        colors,sizes,raw=palette(data)
        products.append((data,price,link,gallery,colors,sizes,raw))
    if not products:
        raise CollectionError(('검색 결과는 있으나 스튜디오컷 통과 사진 없음: ' if other else '검색 결과는 있으나 페리미츠 동일 품번 상품 없음: ')+code)
    colors=[];photos=[];sizes=[]
    for data,price,link,gallery,options,actual_sizes,raw in products:
        for color in options:
            if color not in colors:colors.append(color)
        sizes.extend(actual_sizes)
        for photo in gallery:
            if photo not in photos:photos.append(photo)
    # Prefer the brand's original product body over reseller wrappers.
    products.sort(key=lambda p:not ('cmcrftp.godohosting.com/perimitz/' in str((p[0].get('productDesc') or {}).get('prdDescContClob',''))))
    data,prices,link,_,_,_,raw=products[0]
    name=re.sub(r'\([^)]*'+re.escape(code)+r'[^)]*\)|'+re.escape(code),'',data['prdNm'],flags=re.I)
    name=re.sub(r'^\s*\[?페리미츠\]?\s*','',name).strip(' _-')
    record=dict(product_code=code,product_name=name,product_url=link)
    record['원본 상품명']=data['prdNm'];record['원본 옵션']='\n'.join(raw)
    if ctx.enabled('size'):record['size']=','.join(dict.fromkeys(sizes))
    # Public selling price; do not apply coupons or infer the normal price.
    if ctx.enabled('price'):record['price']=prices.get('selPrc') or None
    if ctx.enabled('regular_price'):record['regular_price']=prices.get('normPrc') or None
    if ctx.enabled('category'):record['category']=(data.get('stdCtgr') or {}).get('stdCtgrNm3') or None
    if ctx.enabled('detail_page'):
        urls=body_images(data)
        record['detail_page']=''.join('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세">' for url in urls)
        ctx.log('상세 본문 한 링크 사용 / 배송·교환·시즌 배너 제외 / '+str(len(urls))+'장','INFO')
    for photo in photos:ctx.emit_unmapped(dict(record),colors,photo,shared_detail_html=record.get('detail_page',''))
    ctx.log(code+': '+str(len(colors))+'색상 / '+str(len(photos))+'장 수동 매핑 후보','SUCCESS')


def crawl(ctx):
    try:return _crawl(ctx)
    except CollectionError as exc:
        ctx.fail_item(str(exc))
        raise
