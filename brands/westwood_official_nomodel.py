PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

"""WESTWOOD official product-only strips -> Crawler Hub local JPEG + editable OCR color mapping. Requires Crawler Hub 0.8.3+."""
import difflib
import ast
import hashlib
import html
import io
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageOps

ORIGIN = 'https://westwoodmall.co.kr'
MAX_DOCUMENT = 3_000_000
MAX_STRIP = 10_000_000
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


class CrawlError(RuntimeError):
    pass


def unique(items):
    return list(dict.fromkeys(x for x in items if x))


def text(node):
    return node.get_text(' ', strip=True) if node else ''


def public_url(value, base=ORIGIN):
    parsed=urlsplit(urljoin(base,str(value).strip()))
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise CrawlError('Invalid public URL')
    return quote(parsed.geturl(),safe=":/?=&%+;,@!~*'()-._")


def request(ctx,url,limit=MAX_DOCUMENT,optional=False):
    url=public_url(url)
    for attempt in range(2):
        ctx.checkpoint();ctx.log('GET '+url,'INFO')
        failure=None;retry=False
        try:
            req=Request(url,headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity'})
            with build_opener().open(req,timeout=25) as response:
                data=bytearray()
                while True:
                    ctx.checkpoint()
                    chunk=response.read(min(65536,limit+1-len(data)))
                    if not chunk:break
                    data.extend(chunk)
                    if len(data)>limit:raise CrawlError('Response exceeds byte limit: '+url)
            result=bytes(data)
        except HTTPError as exc:
            failure='HTTP '+str(exc.code)+': '+url
            if optional and exc.code in (404,410):return None
            retry=exc.code in (408,429,500,502,503,504)
        except (URLError,TimeoutError,OSError) as exc:
            failure='Network error '+str(exc)+': '+url;retry=True
        ctx.checkpoint()
        if failure is None:return result
        if not retry or attempt:raise CrawlError(failure)
        ctx.log(failure,'WARNING')
        for _ in range(10):ctx.checkpoint();time.sleep(.1)
    raise CrawlError('Request incomplete: '+url)


def json_write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def find_products(ctx,code):
    queue=[ORIGIN+'/product/search.html?'+urlencode({'keyword':code})]
    seen=set();links=[]
    while queue:
        ctx.checkpoint();url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=50:raise CrawlError('Search exceeds 50 pages')
        seen.add(url);soup=BeautifulSoup(request(ctx,url),'html.parser')
        for a in soup.select('.prdList .name a[href]'):
            target=public_url(a['href']);p=urlsplit(target)
            if p.hostname not in ('westwoodmall.co.kr','www.westwoodmall.co.kr'):continue
            # Match the official style token, without requiring a brand name.
            if not re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',text(a),re.I):continue
            match=re.search(r'/product/[^/]+/(\d+)/',p.path)
            gid=match[1] if match else (parse_qs(p.query).get('product_no') or [''])[0]
            if gid.isdigit():links.append(ORIGIN+'/product/detail.html?product_no='+gid)
        for a in soup.select('.ec-base-paginate a[href]'):
            if a['href'].startswith(('#','javascript:')):continue
            target=public_url(a['href'],url);p=urlsplit(target);q=parse_qs(p.query)
            page=(q.get('page') or [''])[0]
            if p.hostname in ('westwoodmall.co.kr','www.westwoodmall.co.kr') and q.get('keyword')==[code] and page.isdigit():
                target=ORIGIN+'/product/search.html?'+urlencode({'keyword':code,'page':page})
                if target not in seen:queue.append(target)
    if not links:raise CrawlError('공식 검색에서 정확히 일치하는 품번이 없습니다: '+code)
    return unique(links)


def parse_product(raw,code,url):
    soup=BeautifulSoup(raw,'html.parser')
    name=text(soup.select_one('.headingArea h2')) or text(soup.select_one('.infoArea h2'))
    if not name:
        meta=soup.find('meta',attrs={'property':'og:title'});name=meta.get('content','') if meta else ''
    # The public detail filename itself supplies an independent exact style ID.
    sources=[]
    for img in soup.select('#prdDetail img'):
        value=img.get('ec-data-src') or img.get('data-src') or img.get('src','')
        if not value:continue
        source=public_url(value);p=urlsplit(source)
        if '/westwood/product/' in p.path and re.fullmatch(re.escape(code)+r'(?:-(?:S|SP|SS|D))?\.(?:jpg|jpeg|png)',p.path.rsplit('/',1)[-1],re.I):sources.append(source)
    if not sources:raise CrawlError('공식 상품 상세 이미지 파일명을 확인할 수 없습니다: '+code)
    if not name:raise CrawlError('Product name missing')
    original_name=name
    name=re.sub(r'(?:[_\s-]*)'+re.escape(code)+r'(?![A-Z0-9])','',name,flags=re.I).strip(' _-')
    name=re.sub(r'^\s*(?:\[웨스트우드\]|웨스트우드)(?:\s+|$)','',name).strip()
    colors=[]
    for area in soup.select('[option_title]'):
        if not re.search(r'색상|컬러|color',area.get('option_title',''),re.I):continue
        for option in area.select('option,li[option_value]'):
            if option.get('value') in ('*','**',''):continue
            label=option.get('option_value') or text(option)
            match=re.fullmatch(r'\s*(.*?)\s*\(([A-Za-z0-9]{1,4})\)\s*',label)
            if match:colors.append({'color_name':match[1].strip(),'color_code':match[2]})
            elif label:colors.append({'color_name':label,'color_code':''})
    colors=list({(c['color_name'],c['color_code']):c for c in colors}.values())
    if not colors:raise CrawlError('Official color options missing')
    return {'product_code':code,'product_name':name,'original_name':original_name,'product_url':url,'colors':colors,'sources':unique(sources)}


def decode_strip(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ('JPEG','PNG','WEBP') or getattr(image,'is_animated',False):raise CrawlError('Not a supported static image')
        if image.width*image.height>60_000_000 or image.width<500 or image.height<700:raise CrawlError('Unexpected detail strip dimensions')
        image.load();return ImageOps.exif_transpose(image).convert('RGB')


def segment_strip(image):
    """Split white-background, separate photo blocks above DETAIL VIEW.

    A short, centered text block starts a new color group, even if OCR fails.
    The near-full-width dark divider marks the detail collage/info area.
    """
    working=image.copy();working.thumbnail((1000,100000))
    w,h=working.size;scale=image.width/w
    mask=ImageChops.difference(working,Image.new('RGB',working.size,'white')).convert('L').point(lambda p:255 if p>30 else 0)
    pixels=mask.tobytes();counts=[pixels[y*w:(y+1)*w].count(255) for y in range(h)]
    divider=next((y for y in range(200,max(200,h-20)) if min(counts[y:y+20])>w*.97),None)
    if divider is None:raise CrawlError('DETAIL VIEW divider not found; refusing to include size/info collage as product cuts')
    runs=[];start=None;last=None
    for y,count in enumerate(counts[:divider]):
        if count>max(6,w*.012):
            if start is None:start=y
            last=y
        elif start is not None and y-last>8:runs.append((start,last+1));start=None
    if start is not None:runs.append((start,last+1))
    groups=[];current=None
    for start,end in runs:
        box=mask.crop((0,start,w,end)).getbbox()
        if not box:continue
        left,_,right,_=box;height=end-start;width=right-left
        if 10<=height<=85 and 25<=width<w*.72 and left>w*.12 and right<w*.88:
            current={'header_box':[0,int(max(0,start-25)*scale),image.width,int(min(h,end+25)*scale)],'header_text':'','photos':[]}
            groups.append(current)
        elif height>=140 and width>=w*.25:
            if current is None:
                current={'header_box':None,'header_text':'','photos':[]};groups.append(current)
            pad=7
            crop=[int(max(0,left-pad)*scale),int(max(0,start-pad)*scale),int(min(w,right+pad)*scale),int(min(h,end+pad)*scale)]
            current['photos'].append(crop)
    groups=[g for g in groups if g['photos']]
    if not groups:raise CrawlError('No safely separated product photos')
    return groups,int(divider*scale)


def recognize_headers(ctx,image,groups,work):
    work=Path(work);work.mkdir(parents=True,exist_ok=True);items=[]
    for i,group in enumerate(groups):
        ctx.checkpoint()
        if not group.get('header_box'):continue
        tile=image.crop(group['header_box']);factor=min(2.0,2400/tile.width,3500/tile.height)
        tile=tile.resize((round(tile.width*factor),round(tile.height*factor)),Image.Resampling.LANCZOS)
        path=work/('header_'+str(i)+'.png');tile.save(path)
        items.append({'index':i,'path':str(path.resolve())})
    if not items:return ['No header block']
    if os.name!='nt':return ['Windows OCR unavailable on this OS; saved cuts remain unmapped']
    script=work/'recognize.ps1';script.write_text(POWERSHELL_OCR,encoding='utf-8-sig')
    manifest=work/'ocr_input.json';json_write(manifest,items)
    command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script.resolve()),'-Manifest',str(manifest.resolve())]
    errors=[]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+max(45,len(items)*12)
        try:
            while process.poll() is None:
                ctx.checkpoint()
                if time.monotonic()>deadline:raise CrawlError('Windows OCR timeout')
                time.sleep(.1)
            stdout.seek(0);stderr.seek(0)
            output=stdout.read().decode('utf-8-sig',errors='replace');error=stderr.read().decode('utf-8',errors='replace')
            if process.returncode:raise CrawlError('Windows OCR failed: '+error[:500])
            result=json.loads(output)
            if isinstance(result,dict):result=[result]
            for item in result:
                groups[int(item['index'])]['header_text']=str(item.get('text') or '')
                if item.get('error'):errors.append(item['error'])
        except (CrawlError,ValueError,OSError) as exc:
            errors.append(str(exc))
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=5)
    return errors


