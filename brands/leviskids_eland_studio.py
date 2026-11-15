PORTAL_API_VERSION = 1

import base64
import subprocess
import tempfile
from pathlib import Path
import hashlib
from email.utils import parsedate_to_datetime
import html
import http.client
import io
import json
import re
import socket
import time
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit
import urllib.error
import urllib.request

from bs4 import BeautifulSoup
from PIL import Image, ImageStat, UnidentifiedImageError

SITE = 'https://www.elandmall.co.kr'
BRAND_NO = '1600003134'
VENDOR_NO = ''
DETAIL_MAX_BYTES = 64 * 1024 * 1024
POLICY = dict(checked_on='2026-11-10', source='https://marketplace.coupang.com/mba/listing', min_side=500, max_bytes=10000000, formats=['JPEG','PNG','WEBP'], max_aspect=1.8)
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
        retry_delay=.5
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
            if exc.code==429:
                retry_after=exc.headers.get('Retry-After','') if exc.headers else ''
                try:retry_delay=max(0,float(retry_after))
                except ValueError:
                    try:retry_delay=max(0,parsedate_to_datetime(retry_after).timestamp()-time.time())
                    except (ValueError,TypeError,OverflowError):retry_delay=3*(attempt+1)
                if retry_delay>=deadline-time.monotonic():raise CollectionError('HTTP 429 일시적 요청 제한: 재시도 대기시간이 제한을 초과함 / '+url) from exc
                ctx.log('HTTP 429 일시적 요청 제한: %.1f초 대기 후 재시도'%retry_delay,'WARNING')
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise CollectionError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt == 2:
                raise CollectionError('통신 실패: %s / %s' % (url, exc)) from exc
        for _ in range(max(1,int(retry_delay*10+.999999))):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CollectionError('요청 실패: ' + url)


def image_url(url):
    # Preserve existing percent escapes and URL delimiters, encode Unicode paths.
    return quote(url, safe=":/?#[]@!$&'()*+,;=%")


def page(ctx, url):
    return BeautifulSoup(fetch(ctx, url, 6_000_000).decode('utf-8-sig'), 'html.parser')


class SearchResults(list):
    def __init__(self, links, total, vendors):
        super().__init__(links)
        self.total = total
        self.vendors = vendors


def search_site(ctx, code, site):
    links, seen, signatures = {}, set(), set()
    for number in range(1, 101):
        ctx.stage('리바이스키즈 공개 검색 %s페이지' % number)
        soup = page(ctx, site+'/s/srch?'+urlencode(
            {'q':code,'size':100,'page':number,'from':(number-1)*100}))
        counter = soup.select_one('[data-searchresult-total]')
        if not counter or not re.fullmatch(r'[\d,]+',counter.get_text(strip=True)):
            raise CollectionError('검색 건수 없음: 오류/인증/사이트 변경')
        total = int(counter.get_text(strip=True).replace(',',''))
        current = []
        for node in soup.select('a.ui-item__link[href]'):
            ctx.checkpoint()
            parsed = urlsplit(urljoin(SITE,node['href']))
            params = parse_qs(parsed.query)
            if parsed.hostname not in ('www.elandmall.co.kr',) or parsed.path!='/i/item':
                continue
            if not params.get('itemNo') or not params.get('lowerVendNo'):
                continue
            key = params['itemNo'][0],params['lowerVendNo'][0]
            if key not in current:
                current.append(key)
            links[key] = SITE+'/i/item?'+urlencode({'itemNo':key[0],'lowerVendNo':key[1]})
        signature = tuple(current)
        if total and (not current or signature in signatures):
            raise CollectionError('검색 페이지 반복/비어 있음: 누락 방지 중단')
        signatures.add(signature)
        seen.update(current)
        if len(seen)>=total:
            return SearchResults(links.values(), total, sorted({vendor for _, vendor in seen}))
    raise CollectionError('검색 100페이지 안전 한도 초과: 미완료')


def search(ctx,code):
    result=search_site(ctx,code,SITE)
    if not result:raise CollectionError('이랜드몰 검색 결과 0건: '+code)
    return result


