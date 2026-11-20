PORTAL_API_VERSION = 1

"""패션플러스 품목번호 → 통짜 상세 이미지 크롭 + 사람 색상 매핑 (로컬 JPEG). '패션플러스 크롭필요건'.

- 패션플러스 중 상세가 긴 통짜 이미지인 상품용. 일반 상품은 기존 fashionplus_item(URL 방식)을 쓴다.
- 상품·옵션·상세 조회는 팀이 실사이트로 운영 확인한 fashionplus_item 로직을 그대로 사용(품목번호 직접 조회, 검색 없음).
- 갤러리 사진은 통째로, 상세 통짜 이미지는 흰 여백·가는 흰 선 경계로만 개별 컷을 자른다(고정 좌표·등분·확대 없음).
  500px에 못 미치는 컷은 주변 실제 배경(흰 여백)만 포함해 넓힐 수 있을 때만 쓰고, 이웃 사진이 들어오면 제외한다.
- 색상명 OCR(Windows 기본 OCR): 공식 옵션명을 정답지로 정확/음역/유사 대조, 한 색상만 맞을 때 그 글자에 붙은 컷만 사전 매핑.
  패션플러스 통짜 이미지는 색상명이 사진 '아래'에 붙는 경우가 확인되어 LABEL_POSITION='below'. 나머지는 사람이 매핑.
- 상세 HTML은 전 색상 공통: 패션플러스 공식 상세 본문(공통 배송·행사·브랜드 배너 제외).
"""
import difflib
import html
import http.client
import io
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString
from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError

from detail_store import excel_detail

REVISION = '2026-11-20-fashionplus-crop-3'
BASE = 'https://www.fashionplus.co.kr'
MAX_BYTES = 10_000_000
MAX_IMAGE = 30_000_000
MAX_OCR_TILES = 40
# 색상명 위치: 사진 위·아래 중 가장 가까운 글자(2026-11-20, 레드페이스와 같은 규칙). 한 색상명은 가장 가까운 사진 하나에만.
LABEL_POSITION = 'nearest'
# 흰 여백 보충(2026-11-20 사용자 승인, 이 브랜드만): 배경이 순백색인 상세 통이미지에서 500px 미만 컷은
# ① 주변 실제 흰 배경으로 먼저 넓히고 ② 그래도 모자란 만큼만 같은 흰색으로 채운다. 상품 화소는 그대로.
# 채우는 쪽 가장자리가 실제 배경일 때만, 한 방향 보충은 최종 길이의 40% 이하.
PAD_MIN_WHITE = 245
PAD_MAX_SHARE = .4

SIZE_TOKEN=r'(?:FREE|FR|F|ONE\s*SIZE|XS|S|M|L|XL|XXL|XXXL|[2-6]XL|\d{2,3}[A-H]|\d{2,3}(?:MM|CM)?|[CJM]\d+(?:W\d+)?)'

class CollectionError(RuntimeError):pass


class MissingResource(CollectionError):pass


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


def source_url(node):
    if node is None:
        return ''
    for key in ('data-preload', 'data-lazyload'):
        match = re.search(r"['_\"]*src['\"]\s*:\s*['\"]([^'\"]+)",
                          html.unescape(node.get(key, '')))
        if match:
            return urllib.parse.urljoin(BASE, match.group(1))
    src = node.get('data-src') or node.get('ec-data-src') or node.get('src', '')
    return urllib.parse.urljoin(BASE, src) if src and not src.startswith('data:') else ''


def money(value):
    text = str(value or '').strip()
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)\s*원?', text):
        return None
    number = int(re.sub(r'\D', '', text))
    return number if number > 0 else None


def read_price(soup, regular=False):
    for row in soup.select('.discount-detail__row'):
        title, value = row.find('dt'), row.find('dd')
        if not title or not value:
            continue
        label = title.get_text(' ', strip=True)
        if ('희망소비자가' in label if regular else label.startswith('판매가')):
            return money(value.get_text(strip=True))
    return None


def color_parts(value):
    value = value.strip()
    match = re.fullmatch(r'\((\d+)\)\s*(.*)', value)
    if not match:
        match = re.fullmatch(r'(\d{2})([^\d].*)', value)
    if match:
        return {'color_name': match[2].strip(), 'color_code': match[1]}
    return {'color_name': value, 'color_code': ''}


def item_number(raw):
    raw=html.unescape(str(raw).strip())
    if raw.lower().startswith(('http://','https://')):
        p=urllib.parse.urlsplit(raw)
        match=re.fullmatch(r'/goods/detail/(\d+)/?',p.path)
        if p.hostname not in ('www.fashionplus.co.kr','fashionplus.co.kr') or p.username or p.password or not match:raise CollectionError('패션플러스 상품 링크만 입력할 수 있습니다.')
        return match[1]
    if not re.fullmatch(r'\d+',raw):raise CollectionError('패션플러스 품목 번호(숫자) 또는 상품 링크를 입력해주세요.')
    return raw