def match_color(recognized,colors):
    compact=lambda v:re.sub(r'[^가-힣A-Z0-9]','',v.upper())
    tokens=re.findall(r'[A-Z0-9]{1,4}',recognized.upper());candidates=[]
    korean=''.join(re.findall(r'[가-힣]+',recognized))
    for color in colors:
        code=color['color_code'].upper()
        if not code:continue
        name=''.join(re.findall(r'[가-힣]+',color['color_name']))
        name_score=difflib.SequenceMatcher(None,korean,name).ratio() if name and korean else 0
        if code in tokens:
            # Exact OCR code on a separate centered heading is sufficient.
            candidates.append((.99 if name_score>.7 else .94,color,'exact_code'))
        elif name_score>=.65:
            normalize=lambda value:value.translate(str.maketrans({'I':'1','O':'0'}))
            if any(normalize(token)==normalize(code) for token in tokens):candidates.append((.91,color,'code_alias_and_name'))
    if len(candidates)==1:return {**candidates[0][1],'confidence':candidates[0][0],'reason':candidates[0][2]}
    return {'color_name':'','color_code':'','confidence':0,'reason':'ambiguous' if candidates else 'unrecognized'}


def option_stock(raw):
    source=raw.decode('utf-8') if isinstance(raw,bytes) else raw
    match=re.search(r"\bvar\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")",source)
    if not match:raise CrawlError('공식 색상-사이즈 옵션 데이터가 없습니다.')
    data=json.loads(ast.literal_eval(match[1]))
    if not isinstance(data,dict) or not data:raise CrawlError('공식 옵션 데이터가 비어 있습니다.')
    return list(data.values())


