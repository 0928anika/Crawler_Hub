PORTAL_API_VERSION = 1

import html
import base64
import hashlib
import subprocess
import tempfile
from pathlib import Path
import http.client
import io
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageFilter, ImageStat, UnidentifiedImageError

BASE = 'https://www.halfclub.com'
MAX_BYTES = 10_000_000



class CollectionError(RuntimeError):
    """Expected transport, source-format or collection failure."""


class MissingDetail(CollectionError):
    """The selected page has no usable long product body."""


class MissingResource(CollectionError):
    """Permanent HTTP 404; site itself removes these gallery slots on error."""


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
    foreground=ImageStat.Stat(im.crop((30,35,170,233)))
    accepted=best>=7 and max(foreground.stddev)>=6
    return accepted,'가장자리 단색 일치 패치 %s/%s (필요7)'%(best,len(patches))


def image_get(ctx, url):
    raw = fetch(ctx, url, MAX_BYTES)
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP'):
                raise CollectionError('지원하지 않는 실제 이미지 형식: ' + str(source.format))
            if getattr(source, 'is_animated', False):
                raise CollectionError('애니메이션 이미지')
            if source.width * source.height > 40_000_000:
                raise CollectionError('이미지 해독 크기 초과')
            source.load()
            return source.copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise CollectionError('손상 또는 이미지가 아닌 응답: ' + url) from exc


def gallery_ok(image):
    w, h = image.size
    if min(w, h) < 500:
        return False, '가로·세로 최소 500px 미달: %sx%s' % (w, h)
    if max(w, h) / min(w, h) > 1.8:
        return False, '통짜/긴 이미지 비율: %sx%s' % (w, h)
    # Multiple full-width/full-height internal white gutters signal a collage.
    im = image.convert('RGB').resize((120, 160))
    runs = []
    for vertical in (False, True):
        blank = []
        length = im.width if vertical else im.height
        for pos in range(int(length * .18), int(length * .82)):
            line = im.crop((pos, 0, pos + 1, im.height) if vertical
                           else (0, pos, im.width, pos + 1))
            stat = ImageStat.Stat(line)
            blank.append(min(stat.mean) > 247 and max(stat.stddev) < 5)
        # 바깥 여백은 사진 분할선이 아니다. 띠의 양쪽에 실제 사진 영역이 있어야 한다.
        runs.append(sum(value and i > 0 and not blank[i - 1] and any(not v for v in blank[i+1:])
                        for i, value in enumerate(blank)))
    if max(runs) >= 2:
        return False, '복수 내부 구분선/콜라주 신호'
    return True, '실제 상품 갤러리 정지 이미지'


OCR_SCRIPT = "\n$ProgressPreference='SilentlyContinue'\n$ErrorActionPreference='Stop'\nAdd-Type -AssemblyName System.Runtime.WindowsRuntime\n$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]\n$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]\n$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]\n$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]\n$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]\n$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]\n$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1})[0]\nfunction Await($Op,$Type) { $task=$asTask.MakeGenericMethod($Type).Invoke($null,@($Op));$task.Wait();$task.Result }\n\nforeach($ImagePath in $ImagePaths) {\n$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])\n$stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])\n$decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])\n$bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])\n$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]\n$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko-KR'))\nif ($null -eq $engine) { throw 'Korean OCR language is not installed' }\n$result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])\n[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)\n$result.Text\n$bitmap.Dispose();$stream.Dispose()\n\n}\n"

SELLER_BANNER = re.compile(
    r'백화점|아울렛|패션아일랜드|DEBEC|대백|모다|매장(?:안내|전화|번호|문의|으로연락|에서(?:출고|발송|판매))|'
    r'배송안내|배송및출고|교환(?:및)?반품안내|교환(?:및)?환불안내|단체구매|상담시간|고객센터(?:안내|운영|상담)|'
    r'구매하기|배송비|택배비|착불|동봉입금|반송상품|반송시|교환반품접수|주문전꼭|주문전필독|매장재고|판매자(?:안내|공지|정보)|판매처(?:안내|공지|정보)', re.I)


def ocr_text(ctx, picture):
    """Read every overlapping image tile locally; never upload product images."""
    width=min(1600,max(picture.width,1200))
    scaled=picture.convert('RGB').resize((width,round(picture.height*width/picture.width)))
    with tempfile.TemporaryDirectory(prefix='benetton-halfclub-ocr-') as folder:
        paths=[]
        for y in range(0,scaled.height,2100):
            ctx.checkpoint()
            path=Path(folder)/(str(y)+'.png')
            scaled.crop((0,y,width,min(y+2300,scaled.height))).save(path)
            paths.append("'"+str(path.resolve()).replace("'","''")+"'")
        script='$ImagePaths=@('+','.join(paths)+')\n'+OCR_SCRIPT
        command=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        process=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+min(120,20+len(paths)*10)
        try:
            while True:
                ctx.checkpoint()
                if time.monotonic()>deadline:raise CollectionError('상세 이미지 OCR 검사 시간 초과')
                try:
                    output,error=process.communicate(timeout=.2)
                    break
                except subprocess.TimeoutExpired:pass
            if process.returncode:raise CollectionError('Windows 한국어 OCR 검사 실행 실패')
        finally:
            if process.poll() is None:process.kill();process.communicate()
    return output



