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
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs, quote, unquote
from bs4 import BeautifulSoup, Comment
from PIL import Image, UnidentifiedImageError
from crawler_rules import validate_gallery_image, gallery_slots

SITE='https://www.looxloo.com'
HOSTS=('www.looxloo.com','looxloo.com')
MAX_BYTES=30_000_000
REQUEST_SECONDS=60
SOCKET_SECONDS=15
REQUEST_ATTEMPTS=3
class CrawlError(RuntimeError):pass
class MissingResource(CrawlError):pass

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


def text(node):return node.get_text(' ',strip=True) if node else ''
def url(value):return quote(urljoin(SITE,str(value).strip()),safe=":/?#[]@!$&'()*+,;=%")
def page(ctx,link):
    doc=BeautifulSoup(fetch(ctx,url(link),12_000_000),'html.parser')
    message=text(doc)
    if 'For a secure experience, please check your access' in message or doc.select_one('.g-recaptcha, #challenge-running'):
        raise CrawlError('공홈 접속 확인 화면: 상품 없음으로 처리하지 않음. 잠시 후 다시 실행하세요.')
    return doc
def normalize_input(value):
    compact=re.sub(r'[-\s]','',str(value)).upper()
    if not re.fullmatch(r'[A-Z0-9]{5}\d{5}',compact):raise CrawlError('품번 형식 확인: 86C1916070 또는 86C19-160-70')
    return compact[:5]+'-'+compact[5:8]+'-'+compact[8:]
def field(doc,label):
    for row in doc.select('.infoArea .xans-product-detaildesign tr'):
        if row.th and row.td and text(row.th)==label:return text(row.td)
    return ''
def identity(doc,code):
    title=text(doc.select_one('.infoArea .prd_name h1')) or field(doc,'상품명')
    brand=field(doc,'브랜드') or text(doc.select_one('.infoArea .prd_brand .t01'))
    if not re.fullmatch(r'laughing\s*child|래핑\s*차일드',brand,re.I):return None
    codes=re.findall(r'(?<![A-Z0-9])([A-Z0-9]{5}-\d{3}-\d{2})(?![A-Z0-9])',title.upper())
    if code not in codes:
        family=code.rsplit('-',1)[0]
        compact=code.replace('-','')
        references=[node.get('data-origin-src','')+' '+node.get('alt','')
                    for node in doc.select('#prdDetail img')]
        if not any(value.startswith(family+'-') for value in codes) or not any(
                re.search(r'(?<![A-Z0-9])'+re.escape(compact)+r'(?![A-Z0-9])',value.upper())
                for value in references):return None
    name=title
    for source_code in codes:name=re.sub(re.escape(source_code),'',name,flags=re.I)
    name=re.sub(r'laughing\s*child|래핑\s*차일드','',name,flags=re.I)
    name=re.sub(r'\(\s*\)|\[\s*\]','',name).strip(' _-')
    if not name:raise CrawlError('상품명 없음')
    return name,title

def search_query(ctx,code):
    found={};seen=set()
    for number in range(1,101):
        ctx.checkpoint();ctx.stage('래핑차일드 공식 검색 %s페이지'%number)
        doc=page(ctx,SITE+'/product/search.html?'+urlencode(dict(keyword=code,page=number)))
        counter=doc.select_one('.prdCount')
        if counter is None:
            scripts='\n'.join(node.get_text() for node in doc.select('script:not([src])'))
            if re.search(r"alert\(\s*['\"]검색결과가 없습니다\.['\"]\s*\)",scripts):return []
            # Unknown/interrupted response is never evidence of no products.
            doc=page(ctx,SITE+'/product/search.html?'+urlencode(dict(keyword=code,page=number)))
            counter=doc.select_one('.prdCount')
            if counter is None:raise CrawlError('공식 검색 응답 확인 실패(상품 없음으로 단정하지 않음)')
        count=re.search(r'[\d,]+',text(counter))
        if not count:raise CrawlError('공식 검색 건수 형식 변경')
        total=int(count[0].replace(',',''))
        current=set()
        for card in doc.select('.prdList li[id^="anchorBoxId_"]'):
            a=card.select_one('.name a[href]')
            if a is None:continue
            link=url(a['href']);parts=urlsplit(link)
            m=re.search(r'/product/(?:[^/]+/)?(\d+)(?:/|$)',unquote(parts.path))
            no=m[1] if m else parse_qs(parts.query).get('product_no',[''])[0]
            if parts.hostname not in HOSTS or not no.isdigit():continue
            current.add(no);found[no]=SITE+'/product/detail.html?'+urlencode(dict(product_no=no))
        if total==0:return []
        if not current or current.issubset(seen):raise CrawlError('공식 검색 페이지 누락/반복')
        seen.update(current)
        if len(seen)>=total:return list(found.values())
    raise CrawlError('공식 검색 페이지 한도 초과')

