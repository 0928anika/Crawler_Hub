PORTAL_API_VERSION = 1
import hashlib
import base64
import binascii
import html
import io
import os
from pathlib import Path
from contextlib import contextmanager
import json
import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs,urlencode,urlsplit,urljoin,unquote
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image,gallery_slots
SITE='https://brand.naver.com'
STORE='castelbajac'
CHANNEL_UID='2sWDwtZKadOpe9nfngD2U'
REVISION='2026-11-15-castelbajac-5-portable-profile'

def state_from(source):
    doc = BeautifulSoup(source,'html.parser')
    for node in doc.find_all('script'):
        text = node.get_text()
        match = re.search(r'window\.__PRELOADED_STATE__\s*=\s*',text)
        if match:
            raw = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',lambda m:'null' if m[0]=='undefined' else m[0],text[match.end():])
            data = json.JSONDecoder().raw_decode(raw)[0]
            if (data.get('channel') or {}).get('brandUrl') != STORE:
                raise ValueError('지정한 까스텔바작 네이버 공식 스토어의 응답이 아닙니다.')
            return data
    raise ValueError('공개 스토어 데이터가 없습니다. 접근 제한/페이지 구조를 확인하세요.')

class RateLimited(ValueError):
    def __init__(self,endpoint,retry_after=''):
        self.endpoint=endpoint;self.retry_after=retry_after
        super().__init__('네이버 일시적 요청 제한 HTTP 429: '+endpoint+' / 상품 미조회가 아닙니다.')


def retry_seconds(value,fallback):
    try:return max(0,float(value))
    except (ValueError,TypeError):
        try:return max(0,parsedate_to_datetime(value).timestamp()-time.time())
        except (ValueError,TypeError,OverflowError):return fallback


