PORTAL_API_VERSION = 1

import base64
import hashlib
import html
import http.client
import io
import json
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, unquote
from bs4 import BeautifulSoup, NavigableString
from PIL import Image, ImageStat, UnidentifiedImageError

SITE='https://www.lotteon.com'
CDN='https://contents.lotteon.com'
BRAND='P3782'
MAX_BYTES=15_000_000
class CollectionError(RuntimeError):
    """Public source or validation error."""
class MissingResource(CollectionError):
    """Resource permanently removed."""

OCR_SCRIPT="\n$ProgressPreference='SilentlyContinue'\n$ErrorActionPreference='Stop'\nAdd-Type -AssemblyName System.Runtime.WindowsRuntime\n$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]\n$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]\n$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]\n$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]\n$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]\n$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]\n$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1})[0]\nfunction Await($Op,$Type) { $task=$asTask.MakeGenericMethod($Type).Invoke($null,@($Op));$task.Wait();$task.Result }\n$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])\n$stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])\n$decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])\n$bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])\n$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()\n$result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])\n[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)\n$result.Text\n$bitmap.Dispose();$stream.Dispose()\n"

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
        raise CollectionError('공개 HTTP/HTTPS 주소가 아님: '+url)
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




def json_get(ctx,url):
    data=json.loads(fetch(ctx,url,MAX_BYTES).decode('utf-8-sig'))
    if 'returnCode' in data:
        if str(data['returnCode'])!='200':raise CollectionError('공개 API 오류: '+str(data.get('message')))
        return data['data']
    return data


def search(ctx,code):
    result=[];seen=set();offset=0;total=None
    for _ in range(30):
        data=json_get(ctx,SITE+'/csearch/search/search?'+urlencode({'u2':offset,'u3':60,'u16':'ranking.desc','u37':'true','u39':2,'render':'qapi','platform':'pc','collection_id':9,'q':code,'mallId':1}))
        rows=data.get('itemList');count=data.get('total')
        if not isinstance(rows,list) or not isinstance(count,int):raise CollectionError('검색 목록/전체 개수 형식 변경')
        if total is None:total=count
        if total!=count:raise CollectionError('검색 중 상품 수 변경: 다시 수집 필요')
        fresh=0
        for row in rows:
            key=row.get('key')
            if not key:raise CollectionError('검색 상품 ID 누락')
            if key in seen:continue
            seen.add(key);fresh+=1
            if row.get('brandId')!=BRAND:continue
            if not re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',str(row.get('pdName','')).upper()):continue
            # A search-card emblem is a visible overlay even when the raw
            # product image underneath is clean. Exclude the whole link.
            if row.get('emblemInfo'):
                ctx.log('왼쪽 위 판매/행사 로고 상품 제외: '+row['pdId'],'INFO');continue
            result.append(row)
        offset+=len(rows)
        if offset>=total:return result
        if not rows or not fresh:raise CollectionError('검색 페이지 진행 실패')
    raise CollectionError('검색 페이지 한도 초과')


def image(ctx,url,cache):
    if url not in cache:
        raw=fetch(ctx,url,MAX_BYTES)
        try:
            with Image.open(io.BytesIO(raw)) as pic:
                if pic.format not in ('JPEG','PNG','WEBP') or getattr(pic,'is_animated',False) or pic.width*pic.height>60_000_000:raise CollectionError('지원하지 않는 이미지/크기: '+url)
                pic.load();cache[url]={'raw':raw,'image':pic.convert('RGB'),'digest':hashlib.sha256(raw).hexdigest()}
        except (OSError,UnidentifiedImageError) as exc:raise CollectionError('이미지 해독 실패: '+url) from exc
    return cache[url]