def search(ctx,code):
    links=search_query(ctx,code)
    if not links:
        family=code.rsplit('-',1)[0]
        ctx.log('정확 품번 검색 결과0건: 동일 스타일 검색 후 원본 이미지 품번 재검증 / '+family,'INFO')
        links=search_query(ctx,family)
    return links


def option_labels(doc,label):
    values=[]
    for group in doc.select('.infoArea [option_title]'):
        if group.get('option_title')!=label:continue
        for node in group.select('li[option_value], option[value]'):
            value=node.get('option_value',node.get('value',''))
            if value in ('','*','**'):continue
            name=re.sub(r'\s*\[품절\]\s*$','',node.get('option_value') or text(node)).strip()
            if name and name not in values:values.append(name)
    return values

def color_parts(value):
    value=str(value).strip()
    match=(re.fullmatch(r'\(([A-Za-z]{2})\)\s*(.+)',value) or
           re.fullmatch(r'([A-Za-z]{2})[\s:/_-]+(.+)',value))
    if match:return dict(color_name=match[2].strip(),color_code=match[1].upper())
    match=re.fullmatch(r'(.+?)\s*[\[(]([A-Za-z]{2})[\])]',value)
    if match:return dict(color_name=match[1].strip(),color_code=match[2].upper())
    return dict(color_name=value,color_code=value.upper() if re.fullmatch(r'[A-Za-z]{2}',value) else '')


def color_palette(doc,values):
    result=[]
    for value in values:
        part=color_parts(value)
        # Some Cafe24 buttons store BR while displaying BROWN.
        for group in doc.select('.infoArea [option_title="색상"]'):
            for node in group.select('li[option_value],option[value]'):
                raw=node.get('option_value',node.get('value',''))
                if raw==value and re.fullmatch(r'[A-Za-z]{2}',raw) and text(node) and text(node)!=raw:
                    part=dict(color_name=text(node),color_code=raw.upper())
        if part not in result:result.append(part)
    return result


def money(value):
    m=re.match(r'^\s*([\d,]+)(?:\s|원|$)',str(value or ''))
    return int(m[1].replace(',','')) if m and int(m[1].replace(',',''))>0 else None

def image(ctx,link,cache):
    if link in cache:return cache[link]
    raw=fetch(ctx,link,MAX_BYTES)
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.width*im.height>80_000_000:raise CrawlError('이미지 픽셀 한도 초과')
            im.load();result=dict(format=im.format,width=im.width,height=im.height,bytes=len(raw),frames=getattr(im,'n_frames',1),digest=hashlib.sha256(raw).hexdigest())
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:raise CrawlError('이미지 해독 실패: '+link) from exc
    cache[link]=result;return result

def image_source(node):return url(node.get('data-origin-src') or node.get('ec-data-src') or node.get('data-src') or node.get('src') or '')

def gallery(ctx,doc,cache):
    result=[];digests=set()
    for node in doc.select('.detailArea > .xans-product-image img'):
        ctx.checkpoint();link=image_source(node)
        if link==SITE or link in result:continue
        try:info=image(ctx,link,cache)
        except MissingResource:
            ctx.log('갤러리 원본404 제외: '+link,'WARNING');continue
        if info['format'] not in ('JPEG','PNG','WEBP') or info['frames']!=1 or info['bytes']>10_000_000 or min(info['width'],info['height'])<500 or max(info['width'],info['height'])/min(info['width'],info['height'])>1.8:
            ctx.log('갤러리 개별 사진 기준 제외: '+link,'WARNING');continue
        if info['digest'] not in digests:result.append(link);digests.add(info['digest'])
    if not result:raise CrawlError('유효 상품사진 없음')
    return result