def pause(ctx,page,seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        ctx.checkpoint();page.wait_for_timeout(min(250,max(1,(deadline-time.monotonic())*1000)))
    ctx.checkpoint()


def with_backoff(ctx,page,loader):
    for attempt in range(3):
        try:return loader()
        except RateLimited as exc:
            delay=max(2.5,retry_seconds(exc.retry_after,15*(2**attempt)))
            if attempt==2 or delay>60:
                raise RateLimited(exc.endpoint+' / 요청 중단. 서버 안내 대기시간 '+str(round(delay))+'초 후 다시 실행해주세요.',exc.retry_after) from exc
            ctx.log('네이버 HTTP 429 요청 제한: %.1f초 대기 후 재시도 (%d/2) / %s'%(delay,attempt+1,exc.endpoint),'WARNING')
            pause(ctx,page,delay)


def navigate_once(ctx,page,url):
    # Pace normal navigation as well as retries. No proxy/session rotation.
    pause(ctx,page,max(0,getattr(page,'_castel_next_request',0)-time.monotonic()))
    ctx.checkpoint();response=page.goto(url,wait_until='domcontentloaded',timeout=30000)
    page._castel_next_request=time.monotonic()+2.5
    ctx.checkpoint()
    if response and response.status==429:raise RateLimited('페이지 '+url,response.headers.get('retry-after',''))
    if response and response.status in (401,403):raise ValueError('네이버 접근 제한 HTTP '+str(response.status)+' / '+url)
    if response and response.status>=400:raise ValueError('네이버 페이지 HTTP '+str(response.status)+' / '+url)
    parsed=urlsplit(page.url)
    if parsed.hostname!='brand.naver.com' or not parsed.path.startswith('/'+STORE+'/'):raise ValueError('지정한 공식 스토어 외부로 이동했습니다.')


def navigate(ctx,page,url):
    return with_backoff(ctx,page,lambda:navigate_once(ctx,page,url))


def wait_data(ctx,page,captured,key):
    deadline=time.monotonic()+25
    while True:
        ctx.checkpoint()
        if captured.get('rate_limit'):raise RateLimited(**captured['rate_limit'])
        if captured.get('error'):raise ValueError(captured['error'])
        if key in captured:return captured[key]
        if time.monotonic()>deadline:raise ValueError('공식 '+key+' 응답 로딩 시간 초과')
        page.wait_for_timeout(100)


def load_product(ctx,page,url,number,wants_detail):
    expected='/n/v2/channels/'+CHANNEL_UID+'/products/'+number
    def attempt():
        captured={}
        def receive(response):
            path=urlsplit(response.url).path
            key='product' if path==expected else 'contents' if path.startswith(expected+'/contents/') and wants_detail else None
            if not key:return
            if response.status==429:
                captured['rate_limit']=dict(endpoint=key.upper()+' '+number,retry_after=response.headers.get('retry-after',''));return
            try:
                if response.status!=200:raise ValueError('공식 '+key+' HTTP '+str(response.status))
                data=response.body()
                if len(data)>6000000:raise ValueError('공식 응답 크기 한도 초과')
                captured[key]=json.loads(data)
            except Exception as exc:captured['error']=str(exc)
        page.on('response',receive)
        try:
            navigate_once(ctx,page,url)
            product=wait_data(ctx,page,captured,'product')
            contents=wait_data(ctx,page,captured,'contents') if wants_detail else None
            return product,contents
        finally:page.remove_listener('response',receive)
    return with_backoff(ctx,page,attempt)


def money(value):
    if isinstance(value,bool):return None
    try:
        number=float(value)
        return int(number) if number>0 and number.is_integer() else None
    except (ValueError,TypeError,OverflowError):return None

def product_name(value,code):
    value=re.sub(r'[\[(]\s*'+re.escape(code)+r'\s*[\])]', ' ', value, flags=re.I)
    value=re.sub(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',' ',value,flags=re.I)
    for alias in ('까스텔바작 본사','까스텔바작','CASTELBAJAC'):
        value=re.sub(r'(?<![A-Za-z0-9가-힣])'+re.escape(alias)+r'(?![A-Za-z0-9가-힣])',' ',value,flags=re.I)
    value=re.sub(r'\(\s*정상가\s*[\d,]+\s*원\s*\)',' ',value)
    value=re.sub(r'\[\s*\]|\(\s*\)',' ',value)
    return re.sub(r'\s+',' ',value).strip(' _-/')

def common_price(ctx,product):
    values = {row.get('price') for row in product.get('optionCombinations',[])}
    if values!={0}:
        ctx.log('옵션 추가금이 있거나 불명확하여 공통 판매가는 빈칸으로 저장합니다.','WARNING');return None
    benefits = product.get('benefitsView') or {}
    value = benefits.get('discountedSalePrice') if 'discountedSalePrice' in benefits else product.get('salePrice')
    result = money(value)
    if result is None:ctx.log('공식 현재 판매가 확인 불가: 빈칸','WARNING')
    return result


def model_of(product):
    return str((product.get('naverShoppingSearchInfo') or {}).get('modelName') or '').strip().upper()


def matches(model,code):
    return model==code or bool(re.fullmatch(re.escape(code)+r'[A-Z0-9]{2}',model))


def discover(ctx,page,code):
    pending=[SITE+'/'+STORE+'/search?'+urlencode({'q':code})];visited=set();found={};seen=set();total=None
    while pending:
        url=pending.pop(0)
        if url in visited:continue
        if len(visited)>=30:raise ValueError('공식 검색 페이지 한도 초과')
        visited.add(url);navigate(ctx,page,url);state=state_from(page.content())
        if (state.get('channel') or {}).get('channelUid')!=CHANNEL_UID:raise ValueError('공식 스토어 채널 불일치')
        data=state.get('keywordSearch') or {};rows=data.get('simpleProducts')
        if str(data.get('keyword','')).upper()!=code or not isinstance(rows,list) or not isinstance(data.get('totalCount'),int):raise ValueError('공식 검색 응답 형식 확인 실패')
        total=data['totalCount']
        for row in rows:
            number=str(row.get('id') or '')
            if not number.isdecimal():raise ValueError('공식 검색 상품번호 누락')
            seen.add(number)
            if matches(model_of(row),code):found[number]=SITE+'/'+STORE+'/products/'+number
        if len(seen)>=total:break
        for href in page.locator('a[href]').evaluate_all('(ns)=>ns.map(n=>n.href)'):
            parsed=urlsplit(href);q=parse_qs(parsed.query)
            if parsed.hostname=='brand.naver.com' and parsed.path=='/'+STORE+'/search' and q.get('q')==[code] and href not in visited and href not in pending:pending.append(href)
        if not pending:raise ValueError('공식 검색 다음 페이지 확인 실패')
    if not found:raise ValueError(('[검색 결과 0건] ' if total==0 else '[검색 결과는 있으나 동일 품번 없음] ')+code)
    return found


def original_image(raw):
    if not isinstance(raw,str):return ''
    p=urlsplit(raw)
    if p.scheme not in ('http','https') or p.username or p.password or '..' in unquote(p.path).split('/'):return ''
    if p.hostname=='castelgolf.cafe24.com' and p.path.startswith('/BG/'):return re.sub(r'^http:', 'https:', raw)
    if p.hostname=='shop-phinf.pstatic.net' and p.scheme=='https':return raw
    return ''


def detail_original(raw):
    direct=original_image(raw)
    if direct:return direct
    if not isinstance(raw,str):return ''
    parsed=urlsplit(raw)
    if parsed.scheme!='https' or parsed.hostname!='proxy-smartstore.naver.net' or parsed.username or parsed.password or not parsed.path.startswith('/img/'):return ''
    encoded=unquote(parsed.path[len('/img/'):])
    if len(encoded)>16000:return ''
    try:
        decoded=base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True).decode('utf-8')
    except (ValueError,UnicodeError,binascii.Error):return ''
    # The proxy path contains the source URL. Use only our verified public
    # original hosts; never export the expiring proxy query/token.
    decoded=decoded if decoded.startswith(('http://','https://')) else 'https://'+decoded
    return original_image(decoded)


def checked(ctx,url,cache,detail=False):
    if not original_image(url):raise ValueError('확인되지 않은 공식 원본 이미지 주소: '+url)
    key=(url,detail)
    if key not in cache:
        earlier=cache.get((url,False))
        cache[key]=earlier if earlier and earlier['status']=='accessible' else check_public_image(url,timeout=15,retries=1,max_bytes=64*1024*1024 if detail else 10000000,checkpoint=ctx.checkpoint)
    result=cache[key]
    if result['status']!='accessible':raise ValueError('공식 원본 이미지 조회 실패: '+url+' / '+', '.join(result['reasons']))
    return result


def gallery(ctx,product,cache):
    rows=sorted(product.get('productImages') or [],key=lambda r:(r.get('imageType')!='REPRESENTATIVE',r.get('order') or 0))
    accepted=[];seen=set()
    for row in rows:
        if row.get('imageType') not in ('REPRESENTATIVE','OPTIONAL'):continue
        url=row.get('url') or ''
        if not url or url in seen:continue
        seen.add(url);result=checked(ctx,url,cache)
        if result['format'] not in ('JPEG','PNG','WEBP') or min(result['width'],result['height'])<300 or max(result['width'],result['height'])/min(result['width'],result['height'])>1.8:continue
        with Image.open(io.BytesIO(result['data'])) as im:
            if getattr(im,'is_animated',False):continue
        accepted.append(url)
    if not accepted:raise ValueError('공식 대표·추가사진 수집 결과 없음')
    return accepted


def detail_urls(contents,model):
    doc=BeautifulSoup(contents.get('renderContent') or '','html.parser');urls=[]
    for node in doc.select('img'):
        anchor=node.find_parent('a',attrs={'data-linkdata':True});raw=''
        if anchor:
            try:raw=json.loads(anchor['data-linkdata']).get('src') or ''
            except (ValueError,TypeError):pass
        if not raw:raw=node.get('data-src') or node.get('src') or ''
        url=detail_original(raw)
        if not url:
            for candidate in (node.get('data-src'),node.get('src')):
                url=detail_original(candidate)
                if url:break
        if not url:raise ValueError('상세 원본 주소 미확인: '+raw[:180])
        p=urlsplit(url)
        if re.search(r'banner|shipping|delivery|coupon|notice|/event/|배송|교환|반품',p.path,re.I):continue
        if p.hostname=='castelgolf.cafe24.com' and '/'+model+'/' not in p.path:continue
        if url not in urls:urls.append(url)
    if not urls:raise ValueError('해당 상품 상세 본문 이미지 없음')
    return urls


def detail(ctx,contents,model,cache):
    parts=[]
    for url in detail_urls(contents,model):
        checked(ctx,url,cache,True)
        parts.append('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="display:block;width:100%;height:auto;margin:0 auto">')
    return '<div style="width:100%;max-width:780px;margin:0 auto;text-align:center">'+''.join(parts)+'</div>'


def color_info(product,code):
    model=model_of(product);groups=product.get('options') or []
    if any(str(g.get('groupName','')).strip().upper() in ('색상','컬러','COLOR') for g in groups):raise ValueError('색상 복수 옵션 상품: 색상별 사진 연결 구조 확인 필요')
    suffix=model[len(code):] if model.startswith(code) else ''
    if not suffix and re.fullmatch(r'[A-Z]{6}[0-9]{3}[A-Z0-9]{2}',model):suffix=model[-2:]
    name=str(((product.get('productInfoProvidedNoticeView') or {}).get('basic') or {}).get('색상') or '').strip()
    if name in ('상세설명에 표시','상세페이지 참조','상세설명참조'):name=''
    if not suffix and not name:raise ValueError('공식 색상명/색상코드 확인 실패')
    return dict(color=name or suffix,color_name=name,color_code=suffix)


def sizes(product):
    position=next((i+1 for i,g in enumerate(product.get('options') or []) if str(g.get('groupName','')).strip().upper() in ('사이즈','SIZE')),None)
    if position is None:return ''
    return ','.join(dict.fromkeys(str(r.get('optionName'+str(position)) or '') for r in product.get('optionCombinations',[]) if r.get('optionName'+str(position))))


def regular_price(ctx,product):
    m=re.search(r'정상가\s*([0-9,]+)\s*원',str(product.get('name') or ''))
    if m:return money(m[1].replace(',',''))
    ctx.log('공식 독립 정상가 없음: 빈칸','INFO');return None


def chrome_profile():
    # Frozen worker bundles do not include APP's local_paths module.
    # Resolve the same local runtime path using only the standard library.
    configured=os.environ.get('CRAWLER_HUB_HOME')
    if configured:home=Path(configured).expanduser().resolve()
    else:
        base=Path(os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')
        home=(base/'CrawlerHub').resolve()
    roots=[Path(v).expanduser().resolve() for k,v in os.environ.items() if k.lower().startswith('onedrive') and v]
    if str(home).startswith('\\\\') or any('onedrive' in part.lower() or 'sharepoint' in part.lower() for part in home.parts) or any(home==root or root in home.parents for root in roots):
        raise ValueError('수집용 크롬 프로필은 동기화 폴더가 아닌 로컬 디스크에 저장해야 합니다.')
    return home/'browser-profiles'/'castelbajac-chrome'


@contextmanager
def profile_lease(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    handle=(folder.parent/(folder.name+'.lock')).open('a+b');locked=False
    try:
        handle.seek(0,2)
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError as exc:raise ValueError('까스텔바작 수집용 크롬 프로필이 사용 중입니다. 실행 중인 수집이 끝난 후 다시 실행해주세요.') from exc
        yield folder
    finally:
        if locked:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()


@contextmanager
def chrome_session(ctx,engine):
    with profile_lease(chrome_profile()) as folder:
        ctx.log('까스텔바작 '+REVISION+' / Chrome 전용 프로필 재사용: '+str(folder),'INFO')
        try:browser=engine.chromium.launch_persistent_context(str(folder),channel='chrome',headless=True,locale='ko-KR')
        except Exception as exc:raise ValueError('수집용 Chrome 실행 실패. Chrome 설치 및 전용 프로필 사용 여부를 확인해주세요: '+str(exc)) from exc
        try:yield browser
        finally:browser.close()


def crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not code:raise ValueError('품번이 비어 있습니다')
    from playwright.sync_api import sync_playwright
    cache={};failures=[];emitted=0
    try:
        with sync_playwright() as engine:
            with chrome_session(ctx,engine) as browser:
                page=browser.pages[0] if browser.pages else browser.new_page();page.set_default_timeout(15000)
                def route(request):
                    parsed=urlsplit(request.request.url);path=parsed.path
                    if request.request.resource_type in ('image','media','font'):return request.abort()
                    if parsed.hostname=='brand.naver.com' and path.startswith('/n/'):
                        main=bool(re.fullmatch(r'/n/v2/channels/[^/]+/products/\d+',path))
                        content=ctx.enabled('detail_page') and '/products/' in path and '/contents/' in path
                        if not main and not content:return request.abort()
                    return request.continue_()
                page.route('**/*',route);ctx.stage('까스텔바작 공식 품번 검색: '+code)
                links=discover(ctx,page,code)
                for number,url in links.items():
                    try:
                        ctx.stage('공식 색상별 상품 조회: '+number)
                        product,contents=load_product(ctx,page,url,number,ctx.enabled('detail_page'));model=model_of(product)
                        if str(product.get('id'))!=number or (product.get('channel') or {}).get('channelUid')!=CHANNEL_UID or not matches(model,code):raise ValueError('공식 상품번호·스토어·품번 불일치')
                        color=color_info(product,code)
                        record=dict(product_code=code,product_name=product_name(str(product.get('name') or ''),model),product_url=url,**color)
                        if ctx.enabled('size'):record['size']=sizes(product)
                        if ctx.enabled('price'):record['price']=common_price(ctx,product)
                        if ctx.enabled('regular_price'):record['regular_price']=regular_price(ctx,product)
                        if ctx.enabled('category'):record['category']=str((product.get('category') or {}).get('wholeCategoryName') or '')
                        if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                            photos=gallery(ctx,product,cache)
                            if ctx.enabled('main_image'):record['main_image']=photos[0]
                            extras=photos[1:6] or photos[:1]
                            if ctx.enabled('additional_images'):record['additional_images']=','.join(extras[i%len(extras)] for i in range(5))
                        if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,contents,model,cache)
                        ctx.emit_result(record);emitted+=1
                    except RateLimited:raise
                    except Exception as exc:failures.append(number+' / '+str(exc))
        if failures:raise RuntimeError('일부 상품 수집 미완료: '+' / '.join(failures))
        if not emitted:raise ValueError('공식 동일 품번 수집 결과 없음')
    except Exception as exc:ctx.fail_item(str(exc));raise