def studio(pic):
    if min(pic.size)<250:return False,'사진 해상도 부족'
    p=pic.resize((160,160));pixels=p.load()
    # Bright uniform outer background, excluding the central garment.
    rim=[pixels[x,y] for y in range(160) for x in range(160) if x<8 or x>=152 or y<8 or y>=152]
    bright=[v for v in rim if min(v)>=220 and max(v)-min(v)<=24]
    if len(bright)/len(rim)<.82:return False,'밝고 균일한 외곽 배경 아님'
    means=[sum(v[c] for v in bright)/len(bright) for c in range(3)]
    uniform=sum(max(abs(v[c]-means[c]) for c in range(3))<=18 for v in rim)/len(rim)
    if uniform<.80:return False,'배경 색상 불균일'
    corner=p.crop((0,0,48,25));cp=corner.load();dark=sum(min(cp[x,y])<180 for y in range(25) for x in range(48))/(48*25)
    if dark>.008:return False,'왼쪽 위 로고/문자/오브젝트 신호'
    return True,'밝은 단색 배경·왼쪽 위 배지 없음'


def ocr(ctx,pic,folder,cache):
    digest=hashlib.sha256(pic.tobytes()).hexdigest()
    if digest in cache:return cache[digest]
    # Windows built-in Korean OCR; local computation, no external upload.
    texts=[];width=min(1200,pic.width);scaled=pic.resize((width,max(1,round(pic.height*width/pic.width))))
    for y in range(0,scaled.height,2100):
        ctx.checkpoint();part=scaled.crop((0,y,width,min(y+2300,scaled.height)))
        path=folder/(digest+'_'+str(y)+'.png');part.save(path)
        script="$ImagePath='"+str(path.resolve()).replace("'","''")+"'\n"+OCR_SCRIPT
        command=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        run=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',command],capture_output=True,text=True,encoding='utf-8',timeout=25,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if run.returncode:raise CollectionError('Windows 한국어 OCR 실행 실패: 배너 검사를 생략하지 않습니다')
        texts.append(run.stdout);path.unlink(missing_ok=True)
    cache[digest]=' '.join(texts);return cache[digest]


BANNER=re.compile(r'신세계\s*라이브\s*쇼[핑평]|내일\s*도착|오늘\s*23\s*시|주문상품.*내일|교환.{0,4}반품.{0,8}안내|배송.{0,4}안내|무단.{0,8}(?:사용|복제)|저작권|정품.{0,4}(?:안내|인증)|구매대행.{0,6}안내',re.I)


def body_markup(ctx,data,cache,ocr_cache,folder):
    parts=[];digests=set();valid_images=0
    entries=[x for x in data.get('descInfo',{}).get('epnJsn',[]) if x.get('pdEpnTypCd')=='DSCRP']
    if not entries:raise CollectionError('판매자 상세 본문 없음')
    for entry in entries:
        model='itemdetail'+entry['dtlFileRteNm']+entry['dtlFileNm']
        url='https://llis-goodsdetail.lotteon.com/goodsdetail?'+urlencode({'model':model})
        soup=BeautifulSoup(fetch(ctx,url,MAX_BYTES),'html.parser');root=soup.select_one('#llis-root')
        if root is None:raise CollectionError('판매자 본문 컨테이너 없음')
        for n in root.select('script,style,iframe,object,form'):n.decompose()
        # Preserve original text, images and existing size/legal tables only.
        # Never read artlInfo or reconstruct size/notice tables from other APIs.
        def render(node):
            nonlocal valid_images
            if isinstance(node,NavigableString):return html.escape(str(node))
            if not getattr(node,'name',None):return ''
            if node.name=='img':
                source=node.get('data-src') or node.get('src') or ''
                if not source:return ''
                imgurl=urljoin(url,source)
                hint=unquote(str(node.get('title',''))+' '+str(node.get('alt',''))+' '+urlsplit(imgurl).path)
                if re.search(r'banner|shipping|delivery|coupon|배송|교환|반품|공지|정품',hint,re.I):
                    ctx.log('안내 이미지 제외: '+hint,'INFO');return ''
                info=image(ctx,imgurl,cache)
                if info['digest'] in digests:return ''
                words=ocr(ctx,info['image'],folder,ocr_cache)
                if BANNER.search(words):
                    notice_count=sum(bool(re.search(term,words)) for term in (r'배송\s*안내',r'교환.{0,4}반품.{0,4}안내',r'고객\s*(?:센터|서비스)',r'구매\s*전'))
                    if info['image'].height>info['image'].width*3 and notice_count<2:raise CollectionError('본문과 안내 배너가 합쳐진 긴 이미지: 자동 전체 삭제 대신 확인 필요 '+imgurl)
                    ctx.log('쇼핑/배송/저작권 배너 제외: '+words[:100],'INFO');return ''
                digests.add(info['digest']);valid_images+=1
                return '<img src="'+html.escape(imgurl,quote=True)+'" alt="상품 상세 본문" style="max-width:100%;height:auto">'
            content=''.join(render(n) for n in node.children)
            if not content.strip():return ''
            if node.name in ('p','div','center','span','b','strong','ul','ol','li','table','thead','tbody','tfoot','tr','th','td','h1','h2','h3','h4','br'):
                if node.name=='br':return '<br>'
                name='div' if node.name=='center' else node.name
                if node.name not in ('table','thead','tbody','tfoot','tr','th','td') and BANNER.search(node.get_text(' ',strip=True)) and not node.find(['img','table']):return ''
                attrs=''
                if name in ('td','th'):
                    for k in ('rowspan','colspan'):
                        val=str(node.get(k,''))
                        if re.fullmatch(r'[1-9]\d?',val):attrs+=' '+k+'="'+val+'"'
                return '<'+name+attrs+'>'+content+'</'+name+'>'
            return content
        parts.append(render(root))
    if not valid_images:raise CollectionError('배너 제외 후 실제 상세 본문 이미지 없음')
    result='<div>'+''.join(parts)+'</div>'
    if len(result.encode('utf-16-le'))//2>32767:raise CollectionError('원문 상세 HTML 엑셀 셀 한도 초과')
    return result