def detail_photo_candidates(ctx,doc,code,cache,existing):
    """Original SKU-labelled packshots only, not editorial banners or tables."""
    family=code.replace('-','')[:8]
    result=list(existing)
    seen_urls=set(result)
    seen_digests={cache[u]['digest'] for u in result if u in cache}
    for node in doc.select('#prdDetail > .cont img'):
        ctx.checkpoint()
        if node.find_parent('table'):continue
        link=image_source(node)
        source_path=unquote(urlsplit(link).path)
        evidence=source_path+' '+str(node.get('alt') or '')
        if not re.search(r'(?<![A-Z0-9])'+re.escape(family)+r'[A-Z0-9]{2}(?![A-Z0-9])',evidence.upper()):continue
        if re.search(r'notice|banner|size|guide|chart|gosi|shipping|coupon|고시|사이즈|배송',evidence,re.I):continue
        if link in seen_urls:continue
        try:info=image(ctx,link,cache)
        except MissingResource:
            ctx.log('상세 상품컷404 후보 제외: '+link,'WARNING');continue
        if info['format'] not in ('JPEG','PNG','WEBP') or info['frames']!=1 or info['bytes']>10_000_000 or min(info['width'],info['height'])<500 or max(info['width'],info['height'])/min(info['width'],info['height'])>1.8:
            ctx.log('상세 표/통짜·저해상도 이미지 후보 제외: '+link,'INFO');continue
        seen_urls.add(link)
        if info['digest'] in seen_digests:continue
        seen_digests.add(info['digest']);result.append(link)
    ctx.log('상세 상품컷 수동 후보 %s장 추가(갤러리 포함 전체%s장)'%(len(result)-len(existing),len(result)),'INFO')
    return result


def size_grid(doc):
    labels=doc.select('.grid-fixed > .cell');rows=doc.select('.grid-scroll > .row')
    if not labels or len(labels)!=len(rows):raise CrawlError('공통 사이즈 정보 행 구조 변경')
    widths=[len(row.select(':scope > .cell')) for row in rows[1:]]
    if not widths or len(set(widths))!=1:raise CrawlError('공통 사이즈 정보 열 구조 변경')
    output='<h2>사이즈 정보 (브랜드 공통)</h2><table><tbody>'
    output+='<tr><th>'+html.escape(text(labels[0]))+'</th><th colspan="'+str(widths[0])+'">'+html.escape(text(rows[0]))+'</th></tr>'
    for label,row in zip(labels[1:],rows[1:]):
        output+='<tr><th>'+html.escape(text(label))+'</th>'+''.join('<td>'+html.escape(text(cell))+'</td>' for cell in row.select(':scope > .cell'))+'</tr>'
    return output+'</tbody></table>'

def detail(ctx,doc,cache):
    source=doc.select_one('#prdDetail > .cont')
    if source is None:raise CrawlError('공식 상세 본문 없음')
    tables=source.select('table')
    notice=next((table for table in tables if '제조' in text(table) and '소재' in text(table)),None)
    size_tables=[table for table in tables if table is not notice and any(word in text(table) for word in ('신장','가슴','총장'))]
    if notice is None:raise CrawlError('공식 상품고시 표 확인 실패')
    if not size_tables and not source.select_one('.m_pro_size'):raise CrawlError('공식 사이즈 표 확인 실패')
    def clean_table(table):
        cleaned=BeautifulSoup(str(table),'html.parser')
        for node in list(cleaned.select('img,script,style,svg')):node.decompose()
        for node in list(cleaned.find_all(True)):
            if node.name in ('table','caption','thead','tbody','tfoot','tr','th','td','br'):
                node.attrs={k:str(node[k]) for k in ('colspan','rowspan') if str(node.get(k,'')).isdigit()}
            else:node.unwrap()
        return str(cleaned)
    pairs={text(row.th):text(row.td) for row in notice.select('tr') if row.th and row.td}
    heading=source.select_one('h1,h2')
    output='<h2>'+html.escape(text(heading) or '상품 상세')+'</h2>'
    info=[('색상',pairs.get('색상','')),('소재',pairs.get('소재',''))]
    output+='<table><tbody>'+''.join('<tr><th>'+html.escape(k)+'</th><td>'+html.escape(v)+'</td></tr>' for k,v in info if v)+'</tbody></table>'
    # Keep the real product introduction, not the legal/AI notices or TIP cards.
    if heading is not None:
        intro=heading.find_parent('section')
        if intro is not None:
            for paragraph in intro.select('p'):
                value=text(paragraph)
                if value:output+='<p>'+html.escape(value)+'</p>'
    count=0;seen=set()
    for node in source.select('img'):
        original=node.get('data-origin-src') or node.get('ec-data-src') or node.get('src') or ''
        path=unquote(urlsplit(url(original)).path)
        if node.find_parent('table') or re.search(r'/contents/notice/|/WEBVISUAL/|kc_mark|/morenvyimg/|shipping|delivery|banner|coupon|배송|반품|교환',path,re.I):continue
        link=image_source(node)
        if link in seen:continue
        try:info=image(ctx,link,cache)
        except MissingResource as exc:raise CrawlError('상세 원본 이미지404: '+link) from exc
        if min(info['width'],info['height'])<=1:continue
        output+='<img src="'+html.escape(link,quote=True)+'" alt="상품 상세">';count+=1;seen.add(link)
        container=node.find_parent(class_=re.compile(r'^syn_.*prdImageDe$'))
        if container is not None:
            for paragraph in container.select('p'):
                value=text(paragraph)
                if value:output+='<p>'+html.escape(value)+'</p>' 
    if not count:raise CrawlError('상세 상품 이미지 없음')
    for table in size_tables:output+='<h2>권장 사이즈 가이드</h2>'+clean_table(table)
    output+='<h2>상품정보제공고시</h2>'+clean_table(notice)
    for grid in source.select('.m_pro_size'):output+=size_grid(grid)
    # Marketing copy/AI disclaimer/purchase tips and detached COLOR/FABRIC spans
    # are intentionally omitted. Source measurements and notice text stay intact.
    return output