def product_once(ctx, url, code, diagnostics=None):
    params = parse_qs(urlsplit(url).query)
    soup = page(ctx,url)
    for script in soup.find_all('script',src=False):
        text = script.get_text()
        match = re.search(r'const\s+itemDetailJson\s*=\s*',text)
        if not match:
            continue
        try:
            detail = json.JSONDecoder().raw_decode(text[match.end():])[0]['itemDetail']
            main = detail['itemMain']
        except (KeyError,ValueError,TypeError) as exc:
            raise CollectionError('공식 상품 JSON 형식 변경: '+url) from exc
        model=str(main.get('modelNo') or '').strip().upper()
        exact=True if code is None else bool(re.fullmatch(re.escape(code)+r'(?:[_-][A-Z0-9]+)?',model)) if model else bool(re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',str(main.get('itemName') or '').upper()))
        if str(main.get('brandNo'))!=BRAND_NO or not exact:
            reason='검색 결과는 있으나 실제 공식 품번/브랜드 불일치: 요청 %s, 실제 %s, 브랜드 %s / %s' % (code, main.get('modelNo'), main.get('brandNo'), url)
            if diagnostics is not None:diagnostics.append(reason)
            ctx.log(reason,'WARNING')
            return None
        params = parse_qs(urlsplit(url).query)
        if str(main.get('itemNo'))!=params['itemNo'][0] or str(main.get('lowerVendNo'))!=params['lowerVendNo'][0]:
            raise CollectionError('조회 상품/판매자와 응답 ID 불일치: '+url)
        if not main.get('itemName'):
            raise CollectionError('필수 상품명 없음: '+url)
        return detail
    raise CollectionError('공식 상품 JSON 없음: '+url)


def product(ctx, url, code, diagnostics=None):
    for attempt in range(2):
        try:
            return product_once(ctx, url, code, diagnostics)
        except CollectionError as exc:
            if attempt or '공식 상품 JSON 없음' not in str(exc):
                raise
            ctx.log('일시적 상세 응답 누락: 공식 상품 재요청', 'WARNING')


def options(ctx, main, parent=None):
    params = {'itemNo':main['itemNo'],'lowerVendNo':main['lowerVendNo'],'itemTcode':main['itemTcode']}
    if parent is not None:
        params['itemOptionNoList'] = str(parent)
    try:
        data = json.loads(fetch(ctx,SITE+'/v1/item/option/api?'+urlencode(params)).decode('utf-8-sig'))
        if str(data['resultCode'])!='200':
            raise CollectionError('옵션 API 오류: '+str(data.get('resultMessage')))
        rows = data['data']['itemOption']
        if not isinstance(rows,list):
            raise CollectionError('옵션 목록 형식 변경')
        selected=[]
        for row in rows:
            if str(row.get('itemNo'))!=str(main['itemNo']):raise CollectionError('옵션 상품 ID 불일치')
            if str(row.get('lowerVendNo'))!=str(main['lowerVendNo']):
                ctx.log('지정 판매자 외 옵션 행 제외: '+str(row.get('lowerVendNo'))+' / '+str(row.get('itemOptionName')),'WARNING')
                continue
            selected.append(row)
        if rows and not selected:raise CollectionError('조회 상품 판매자와 일치하는 옵션 행 없음')
        return selected
    except (KeyError,ValueError,TypeError) as exc:
        raise CollectionError('공식 옵션 JSON 형식 변경') from exc


def colors(ctx, rows):
    palette, actual = [], []
    for row in rows:
        ctx.checkpoint()
        if str(row.get('itemOptionTcode')) not in ('10','99'):
            raise CollectionError('확인되지 않은 첫 옵션 유형: 색상 후보 확인 불가')
        label = str(row.get('itemOptionName') or '').strip()
        if not label:
            raise CollectionError('공식 색상 옵션명 누락')
        if re.fullmatch(r'AA\s+대표(?:칼라|컬러|색상)',label,re.I):
            ctx.log('사이트 표시용 대표칼라 옵션 제외: '+label,'INFO')
            continue
        match = re.fullmatch(r'\((\d{2})\)\s*(.+)',label) or re.fullmatch(r'(\d{2})\s+(.+)',label)
        color = {'color_name':match[2].strip() if match else label,'color_code':match[1] if match else ''}
        if color not in palette:
            palette.append(color)
        actual.append(row)
    if not palette:
        raise CollectionError('실제 색상 후보 없음')
    return palette, actual


def money(value):
    if isinstance(value,bool):
        return None
    text = str(value or '').strip()
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)',text):
        return None
    result = int(text.replace(',',''))
    return result if result>0 else None