def product_info(doc,number):
    title=doc.select_one('.m__product-info .text_product')
    if not title or not title.get_text(strip=True):raise CollectionError('상품이 없거나 상세 응답이 올바르지 않습니다: '+number)
    canonical=doc.select_one('meta[property="og:url"]')
    if canonical and item_number(canonical.get('content',''))!=number:raise CollectionError('요청 품목 번호와 실제 상품이 다릅니다.')
    brand_node=doc.select_one('.m_prodetail-head .mm_breadcrumb a[href*="brand-shop"]')
    brand=brand_node.get_text(' ',strip=True).strip(' >') if brand_node else ''
    name=title.get_text(' ',strip=True)
    record=dict(product_code=number,product_name=name,product_url=BASE+'/goods/detail/'+number,**{'패션플러스 품목번호':number,'브랜드':brand})
    provided={};seller='';seller_number=''
    for table in doc.select('table'):
        section=table.find_parent('section');heading=section.find(['h4','h5']) if section else None
        is_notice=bool(heading and '제공고시' in heading.get_text().replace(' ',''))
        for row in table.select('tr'):
            th,td=row.find('th'),row.find('td')
            if not th or not td:continue
            key=th.get_text(' ',strip=True);value=td.get_text(' ',strip=True)
            if is_notice:provided[key]=value
            if key=='업체명' and not seller:seller=value
            if key=='상품번호' and not seller_number:seller_number=value
    structured_skus=[]
    def product_nodes(value):
        if isinstance(value,list):
            for entry in value:yield from product_nodes(entry)
        elif isinstance(value,dict):
            kind=value.get('@type',[]);kind=[kind] if isinstance(kind,str) else kind
            if 'Product' in kind:yield value
            if '@graph' in value:yield from product_nodes(value['@graph'])
    for script in doc.select('script[type="application/ld+json"]'):
        try:data=json.loads(script.string or script.get_text())
        except (ValueError,TypeError):continue
        for product in product_nodes(data):
            if product.get('mpn') is not None and str(product['mpn'])!=number:continue
            sku=product.get('sku')
            if isinstance(sku,(str,int)) and str(sku).strip():structured_skus.append(str(sku).strip())
    structured_skus=list(dict.fromkeys(structured_skus))
    if len(structured_skus)>1 or seller_number and structured_skus and seller_number!=structured_skus[0]:raise CollectionError('상품번호(SKU) 원문 불일치: '+number)
    sku=seller_number or (structured_skus[0] if structured_skus else '')
    if not sku:raise CollectionError('상품번호(SKU) 미표시: '+number+' / 상품명에서 추정하지 않습니다.')
    record.update(product_code=sku,**{'상위품목ID':number})
    record.update({'판매자':seller,'판매자 상품번호':seller_number,'상품정보 원문':'\n'.join(k+': '+v for k,v in provided.items())})
    return record


def read_options(ctx,link):
    try:payload=json.loads(fetch(ctx,link+'/fetch-option-data').decode('utf-8-sig'))
    except (ValueError,UnicodeError) as exc:raise CollectionError('옵션 JSON 응답 해석 실패') from exc
    if not isinstance(payload,list):raise CollectionError('공식 옵션 응답 형식 변경')
    leaves=[]
    def visit(node,parents):
        if not isinstance(node,dict):raise CollectionError('옵션 항목 구조 변경')
        children=node.get('options');name=str(node.get('_name') or '').strip()
        if children is not None and not isinstance(children,list):raise CollectionError('옵션 하위 구조 변경')
        if children:
            for child in children:visit(child,parents+([name] if name else []))
        elif name:leaves.append(dict(label=' / '.join(parents+[name]),price=node.get('_price'),stock=node.get('_stock'),option_id=node.get('_id')))
    for group in payload:
        if not isinstance(group,dict):raise CollectionError('상품 옵션 그룹 구조 변경')
        if 'options' not in group:raise CollectionError('공식 옵션 목록 필드 누락')
        for option in group.get('options') or []:visit(option,[])
    palette=[];sizes=[]
    for row in leaves:
        label=row['label'];match=re.fullmatch(r'(.+?)[\s/]+('+SIZE_TOKEN+r')',label,re.I)
        if match:
            color_label=match[1].strip();size=match[2].strip()
            if size not in sizes:sizes.append(size)
            if color_label.upper() in ('사이즈','SIZE'):color_label='공통'
        elif re.fullmatch(SIZE_TOKEN,label,re.I):
            color_label='공통'
            if label not in sizes:sizes.append(label)
        else:color_label=label
        candidate=color_parts(color_label)
        if candidate not in palette:palette.append(candidate)
    if not palette:palette=[dict(color_name='옵션 미표시',color_code='')]
    return palette,sizes,leaves


def image_info(ctx,url,cache,detail=False):
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:raise CollectionError('공개 이미지 URL 확인 실패: '+url)
    key=(url,detail)
    if key not in cache:
        prior=cache.get((url,not detail))
        if isinstance(prior,MissingResource):raise prior
        if isinstance(prior,dict) and (detail or len(prior['data'])<=10000000):
            cache[key]=prior;return prior
        try:data=fetch(ctx,url,limit=64*1024*1024 if detail else 10000000)
        except MissingResource as exc:cache[key]=exc;raise
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt=im.format;width,height=im.size;im.verify()
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError) as exc:raise CollectionError('상품 이미지 해독 실패: '+url) from exc
        cache[key]=dict(data=data,width=width,height=height,format=fmt)
    if isinstance(cache[key],MissingResource):raise cache[key]
    return cache[key]


def common_banner(url,node):
    path=urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    context=path+' '+str(node.get('alt') or '')+' '+str(node.get('title') or '')
    return bool(re.search(r'/mall/(?:event|md_image)/|/brand_info/|/(?:notice|delivery|shipping|banner|coupon)/',path,re.I) or re.search(r'delivery|shipping|refund|copyright|(?:^|[/_-])banner|배송|교환.?반품|저작권',context,re.I))


def detail_document(ctx,link,cache):
    key=('detail-document',link)
    if key not in cache:cache[key]=soup_get(ctx,link+'/description-iframe')
    area=cache[key].select_one('.m_prodetail-desc .mm_pinchzoom-wrapper')
    if area is None:raise CollectionError('상품 상세 본문 영역 확인 실패')
    return area


