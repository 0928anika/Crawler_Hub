PORTAL_API_VERSION = 1

import ast
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
from urllib.parse import urlencode,urljoin,urlsplit,parse_qs
from bs4 import BeautifulSoup
from PIL import Image,ImageChops,ImageStat,UnidentifiedImageError
from crawler_rules import validate_gallery_image,gallery_slots
SITE='https://awkmall.com'
HOSTS=('awkmall.com','www.awkmall.com')
MAX_BYTES=10000000
class CrawlError(RuntimeError):
    """Official source or collection error."""
class MappingFailure(CrawlError):
    """Valid product but color-to-photo assignment is unconfirmed."""
class MissingResource(CrawlError):
    """Source permanently removed."""

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
    if urlsplit(url).scheme not in ('http','https'):
        raise CrawlError('공개 HTTP/HTTPS 주소가 아님: '+url)
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
                    raise CrawlError('응답 크기 초과: ' + url)
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
                        raise CrawlError('응답 크기 초과: ' + url)
            ctx.checkpoint()
            ctx.log('공개 요청 완료 %.1f초: %s' % (time.monotonic()-started, url), 'INFO')
            return b''.join(chunks)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise MissingResource('HTTP 404: ' + url) from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise CrawlError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt:
                raise CrawlError('통신 실패: %s / %s' % (url, exc)) from exc
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CrawlError('요청 실패: ' + url)




def parse(raw):
    return BeautifulSoup(raw,'html.parser')


def field(doc,label):
    for tr in doc.select('.infoArea tr'):
        th,td=tr.find('th'),tr.find('td')
        if th and td and th.get_text(' ',strip=True)==label:return td.get_text(' ',strip=True)
    return ''


def search(ctx,code):
    queue=[SITE+'/product/search.html?'+urlencode({'keyword':code})];seen=set();links=[];numbers=set()
    while queue:
        ctx.checkpoint();url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise CrawlError('공식 검색30페이지 한도 초과')
        seen.add(url);doc=parse(fetch(ctx,url))
        for node in doc.select('.prdList .name a[href]'):
            link=urljoin(url,node['href']);p=urlsplit(link);pid=parse_qs(p.query).get('product_no',[''])[0]
            if p.hostname in HOSTS and pid and pid not in numbers:numbers.add(pid);links.append(link)
        for node in doc.select('.xans-search-paging a[href]'):
            link=urljoin(url,node['href']);p=urlsplit(link);params=parse_qs(p.query)
            if p.hostname in HOSTS and p.path=='/product/search.html' and params.get('keyword')==[code] and link not in seen and link not in queue:queue.append(link)
    return links


def product_group(doc):
    name=field(doc,'상품명')
    for script in doc.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.string or script.get_text())
        except ValueError:continue
        for group in data if isinstance(data,list) else [data]:
            if isinstance(group,dict) and group.get('@type')=='ProductGroup' and group.get('name')==name:return group
    raise CrawlError('공식 ProductGroup 없음')


def stock_options(doc):
    for script in doc.find_all('script'):
        raw=script.string or script.get_text()
        match=re.search(r"\bvar\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")",raw)
        if match:
            try:
                data=json.loads(ast.literal_eval(match[1]))
                if isinstance(data,dict):return list(data.values())
            except (ValueError,SyntaxError,TypeError) as exc:raise CrawlError('공식 option_stock_data 파싱 실패') from exc
    return []