SELLER_BANNER = re.compile(SELLER_BANNER.pattern + r'|구매전참고|주문전마감|구매전필독|문의게시판|문의사항|상담가능|교환.{0,12}반품|INFORMATION',re.I)


def asset_url(raw):
    url=urllib.parse.urljoin('https://cdn2.halfclub.com/',raw)
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('http','https') or p.username or p.password or not (p.hostname in ('cdn2.halfclub.com','ai.esmplus.com','gi.esmplus.com') or p.hostname == 'bnj0995.hgodo.com' and p.path.startswith('/Benetton/')):
        raise CollectionError('미확인 이미지 호스트: '+url)
    return urllib.parse.quote(url,safe=':/%?=&+@')


def page(ctx,number):
    url=BASE+'/product/'+str(number)
    doc=soup_get(ctx,url);node=doc.find('script',id='pdp-data')
    if node is None:raise CollectionError('공개 상품 데이터 없음: '+url)
    data=json.loads(node.get_text());info=data['productInfo']
    if str(info.get('prdNo'))!=str(number):raise CollectionError('상품 번호 불일치')
    if info.get('brandMainNmKr')!='베네통':raise CollectionError('베네통 브랜드 불일치')
    return data


def search(ctx,code):
    numbers=[];offset=0
    while True:
        ctx.checkpoint()
        url='https://cf-hapi.halfclub.com/searches/prdList/?'+urllib.parse.urlencode(dict(keyword=code,siteCd='2',device='pc',limit=str(offset)+',100',sortSeq=12))
        result=json.loads(fetch(ctx,url))['data']['result']
        if result.get('timed_out'):raise CollectionError('공개 검색 시간 초과')
        hits=result['hits'];total=hits['total']
        if isinstance(total,dict):total=total['value']
        entries=hits['hits']
        for hit in entries:
            item=hit['_source']
            if '베네통' not in item.get('brand',''):continue
            if not re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',item.get('prdNm',item.get('prdSearchKeyword','')).upper()):continue
            number=str(hit['_id'])
            if number.isdigit() and number not in numbers:numbers.append(number)
        offset+=len(entries)
        if offset>=int(total):return numbers
        if not entries:raise CollectionError('검색 페이지가 전체 결과 이전에 비어 있음')


def palette(info,include_size):
    groups=info.get('optionItem',[])
    color=next((g for g in groups if g.get('optItemNm') in ('컬러','색상','COLOR')),None)
    size=next((g for g in groups if g.get('optItemNm') in ('사이즈','SIZE')),None)
    if not color:raise CollectionError('공식 색상 옵션 없음')
    result=[]
    for option in color['optValueList']:
        label=option.get('optValueNm','').strip()
        if not label:continue
        item=next((c for c in result if c['color_name']==label),None)
        if item is None:item=dict(color_name=label,color_code='');result.append(item)
        if include_size and size:
            values=[s['optValueNm'] for s in size['optValueList'] if s.get('stockNo')==option.get('stockNo')]
            item['size']=','.join(dict.fromkeys(filter(None,item.get('size','').split(',')+values)))
    if not result:raise CollectionError('공식 색상 후보 없음')
    return result


def banner_text(text):
    return bool(SELLER_BANNER.search(re.sub(r'[\s·ㆍ•/|,:：_-]+','',text)))


def inspected(ctx,url,cache):
    if url not in cache:
        image=image_get(ctx,url)
        # Inspect every tile; notices embedded below a photograph also fail.
        text=ocr_text(ctx,image)
        cache[url]=(None,image,text)
    return cache[url]