def detail(ctx,link,cache):
    area=detail_document(ctx,link,cache)
    seen=set()
    allowed={'div','p','span','br','strong','b','em','i','h1','h2','h3','h4','h5','ul','ol','li','table','thead','tbody','tfoot','tr','th','td','caption','a'}
    blocked={'script','style','iframe','object','embed','form','input','button','link','meta','noscript'}
    def render(node):
        if isinstance(node,Comment):return ''
        if isinstance(node,NavigableString):return html.escape(str(node))
        if node.name in blocked:return ''
        if node.name=='img':
            url=source_url(node)
            if not url or common_banner(url,node) or url in seen:return ''
            seen.add(url)
            try:image=image_info(ctx,url,cache,True)
            except MissingResource:
                ctx.log('공식 상세 원본 404 파일 제외: '+url,'WARNING');return ''
            if min(image['width'],image['height'])<=1:return ''
            return '<img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="display:block;width:100%;height:auto;margin:0 auto">'
        children=''.join(render(child) for child in node.children)
        if node.name not in allowed or node.name in ('a','span','div'):return children
        if node.name=='br':return '<br>'
        # Source image galleries use ul/li with site-only list-reset CSS.
        # Export blocks instead so WING cannot reintroduce bullet markers.
        if node.name=='ul':return '<div style="margin:0;padding:0">'+children+'</div>' if children.strip() else ''
        if node.name=='li':
            if not children.strip():return ''
            if not node.parent or node.parent.name!='ol':return '<div style="margin:0;padding:0">'+children+'</div>'
        attrs=''
        if node.name=='table':attrs=' style="width:100%;border-collapse:collapse"'
        if node.name in ('td','th'):
            attrs=' style="border:1px solid #ddd;padding:6px"'
            for key in ('rowspan','colspan'):
                value=str(node.get(key) or '')
                if value.isdigit() and 0<int(value)<=100:attrs+=' '+key+'="'+value+'"'
        return '<'+node.name+attrs+'>'+children+'</'+node.name+'>'
    markup=''.join(render(n) for n in area.children)
    check=BeautifulSoup(markup,'html.parser')
    if not check.select('img') and not check.get_text(strip=True):raise CollectionError('안내 배너 제외 후 상품 상세 본문 없음')
    return '<div style="width:100%;max-width:780px;margin:0 auto;text-align:center">'+markup+'</div>'


def preserve_text(record,label,value):
    # Excel cells hold 32,767 UTF-16 units. Preserve long raw options across
    # additional columns instead of letting openpyxl silently truncate them.
    chunks=[];part=[];units=0
    for char in value:
        width=2 if ord(char)>0xffff else 1
        if units+width>28000:
            chunks.append(''.join(part));part=[];units=0
        part.append(char);units+=width
    chunks.append(''.join(part))
    for index,text in enumerate(chunks,1):record[label if index==1 else label+' ('+str(index)+')']=text

POWERSHELL_OCR = r'''
param([string]$Manifest)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$method=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'})[0]
function Await($operation,$type){$task=$method.MakeGenericMethod($type).Invoke($null,@($operation));$task.Wait();return $task.Result}
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if($null -eq $engine){throw 'Windows OCR language unavailable. Install Korean or English OCR language in Windows Settings.'}
$items=Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8|ConvertFrom-Json
$results=@(foreach($item in $items){
    $stream=$null;$bitmap=$null
    try {
        $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync([string]$item.path)) ([Windows.Storage.StorageFile])
        $stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        @{index=$item.index;text=$result.Text;error=''}
    } catch { @{index=$item.index;text='';error=$_.Exception.Message} }
    finally {if($null -ne $stream){$stream.Dispose()};if($null -ne $bitmap){$bitmap.Dispose()}}
})
ConvertTo-Json -InputObject $results -Depth 5 -Compress
'''
TRANSLIT = {'블랙': 'BLACK', '화이트': 'WHITE', '아이보리': 'IVORY', '카키': 'KHAKI', '네이비': 'NAVY', '베이지': 'BEIGE', '그레이': 'GRAY',
            '차콜': 'CHARCOAL', '브라운': 'BROWN', '레드': 'RED', '블루': 'BLUE', '그린': 'GREEN', '핑크': 'PINK', '퍼플': 'PURPLE',
            '옐로우': 'YELLOW', '오렌지': 'ORANGE', '민트': 'MINT', '카멜': 'CAMEL', '와인': 'WINE', '올리브': 'OLIVE', '크림': 'CREAM'}

def unique(items):
    return list(dict.fromkeys(x for x in items if x))


def load_image(ctx, url, cache):
    if url in cache:
        return None if cache[url] is True else cache[url]  # True = 이미 잘라서 닫은 원본
    try:
        payload = request(ctx, url, MAX_IMAGE, optional=True, site_only=False, timeout=15)
    except CrawlError as exc:
        ctx.log('이미지 다운로드 실패로 이 사진만 제외: ' + str(exc), 'WARNING')
        payload = None
    image = None
    if payload:
        try:
            with Image.open(io.BytesIO(payload)) as opened:
                if opened.format not in ('JPEG', 'PNG', 'WEBP') or getattr(opened, 'is_animated', False) or getattr(opened, 'n_frames', 1) > 1:
                    ctx.log('정지 JPEG/PNG/WebP가 아니어서 제외: ' + url, 'INFO')
                elif opened.width * opened.height > 60_000_000:
                    ctx.log('픽셀 수 초과로 제외: ' + url, 'INFO')
                else:
                    opened.load()
                    image = ImageOps.exif_transpose(opened).convert('RGB')
        except (OSError, ValueError) as exc:
            ctx.log('이미지 해독 실패로 제외(' + str(exc)[:60] + '): ' + url, 'INFO')
    cache[url] = image
    return image


def background(image):
    """원본 맨 위 줄이 한 가지 색이면 그 색을 배경으로 본다. 아니면 흰색."""
    row = image.crop((0, 0, image.width, 1)).resize((32, 1))
    raw = row.tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    if max(max(p[i] for p in pixels) - min(p[i] for p in pixels) for i in range(3)) <= 8 and min(sum(p) for p in pixels) >= 600:
        return tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))
    return (255, 255, 255)


def runs_of(counts, threshold, gap):
    out, start, last = [], None, None
    for i, count in enumerate(counts):
        if count > threshold:
            if start is None:
                start = i
            last = i
        elif start is not None and i - last > gap:
            out.append((start, last + 1))
            start = None
    if start is not None:
        out.append((start, last + 1))
    return out