def palette(data):
    options=data.get('optionInfo',{}).get('optionList',[])
    color=next((o for o in options if o.get('title') in ('색상','컬러')),None)
    if color is None:raise CollectionError('공식 색상 후보 없음: 추측하지 않습니다')
    values=[str(o.get('label','')).strip() for o in color.get('options',[])]
    if not values or any(not v for v in values):raise CollectionError('공식 색상 후보 형식 변경')
    return [{'color_name':v,'color_code':''} for v in dict.fromkeys(values)]


def load_product(ctx,row,code):
    sid=row['sitmId'];data=json_get(ctx,'https://pbf.lotteon.com/product/v2/detail/search/base/sitm/'+sid+'?'+urlencode({'sitmNo':sid,'isNotContainOptMapping':'true'}))
    main=data['basicInfo']
    if main.get('pdNo')!=row['pdId'] or main.get('sitmNo')!=sid:raise CollectionError('상품/옵션 ID 불일치')
    if main.get('brdNo')!=BRAND:return None
    if not re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',str(main.get('pdNm','')).upper()):return None
    if not data.get('optionInfo',{}).get('optionList'):
        url='https://pbf.lotteon.com/product/v2/detail/option/mapping/'+row['pdId']+'/'+sid+'?'+urlencode({'trNo':main['trNo'],'trGrpCd':main['trGrpCd'],'lrtrNo':main.get('lrtrNo',''),'pdNo':row['pdId']})
        data['optionInfo']=json_get(ctx,url)['optionInfo']
    return data


def crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'J[A-Z0-9]{8}',code):raise CollectionError('지프 기본 품번 9자리를 입력하세요')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):raise CollectionError('색상 미매핑 버전: 대표 또는 추가이미지를 선택하세요')
    ctx.stage('롯데온 스튜디오컷 검색: '+code)
    cache={};ocr_cache={};records=[];colors=[];photos=[];details=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='jeep-lotteon-') as temp:
        folder=Path(temp)
        for row in search(ctx,code):
            ctx.checkpoint();url=SITE+'/p'+row['pdLink']
            try:
                thumb=urljoin(CDN,row['pdImage']);ok,reason=studio(image(ctx,thumb,cache)['image'])
                if not ok:ctx.log('대표사진 제외: '+row['pdId']+' / '+reason,'INFO');continue
                data=load_product(ctx,row,code)
                if data is None:continue
                if data.get('imgInfo',{}).get('emblInfo'):
                    ctx.log('상세 대표사진 배지 상품 제외: '+row['pdId'],'INFO');continue
                main=data['basicInfo'];choices=palette(data)
                accepted=[]
                for item in data.get('imgInfo',{}).get('imageList',[]):
                    if item.get('epsrTypCd')!='IMG':continue
                    source=item.get('origImgFileNm')
                    if not source:raise CollectionError('실제 원본 상품사진 URL 없음')
                    info=image(ctx,source,cache);ok,why=studio(info['image'])
                    if not ok:ctx.log('추가사진 제외: '+why,'INFO');continue
                    if min(info['image'].size)<500:continue
                    if info['digest'] not in {p['digest'] for p in accepted}:accepted.append({'url':source,'digest':info['digest']})
                if not accepted:ctx.log('기준 통과 원본 상품컷 없음: '+row['pdId'],'WARNING');continue
                name=main['pdNm'];name=re.sub(r'\[[^]]+\]','',name);name=re.sub(r'(?i)지프|\bJEEP\b|'+re.escape(code),'',name);name=re.sub(r'\s+',' ',name).strip(' _-/()')
                record={'product_code':code,'product_name':name or main['pdNm'],'product_url':url,'원본 상품명':main['pdNm']}
                if ctx.enabled('price'):
                    value=data.get('priceInfo',{}).get('slPrc');record['price']=int(value) if isinstance(value,(int,float)) and value>0 else None
                if ctx.enabled('regular_price'):record['regular_price']=None
                if ctx.enabled('size'):
                    opts=[o for o in data['optionInfo']['optionList'] if '사이즈' in o.get('title','')]
                    values=list(dict.fromkeys(str(v['label']) for o in opts for v in o.get('options',[]) if v.get('label')))
                    if not values:raise CollectionError('실제 사이즈 옵션 없음')
                    record['size']=','.join(values)
                if ctx.enabled('category'):record['category']=' > '.join(str(main.get('stdCatLevel'+str(i)+'Nm') or '') for i in range(1,5)).strip(' >')
                markup=body_markup(ctx,data,cache,ocr_cache,folder) if ctx.enabled('detail_page') else ''
                records.append(record);photos.extend(p for p in accepted if p['digest'] not in {x['digest'] for x in photos})
                if markup and markup not in details:details.append(markup)
                for choice in choices:
                    if choice not in colors:colors.append(choice)
            except (CollectionError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
                errors.append(row['pdId']+' / '+str(exc));ctx.fail_item(errors[-1])
        if not records or not photos:
            message='로고 없는 밝은 단색 스튜디오컷 상품 미조회: '+code
            ctx.fail_item(message);raise CollectionError(message)
        out=dict(records[0]);out['원본 상품명']='\n'.join(dict.fromkeys(r['원본 상품명'] for r in records))
        for field in ('price','category'):
            if ctx.enabled(field) and any(r.get(field)!=out.get(field) for r in records):out[field]=None;ctx.log('여러 판매자 공통 '+field+' 값 미확정: 빈칸','WARNING')
        if ctx.enabled('size') and any(r.get('size')!=out.get('size') for r in records):ctx.log('링크별 사이즈가 달라 첫 수집 성공 링크 기준으로 통일','WARNING')
        if ctx.enabled('detail_page'):
            out['detail_page']=''.join(details)
            if len(out['detail_page'].encode('utf-16-le'))//2>32767:raise CollectionError('공통 상세 HTML 엑셀 셀 한도 초과')
        for photo in photos:ctx.emit_unmapped(dict(out),[dict(c) for c in colors],photo['url'],shared_detail_html=out.get('detail_page',''))
        if errors:raise CollectionError('일부 상품 수집 미완료: '+' / '.join(errors))
        ctx.log('상품 '+str(len(records))+'개 / 스튜디오컷 '+str(len(photos))+'장 / 색상 수동 매핑','SUCCESS')
