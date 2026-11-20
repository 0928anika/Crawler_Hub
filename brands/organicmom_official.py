PORTAL_API_VERSION = 1
"""오가닉맘 오가닉맘몰 공식 크롤러: 공식 옵션과 상품 상세 원본 수집."""
import ast
import base64,subprocess,tempfile,time
from pathlib import Path
import hashlib
import html
import json
import re
from urllib.parse import quote, urlencode, urljoin, urlsplit, parse_qs, unquote
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from bs4 import BeautifulSoup
from crawler_rules import check_public_image

SITE='https://organicmommall.co.kr'
HOSTS=('organicmommall.co.kr','www.organicmommall.co.kr')
REVISION='2026-11-20-organicmom-v2'
IMAGE_HOSTS=('organicmommall.co.kr','www.organicmommall.co.kr','cafe24.poxo.com','cafe24img.poxo.com')

class CrawlError(ValueError):pass

def fetch(ctx,url):
    url=quote(url,safe=":/?=&%+;,@!~*'()-._")
    if urlsplit(url).hostname not in HOSTS:raise CrawlError('공식몰 외부 페이지 요청')
    for attempt in range(3):
        ctx.checkpoint()
        try:
            with build_opener().open(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as response:
                if urlsplit(response.geturl()).hostname not in HOSTS:raise CrawlError('공식몰 외부 이동')
                data=response.read(8_000_001)
            if len(data)>8_000_000:raise CrawlError('페이지 응답 용량 초과')
            return data.decode('utf-8-sig')
        except HTTPError as exc:
            if exc.code not in (500,502,503,504) or attempt==2:raise
        except (URLError,TimeoutError,ConnectionError):
            if attempt==2:raise
        ctx.log('공식 페이지 일시 오류 재시도: '+url,'WARNING')

def soup(source):return BeautifulSoup(source,'html.parser')

def field(doc,label):
    for row in doc.select('.infoArea tr, .xans-product-detaildesign tr'):
        key,value=row.find('th'),row.find('td')
        if key and value and key.get_text(' ',strip=True)==label and not value.find('select'):
            return value.get_text(' ',strip=True)
    return ''

def search(ctx,code):
    queue=[SITE+'/product/search.html?'+urlencode({'keyword':code})];seen=set();links=[]
    while queue:
        url=queue.pop(0)
        if url in seen:continue
        if len(seen)>=30:raise CrawlError('검색 30페이지 한도 초과')
        seen.add(url);doc=soup(fetch(ctx,url))
        scope=doc.select_one('.xans-search-result')
        if scope is None:raise CrawlError('공식 검색 결과 영역 없음')
        for node in scope.select('.prdList .name a[href], .name a[href]'):
            link=urljoin(SITE,node['href']);p=urlsplit(link)
            if p.hostname in HOSTS and p.path.startswith('/product/') and link not in links:links.append(link)
        for node in doc.select('.xans-search-paging a[href]'):
            link=urljoin(url,node['href']);p=urlsplit(link);q=parse_qs(p.query)
            if p.hostname in HOSTS and q.get('keyword')==[code] and q.get('page'):
                page=q['page'][0]
                if not page.isdigit():continue
                link=SITE+'/product/search.html?'+urlencode({'keyword':code,'page':str(int(page))})
                if link not in seen and link not in queue:queue.append(link)
    return links

def identity(doc,code):
    heading=doc.select_one('.infoArea h1, .infoArea h2, .headingArea h1, .headingArea h2')
    raw=heading.get_text(' ',strip=True) if heading else field(doc,'상품명')
    if not re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',raw,re.I):return None
    name=re.sub(r'\[[^\]]*\]', '', raw)
    for token in [code,'오가닉맘','ORGANICMOM','ORGANIC MOM']:
        name=re.sub(r'(?<![A-Z0-9])'+re.escape(token)+r'(?![A-Z0-9])','',name,flags=re.I)
    name=re.sub(r'\s+',' ',name).strip(' -_/')
    if not name:raise CrawlError('정리한 상품명 없음')
    return name,raw


def stock_rows(source):
    match=re.search(r"\bvar\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*')",source)
    if not match:raise CrawlError('공식 원본 옵션 재고 데이터 없음')
    try:data=json.loads(ast.literal_eval(match[1]))
    except (ValueError,SyntaxError,TypeError) as exc:raise CrawlError('공식 옵션 JSON 해석 실패') from exc
    if not isinstance(data,dict) or not data:raise CrawlError('공식 옵션 후보 없음')
    return list(data.values())

def color_label(label):
    match=re.fullmatch(r'(.+?)\s*\(([A-Z0-9]{2,4})\)',label,re.I)
    if match:return dict(color_name=match[1].strip(),color_code=match[2].upper())
    match=re.fullmatch(r'\(([A-Z0-9]{2,4})\)\s*(.+)',label,re.I)
    if match:return dict(color_name=match[2].strip(),color_code=match[1].upper())
    return dict(color_name=label,color_code='')

def palette(source, fallback_color=''):
    colors={}
    for row in stock_rows(source):
        names=row.get('option_name_original');values=row.get('option_value_orginal') or row.get('option_value_original')
        if not isinstance(names,list) or not isinstance(values,list) or len(names)!=len(values):raise CrawlError('공식 옵션 이름/값 구조 변경')
        opts=dict(zip(names,values));color=str(opts.get('색상') or opts.get('컬러') or '').strip();size=str(opts.get('사이즈') or '').strip()
        color=color or fallback_color
        if not color:raise CrawlError('공식 색상명 확인 실패')
        entry=colors.setdefault(color,dict(**color_label(color),sizes=[],prices=[]))
        if size not in entry['sizes']:entry['sizes'].append(size)
        price=row.get('option_price')
        if not isinstance(price,(int,float)) or price<=0:raise CrawlError('옵션 판매가 형식 확인 필요')
        entry['prices'].append(price)
    return list(colors.values())

def public_image_url(base,raw):
    return quote(urljoin(base,str(raw or '').strip()),safe=":/?=&%+;,@!~*'()-._")

def gallery_urls(doc,final):
    urls=[]
    for node in doc.select('.imgArea img, meta[property="og:image"]'):
        url=public_image_url(final,node.get('ec-data-src') or node.get('data-src') or node.get('src') or node.get('content'))
        p=urlsplit(url)
        # Only URLs explicitly supplied by the official gallery, no guessed
        # small->big substitutions. Validate real dimensions after downloading.
        if p.hostname in IMAGE_HOSTS and re.search(r'/web/product/(?:big/|extra/(?:small|big|medium)/)',p.path) and url not in urls:urls.append(url)
    # Prefer a declared big image over its same-file small rendition.
    urls.sort(key=lambda u: 0 if '/big/' in u else 1 if '/medium/' in u else 2)
    unique=[];seen=set()
    for url in urls:
        key=re.sub(r'/web/product/(?:extra/)?(?:big|medium|small)/','/web/product/',urlsplit(url).path)
        if key not in seen:seen.add(key);unique.append(url)
    return unique

def detail_urls(doc,final,code):
    area=doc.select_one('#prdDetail')
    if area is None:raise CrawlError('공식 상품 상세 영역 없음')
    urls=[]
    for node in area.select('img'):
        url=public_image_url(final,node.get('ec-data-src') or node.get('data-src') or node.get('src'))
        p=urlsplit(url);path=unquote(p.path);filename=path.rsplit('/',1)[-1]
        if p.hostname!='kkm2524.diskn.com':continue
        if path.lower().endswith('.gif'):continue
        # Preserve only model-specific originals and published size charts.
        product=bool(re.fullmatch(re.escape(code)+r'(?:[_-][A-Z0-9가-힣]+)*\.(?:jpe?g|png|webp)',filename,re.I))
        chart=filename.lower() in ('babychart.png','kidschart.png','juniorchart.png')
        if not product and not chart:continue
        if url not in urls:urls.append(url)
    if not urls:raise CrawlError('공식 품번 상세 이미지 없음')
    return urls

def checked(ctx,url,cache,detail=False):
    key=(url,detail)
    if key not in cache:cache[key]=check_public_image(url,timeout=25,retries=2,max_bytes=64*1024*1024 if detail else 10_000_000,checkpoint=ctx.checkpoint)
    result=cache[key]
    if result['status']!='accessible':raise CrawlError('이미지 조회 미완료: '+url+' / '+', '.join(result.get('reasons',[])))
    if result.get('format') not in ('JPEG','PNG','WEBP'):raise CrawlError('지원하지 않는 이미지 형식: '+url)
    if not detail and (min(result['width'],result['height'])<500 or max(result['width'],result['height'])/min(result['width'],result['height'])>1.8):raise CrawlError('갤러리 이미지 크기/비율 기준 미달: '+url)
    return result

def gallery(ctx,doc,final,cache):
    photos=[];seen=set()
    for url in gallery_urls(doc,final):
        try:result=checked(ctx,url,cache)
        except CrawlError as exc:ctx.log(str(exc),'WARNING');continue
        digest=hashlib.sha256(result['data']).hexdigest()
        if digest not in seen:seen.add(digest);photos.append(url)
    if not photos:raise CrawlError('유효 공식 갤러리 이미지 없음')
    return photos

def detail(ctx,doc,final,code,cache,photos=()):
    parts=[]
    for url in detail_urls(doc,final,code):
        checked(ctx,url,cache,True)
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세정보"></p>')
    table=doc.select_one('#prdDetail #desctable')
    if table:
        rows=[]
        for tr in table.select('tr'):
            cells=tr.find_all(['td','th'],recursive=False)
            if len(cells)==2:
                key,value=(cell.get_text(' ',strip=True) for cell in cells)
                if key and value:rows.append('<tr><th>'+html.escape(key)+'</th><td>'+html.escape(value)+'</td></tr>')
        if rows:parts.append('<table style="width:100%;border-collapse:collapse"><tbody>'+''.join(rows)+'</tbody></table>')
    markup='<div>'+''.join(parts)+'</div>'
    if len(markup.encode('utf-16-le'))//2>32767:raise CrawlError('상세 HTML 엑셀 셀 한도 초과')
    return markup

def extra_slots(photos):
    extras=list(photos[1:10] or photos[:1])
    while len(extras)<5:extras.append(extras[-1])
    return ','.join(extras)

def amount(value):
    match=re.fullmatch(r'\s*([\d,]+)\s*(?:원)?\s*',value)
    return int(match[1].replace(',','')) if match else None

OCR_SCRIPT = r"""[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)

$ProgressPreference='SilentlyContinue'
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1})[0]
function Await($Op,$Type) { $task=$asTask.MakeGenericMethod($Type).Invoke($null,@($Op));$task.Wait();$task.Result }

$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko-KR'))
if ($null -eq $engine) { throw 'Korean OCR language is not installed' }
foreach($ImagePath in $ImagePaths) {
try {
$file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
$stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
[pscustomobject]@{text=$result.Text; lines=@(foreach($line in $result.Lines) { $word=$line.Words[0]; [pscustomobject]@{text=$line.Text;x=$word.BoundingRect.X;y=$word.BoundingRect.Y} })} | ConvertTo-Json -Compress -Depth 5
} catch { [pscustomobject]@{error=$_.Exception.Message} | ConvertTo-Json -Compress } finally { if ($bitmap) {$bitmap.Dispose();$bitmap=$null}; if ($stream) {$stream.Dispose();$stream=$null} }

}
"""

def ocr_many(ctx, pictures):
    """One PowerShell/OCR engine for a bounded group; retain every image tile."""
    if not pictures:return []
    with tempfile.TemporaryDirectory(prefix='cheek-eland-ocr-') as folder:
        paths=[];owners=[]
        for owner,picture in enumerate(pictures):
            width=min(1600,max(picture.width,1200))
            scaled=picture.convert('RGB').resize((width,round(picture.height*width/picture.width)))
            for y in range(0,scaled.height,2100):
                ctx.checkpoint()
                path=Path(folder)/(str(len(paths))+'.png')
                scaled.crop((0,y,width,min(y+2300,scaled.height))).save(path)
                paths.append(str(path.resolve()));owners.append(owner)
        script=OCR_SCRIPT
        manifest=Path(folder)/'tiles.json'
        manifest.write_text(json.dumps(paths,ensure_ascii=False),encoding='utf-8')
        script="$ImagePaths=Get-Content -LiteralPath '"+str(manifest.resolve()).replace("'","''")+"' -Raw -Encoding UTF8 | ConvertFrom-Json\n"+script
        command=base64.b64encode(script.encode('utf-16le')).decode('ascii')
        process=subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',command],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+min(120,20+len(paths)*10)
        try:
            while True:
                ctx.checkpoint()
                if time.monotonic()>deadline:raise CrawlError('상세 이미지 OCR 검사 시간 초과')
                try:
                    output,error=process.communicate(timeout=.2);break
                except subprocess.TimeoutExpired:pass
            if process.returncode:raise CrawlError('Windows 한국어 OCR 검사 실행 실패')
        finally:
            if process.poll() is None:process.kill();process.communicate()
    try:
        tiles=[json.loads(line) for line in output.splitlines() if line.strip()]
        if len(tiles)!=len(owners):raise ValueError('OCR tile count mismatch')
        results=['' for _ in pictures]
        for owner,tile in zip(owners,tiles):
            if 'error' in tile:results[owner]=CrawlError('OCR 이미지 검사 실패: '+tile['error'])
            elif not isinstance(results[owner],Exception):results[owner]+=(' '.join(row['text'] for row in sorted(tile['lines'],key=lambda row:(round(float(min(row['y']) if isinstance(row['y'],list) else row['y'])/12),float(min(row['x']) if isinstance(row['x'],list) else row['x'])))) if tile.get('lines') else tile['text'])+'\n'
        return results
    except (ValueError,KeyError,TypeError) as exc:
        raise CrawlError('OCR 결과 형식 확인 실패') from exc


def colour_from_text(text):
    if not re.search(r'PRODUCT\s*INFO',text,re.I):raise CrawlError('공식 PRODUCT INFO 색상표 미확인')
    match=re.search(r'색\s*상\s*[:：]?\s*(.{1,60}?)(?=\s*(?:소\s*재|혼용|제조|제작|사이즈|인증|PRODUCT|$))',re.sub(r'\s+',' ',text),re.I)
    value=match[1].strip(' :：|') if match else ''
    if not value or len(value)>30:raise CrawlError('상세 PRODUCT INFO 공식 색상 OCR 확인 실패')
    return value


def official_image_color(ctx,doc,link,code,cache):
    from PIL import Image, ImageEnhance
    import io
    urls=[u for u in detail_urls(doc,link,code) if re.search(re.escape(code),unquote(urlsplit(u).path),re.I)]
    if not urls:raise CrawlError('공식 품번 상품정보 이미지 없음')
    for url in urls:
        data=checked(ctx,url,cache,True)['data']
        with Image.open(io.BytesIO(data)) as picture:
            # Product information is usually at the bottom. OCR all tiles so an
            # alternative placement is still read; no colour guess from pixels.
            values=ocr_many(ctx,[picture.copy()])
            if isinstance(values[0],Exception):raise values[0]
            try:return colour_from_text(values[0])
            except CrawlError:pass
            # Fine gray PRODUCT INFO text can disappear in a full-width pass.
            # Magnify the lower information region; still require an explicit
            # color label/value, never infer color from the garment pixels.
            ctx.log('상세 상품정보 글씨 확대 후 색상 재확인: '+code,'INFO')
            crop=picture.crop((round(picture.width*.25),round(picture.height*.60),
                               round(picture.width*.98),picture.height))
            crop=ImageEnhance.Contrast(crop.convert('RGB')).enhance(2)
            focused=ocr_many(ctx,[crop])[0]
        if isinstance(focused,Exception):raise focused
        try:return colour_from_text(focused)
        except CrawlError:continue
    raise CrawlError('공식 상세 이미지에서 색상명 확인 실패: '+code)


def _crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not code:raise CrawlError('품번이 비어 있습니다')
    ctx.allow_automatic_results=True;matched=0
    ctx.stage('오가닉맘 공식 품번 검색: '+code)
    for link in search(ctx,code):
        ctx.checkpoint();source=fetch(ctx,link);doc=soup(source);info=identity(doc,code)
        if info is None:continue
        matched+=1;cache={}
        needs_color=any(not any(n in ('색상','컬러') for n in row.get('option_name_original',[])) for row in stock_rows(source))
        fallback=official_image_color(ctx,doc,link,code,cache) if needs_color else ''
        colors=palette(source,fallback)
        record=dict(product_code=code,product_name=info[0],product_url=link,**{'원본 상품명':info[1]})
        if ctx.enabled('regular_price'):record['regular_price']=amount(field(doc,'소비자가'))
        if ctx.enabled('category'):record['category']=' > '.join(n.get_text(' ',strip=True) for n in doc.select('.xans-product-headcategory li') if n.get_text(' ',strip=True) not in ('홈','현재 위치'))
        wants=ctx.enabled('main_image') or ctx.enabled('additional_images')
        photos=gallery(ctx,doc,link,cache) if wants or ctx.enabled('detail_page') else []
        if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,link,code,cache,photos)
        if len(colors)==1 or not wants:
            for color in colors:
                row=dict(record,color=color['color_name'],color_name=color['color_name'],color_code=color['color_code'])
                if ctx.enabled('size'):row['size']=','.join(color['sizes'])
                if ctx.enabled('price'):row['price']=color['prices'][0] if len(set(color['prices']))==1 else None
                if ctx.enabled('main_image'):row['main_image']=photos[0]
                if ctx.enabled('additional_images'):row['additional_images']=extra_slots(photos)
                ctx.emit_result(row)
        else:
            choices=[dict(color_name=c['color_name'],color_code=c['color_code'],**({'size':','.join(c['sizes'])} if ctx.enabled('size') else {})) for c in colors]
            if ctx.enabled('size'):
                sizes={','.join(c['sizes']) for c in colors};record['size']=next(iter(sizes)) if len(sizes)==1 else ''
            if ctx.enabled('price'):
                prices={p for c in colors for p in c['prices']};record['price']=next(iter(prices)) if len(prices)==1 else None
            for url in photos:ctx.emit_unmapped(dict(record),choices,url,shared_detail_html=record.get('detail_page',''))
        ctx.log(code+': '+str(len(colors))+'색상, '+str(len(photos))+'사진 수집 완료','SUCCESS')
    if not matched:raise CrawlError('공홈 검색 후 정확한 상품코드 일치 없음: '+code)

def crawl(ctx):
    ctx.log('오가닉맘 공식몰 '+REVISION,'INFO')
    try:_crawl(ctx)
    except Exception as exc:ctx.fail_item(str(exc));raise