def photo_like(mask, box):
    """내용 밀도: 사진은 배경 아닌 화소가 촘촘하고, 글자·표·실측선 도식은 듬성하다(근사 판정)."""
    counts = row_counts(mask, box)
    return sum(counts) / max(1, (box[2] - box[0]) * (box[3] - box[1]))


def row_counts(mask, box):
    """box 영역의 행별 내용(배경 아닌) 화소 수. C 수준 bytes.count로 계산."""
    piece = mask.crop(box)
    w, h = piece.size
    raw = piece.tobytes()
    piece.close()
    return [raw[y * w:(y + 1) * w].count(255) for y in range(h)]


def col_counts(mask, box):
    piece = mask.crop(box).transpose(Image.Transpose.TRANSPOSE)
    w, h = piece.size
    raw = piece.tobytes()
    piece.close()
    return [raw[y * w:(y + 1) * w].count(255) for y in range(h)]


def split_seams(mask, box, s, depth=0):
    """사진끼리 여백 없이 붙어 있어도 사이의 '거의 흰 가는 선'(행/열 전체가 배경)에서 나눈다. 원본 해상도 기준."""
    l, t, r, b = box
    if depth > 4:
        return [box]
    rows = row_counts(mask, box)
    parts = [(t + a, t + z) for a, z in runs_of(rows, max(1, (r - l) * .03), 0) if z - a >= 60 * s]
    if len(parts) > 1:
        return [x for a, z in parts for x in split_seams(mask, (l, a, r, z), s, depth + 1)]
    cols = col_counts(mask, box)
    parts = [(l + a, l + z) for a, z in runs_of(cols, max(1, (b - t) * .03), 0) if z - a >= 60 * s]
    if len(parts) > 1:
        return [x for a, z in parts for x in split_seams(mask, (a, t, z, b), s, depth + 1)]
    return [box]