def common(values):
    return values[0] if values and all(v is not None and v==values[0] for v in values) else None


def optional_values(ctx, main, actual, record):
    if ctx.enabled('regular_price'):
        ctx.checkpoint()
        try:record['regular_price'] = money(main.get('orgSellprice'))
        except Exception as exc:
            record['regular_price']=None
            ctx.log('정상가 파싱 실패: '+str(exc),'WARNING')
        if record['regular_price'] is None:
            ctx.log('정상가 orgSellprice 누락/0/형식 불명확: 빈칸으로 수집 계속','WARNING')
        ctx.checkpoint()
    if ctx.enabled('category'):
        record['category'] = str(main.get('depth4StandardCategoryName') or '').strip() or None
    if not (ctx.enabled('size') or ctx.enabled('price')):
        return
    size_groups, price_values = [], []
    raw_labels = list(dict.fromkeys(str(row.get('itemOptionName') or '').strip() for row in actual))
    for color in actual:
        ctx.checkpoint()
        children = options(ctx,main,color['itemOptionNo'])
        if any(str(row.get('itemOptionTcode')) not in ('20','99') for row in children):
            raise CollectionError('확인되지 않은 색상 하위 사이즈 옵션 유형')
        for row in children:
            raw = str(color.get('itemOptionName') or '')+' / '+str(row.get('itemOptionName') or '')
            if raw not in raw_labels:raw_labels.append(raw)
        if ctx.enabled('size'):
            names = list(dict.fromkeys(str(row.get('itemOptionName') or '').strip() for row in children))
            size_groups.append(names if names and all(names) else None)
        if ctx.enabled('price'):
            # The official main sellprice and all actual size/colour sellPrice
            # values must agree before one common manual-mapping price is used.
            price_values.append(money(color.get('sellPrice')))
            price_values.extend(money(row.get('sellPrice')) for row in children)
    record['원본 옵션'] = '\n'.join(raw_labels)
    if ctx.enabled('size'):
        valid = bool(size_groups) and all(v and set(v)==set(size_groups[0] or []) for v in size_groups)
        record['size'] = ','.join(dict.fromkeys(v for group in size_groups if group for v in group)) or None
        if not valid:
            ctx.log('색상별 사이즈가 달라 전체 옵션 사이즈를 합집합으로 수집','WARNING')
    if ctx.enabled('price'):
        record['price'] = common([money(main.get('sellprice'))]+price_values)
        if record['price'] is None:
            ctx.log('공식 판매가/옵션별 판매가 공통값 미확정: 빈칸','WARNING')


def clean_name(name, code):
    # Remove explicit leading campaign badges, not product qualifiers [키즈].
    while True:
        badge = re.match(r'^\s*(?:\[([^\]]{1,60})\]|【([^】]{1,60})】)\s*',name)
        if not badge or not re.search(r'특가|할인|쿠폰|세일|타임딜|한정딜|신상|신상품',next(v for v in badge.groups() if v)):
            break
        name = name[badge.end():]
    # Remove the model first: some sellers put it AFTER the branch suffix.
    for token in ('리바이스키즈','LEVIS KIDS',code):
        name = re.sub(r'(?<![A-Za-z0-9])'+re.escape(token)+(r'(?:[_-](?:[0-9]{2}|[A-Z]{2})(?![가-힣A-Za-z0-9]))?' if token==code else '')+r'(?![A-Za-z0-9])','',name,flags=re.I)
    name = re.sub(r'(?<![A-Za-z])(?:NC|뉴코아|이천일|모다아울렛|롯데백화점|현대백화점|신세계백화점)\s*[가-힣]{1,12}(?:지점|점)(?![가-힣])','',name,flags=re.I)
    name = re.sub(r'\[\s*\]|\(\s*\)','',name)
    # A branch suffix does not have to start with NC/뉴코아: _충장점,
    # _중동점, [평촌점], (뉴코아 부천점), and bare " 충장점" are all retail
    # annotations. Keep product qualifiers such as (주니어) intact.
    branch = r'(?:(?:NC|뉴코아|이천일|이랜드리테일|이랜드몰|모다아울렛)\s*)?[^\s\[\]()_|/\-]{1,35}(?:지점|점)'
    locations=r'(?:대전\s*유성|일산|유성|창원|부천|중앙로역|충장|고잔|산본|괴정|야탑|수원\s*터미널|인천|평촌|강남|신구로|순천|평택|송파|청주)'
    retail=r'(?:(?:NC|뉴코아|이천일|이랜드리테일|이랜드몰|모다아울렛)\s*[가-힣0-9]+(?:\s+[가-힣0-9]+)?(?:지점|점)?|'+locations+r'(?:지점|점)?)'
    suffixes = (
        r'\s*[_|/\-]\s*'+retail+r'\s*$',
        r'\s*[\[(]\s*'+retail+r'\s*[\])]\s*$',
        r'\s+'+retail+r'\s*$',
        r'\s*[_|/\-]\s*'+branch+r'\s*$',
        r'\s*[\[(]\s*[^\[\]()]{1,60}(?:지점|점)\s*[\])]\s*$',
        r'\s+'+branch+r'\s*$',
    )
    while True:
        previous = name
        for pattern in suffixes:
            name = re.sub(pattern,'',name,flags=re.I)
        # A standalone trailing numeric clothing size is not the product name.
        # Keep quantities/length qualifiers such as 2매입, 7부 and (주니어).
        name = re.sub(r'\s+(?:\d{2,3}호?|[\[(]\d{2,3}호?[\])])\s*$','',name)
        if name == previous:
            break
    return re.sub(r'\s+',' ',name).strip(' _-')


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