def details(ctx,info,cache):
    doc=BeautifulSoup(info.get('productDesc',{}).get('prdDescContClob',''),'html.parser')
    parts=[];seen=set()
    for node in doc.select('img[src]'):
        ctx.checkpoint()
        try:
            url=asset_url(node['src'])
            if url in seen:continue
            seen.add(url)
            raw,image,text=inspected(ctx,url,cache)
        except (CollectionError,OSError,subprocess.SubprocessError) as exc:
            ctx.log('상세 이미지 확인 불가: '+node['src']+' / '+str(exc)+' / 상품 사진 수집 계속','WARNING')
            continue
        if banner_text(text):ctx.log('판매자 안내 이미지 제외: '+url,'INFO');continue
        compact=re.sub(r'\s+','',text)
        # Text documents (size/specification/color chart) are detail-only.
        document=len(compact)>=35
        studio,_=studio_background(image)
        if not studio and image.height/image.width>1.4:
            # Two stacked studio views can have different neutral backgrounds.
            studio=all(studio_background(image.crop((0,image.height*k//2,image.width,image.height*(k+1)//2)))[0] for k in range(2))
        if not document and not studio:ctx.log('비균일 배경 본문 사진 제외: '+url,'INFO');continue
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세"></p>')
    if not parts:ctx.log('채택 가능한 상세 이미지 없음: 상세 빈칸으로 상품 사진 수집 계속','WARNING')
    return '<div>'+''.join(parts)+'</div>' if parts else ''


def positive(value):
    try:return int(value) if int(value)>0 else None
    except (TypeError,ValueError):return None


def crawl(ctx):
    raw=str(ctx.product_code).strip();direct=re.fullmatch(r'(?:https://www\.halfclub\.com/product/)?(\d+)(?:[/?#].*)?',raw)
    code=raw.upper();numbers=[direct[1]] if direct else search(ctx,code)
    cache={};emitted=0;errors=[];pending=[];shared=""
    for number in numbers:
        ctx.checkpoint();ctx.stage('베네통 하프클럽 상품 검사: '+number)
        try:
            data=page(ctx,number);info=data['productInfo'];name=info['prdNm']
            if direct:
                match=re.search(r'(?<![A-Z0-9])([A-Z]{3,}[A-Z0-9]*\d[A-Z0-9]*)(?![A-Z0-9])',name.upper())
                if not match:raise CollectionError('상품명에서 기본 품번 확인 불가')
                base=match[1]
            else:
                base=code
                if not re.search(r'(?<![A-Z0-9])'+re.escape(base)+r'(?![A-Z0-9])',name.upper()):continue
            images=info['productImage'];main=asset_url(images['basicExtNm'])
            _,picture,text=inspected(ctx,main,cache)
            studio,reason=studio_background(picture)
            if not studio or banner_text(text):ctx.log('대표컷 조건 미달 상품 제외: '+number+' / '+reason,'INFO');continue
            colors=palette(info,ctx.enabled('size'))
            shots=[]
            for key,value in images.items():
                if not (key=='basicExtNm' or re.fullmatch(r'add\d+ExtNm',key)) or not value:continue
                url=asset_url(value);_,im,words=inspected(ctx,url,cache)
                good,_=gallery_ok(im);plain,_=studio_background(im)
                if good and plain and not banner_text(words) and url not in shots:shots.append(url)
            if not shots:raise CollectionError('채택 가능한 스튜디오 갤러리 없음')
            clean=re.sub(r'\[[^]]*\]','',name);clean=re.sub(re.escape(base),'',clean,flags=re.I);clean=re.sub(r'베네통|BENETTON','',clean,flags=re.I).strip(' _-')
            record=dict(product_code=base,product_name=clean,product_url=BASE+'/product/'+number)
            if ctx.enabled('size'):record['size']=','.join(dict.fromkeys(s for c in colors for s in c.get('size','').split(',') if s))
            prices=data.get('prductPriceInfo',{})
            if ctx.enabled('price'):record['price']=positive(prices.get('selPrc'))
            if ctx.enabled('regular_price'):
                record['regular_price']=positive(prices.get('normPrc'))
                if record['regular_price'] is None:ctx.log('공식 정상가 없음: 빈칸','WARNING')
            if ctx.enabled('category'):
                category=info.get('dispCtgr',{});record['category']=' > '.join(category[k] for k in ('dispCtgrNm1','dispCtgrNm2','dispCtgrNm3') if category.get(k))
            if ctx.enabled('detail_page') and not shared:shared=details(ctx,info,cache)
            if ctx.enabled('detail_page'):record['detail_page']=shared
            pending.append((record,colors,shots))
        except (CollectionError,ValueError,KeyError,TypeError,OSError) as exc:
            errors.append(number+': '+str(exc));ctx.log(errors[-1],'WARNING')
    for field in ('price','regular_price'):
        if ctx.enabled(field) and pending:
            values=[record.get(field) for record,_,_ in pending]
            common=values[0] if len(set(values))==1 else None
            if common is None:ctx.log('판매처 간 공통 '+field+' 미확정: 빈칸','WARNING')
            for record,_,_ in pending:record[field]=common
    for record,colors,shots in pending:
        if ctx.enabled('detail_page'):record['detail_page']=shared
        for url in shots:
            ctx.emit_unmapped(dict(record),colors,url,shared_detail_html=shared);emitted+=1
    if not emitted:
        message='채택된 베네통 스튜디오컷 상품 없음'+(' / '+' / '.join(errors) if errors else '')
        ctx.fail_item(message);raise CollectionError(message)
    if errors:ctx.log('일부 상품 제외: '+' / '.join(errors),'WARNING')