def solid_background(image):
    """맨 위 줄이 한 가지 밝은 색이면 그 색, 아니면 None(흰 여백 보충 불가)."""
    row = image.crop((0, 0, image.width, 1)).resize((32, 1))
    raw = row.tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    if max(max(p[i] for p in pixels) - min(p[i] for p in pixels) for i in range(3)) > 8:
        return None
    color = tuple(sum(p[i] for p in pixels) // len(pixels) for i in range(3))
    return color if min(color) >= PAD_MIN_WHITE else None


def clean_reach(mask, box, side, limit, margin):
    """box의 한 변 바깥으로 내용 없이(실제 배경) 넓힐 수 있는 길이(최대 limit). 글자·이웃 사진 앞 margin은 남긴다."""
    l, t, r, b = box
    width, height = mask.size
    if limit <= 0:
        return 0
    if side == 'top':
        strip = (l, max(0, t - limit - margin), r, t)
    elif side == 'bottom':
        strip = (l, b, r, min(height, b + limit + margin))
    elif side == 'left':
        strip = (max(0, l - limit - margin), t, l, b)
    else:
        strip = (r, t, min(width, r + limit + margin), b)
    if strip[2] <= strip[0] or strip[3] <= strip[1]:
        return 0
    inner = mask.crop(strip).getbbox()
    span = (strip[3] - strip[1]) if side in ('top', 'bottom') else (strip[2] - strip[0])
    if inner is None:
        return min(limit, span)
    if side == 'top':
        free = span - inner[3]
    elif side == 'bottom':
        free = inner[1]
    elif side == 'left':
        free = span - inner[2]
    else:
        free = inner[0]
    return max(0, min(limit, free - margin))


def pad_fit(mask, box, size, s):
    """fit_min이 실패한 컷: 실제 배경으로 최대한 넓힌 뒤 모자란 만큼 흰 여백 보충 계획.
    반환 (실제 잘라낼 box, (왼, 위, 오른, 아래) 보충 px) 또는 None."""
    width, height = size
    l, t, r, b = box
    margin = max(4, int(6 * s))
    pads = [0, 0, 0, 0]
    for axis in ('h', 'v'):
        span = (r - l) if axis == 'h' else (b - t)
        need = 500 - span
        if need <= 0:
            continue
        lo, hi = ('left', 'right') if axis == 'h' else ('top', 'bottom')
        cur = (l, t, r, b)
        reach_lo, reach_hi = clean_reach(mask, cur, lo, need, margin), clean_reach(mask, cur, hi, need, margin)
        ext_lo = min(reach_lo, need // 2)
        ext_hi = min(reach_hi, need - ext_lo)
        ext_lo = min(reach_lo, need - ext_hi)
        rest = need - ext_lo - ext_hi
        if axis == 'h':
            l, r = l - ext_lo, r + ext_hi
        else:
            t, b = t - ext_lo, b + ext_hi
        if rest:
            first, second = rest // 2, rest - rest // 2
            if max(first, second) > 500 * PAD_MAX_SHARE:
                return None
            if axis == 'h':
                pads[0], pads[2] = first, second
            else:
                pads[1], pads[3] = first, second
    real = (l, t, r, b)
    edges = []
    if pads[0]:
        edges.append((l, t, min(r, l + 3), b))
    if pads[2]:
        edges.append((max(l, r - 3), t, r, b))
    if pads[1]:
        edges.append((l, t, r, min(b, t + 3)))
    if pads[3]:
        edges.append((l, max(t, b - 3), r, b))
    for edge in edges:  # 보충하는 쪽 가장자리가 사진·글자에 닿아 있으면(배경이 아니면) 이음매가 생기므로 불가
        if mask.crop(edge).getbbox() is not None:
            return None
    if not white_extension(mask, tuple(box), real):
        return None
    final_w, final_h = r - l + pads[0] + pads[2], b - t + pads[1] + pads[3]
    if min(final_w, final_h) < 500 or max(final_w, final_h) / min(final_w, final_h) > 1.8:
        return None
    return real, tuple(pads)


def white_extension(mask, inner, outer):
    """outer로 넓힌 부분이 실제 배경(내용 2% 미만)인지. 이웃 사진을 끌어오지 않기 위한 검사."""
    total = sum(row_counts(mask, outer))
    own = sum(row_counts(mask, inner))
    area = (outer[2] - outer[0]) * (outer[3] - outer[1]) - (inner[2] - inner[0]) * (inner[3] - inner[1])
    if area <= 0:
        return True
    extra = total - own
    if extra > area * .002:
        return False  # 넓힌 부분에 글자·이웃 사진 등 내용이 있으면 불가
    # 넓힌 가장자리 줄에 글자가 걸치는지(전체 비율은 작아도 한 줄에 몰린 내용)도 확인
    for edge in ((outer[0], outer[1], outer[2], inner[1]), (outer[0], inner[3], outer[2], outer[3]),
                 (outer[0], inner[1], inner[0], inner[3]), (inner[2], inner[1], outer[2], inner[3])):
        if edge[2] > edge[0] and edge[3] > edge[1] and max(row_counts(mask, edge) or [0]) > max(3, (edge[2] - edge[0]) * .02):
            return False
    return True


def fit_ratio(mask, box, size):
    """비율 1.8 초과 컷은 실제 배경 화소로만 짧은 변을 넓힌다(가짜 여백·확대 없음). 불가능하면 None."""
    width, height = size
    l, t, r, b = box
    bw, bh = r - l, b - t
    if bh / bw > 1.8:
        need = int(bh / 1.8) + 1
        if need > width:
            return None
        nl = max(0, min(l - (need - bw) // 2, width - need))
        out = (nl, t, nl + need, b)
    elif bw / bh > 1.8:
        need = int(bw / 1.8) + 1
        if need > height:
            return None
        nt = max(0, min(t - (need - bh) // 2, height - need))
        out = (l, nt, r, nt + need)
    else:
        return box
    return out if white_extension(mask, box, out) else None


def segment(image):
    """원본 해상도에서
    ① 전체 폭 배경 여백(가로 띠)으로 블록을 나누고 ② 블록 안 세로 여백으로 나란한 사진을 나누고
    ③ 여백 없이 붙은 사진은 사이의 가는 흰 선에서 다시 나눈다.
    낮은 글자 띠는 색상명 후보로 모으고, 띠 안의 단어 덩어리(열)별로 위치를 기록한다.
    반환: (photos=[(box, (띠번호, 단어번호)|None)], labels=[(띠 box, [단어 box...])])"""
    width, height = image.size
    s = width / 1000
    solid = solid_background(image)
    bg = Image.new('RGB', image.size, background(image))
    mask = ImageChops.difference(image, bg).convert('L').point(lambda p: 255 if p > 24 else 0)
    bg.close()
    events, labels = [], []
    try:
        rows = row_counts(mask, (0, 0, width, height))
        for top, bottom in runs_of(rows, max(4, width * .01), int(10 * s)):
            whole = mask.crop((0, top, width, bottom)).getbbox()
            if not whole:
                continue
            if bottom - top < 120 * s:
                if 8 * s <= bottom - top and width * .03 <= whole[2] - whole[0] <= width * .85:
                    pad = int(12 * s)
                    band = (0, max(0, top - pad), width, min(height, bottom + pad))
                    words = [(max(0, a - pad), band[1], min(width, z + pad), band[3])
                             for a, z in runs_of(col_counts(mask, (0, top, width, bottom)), 0, int(15 * s))]
                    labels.append((band, words))
                    events.append(('label', len(labels) - 1))
                continue
            found = False
            for left, right in runs_of(col_counts(mask, (0, top, width, bottom)), 1, int(10 * s)):
                if right - left < width * .2:
                    continue
                for piece in split_seams(mask, (left, top, right, bottom), s):
                    inner = mask.crop(piece).getbbox()
                    if not inner:
                        continue
                    box = (piece[0] + inner[0], piece[1] + inner[1], piece[0] + inner[2], piece[1] + inner[3])
                    if box[3] - box[1] < 120 * s or photo_like(mask, box) < .22:
                        continue  # 글자·표·실측선 도식처럼 듬성한 영역
                    pad = int(8 * s)
                    padded = (max(piece[0], box[0] - pad), max(piece[1], box[1] - pad), min(piece[2], box[2] + pad), min(piece[3], box[3] + pad))
                    events.append(('photo', padded))
                    found = True
            if not found:
                events.append(('reset', None))  # 사진이 아닌 큰 영역(실측표 등)에서 색상명의 범위를 끊는다
        # 색상명 → 사진 연결: 사진마다 위·아래로 가장 가까운 글자 띠(사진 아닌 큰 영역은 넘지 않음)
        photos = []
        for i, (kind, value) in enumerate(events):
            if kind != 'photo':
                continue
            near = []
            for step in (-1, 1):
                j = i + step
                while 0 <= j < len(events) and events[j][0] != 'reset':
                    if events[j][0] == 'label':
                        band = labels[events[j][1]][0]
                        near.append((max(0, value[1] - band[3] if step < 0 else band[1] - value[3]), step, events[j][1]))
                        break
                    j += step
            near.sort()
            photos.append((value, near[0][2] if near else None, near[0][0] if near else None))
        out = []
        for box, label, gap in photos:
            fitted = fit_ratio(mask, box, image.size)
            if fitted is None:
                continue
            pads = None
            if min(fitted[2] - fitted[0], fitted[3] - fitted[1]) < 500:
                ratio_box = fitted
                fitted = fit_min(mask, ratio_box, image.size)  # 실제 배경으로만 넓힘(확대 없음)
                if fitted is not None and max(fitted[2] - fitted[0], fitted[3] - fitted[1]) / min(fitted[2] - fitted[0], fitted[3] - fitted[1]) > 1.8:
                    fitted = None
                if fitted is None and solid is not None:
                    plan = pad_fit(mask, ratio_box, image.size, s)  # 실제 흰 배경 + 모자란 만큼 같은 흰색 보충
                    if plan is not None:
                        fitted, pads = plan
                        if not any(pads):
                            pads = None  # 실제 흰 배경만으로 충분(한쪽으로 치우쳐 넓힘)
                if fitted is None:
                    continue
            key = None
            if label is not None:
                words = labels[label][1]
                if len(words) == 1:
                    key = (label, 0)
                else:  # 여러 색상명이 한 줄에 나란히 있으면 사진 바로 위(가로 위치가 겹치는) 단어만
                    overlaps = [max(0, min(fitted[2], w[2]) - max(fitted[0], w[0])) for w in words]
                    best = max(range(len(words)), key=lambda k: overlaps[k])
                    if overlaps[best] > 0 and sorted(overlaps)[-2:][0] < overlaps[best]:
                        key = (label, best)
            out.append([tuple(int(v) for v in fitted), key, pads and (solid, pads), gap])
        # 한 색상명(단어)은 가장 가까운 사진 하나에만: 위쪽 그림·배너가 아래 사진의 색상명을 같이 받지 않게
        best = {}
        for entry in out:
            if entry[1] is not None and (entry[1] not in best or entry[3] < best[entry[1]][3]):
                best[entry[1]] = entry
        for entry in out:
            if entry[1] is not None and best[entry[1]] is not entry:
                entry[1] = None
        return [(box, key, pad) for box, key, pad, _ in out], labels
    finally:
        mask.close()


def ocr_tiles(ctx, tiles, work):
    """Windows 기본 OCR로 글자 띠들을 한 번에 읽는다(상품당 PowerShell 1회). 윈도우가 아니거나 실패하면 빈 결과(→ 미매핑)."""
    if not tiles:
        return {}
    if os.name != 'nt':
        ctx.log('Windows가 아니어서 색상명 OCR을 건너뜁니다 → 사진은 수동 매핑', 'INFO')
        return {}
    if len(tiles) > MAX_OCR_TILES:
        ctx.log('색상명 후보 띠가 ' + str(len(tiles)) + '개로 많아 앞 ' + str(MAX_OCR_TILES) + '개만 OCR합니다(나머지 컷은 수동 매핑)', 'WARNING')
        tiles = tiles[:MAX_OCR_TILES]
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    items = []
    for i, tile in enumerate(tiles):
        ctx.checkpoint()
        factor = min(2.0, 2400 / tile.width, 3500 / max(1, tile.height))
        big = tile.resize((max(1, round(tile.width * factor)), max(1, round(tile.height * factor))), Image.Resampling.LANCZOS)
        path = work / ('label_' + str(i) + '.png')
        big.save(path)
        big.close()
        items.append({'index': i, 'path': str(path.resolve())})
    script = work / 'recognize.ps1'
    script.write_text(POWERSHELL_OCR, encoding='utf-8-sig')
    manifest = work / 'ocr_input.json'
    manifest.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script.resolve()), '-Manifest', str(manifest.resolve())]
    texts = {}
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + min(120, 40 + len(items) * 2)
        try:
            while process.poll() is None:
                ctx.checkpoint()
                if time.monotonic() > deadline:
                    raise CrawlError('Windows OCR 시간 초과')
                time.sleep(.1)
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read().decode('utf-8-sig', errors='replace')
            if process.returncode:
                raise CrawlError('Windows OCR 실패: ' + stderr.read().decode('utf-8', errors='replace')[:300])
            result = json.loads(output)
            for item in result if isinstance(result, list) else [result]:
                texts[int(item['index'])] = str(item.get('text') or '')
                if item.get('error'):
                    ctx.log('OCR 오류(해당 띠 미매핑): ' + str(item['error'])[:200], 'WARNING')
        except (CrawlError, ValueError, OSError, KeyError) as exc:
            ctx.log(str(exc) + ' → 사진은 수동 매핑', 'WARNING')
            return {}
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
    return texts


def match_color(recognized, colors):
    """OCR 글자를 공식 색상 옵션(정답지)과 대조. 정확 일치 우선, 없으면 오인식 1~2글자 수준의 유사 일치(0.8 이상).
    정확히 한 색상만 남을 때만 그 색상, 아니면 (None, 사유)."""
    upper = recognized.upper()
    tokens = set(re.findall(r'[A-Z0-9]+', upper))
    words = set(re.findall(r'[가-힣]+', recognized))
    words |= {re.sub(r'(컬러|색상|색)$', '', w) for w in words}
    exact, near = [], []
    for color in colors:
        name, code = color['color_name'].strip(), color['color_code'].strip().upper()
        reasons = []
        if code and code in tokens:
            reasons.append('코드 ' + code)
        if name and (name in words or name.upper() in tokens):
            reasons.append('이름 ' + name)
        elif name in TRANSLIT and TRANSLIT[name] in tokens:
            reasons.append('영문 표기 ' + TRANSLIT[name] + '=' + name)
        if reasons:
            exact.append((color, ', '.join(reasons)))
            continue
        targets = [t for t in ([name.upper()] if re.fullmatch(r'[A-Z0-9 ]+', name.upper()) else []) + ([TRANSLIT[name]] if name in TRANSLIT else []) if len(t) >= 4]
        best = (0.0, '')
        for target in targets:
            for token in tokens:
                if len(token) >= 3:
                    score = difflib.SequenceMatcher(None, token, target).ratio()
                    best = max(best, (score, token + '≈' + target))
        for word in words:
            if len(name) >= 2 and re.fullmatch(r'[가-힣]+', name) and len(word) >= 2:
                score = difflib.SequenceMatcher(None, word, name).ratio()
                best = max(best, (score, word + '≈' + name))
        if best[0] >= .8:
            near.append((color, best))
    if len(exact) == 1:
        return exact[0][0], exact[0][1]
    if exact:
        return None, '여러 색상 동시 일치'
    near.sort(key=lambda x: -x[1][0])
    if len(near) == 1 or (len(near) > 1 and near[0][1][0] - near[1][1][0] >= .1):
        color, (score, how) = near[0]
        return color, '유사 일치 ' + how + ' (' + format(score, '.2f') + ')'
    return None, ('유사 색상 여러 개' if near else '')


def save_cut(ctx, image, box, seen, pad=None):
    try:
        if pad:
            color, (pl, pt, pr, pb) = pad
            piece = image.crop(tuple(box))
            canvas = Image.new('RGB', (piece.width + pl + pr, piece.height + pt + pb), color)
            canvas.paste(piece, (pl, pt))
            piece.close()
            try:
                path = ctx.save_image(canvas)
            finally:
                canvas.close()
        else:
            path = ctx.save_image(image, crop=box)
    except ValueError as exc:
        ctx.log('저장 규격 미달로 제외: ' + str(exc), 'INFO')
        return None
    if path.name in seen:
        return None
    seen.add(path.name)
    return path


def cuts_from(ctx, url, image, seen, tiles=None, split=False):
    """한 원본에서 개별 컷 목록 [(path, box, 글자 타일 번호|None)].
    갤러리 사진(split=False)은 통째로. 공식 상세 이미지(split=True)는 여백·가는 흰 선 경계로 나눠 2컷 이상이면 그 컷들,
    아니면 원본이 개별 컷 규격일 때만 통째. 색상명 후보 글자는 tiles에 모아 나중에 한 번에 OCR."""
    w, h = image.size
    whole_ok = min(w, h) >= 500 and max(w, h) / min(w, h) <= 1.8
    photos, labels = (segment(image) if split else ([], []))
    photos = [(box, key, pad) for box, key, pad in photos]
    if len(photos) < 2:
        if whole_ok:
            path = save_cut(ctx, image, None, seen)
            return [(path, None, None, None)] if path else []
        if split and not photos:
            ctx.log('여백 경계로 안전하게 나눌 수 있는 개별 컷이 없어 제외: ' + url, 'INFO')
        if not photos:
            return []
    keys = {}
    if tiles is not None:
        for key in sorted({key for _, key, _ in photos if key is not None}):
            keys[key] = len(tiles)
            label, word = key
            tiles.append((image.crop(tuple(labels[label][1][word])), url, labels[label][1][word]))
    out = []
    for box, key, pad in photos:
        ctx.checkpoint()
        path = save_cut(ctx, image, list(box), seen, pad)
        if path:
            out.append((path, list(box), keys.get(key), pad[1] if pad else None))
    return out


def release(cache, image):
    for key, value in cache.items():
        if value is image:
            cache[key] = True
    image.close()


class CrawlError(CollectionError):
    pass


def request(ctx, url, limit=MAX_IMAGE, optional=False, site_only=False, timeout=15):
    """이미지 요청: 패션플러스 공개 요청(fetch)으로 받는다. 404는 그 파일만 제외."""
    try:
        return fetch(ctx, url, limit=limit)
    except MissingResource:
        if optional:
            ctx.log('원본 404 파일 제외: ' + url, 'WARNING')
            return None
        raise
    except CollectionError as exc:
        raise CrawlError(str(exc)) from exc


def original(url):
    """패션플러스 이미지의 리사이즈 파라미터(RS·AR)를 빼고 원본 크기로. 통짜 이미지가 왜곡되지 않게."""
    p = urllib.parse.urlsplit(url)
    if p.hostname == 'img.fashionplus.co.kr':
        query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k.upper() not in ('RS', 'AR')]
        p = p._replace(scheme='https', query=urllib.parse.urlencode(query))
    return urllib.parse.urlunsplit(p)


def clean_title(name, sku):
    out = re.sub(r'^\s*(\[[^\]]*\]\s*)+', '', name)
    for code in {sku, re.sub(r'_[A-Za-z0-9]{1,4}$', '', sku)}:
        if code:
            out = re.sub(r'(?<![A-Za-z0-9])' + re.escape(code) + r'(?:_[A-Za-z0-9]{1,4})?(?![A-Za-z0-9])', ' ', out, flags=re.I)
    return re.sub(r'\s+', ' ', out).strip(' -_/') or name


def sizes_by_color(leaves):
    out = {}
    for row in leaves:
        label = row['label']
        match = re.fullmatch(r'(.+?)[\s/]+(' + SIZE_TOKEN + r')', label, re.I)
        if match:
            out.setdefault(match[1].strip(), [])
            if match[2].strip() not in out[match[1].strip()]:
                out[match[1].strip()].append(match[2].strip())
    return out


def fit_min(mask, box, size):
    """500px 미만 변을 주변 '실제 배경'으로만 넓힌다(확대·가짜 여백 없음). 이웃 내용이 들어오면 None."""
    width, height = size
    l, t, r, b = box
    if r - l < 500:
        if width < 500:
            return None
        nl = max(0, min(l - (500 - (r - l)) // 2, width - 500))
        l, r = nl, nl + 500
    if b - t < 500:
        if height < 500:
            return None
        nt = max(0, min(t - (500 - (b - t)) // 2, height - 500))
        t, b = nt, nt + 500
    out = (l, t, r, b)
    if out == tuple(box):
        return out
    return out if white_extension(mask, tuple(box), out) else None




FULL_CATEGORY=re.compile(r"fullCategory\s*:\s*'([^'\n]*)'")
LEVEL_CATEGORY=re.compile(r"'([123])(?:st|nd|rd)_category_name'\s*:\s*'([^'\n]*)'")


def read_category(doc):
    """상품 분류: 페이지 스크립트의 공식 fullCategory('아웃도어/레저>등산잡화>장갑') → 1~3차 category_name → 분류 링크.
    값이 없으면 None(브랜드를 분류로 대체하지 않음)."""
    source=str(doc)
    found=FULL_CATEGORY.search(source)
    parts=[p.strip() for p in html.unescape(found[1]).split('>') if p.strip()] if found else []
    if not parts:
        levels={}
        for level,name in LEVEL_CATEGORY.findall(source):
            if name.strip() and level not in levels:levels[level]=html.unescape(name.strip())
        parts=[levels[k] for k in ('1','2','3') if k in levels]
    if not parts:
        parts=[n.get_text(' ',strip=True) for n in doc.select('.mm_breadcrumb a[href*="/goods/category/"]')]
    return ' > '.join(parts) or None


def crawl(ctx):
    try:
        if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
            raise CollectionError('이미지 저장 유형은 대표이미지 또는 추가이미지 선택이 필요합니다.')
        number = item_number(ctx.product_code)
        link = BASE + '/goods/detail/' + number
        cache, img_cache, seen, pending, tiles = {}, {}, set(), [], []
        ctx.log('패션플러스 크롭필요건 코드 버전: ' + REVISION, 'INFO')
        ctx.stage('패션플러스 품목번호 직접 조회: ' + number)
        doc = soup_get(ctx, link)
        record = product_info(doc, number)
        sku = record['product_code']
        record['원본 상품명'] = record['product_name']
        record['product_name'] = clean_title(record['product_name'], sku)
        palette, sizes, leaves = read_options(ctx, link)
        palette = [c for c in palette if (c['color_name'] or c['color_code']) not in ('공통', '옵션 미표시')]
        if not palette:
            raise CollectionError('공식 색상 옵션이 없어 색상 후보를 만들 수 없습니다(임의 색상 생성 안 함)')
        preserve_text(record, '원본 옵션', json.dumps(leaves, ensure_ascii=False))
        if ctx.enabled('size'):
            per = list(sizes_by_color(leaves).values())
            if per and all(p == per[0] for p in per):
                record['size'] = ','.join(per[0])
            else:
                ctx.log('색상별 사이즈가 달라 공통 사이즈를 확정하지 않습니다(합집합 출력 안 함).', 'WARNING')
        if ctx.enabled('price'):
            record['price'] = read_price(doc)
        if ctx.enabled('regular_price'):
            ctx.checkpoint()
            record['regular_price'] = read_price(doc, True)
            if record['regular_price'] is None:
                ctx.log('희망소비자가(독립 정상가) 없음: 빈칸, 나머지 수집 계속', 'WARNING')
        if ctx.enabled('category'):
            record['category'] = read_category(doc)
            if not record['category']:
                ctx.log('공식 상품 분류(fullCategory) 없음: 카테고리 빈칸, 나머지 수집 계속', 'WARNING')
        gallery_urls = []
        for node in doc.select('.m_prodetail-head-thumbnail .btn_thumbnail [data-preload],.m_prodetail-head-thumbnail .btn_thumbnail img'):
            url = source_url(node)
            if url and original(url) not in gallery_urls:
                gallery_urls.append(original(url))
        detail_urls = []
        for node in detail_document(ctx, link, cache).select('img'):
            url = source_url(node)
            if url and not common_banner(url, node) and original(url) not in detail_urls:
                detail_urls.append(original(url))
        sources = unique(gallery_urls + detail_urls)
        work = Path(ctx.output_dir) / '_fashionplus_crop_ocr' / str(ctx.task_id)
        for n, url in enumerate(sources, 1):
            ctx.stage('이미지 ' + str(n) + '/' + str(len(sources)) + ' 다운로드·컷 저장')
            image = load_image(ctx, url, img_cache)
            if image is None:
                continue
            split = url in detail_urls and url not in gallery_urls[:1]
            for path, box, key, pads in cuts_from(ctx, url, image, seen, tiles, split=split):
                pending.append([path, None, '', url, box, key, pads])
            release(img_cache, image)
        if not pending:
            raise CollectionError('규격(양변 500px·비율 1.8 이하)을 통과한 개별 상품 컷이 없습니다.')
        if tiles:
            ctx.stage('색상명 글자 OCR (' + str(len(tiles)) + '개, 1회 실행)')
            texts = ocr_tiles(ctx, [t for t, _, _ in tiles], work)
            decided = {}
            for key, (tile, source, ybox) in enumerate(tiles):
                tile.close()
                if key not in texts:
                    continue
                color, why = match_color(texts[key], palette)
                if color:
                    decided[key] = (color, 'Windows OCR "' + re.sub(r'\s+', ' ', texts[key]).strip()[:60] + '" → ' + why +
                                    ' / 사진에 붙은 글자 y=' + str(ybox[1]) + '~' + str(ybox[3]) + ' / ' + source)
                else:
                    ctx.log('색상명 OCR "' + texts[key].strip()[:40] + '" ' + (why or '공식 색상과 불일치') + ' → 수동 매핑', 'INFO')
            for entry in pending:
                if entry[5] in decided:
                    entry[1], entry[2] = decided[entry[5]]
        if ctx.enabled('detail_page'):
            compact, overflow = excel_detail(detail(ctx, link, cache))
            if overflow:
                raise CollectionError('상세 HTML이 엑셀 한 셀 한도를 넘습니다(임의로 자르지 않음)')
            record['detail_page'] = compact
        for path, mapped, evidence, source, box, _, pads in pending:
            ctx.checkpoint()
            row = dict(record)
            row['크롭 원본 이미지'] = source
            row['크롭 좌표'] = json.dumps(box) if box else ''
            row['흰 여백 보충'] = ('왼 %d·위 %d·오른 %d·아래 %dpx (원본 배경 흰색, 상품 화소 그대로)' % pads) if pads else ''
            row['OCR 근거'] = evidence
            ctx.emit_local_image(row, palette, path, mapped_color=mapped, mapping_evidence=evidence,
                                 shared_detail_html=record.get('detail_page', ''))
        ctx.log(number + ': 개별 컷 ' + str(len(pending)) + '장 전달 (사전 매핑 ' + str(sum(1 for p in pending if p[1])) + '장). 색상 매핑하기에서 분류하세요.', 'SUCCESS')
    except (CollectionError, ValueError, KeyError) as exc:
        ctx.fail_item(str(exc))
        raise