def choices(doc,group):
    result={}
    for row in stock_options(doc):
        names=row.get('option_name_original') or [];values=row.get('option_value_orginal') or []
        if len(names)!=len(values) or '색상' not in names:raise CrawlError('공식 색상 옵션 구조 변경')
        color=str(values[names.index('색상')]).strip()
        item=result.setdefault(color,{'color':color,'sizes':[],'rows':[],'images':[]})
        item['rows'].append(row)
        if '사이즈' in names:
            size=str(values[names.index('사이즈')]).strip()
            if size and size not in item['sizes']:item['sizes'].append(size)
    # Explicit option_value -> link_image is the original image paired with
    # the small colour button. Prefer this over shared JSON-LD default images.
    linked={}
    for node in doc.select('[option_title="색상"] li[option_value][link_image]'):
        color=str(node.get('option_value') or '').strip()
        source=str(node.get('link_image') or '').strip()
        if not source:
            thumb=node.find('img')
            source=str(thumb.get('src') or '') if thumb else ''
        if not source:continue
        url=urljoin(SITE,source)
        if urlsplit(url).hostname not in HOSTS or not re.match(r'/web/product/(?:big/|extra/big/|option_button/)',urlsplit(url).path):continue
        if color not in result:raise CrawlError('사진 옵션과 공식 색상 목록 불일치: '+color)
        linked.setdefault(color,set()).add(url)
    owners={}
    for variant in group.get('hasVariant',[]):
        color=str(variant.get('color') or '').strip()
        if not color:continue
        item=result.setdefault(color,{'color':color,'sizes':[],'rows':[],'images':[]})
        size=str(variant.get('size') or '').strip()
        if size and size not in item['sizes']:item['sizes'].append(size)
        urls=variant.get('image') or [];urls=[urls] if isinstance(urls,str) else urls
        for value in urls:
            if not isinstance(value,str):continue
            url=urljoin(SITE,value)
            if urlsplit(url).hostname not in HOSTS:continue
            owners.setdefault(url,set()).add(color)
            if url not in item['images']:item['images'].append(url)
    if not result or any(not c for c in result):raise CrawlError('공식 색상 후보 없음')
    # One-colour product: its own BigImage is independently linked to that
    # single option. Multiple colours require exclusive variant image URLs.
    if len(result)==1:
        item=next(iter(result.values()))
        for node in doc.select('.imgArea img'):
            url=urljoin(SITE,node.get('src') or '')
            if urlsplit(url).hostname in HOSTS and re.match(r'/web/product/(?:big/|extra/big/)',urlsplit(url).path) and url not in item['images']:item['images'].append(url)
    else:
        for item in result.values():item['images']=[u for u in item['images'] if owners.get(u)=={item['color']}]
    if linked:
        linked_owners={}
        for color,urls in linked.items():
            for url in urls:linked_owners.setdefault(url,set()).add(color)
        for item in result.values():
            if item['color'] in linked:
                item['images']=[u for u in linked[item['color']] if linked_owners[u]=={item['color']}]
    return list(result.values())


def picture(ctx,url,cache,gallery=False):
    if url not in cache:
        raw=fetch(ctx,url,MAX_BYTES)
        try:
            with Image.open(io.BytesIO(raw)) as im:
                if im.format not in ('JPEG','PNG','WEBP') or getattr(im,'is_animated',False) or im.width*im.height>60000000:raise CrawlError('이미지 형식/크기 오류: '+url)
                im.load();cache[url]={'raw':raw,'width':im.width,'height':im.height,'digest':hashlib.sha256(raw).hexdigest()}
        except (OSError,UnidentifiedImageError) as exc:raise CrawlError('이미지 해독 오류: '+url) from exc
    info=cache[url]
    if gallery:
        validation=validate_gallery_image(info['raw'],role='product_shot',mapping_verified=True,single_shot_verified=True,mapping_evidence='공식 자체상품코드 + 색상 버튼 option_value/link_image 또는 img.src + ProductGroup/단일색상 연결',content_evidence='공식 개별 상품 갤러리 이미지, 본문/사이즈/배너 아닌 역할')
        return dict(info,validation=validation)
    return info


def color_fields(label):
    match=re.fullmatch(r'(.*\D)(\d+)',label)
    return (match[1].strip(),match[2]) if match else (label,'')


def browser_gallery(ctx,link,cache):
    if '_browser_gallery' in cache:return cache['_browser_gallery']
    from playwright.sync_api import sync_playwright
    urls=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,channel='chrome')
        try:
            page=browser.new_page()
            page.route('**/*',lambda r:r.abort() if r.request.resource_type in ('image','media','font') else r.continue_())
            response=page.goto(link,wait_until='domcontentloaded',timeout=45000)
            if response is None or response.status>=400:raise CrawlError('원본 갤러리 화면 조회 실패')
            thumbs=page.locator('.imgArea .ThumbImage')
            for i in range(thumbs.count()):
                ctx.checkpoint();thumbs.nth(i).dispatch_event('mouseover');thumbs.nth(i).dispatch_event('mouseenter');thumbs.nth(i).dispatch_event('click')
                source=page.locator('.imgArea .BigImage').get_attribute('src') or ''
                url=urljoin(link,source)
                if urlsplit(url).hostname in HOSTS and re.match(r'/web/product/(?:big/|extra/big/)',urlsplit(url).path) and url not in urls:urls.append(url)
        finally:browser.close()
    cache['_browser_gallery']=urls
    return urls