class ImageRejected(CollectionError):
    """A decoded asset is not a usable product photograph."""


OCR_SCRIPT="\n$ProgressPreference='SilentlyContinue'\n$ErrorActionPreference='Stop'\nAdd-Type -AssemblyName System.Runtime.WindowsRuntime\n$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]\n$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]\n$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]\n$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]\n$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]\n$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]\n$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1})[0]\nfunction Await($Op,$Type) { $task=$asTask.MakeGenericMethod($Type).Invoke($null,@($Op));$task.Wait();$task.Result }\n$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])\n$stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])\n$decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])\n$bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])\n$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()\n$result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])\n[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)\n$result.Text\n$bitmap.Dispose();$stream.Dispose()\n"

TEXT_PANEL=re.compile(r'사이즈\s*(?:조견|표|가이드)|교환.{0,5}(?:환불|반품)|배송.{0,5}(?:안내|기간)|고객센터|구매.{0,4}주의|저작권|무단.{0,5}복제|REPREVE|RECYCLED\s*(?:FIBER|POLYESTER)|TODDLER|JUNIOR.{0,12}SOCKS',re.I)
def text_panel(words):
    compact=re.sub(r'\s+','',words)
    return bool(TEXT_PANEL.search(words)) or len(compact)>240

def image_words(ctx,image):
    ctx.checkpoint()
    pic=image.convert('RGB');pic.thumbnail((1200,1800))
    with tempfile.TemporaryDirectory(prefix='levis-ocr-') as folder:
        path=Path(folder)/'image.png';pic.save(path)
        script="$ImagePath='"+str(path).replace("'","''")+"'\n"+OCR_SCRIPT
        command=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        try:run=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',command],capture_output=True,text=True,encoding='utf-8',timeout=25,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError,subprocess.TimeoutExpired) as exc:raise CollectionError('상품사진 문자 검사 실행 실패: '+str(exc)) from exc
        if run.returncode:raise CollectionError('Windows 문자 검사 실패: '+run.stderr[-300:])
        return run.stdout

def image_info(ctx, url, cache, detail=False):
    if urlsplit(url).hostname not in ('item.elandrs.com','img-studio.elandmall.com','item-imghub.eland.co.kr') and not (detail and urlsplit(url).hostname in ('file1.partnerbk.com','cmcrftp.godohosting.com','image.style24.com','image.istyle24.com','thumbnail.coupangcdn.com')):
        raise CollectionError('확인되지 않은 이미지 호스트: '+url)
    key=('detail',url) if detail else url
    if key in cache:
        return cache[key]
    raw = fetch(ctx,url,DETAIL_MAX_BYTES if detail else MAX_BYTES)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt,w,h = image.format,image.width,image.height
            if fmt not in (('JPEG','PNG','WEBP','GIF') if detail else ('JPEG','PNG','WEBP')) or (not detail and getattr(image,'is_animated',False)):
                raise ImageRejected('정지 JPEG/PNG/WebP가 아닌 이미지: '+url)
            if w*h>(80_000_000 if detail else 40_000_000):
                raise ImageRejected('이미지 해독 픽셀 한도 초과: '+url)
            image.load()
            panel=False
            if url!=SIZE_IMAGE and min(w,h)>=300 and max(w,h)/min(w,h)<=2:
                panel_key=('text_panel',hashlib.sha256(raw).hexdigest())
                if panel_key not in cache:cache[panel_key]=text_panel(image_words(ctx,image))
                panel=cache[panel_key]
                if panel:ctx.log('글씨·표·공통안내 이미지 후보 제외: '+url,'INFO')
            info = (fmt,w,h,hashlib.sha256(raw).hexdigest(),studio_background(image)[0],panel)
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
        raise ImageRejected('손상 또는 이미지가 아닌 응답: '+url) from exc
    cache[key] = info
    return info