def collect_optional(ctx,raw,colors):
    result={}
    if ctx.enabled('size') or ctx.enabled('price'):
        try:
            rows=option_stock(raw);by_color={c['color_code']:[] for c in colors}
            for row in rows:
                # Keep sold-out/hidden options and their leading-zero size labels.
                values=row.get('option_value_orginal') or row.get('option_value_original')
                names=row.get('option_name_original')
                if not isinstance(values,list) or not isinstance(names,list) or len(values)!=len(names):raise CrawlError('공식 옵션 항목/값 배열 구조가 다릅니다.')
                color_index=next((i for i,n in enumerate(names) if re.search(r'색상|컬러|color',str(n),re.I)),None)
                size_index=next((i for i,n in enumerate(names) if re.search(r'사이즈|size',str(n),re.I)),None)
                if color_index is None:raise CrawlError('색상 옵션 위치를 확인할 수 없습니다.')
                label=str(values[color_index]);match=re.search(r'\(([A-Za-z0-9]{1,4})\)\s*$',label)
                code=match[1] if match else next((c['color_code'] for c in colors if c['color_name']==label),'')
                if code in by_color:by_color[code].append((row,str(values[size_index]) if size_index is not None else None))
            if ctx.enabled('size'):
                sizes=[unique(size for row,size in by_color[c['color_code']] if size not in ('',None)) for c in colors]
                if sizes and all(v and set(v)==set(sizes[0]) for v in sizes):result['size']=','.join(sizes[0])
                else:ctx.log('색상별 사이즈가 다르거나 옵션이 누락되어 공통 사이즈를 확정하지 못했습니다. 임의 합집합으로 출력하지 않습니다.','WARNING')
            if ctx.enabled('price'):
                prices=[]
                if not all(by_color.values()):raise CrawlError('일부 색상의 공식 가격 옵션이 없습니다.')
                for options in by_color.values():
                    for row,size in options:
                        value=row.get('option_price')
                        if isinstance(value,bool) or not re.fullmatch(r'\d+(?:\.0+)?',str(value)):raise CrawlError('공식 옵션 판매가가 숫자가 아닙니다.')
                        prices.append(int(float(value)))
                if prices and len(set(prices))==1:result['price']=prices[0]
                else:ctx.log('옵션별 가격이 달라 공통 판매가를 확정하지 못했습니다.','WARNING')
        except (CrawlError,ValueError,TypeError,SyntaxError) as exc:
            ctx.log('선택 정보 확인: '+str(exc),'WARNING')
    collect_regular_price(ctx, result, raw, 'cafe24')
    if ctx.enabled('category'):
        soup=BeautifulSoup(raw,'html.parser')
        values=unique(text(a) for a in soup.select('.xans-product-headcategory ol li a') if text(a) and text(a) not in ('홈','HOME'))
        if values:result['category']=' > '.join(values)
        else:ctx.log('공식 상품 페이지의 카테고리 경로가 없습니다.','WARNING')
    return result