def crawl_impl(ctx):
    code=normalize_input(ctx.product_code);links=search(ctx,code);matched=[];cache={}
    for link in links:
        ctx.checkpoint();doc=page(ctx,link);found=identity(doc,code)
        if found is not None:matched.append((link,doc,found))
    if not matched:
        message='공식 검색 결과 없음: '+code if not links else '검색 결과의 브랜드/품번 불일치: '+code
        ctx.fail_item(message);raise CrawlError(message)
    if len(matched)>1:raise CrawlError('한 품번에 복수 상품 ID가 조회되어 확인 필요: '+code)
    link,doc,(name,raw_name)=matched[0]
    colors=option_labels(doc,'색상')
    if not colors:
        for table in doc.select('#prdDetail table'):
            for row in table.select('tr'):
                if row.th and row.td and text(row.th)=='색상':
                    colors=list(dict.fromkeys(v.strip() for v in re.split(r'[,/]',text(row.td)) if v.strip()))
        if colors:ctx.log('옵션 색상 표시 없음: 공식 고시의 색상명 사용','INFO')
    if not colors:raise CrawlError('공식 색상 옵션/고시 정보 없음')
    palette=color_palette(doc,colors)
    first=palette[0]
    record=dict(product_code=code,product_name=name,product_url=link,color=first['color_name'],color_name=first['color_name'],color_code=first['color_code'],원본상품명=raw_name)
    if ctx.enabled('size'):
        values=option_labels(doc,'사이즈')
        if not values:
            for table in doc.select('#prdDetail table'):
                for row in table.select('tr'):
                    if row.th and row.td and text(row.th)=='치수':
                        listed=[v.strip() for v in text(row.td).split(',') if v.strip()]
                        if listed and all(re.fullmatch(r'\d{2,3}|[2-4]?X?[SML]|FREE|ONE SIZE',v,re.I) for v in listed):values=listed
            if values:ctx.log('선택 옵션 미표시: 상품고시의 명시적 치수 목록 사용(권장 사이즈표에서 추측하지 않음)','WARNING')
        if not values:raise CrawlError('공식 옵션/상품고시 사이즈 없음')
        record['size']=','.join(values)
    if ctx.enabled('price'):
        record['price']=money(field(doc,'할인판매가')) or money(field(doc,'판매가'))
        if record['price'] is None:ctx.log('공식 판매가 파싱 실패: 빈칸','WARNING')
    if ctx.enabled('regular_price'):
        record['regular_price']=money(field(doc,'소비자가'))
        if record['regular_price'] is None:ctx.log('공식 소비자가 없음/0: 정상가는 빈칸','WARNING')
    if ctx.enabled('category'):
        nodes=doc.select('.infoArea .xans-product-headcategory li:not(.displaynone) a')
        record['category']=text(nodes[-1]) if nodes else None
    if ctx.enabled('main_image') or ctx.enabled('additional_images') or len(colors)>1:
        photos=gallery(ctx,doc,cache)
        if ctx.enabled('main_image'):record['main_image']=photos[0]
        if ctx.enabled('additional_images'):
            extra=photos[1:] or photos;record['additional_images']=','.join(extra[i%len(extra)] for i in range(5))
    if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,cache)
    if len(colors)==1:
        ctx.emit_result(record)
        ctx.log(code+' 단일 색상 '+colors[0]+' 자동 매핑 수집 완료','SUCCESS')
    else:
        photos=detail_photo_candidates(ctx,doc,code,cache,photos)
        record['color']=','.join(colors);record['color_name']='';record['color_code']=''
        ctx.log('공홈 복수 색상 옵션: 사진·색상 연결 미확정이므로 품번 전체 수동 매핑 후보로 수집','WARNING')
        for photo in photos:ctx.emit_unmapped(record,palette,photo,shared_detail_html=record.get('detail_page',''))
        ctx.log(code+' 복수 색상 수동 매핑 후보 수집 완료','SUCCESS')


def crawl(ctx):
    ctx.allow_automatic_results=True
    try:crawl_impl(ctx)
    except CrawlError as exc:
        ctx.fail_item(str(exc))
        raise