def matching_original(ctx,thumb,link,cache):
    with Image.open(io.BytesIO(thumb['raw'])) as image:sample=image.convert('RGB').resize((64,64))
    matches=[]
    for url in browser_gallery(ctx,link,cache):
        info=picture(ctx,url,cache)
        if min(info['width'],info['height'])<500:continue
        with Image.open(io.BytesIO(info['raw'])) as image:other=image.convert('RGB').resize((64,64))
        score=sum(ImageStat.Stat(ImageChops.difference(sample,other)).mean)/3
        matches.append((score,url))
    matches.sort()
    if not matches or matches[0][0]>4 or (len(matches)>1 and matches[1][0]-matches[0][0]<6):
        raise MappingFailure('옵션 썸네일과 동일한 원본을 고유하게 확인하지 못함')
    ctx.log('옵션 사진 동일 원본 확인: 오차 '+str(round(matches[0][0],3))+' / '+matches[0][1],'INFO')
    return matches[0][1]


def gallery(ctx,item,cache,doc=None,link=None):
    accepted=[];seen=set()
    for url in item['images']:
        info=picture(ctx,url,cache,True)
        if min(info['width'],info['height'])<500 and '/option_button/' in url and link:
            url=matching_original(ctx,info,link,cache);info=picture(ctx,url,cache,True)
        if info['validation']['passed'] and info['digest'] not in seen:
            seen.add(info['digest']);accepted.append({'url':url,'validation':info['validation']})
        else:ctx.log('상품컷 제외: '+url,'WARNING')
    if not accepted:raise MappingFailure('색상별 사진 자동 연결 불가: '+item['color']+' / 공식 옵션들이 같은 사진을 공유하거나 독립 사진이 없음. 색상 미매핑 수집 필요')
    main,extras,warning=gallery_slots(accepted)
    if warning:ctx.log(warning,'WARNING')
    return main,','.join(extras)


def is_shipping(url):
    p=urlsplit(url)
    return p.hostname=='jc2016.cafe24.com' and p.path.lower().endswith('/notice_2021.jpg')


def detail(ctx,doc,link,cache):
    area=doc.select_one('#prdDetail .cont')
    if area is None:raise CrawlError('공식 상세 본문 영역 없음')
    parts=[];seen=set()
    for node in area.find_all('img'):
        url=urljoin(link,node.get('ec-data-src') or node.get('data-src') or node.get('src') or '')
        if not url or url in seen:continue
        if is_shipping(url):ctx.log('배송/교환/환불 공통 안내 이미지 제외: '+url,'INFO');continue
        if urlsplit(url).hostname not in HOSTS+('jc2016.cafe24.com',):raise CrawlError('미확인 상세 이미지 출처: '+url)
        info=picture(ctx,url,cache);seen.add(url)
        if info['height']<3:continue
        parts.append('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세정보" style="max-width:100%;height:auto"><br>')
    if not parts:raise CrawlError('배송 안내 제외 후 본문 이미지 없음')
    result='<div>'+''.join(parts)+'</div>'
    if len(result.encode('utf-16-le'))//2>32767:raise CrawlError('상세 HTML 엑셀 셀 한도 초과')
    # Existing image-based size/legal information stays; no new tables.
    return result


def amount(value):
    match=re.fullmatch(r'\s*(?:₩)?\s*([0-9,]+)\s*원?\s*',str(value or ''))
    if not match:return None
    number=int(match[1].replace(',',''));return number or None


def regular_price(ctx,doc):
    try:value=amount(field(doc,'소비자가'))
    except Exception as exc:ctx.log('정상가 파싱 불가: '+str(exc),'WARNING');return None
    if value is None:ctx.log('정상가 누락/0/형식 불명확: 빈칸, 나머지 수집 계속','WARNING')
    return value


def manual_candidates(ctx,products):
    photos=[];seen=set()
    for doc,link,options,cache in products:
        urls=[]
        for item in options:
            for url in item['images']:
                if url not in urls:urls.append(url)
        urls.extend(u for u in browser_gallery(ctx,link,cache) if u not in urls)
        for url in urls:
            try:info=picture(ctx,url,cache)
            except MissingResource:
                ctx.log('삭제된 수동 후보 사진 제외: '+url,'WARNING');continue
            if min(info['width'],info['height'])<500 or max(info['width'],info['height'])/min(info['width'],info['height'])>1.8:continue
            if info['digest'] in seen:continue
            seen.add(info['digest']);photos.append(url)
    return photos