SIZE_IMAGE='https://image.style24.com/Upload/editor/%EB%A6%AC%EB%B0%94-%EC%82%AC%EC%9D%B4%EC%A6%88%EC%A1%B0%EA%B2%AC%ED%91%9C_20240202.jpg'
REVISION='2026-11-16-leviskids-eland-studio-2'
NEGATIVE=re.compile(r'banner|notice|shipping|delivery|refund|coupon|copyright|CCTV|/MD_buyer/|brandSizeImg|size|chart|guide|gosi|specification|고시|사이즈|배송|판매처|매장.?안내|저작권',re.I)


def primary_studio(ctx,main,cache):
    primary=next((v for v in main.get('itemImage',[]) if str(v.get('imageDcode'))=='010' and v.get('imagePath')),None)
    if primary is None:raise CollectionError('대표사진 없음: 스튜디오컷 판별 불가')
    url=image_url(urljoin('https://item.elandrs.com/',primary['imagePath']))
    return image_info(ctx,url,cache)[4]


def gallery(ctx,main,cache):
    photos=[]
    rows=sorted(main.get('itemImage',[]),key=lambda v:str(v.get('imageDcode'))!='010')
    for row in rows:
        if str(row.get('imageDcode')) not in ('010','030') or not row.get('imagePath'):continue
        url=image_url(urljoin('https://item.elandrs.com/',row['imagePath']))
        try:info=image_info(ctx,url,cache)
        except (MissingResource,ImageRejected):continue
        if len(info)>5 and info[5]:continue
        if min(info[1:3])<300 or max(info[1:3])/min(info[1:3])>2:continue
        photos.append((url,info[3]))
    return photos


def body_photos(ctx,data,cache):
    markup=(data.get('itemDesc') or {}).get('itemDescdocumentCon') or ''
    doc=BeautifulSoup(html.unescape(markup),'html.parser');result=[];seen=set()
    for node in doc.select('img'):
        raw=node.get('ec-data-src') or node.get('data-original') or node.get('data-src') or node.get('src') or ''
        if not raw or raw.startswith('data:'):continue
        url=image_url(urljoin(SITE,raw));path=unquote(urlsplit(url).path)
        if re.search(r'_L\d+(?:\[\d+\])?\.(?:jpe?g|png|webp)$',path,re.I):continue
        if NEGATIVE.search(path+' '+str(node.get('alt') or '')+' '+str(node.get('title') or '')):continue
        if url in seen:continue
        seen.add(url)
        if url.startswith('http:'):url='https:'+url[5:]
        try:info=image_info(ctx,url,cache,detail=True)
        except (MissingResource,AccessDenied,ImageRejected) as exc:
            ctx.log('상세 후보 원본 제외: '+str(exc),'WARNING');continue
        if len(info)>5 and info[5]:continue
        if info[0] not in ('JPEG','PNG','WEBP') or min(info[1:3])<300 or max(info[1:3])/min(info[1:3])>2:continue
        result.append((url,info[3]))
    return result



def input_link(value):
    parsed=urlsplit(html.unescape(value.strip()))
    host=parsed.hostname or ''
    if parsed.scheme not in ('http','https') or not (host=='elandmall.co.kr' or host.endswith('.elandmall.co.kr')) or parsed.username or parsed.password or parsed.port not in (None,80,443) or parsed.path!='/i/item':
        raise CollectionError('이랜드몰 상품 링크(/i/item)를 입력해주세요.')
    params=parse_qs(parsed.query);items=params.get('itemNo',[]);vendors=params.get('lowerVendNo',[])
    if len(items)!=1 or not items[0].isdigit() or len(vendors)!=1 or not re.fullmatch(r'[A-Za-z0-9]+',vendors[0]):raise CollectionError('상품 링크의 itemNo 또는 lowerVendNo를 확인해주세요.')
    return SITE+'/i/item?'+urlencode(dict(itemNo=items[0],lowerVendNo=vendors[0]))