def crop_for_hub(image,box):
    """Keep the reference crop and white padding, without upscaling garment pixels."""
    cropped=image.crop(box)
    # Trousers are naturally narrow: validate the full garment block, not a 400px short-side assumption.
    if min(cropped.size)<150 or max(cropped.size)<500:
        cropped.close();raise CrawlError('Individual cut too small: '+str(box))
    side=max(500,round(max(cropped.size)*1.08))
    if side>2600:
        cropped.thumbnail((2400,2400),Image.Resampling.LANCZOS);side=2600
    canvas=Image.new('RGB',(side,side),'white')
    canvas.paste(cropped,((side-cropped.width)//2,(side-cropped.height)//2));cropped.close()
    return canvas


SOURCE_NOTICE = '본 상세페이지는 WESTWOOD 본사에서 운영하는 공식 판매처에서 제공하는 상품 페이지입니다. 본 사이트 내 모든 이미지, 문구, 디자인 및 콘텐츠는 저작권법 및 상표법 등 관련 법령에 의해 보호되며, 무단 사용, 복제, 편집, 배포 시 사전 경고 없이 민·형사상 법적 조치를 취할 수 있습니다.'


def product_info_box(image,divider):
    # The official template puts PRODUCT INFO and SIZE together on the final
    # continuous neutral-grey panel. Verify its two headings by OCR below.
    left=round(image.width*.06);right=round(image.width*.94)
    start=None;last=None;runs=[]
    for y in range(divider,image.height):
        a=image.getpixel((left,y));b=image.getpixel((right,y))
        grey=all(230<=v<=251 for v in a+b) and max(a)-min(a)<=5 and max(b)-min(b)<=5 and max(abs(a[i]-b[i]) for i in range(3))<=5
        if grey:
            if start is None:start=y
            last=y
        elif start is not None and y-last>6:
            if last-start>=10:runs.append((start,last+1))
            start=None
    if start is not None and last-start>=10:runs.append((start,last+1))
    joined=[]
    for top,bottom in runs:
        if joined and top-joined[-1][1]<=50:joined[-1]=(joined[-1][0],bottom)
        else:joined.append((top,bottom))
    panels=[box for box in joined if box[1]-box[0]>=350]
    if not panels:raise CrawlError('PRODUCT INFO/SIZE 패널 경계를 확인할 수 없습니다.')
    top,bottom=panels[-1]
    return (0,top,image.width,bottom)


def product_detail_box(image,divider,info_top):
    # Preserve the original DETAIL VIEW heading and the following detail photos.
    # Stop at the last large photographic block before the information panel.
    region=image.crop((0,divider,image.width,info_top))
    working=region.copy();working.thumbnail((1000,100000));w,h=working.size
    scale=region.width/w
    mask=ImageChops.difference(working,Image.new('RGB',working.size,'white')).convert('L').point(lambda p:255 if p>30 else 0)
    pixels=mask.tobytes();runs=[];start=None;last=None
    for y in range(h):
        count=pixels[y*w:(y+1)*w].count(255)
        if count>max(6,w*.012):
            if start is None:start=y
            last=y
        elif start is not None and y-last>8:
            runs.append((start,last+1));start=None
    if start is not None:runs.append((start,last+1))
    photos=[]
    for top,bottom in runs:
        box=mask.crop((0,top,w,bottom)).getbbox()
        if box and bottom-top>=140 and box[2]-box[0]>=w*.25:photos.append((top,bottom))
    region.close();working.close();mask.close()
    if not photos:raise CrawlError('제품 세부이미지의 사진 경계를 확인하지 못했습니다.')
    end=min(info_top,divider+round(max(b for a,b in photos)*scale)+8)
    return (0,divider,image.width,end)


def compose_detail(ctx,image,groups,divider,work):
    headers=[g['header_box'] for g in groups if g.get('header_box')]
    photos=[box for g in groups for box in g['photos']]
    if not headers or not photos:raise CrawlError('색상명과 마지막 상품사진 경계가 없습니다.')
    # A single full-width crop preserves color-label/photo interleaving exactly.
    gallery=(0,min(b[1] for b in headers),image.width,max(b[3] for b in photos))
    info=product_info_box(image,divider)
    check=[dict(header_box=list(info),header_text='',photos=[])]
    errors=recognize_headers(ctx,image,check,Path(work)/'detail_panel_check')
    normalized=re.sub(r'[^A-Z]','',check[0]['header_text'].upper().replace('0','O').replace('1','I'))
    if not errors and ('PRODUCTINFO' not in normalized or 'SIZE' not in normalized):
        raise CrawlError('선택한 패널에서 PRODUCT INFO와 SIZE 표제를 확인하지 못했습니다.')
    if errors:ctx.log('상세 표제 OCR 미확정: 공식 회색 정보 패널 구조를 기준으로 구성합니다.','WARNING')
    product_details=product_detail_box(image,divider,info[1])
    parts=[image.crop(box) for box in (gallery,product_details,info)]
    merged=Image.new('RGB',(image.width,sum(part.height for part in parts)+48),'white')
    y=0
    for part in parts:
        merged.paste(part,(0,y));y+=part.height+24;part.close()
    return merged,dict(color_gallery=list(gallery),product_details=list(product_details),product_info_size=list(info))


def crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]{7,20}',code):raise CrawlError('기본 품번을 입력하세요. 예: WO3MTJW211')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        raise CrawlError('이미지 저장 유형은 대표이미지 또는 추가이미지 선택이 필요합니다.')
    ctx.stage('웨스트우드 공식 상품 검색')
    products=[];failures=[]
    try:links=find_products(ctx,code)
    except (CrawlError,OSError,ValueError) as exc:
        ctx.fail_item('공식 품번 검색 실패: '+str(exc));raise
    for url in links:
        ctx.checkpoint()
        try:
            raw=request(ctx,url);product=parse_product(raw,code,url)
            product['optional']=collect_optional(ctx,raw,product['colors']);products.append(product)
        except (CrawlError,ValueError,OSError) as exc:
            failures.append(url+' / '+str(exc));ctx.log(str(exc),'ERROR')
    if not products:
        ctx.fail_item('정확한 공식 상품 정보를 확인할 수 없습니다.');raise CrawlError('No valid exact product')
    colors=list({(c['color_name'],c['color_code']):c for p in products for c in p['colors']}.values())
    declared=unique(u for p in products for u in p['sources'])
    # The source crawler verified this CDN's -S model/original product pair.
    # 2026 pages declare -SP/-SS intro/model strips and a -D product-only strip.
    # Only actual declared -D or legacy bare sources are candidates; never import -SP/-SS/-S.
    # A legacy -S -> bare pair still requires the independent heading/divider structural check.
    candidates=unique(u for u in declared if re.fullmatch(re.escape(code)+r'(?:-D)?\.(?:jpg|jpeg|png)',urlsplit(u).path.rsplit('/',1)[-1],re.I))
    candidates=unique(candidates+[
        re.sub(r'-S(?=\.(?:jpg|jpeg|png)$)','',u,flags=re.I)
        for u in declared if re.search(r'-S\.(?:jpg|jpeg|png)$',urlsplit(u).path,re.I)
    ])
    saved=[];detail_sources=[];seen_strips=set();seen_cuts=set();detail_errors=[];detail_image_path=None;detail_regions=None;detail_source=''
    work=Path(ctx.output_dir)/'_westwood_ocr'/str(ctx.task_id)
    for source_index,source in enumerate(candidates):
        ctx.checkpoint();ctx.stage('모델컷 제외 제품 통이미지 확인 · 개별 컷/OCR')
        image=None
        try:
            payload=request(ctx,source,MAX_STRIP,optional=True)
            if payload is None:continue
            digest=hashlib.sha256(payload).hexdigest()
            if digest in seen_strips:continue
            image=decode_strip(payload);groups,divider=segment_strip(image)
            if not groups[0].get('header_box'):
                raise CrawlError('첫 상품 블록에 색상 제목이 없어 모델 이미지로 오인할 수 있습니다. 해당 통이미지는 제외합니다.')
            seen_strips.add(digest);detail_sources.append(source)
            try:ocr_errors=recognize_headers(ctx,image,groups,work/('strip_'+str(source_index)))
            except (CrawlError,OSError,ValueError) as exc:ocr_errors=[str(exc)]
            for error in ocr_errors:ctx.log('OCR 미확정 · 사진은 수동 매핑으로 유지: '+error,'WARNING')
            for group in groups:
                matched=match_color(group.get('header_text',''),colors)
                mapped={k:matched[k] for k in ('color_name','color_code')} if matched['color_code'] and matched['confidence']>=.90 else None
                evidence=('Windows OCR: '+group['header_text']+' / '+matched['reason']+' / '+source) if mapped else ''
                for box in group['photos']:
                    ctx.checkpoint()
                    try:
                        canvas=crop_for_hub(image,box)
                    except CrawlError as exc:
                        # A small gallery fragment must not discard the source strip
                        # or prevent its independent, full-width detail composition.
                        ctx.log('대표/추가이미지 후보만 제외: '+str(exc),'INFO')
                        continue
                    try:path=ctx.save_image(canvas)
                    finally:canvas.close()
                    if path.name in seen_cuts:continue
                    seen_cuts.add(path.name)
                    saved.append(dict(path=path,mapped=mapped,evidence=evidence,ocr_text=group.get('header_text',''),source=source,box=box))
                    ctx.log('개별 컷 저장 · '+(mapped['color_name']+' ('+mapped['color_code']+')' if mapped else '색상 미확정 · 웹에서 선택'),'SUCCESS' if mapped else 'INFO')
            if ctx.enabled('detail_page') and detail_image_path is None:
                merged,detail_regions=compose_detail(ctx,image,groups,divider,work/('strip_'+str(source_index)))
                try:detail_image_path=ctx.save_detail_image(merged,source_metadata=SOURCE_NOTICE+'\nSource: '+source)
                finally:merged.close()
                detail_source=source
                ctx.log('공통 상세이미지 저장: 색상명/상품사진 → 제품 세부이미지 → PRODUCT INFO/SIZE','SUCCESS')
        except (CrawlError,OSError,ValueError) as exc:
            detail_errors.append(str(exc));ctx.log(source+' 제외: '+str(exc),'WARNING')
        finally:
            if image is not None:image.close()
    if not saved:
        ctx.fail_item('모델컷 제외 조건과 이미지 규격을 통과한 개별 상품 컷이 없습니다.')
        raise CrawlError('No individual product cuts')
    if ctx.enabled('detail_page') and detail_image_path is None:
        reason='공통 상세이미지 생성/저장 실패: '+(' / '.join(detail_errors[-3:]) or '상세 결과 없음')
        ctx.fail_item(reason);raise CrawlError(reason)
    common={}
    for field in ('size','price','regular_price','category'):
        if not ctx.enabled(field):continue
        values=[product['optional'].get(field) for product in products]
        if values and values[0] not in (None,'') and all(value==values[0] for value in values):common[field]=values[0]
        else:ctx.log('상품 링크 간 공통 '+{'size':'사이즈','price':'판매가','regular_price':'정상가','category':'카테고리'}[field]+' 정보가 없거나 서로 달라 빈 값으로 유지합니다.','WARNING')
    for item in saved:
        ctx.checkpoint()
        record=dict(product_code=code,product_name=products[0]['product_name'],product_url=products[0]['product_url'])
        record.update(common)
        record['원본 상품명']='\n'.join(unique(p['original_name'] for p in products))
        record['OCR 텍스트']=item['ocr_text'];record['크롭 원본 이미지']=item['source']
        record['크롭 좌표']=json.dumps(item['box'],ensure_ascii=False)
        if ctx.enabled('detail_page'):
            record['상세 원본 이미지']=detail_source;record['상세 영역 좌표']=json.dumps(detail_regions,ensure_ascii=False)
            record['원본 권리안내']=SOURCE_NOTICE
        ctx.emit_local_image(record,colors,item['path'],mapped_color=item['mapped'],mapping_evidence=item['evidence'],detail_image_path=detail_image_path)
    if failures:ctx.fail_item('일부 공식 상품 링크 수집 실패: '+' / '.join(failures))
    ctx.log(code+': '+str(len(saved))+'개 제품 컷을 허브에 전달했습니다. OCR 결과는 웹에서 확인·수정하세요.','SUCCESS')