def crawl(ctx):
    ctx.allow_automatic_results=True
    code=str(ctx.product_code).strip().upper();errors=[];work=[];products=[];seen=set()
    if not code:raise CrawlError('품번이 비어 있습니다')
    ctx.stage('AWK 공식 품번 검색: '+code)
    try:links=search(ctx,code)
    except Exception as exc:ctx.fail_item(str(exc));raise
    for link in links:
        try:
            doc=parse(fetch(ctx,link))
            if field(doc,'자체상품코드').strip().upper()!=code:continue
            group=product_group(doc);pid=parse_qs(urlsplit(link).query)['product_no'][0]
            if not str(group.get('productGroupID','')).endswith('_'+pid):raise CrawlError('상품번호와 구조화 상품 ID 불일치')
            variants=group.get('hasVariant',[])
            if any(not re.search(r'\bAWK\b',str(v.get('brand',{}).get('name','')),re.I) for v in variants):raise CrawlError('공식 AWK 브랜드 불일치')
            options=choices(doc,group);cache={};markup=detail(ctx,doc,link,cache) if ctx.enabled('detail_page') else ''
            products.append((doc,link,options,cache))
            for item in options:
                key=(pid,item['color'])
                if key in seen:continue
                seen.add(key)
                work.append((item,doc,link,cache,markup))
        except Exception as exc:errors.append(link+' / '+str(exc));ctx.fail_item(errors[-1])
    if errors:raise CrawlError('상품은 조회됐으나 수집 실패: '+' / '.join(errors))
    if not work:
        message='공식 자체상품코드와 일치하는 AWK 상품 없음: '+code
        ctx.fail_item(message);raise CrawlError(message)
    records=[]
    for item,doc,link,cache,markup in work:
        name=field(doc,'상품명');name=re.sub(r'(?i)^\s*AWK[\s_-]*','',name);name=name.replace(code,'').strip(' _-/()')
        record={'product_code':code,'product_name':name,'product_url':link,'color':item['color'],'color_name':color_fields(item['color'])[0],'color_code':color_fields(item['color'])[1]}
        if ctx.enabled('size'):
            if not item['sizes']:raise CrawlError('공식 사이즈 옵션 없음')
            record['size']=','.join(item['sizes'])
        if ctx.enabled('price'):
            values={int(r['option_price']) for r in item['rows'] if isinstance(r.get('option_price'),(int,float)) and r['option_price']>0}
            if len(values)>1:raise CrawlError('동일 색상 사이즈별 판매가 상이')
            record['price']=next(iter(values)) if values else amount(field(doc,'판매가'))
        if ctx.enabled('regular_price'):record['regular_price']=regular_price(ctx,doc)
        if ctx.enabled('category'):
            cats=[a.get_text(' ',strip=True) for a in doc.select('.xans-product-headcategory li a')]
            record['category']=' > '.join(x for x in cats if x and x not in ('홈','현재 위치'))
        if ctx.enabled('detail_page'):record['detail_page']=markup
        records.append(record)
    wants_images=ctx.enabled('main_image') or ctx.enabled('additional_images')
    auto=[];unconfirmed=[]
    for record,(item,doc,link,cache,markup) in zip(records,work):
        output=dict(record)
        if wants_images:
            try:main,extra=gallery(ctx,item,cache,doc,link)
            except (MappingFailure,MissingResource) as exc:
                unconfirmed.append(str(exc));break
            if ctx.enabled('main_image'):output['main_image']=main
            if ctx.enabled('additional_images'):output['additional_images']=extra
        auto.append(output)
    if not unconfirmed:
        for record in auto:ctx.emit_result(record)
        ctx.log('품번 전체 공식 색상 연결 확인: 자동 완료, 수동 매핑 화면에서 제외','SUCCESS')
        return
    # No partial auto rows are emitted: one uncertain option sends the whole
    # article, every colour and all photographs, to one manual group.
    ctx.log('색상 일부 미확정: 품번 전체 수동 매핑으로 전환 / '+' / '.join(unconfirmed),'INFO')
    photos=manual_candidates(ctx,products)
    if not photos:
        message='수동 매핑에 사용할 유효한 원본 상품사진 없음: '+code
        ctx.fail_item(message);raise CrawlError(message)
    palette=[]
    for record in records:
        value={'color_name':record['color_name'],'color_code':record['color_code']}
        if value not in palette:palette.append(value)
    common=dict(records[0])
    for key in ('color','color_name','color_code'):common.pop(key,None)
    for key in ('size','price','regular_price','category'):
        if ctx.enabled(key) and any(r.get(key)!=common.get(key) for r in records):
            common[key]=None;ctx.log('품번 공통 '+key+' 값이 달라 빈칸으로 유지','WARNING')
    shared=''.join(dict.fromkeys(r.get('detail_page','') for r in records)) if ctx.enabled('detail_page') else ''
    if len(shared.encode('utf-16-le'))//2>32767:raise CrawlError('공통 상세 HTML 엑셀 셀 한도 초과')
    if ctx.enabled('detail_page'):common['detail_page']=shared
    for url in photos:ctx.emit_unmapped(dict(common),[dict(c) for c in palette],url,shared_detail_html=shared)
    ctx.log('품번 전체 수동 매핑 후보 '+str(len(photos))+'장 / 색상 '+str(len(palette))+'개','SUCCESS')