def actual_code(main):
    model=str(main.get('modelNo') or '').strip().upper()
    if model:return re.split(r'[_-]',model)[0]
    found=re.search(r'(?<![A-Z0-9])([A-Z]\d{3}[A-Z]{2}\d{3}[A-Z]?)(?![A-Z0-9])',str(main.get('itemName') or '').upper())
    if found:return found.group(1)
    raise CollectionError('지정 링크에서 실제 품번을 확인할 수 없습니다.')


def crawl(ctx):
    raw=str(ctx.product_code).strip()
    direct=raw.lower().startswith(('http://','https://'))
    code=raw.upper()
    if not code:raise CollectionError('품번이 비어 있습니다')
    if not(ctx.enabled('main_image') or ctx.enabled('additional_images')):raise CollectionError('색상 매핑 후보를 위해 대표/추가이미지를 선택해주세요.')
    products=[];palette=[];photos=[];digests=set();cache={};errors=[]
    try:
        ctx.log('리바이스키즈 이랜드몰 스튜디오컷 '+REVISION,'INFO')
        tail=''
        if ctx.enabled('detail_page'):
            image_info(ctx,SIZE_IMAGE,cache,detail=True)
            tail='<img src="'+SIZE_IMAGE+'" alt="리바이스키즈 공식 사이즈표" style="display:block;width:100%;height:auto;margin:0 auto">'
        for link in ([input_link(raw)] if direct else search(ctx,code)):
            try:
                data=product(ctx,link,None if direct else code)
                if data is None:continue
                main=data['itemMain']
                if direct:code=actual_code(main)
                if not primary_studio(ctx,main,cache):
                    ctx.log('대표사진 배경 스튜디오컷 조건 미통과: 해당 링크 제외 / '+link,'INFO');continue
                main_photos=gallery(ctx,main,cache)
                detail_photos=body_photos(ctx,data,cache)
                choices,actual=colors(ctx,options(ctx,main))
                record=dict(product_code=code,product_name=clean_name(main['itemName'],code),product_url=link)
                record['원본 상품명']=main['itemName'];record['판매자번호']=str(main['lowerVendNo']);record['원본 옵션']='\n'.join(str(v.get('itemOptionName') or '') for v in actual)
                optional_values(ctx,main,actual,record);products.append(record)
                for color in choices:
                    if color not in palette:palette.append(color)
                # Put every accepted representative/gallery photo before body cuts.
                for role,images in ((0,main_photos),(1,detail_photos)):
                    for url,digest in images:
                        if digest not in digests:digests.add(digest);photos.append((role,url))
            except (CollectionError,KeyError,ValueError) as exc:errors.append(link+' / '+str(exc))
        if not products or not photos:raise CollectionError('대표사진 스튜디오컷 통과 상품/사진 없음: '+code+(' / '+'; '.join(errors) if errors else ''))
        record=dict(product_code=code,product_name=products[0]['product_name'],product_url=products[0]['product_url'])
        for field in ('원본 상품명','판매자번호','원본 옵션'):record[field]='\n'.join(dict.fromkeys(line for p in products for line in str(p.get(field) or '').splitlines() if line))
        for field in ('size','price','regular_price','category'):
            if ctx.enabled(field):record[field]=(','.join(dict.fromkeys(v for p in products for v in str(p.get(field) or '').split(',') if v)) or None) if field=='size' else common([p.get(field) for p in products])
        if ctx.enabled('detail_page'):record['detail_page']=tail
        for _,url in sorted(photos,key=lambda v:v[0]):ctx.emit_unmapped(dict(record),palette,url,shared_detail_html=tail)
        if errors:raise CollectionError('일부 링크 수집 미완료: '+'; '.join(errors))
        ctx.log(code+': '+str(len(products))+'개 링크 / '+str(len(palette))+'색상 / 후보 '+str(len(photos))+'장','SUCCESS')
    except CollectionError as exc:ctx.fail_item(str(exc));raise
