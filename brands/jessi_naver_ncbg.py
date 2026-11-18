PORTAL_API_VERSION = 1
import hashlib, html, io, json, re, time, base64, binascii, os, subprocess, tempfile
import sys
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from urllib.request import build_opener
from urllib.error import URLError
from urllib.parse import urlsplit, unquote
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image, gallery_slots
SITE = 'https://smartstore.naver.com'
STORE = 'ncdept297'
POLICY = dict(min_side=500,max_bytes=10000000,max_aspect=1.8,formats=['JPEG','PNG','WEBP'])

def money(value):
    if isinstance(value,bool):return None
    try:
        number=float(value)
        return int(number) if number>0 and number.is_integer() else None
    except (ValueError,TypeError,OverflowError):return None


def public_image(ctx,url,cache,detail=False):
    parsed = urlsplit(url)
    external=False
    if parsed.scheme!='https' or parsed.username or parsed.password or '..' in unquote(parsed.path).split('/') or not (external or parsed.hostname in ('shop-phinf.pstatic.net','shop1.phinf.naver.net','shop2.phinf.naver.net')):
        raise ValueError('확인되지 않은 상품 이미지 호스트: '+url)
    key=(url,detail)
    if key not in cache:
        earlier=cache.get((url,False))
        cache[key]=earlier if detail and earlier and earlier['status']=='accessible' else check_public_image(url,timeout=12,retries=1,max_bytes=64*1024*1024 if detail else 10000000,checkpoint=ctx.checkpoint)
    result=cache[key]
    if result['status']=='unknown':raise ValueError('이미지 통신 확인 미완료: '+url+' / '+', '.join(result['reasons']))
    if result['status']!='accessible':
        ctx.log('이미지 제외: '+url+' / '+', '.join(result['reasons']),'WARNING');return None
    return result


def internal_gutter(image):
    thumb=image.convert('RGB');thumb.thumbnail((160,160))
    for source in (thumb,thumb.transpose(Image.Transpose.ROTATE_90)):
        width,height=source.size;px=source.load()
        white=[sum(min(px[x,y])>=245 for x in range(width))/width for y in range(height)]
        for y in range(int(height*.18),int(height*.82)-1):
            if white[y]>=.98 and white[y+1]>=.98 and any(v<.85 for v in white[:y]) and any(v<.85 for v in white[y+2:]):return True
    return False


def gallery(ctx,product,cache,require=True):
    accepted,seen,digests=[],set(),set()
    for row in product.get('productImages') or []:
        ctx.checkpoint();url=str(row.get('url') or '')
        if url in seen or not url:continue
        seen.add(url)
        if row.get('imageType') not in ('REPRESENTATIVE','OPTIONAL') or re.search(r'chart|size|guide|measur|banner|logo|collage|swatch|고시|사이즈|실측',urlsplit(url).path,re.I):
            ctx.log('상품 사진 이외 후보 제외: '+url,'WARNING');continue
        result=public_image(ctx,url,cache)
        if not result:continue
        with Image.open(io.BytesIO(result['data'])) as im:
            width,height=im.size;fmt=im.format;animated=getattr(im,'is_animated',False);im.verify()
        if fmt not in POLICY['formats'] or animated or min(width,height)<500 or max(width,height)/min(width,height)>1.8 or len(result['data'])>10000000:
            ctx.log('갤러리 실제 형식/픽셀/용량/비율 제외: '+url,'WARNING');continue
        with Image.open(io.BytesIO(result['data'])) as im:
            if internal_gutter(im):
                ctx.log('독립 사진 영역 사이 구분 띠: 콜라주 의심 제외 '+url,'WARNING');continue
        digest=hashlib.sha256(result['data']).hexdigest()
        if digest not in digests:accepted.append(url);digests.add(digest)
    if require and not accepted:raise ValueError('공식 갤러리에서 규격을 통과한 상품 사진이 없습니다.')
    return accepted

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
[pscustomobject]@{text=$result.Text} | ConvertTo-Json -Compress
} catch { [pscustomobject]@{error=$_.Exception.Message} | ConvertTo-Json -Compress } finally { if ($bitmap) {$bitmap.Dispose();$bitmap=$null}; if ($stream) {$stream.Dispose();$stream=$null} }

}
"""

def ocr_many(ctx, pictures):
    """One PowerShell/OCR engine for a bounded group; retain every image tile."""
    if not pictures:return []
    with tempfile.TemporaryDirectory(prefix='jessi-body-ocr-') as folder:
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
                if time.monotonic()>deadline:raise ValueError('상세 이미지 OCR 검사 시간 초과')
                try:
                    output,error=process.communicate(timeout=.2);break
                except subprocess.TimeoutExpired:pass
            if process.returncode:raise ValueError('Windows 한국어 OCR 검사 실행 실패')
        finally:
            if process.poll() is None:process.kill();process.communicate()
    try:
        tiles=[json.loads(line) for line in output.splitlines() if line.strip()]
        if len(tiles)!=len(owners):raise ValueError('OCR tile count mismatch')
        results=['' for _ in pictures]
        for owner,tile in zip(owners,tiles):
            if 'error' in tile:results[owner]=ValueError('OCR 이미지 검사 실패: '+tile['error'])
            elif not isinstance(results[owner],Exception):results[owner]+=tile['text']+'\n'
        return results
    except (ValueError,KeyError,TypeError) as exc:
        raise ValueError('OCR 결과 형식 확인 실패') from exc


BODY_NON_PRODUCT = re.compile(r'사이즈표|실측|사이즈가이드|상품정보|상품고시|상품품질|제품소재|제조국|제조자|세탁방법|취급주의|고객센터|배송|교환|반품|쿠폰|구매안내|품질보증|sizechart|sizeguide|careinstruction|shipping|delivery|refund|coupon',re.I)


def body_image_urls(source):
    doc=BeautifulSoup(source or '', 'html.parser');area=doc.select_one('.se-main-container') or doc;found=[]
    for node in area.select('img'):
        declared={};component=node.find_parent(class_='se-image')
        anchor=component.select_one('[data-linkdata]') if component else None
        if anchor:
            try:declared=json.loads(anchor.get('data-linkdata') or '{}')
            except (ValueError,TypeError):pass
        url=str(declared.get('src') or node.get('data-src') or node.get('src') or '').strip()
        if url.startswith('//'):url='https:'+url
        if urlsplit(url).hostname not in ('shop-phinf.pstatic.net','shop1.phinf.naver.net','shop2.phinf.naver.net'):continue
        if url not in found:found.append(url)
    return found


def body_gallery(ctx,source,cache):
    eligible=[]
    for url in body_image_urls(source):
        if re.search(r'chart|size|guide|measur|banner|logo|collage|swatch|고시|사이즈|실측|배송',unquote(urlsplit(url).path),re.I):continue
        result=public_image(ctx,url,cache,True)
        if not result:continue
        with Image.open(io.BytesIO(result['data'])) as im:
            if im.format not in POLICY['formats'] or getattr(im,'is_animated',False) or min(im.size)<500 or max(im.size)/min(im.size)>1.8 or len(result['data'])>10000000:
                ctx.log('상세 후보 제외: 개별 컷 형식/크기/비율 조건 미달 / '+url,'INFO');continue
            if internal_gutter(im):ctx.log('상세 후보 제외: 여러 컷 구분 띠 / '+url,'INFO');continue
            eligible.append((url,hashlib.sha256(result['data']).hexdigest(),im.copy()))
    kept=[]
    for offset in range(0,len(eligible),4):
        batch=eligible[offset:offset+4];ctx.stage('상세 개별 상품컷 검사 '+str(offset+1)+'/'+str(len(eligible)))
        texts=ocr_many(ctx,[im for _,_,im in batch])
        for (url,digest,image),text in zip(batch,texts):
            if isinstance(text,Exception):raise ValueError('상세 상품컷 문자 검사 미완료: '+url+' / '+str(text))
            compact=re.sub(r'\s+','',text)
            if BODY_NON_PRODUCT.search(compact) or sum(word in compact for word in ('어깨','가슴','허리','총장','밑단'))>=2:
                ctx.log('상세 후보 제외: 사이즈/고시/안내 이미지 / '+url,'INFO');continue
            kept.append((url,digest))
    return kept


def photo_candidates(ctx,product,source,cache):
    urls=gallery(ctx,product,cache,require=False);seen=set()
    for url in urls:
        result=cache.get((url,False))
        if result and result.get('data'):seen.add(hashlib.sha256(result['data']).hexdigest())
    thumbs=len(urls);added=0
    for url,digest in body_gallery(ctx,source,cache):
        if url not in urls and digest not in seen:urls.append(url);seen.add(digest);added+=1
    if not urls:raise ValueError('썸네일·상세 본문에서 통과한 개별 상품컷 없음')
    ctx.log('사진 매핑 후보: 썸네일 '+str(thumbs)+'장 + 상세 개별컷 '+str(added)+'장','SUCCESS')
    return urls


def cell(value):
    if isinstance(value,dict):return '<br>'.join(html.escape(str(k))+': '+cell(v) for k,v in value.items())
    if isinstance(value,list):return '<br>'.join(cell(v) for v in value)
    return html.escape(str(value if value is not None else ''))


def notice_table(ctx, product):
    values=(product.get('productInfoProvidedNoticeView') or {}).get('basic') or {}
    if not values:
        ctx.log('공식 상품정보 고시 없음: 표 생략','WARNING');return ''
    return '<h3>상품정보제공고시</h3><table border="1" cellpadding="8" style="width:100%;border-collapse:collapse"><tbody>'+''.join('<tr><th>'+html.escape(str(k))+'</th><td>'+cell(v)+'</td></tr>' for k,v in values.items())+'</tbody></table>'


def product_number(value):
    value=str(value).strip()
    if not re.fullmatch(r'[1-9][0-9]{5,19}',value): raise ValueError('품번 대신 네이버 상품번호 숫자만 입력하세요.')
    return value

def state_from(source):
    for script in BeautifulSoup(source,'html.parser').find_all('script'):
        text=script.get_text();match=re.search(r'window\.__PRELOADED_STATE__\s*=\s*',text)
        if not match: continue
        raw=re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',lambda m:'null' if m[0]=='undefined' else m[0],text[match.end():])
        state=json.JSONDecoder().raw_decode(raw)[0]
        store=(state.get('channel') or {}).get('url')
        if store and store!=STORE: raise ValueError('지정 NC불광 스토어와 응답 불일치')
        return state
    return {}

def verify_product(product,number):
    if str(product.get('id') or '')!=number: raise ValueError('요청한 상품번호와 상세 응답 불일치')
    if not str(product.get('name') or '').strip(): raise ValueError('상품명 없음: 접근/상품 응답 확인 필요')

def color_value(label):
    label=str(label).strip()
    match=re.fullmatch(r'\(([^()]+)\)\s*(.+)',label) or re.fullmatch(r'([0-9]{2,3})\s+(.+)',label)
    return dict(color_code=match[1],color_name=match[2]) if match else dict(color_code='',color_name=label)

def notice_value(product,key):
    value=((product.get('productInfoProvidedNoticeView') or {}).get('basic') or {}).get(key)
    if not isinstance(value,str):return ''
    value=value.strip()
    if not value or value in ('-', '--', '상세정보 확인') or re.search(r'상세.*(?:참조|참고|확인)|이미지.*참조',value):return ''
    return value


def option_palette(product):
    groups=product.get('options') or []
    colorpos=next((i+1 for i,g in enumerate(groups) if str(g.get('groupName','')).strip().casefold() in ('색상','컬러','color','colour')),None)
    sizepos=next((i+1 for i,g in enumerate(groups) if str(g.get('groupName','')).strip().casefold() in ('사이즈','size')),None)
    rows=product.get('optionCombinations') or [];colors={};deltas=[]
    if rows:
        if any(i+1 not in (colorpos,sizepos) for i in range(len(groups))): raise ValueError('색상/사이즈 외 복합 옵션: 구조 확인 필요')
        fixed=notice_value(product,'색상')
        for row in rows:
            label=str(row.get('optionName'+str(colorpos)) or '').strip() if colorpos else fixed
            if not label: raise ValueError('공식 색상 옵션/고시 색상 미확인')
            color=colors.setdefault(label,dict(color_value(label),size=[]))
            size=str(row.get('optionName'+str(sizepos)) or '').strip() if sizepos else ''
            if size and size not in color['size']:color['size'].append(size)
            deltas.append(row.get('price'))
    else:
        standards=product.get('optionStandards') or []
        labels=list(dict.fromkeys(str(o.get('optionName') or '').strip() for o in standards if str(o.get('optionGroupName','')).strip().casefold() in ('색상','컬러','color','colour')))
        sizes=list(dict.fromkeys(str(o.get('optionName') or '').strip() for o in standards if str(o.get('optionGroupName','')).strip().casefold() in ('사이즈','size')))
        labels=[v for v in labels if v];sizes=[v for v in sizes if v]
        scope='색상별 옵션'
        if not labels:
            fixed=notice_value(product,'색상')
            if not fixed:raise ValueError('공식 색상 옵션/고시 색상 미확인: 실제 색상 원문이 필요합니다.')
            labels=[fixed];scope='상품고시 색상·치수 (선택 옵션 없음)'
            if not sizes:
                declared=notice_value(product,'치수')
                if declared:sizes=[declared]
        elif len(labels)>1 and len(sizes)>1:
            scope='상품 전체 옵션 목록 · 색상별 사이즈 조합 미확인'
        colors={label:dict(color_value(label),size=sizes,size_scope=scope) for label in labels}
    return [dict(v,size=','.join(v['size'])) for v in colors.values()],deltas

def selected_price(ctx,product,deltas):
    base=money(product.get('discountedSalePrice')) or money((product.get('benefitsView') or {}).get('discountedSalePrice')) or money(product.get('salePrice'))
    if deltas and all(isinstance(v,(int,float)) and not isinstance(v,bool) for v in deltas) and len(set(deltas))==1 and base:
        return money(base+deltas[0])
    if getattr(ctx,'snapshot_collected_at',None) and base:
        ctx.log('저장 원문의 상품 표시 판매가 사용. 옵션 추가금은 포함하지 않음.','WARNING');return base
    ctx.log('옵션 추가금 공통 여부 미확인: 판매가 빈칸','WARNING');return None

def detail_markup(ctx,source,product,cache):
    doc=BeautifulSoup(source,'html.parser');area=doc.select_one('.se-main-container') or doc;parts=[];seen=set()
    for node in area.select('img'):
        url=str(node.get('data-src') or node.get('src') or '').strip()
        if url.startswith('//'):url='https:'+url
        host=urlsplit(url).hostname
        if host not in ('shop-phinf.pstatic.net','shop1.phinf.naver.net','shop2.phinf.naver.net'):continue
        if re.search(r'banner|shipping|delivery|coupon|배송|교환|반품',unquote(urlsplit(url).path),re.I):continue
        if url in seen:continue
        seen.add(url)
        if not public_image(ctx,url,cache,True):raise ValueError('본문 이미지 공개 접근 확인 실패: '+url)
        parts.append('<p><img src="'+html.escape(url,quote=True)+'" style="max-width:100%;height:auto"></p>')
    for node in area.select('p'):
        value=node.get_text(' ',strip=True).replace('\u200b','').strip()
        if value:parts.append('<p>'+html.escape(value)+'</p>')
    for table in area.select('table'):
        rows=[]
        for tr in table.select('tr'):
            cells=['<'+n.name+'>'+html.escape(n.get_text(' ',strip=True))+'</'+n.name+'>' for n in tr.find_all(['th','td'],recursive=False)]
            if cells:rows.append('<tr>'+''.join(cells)+'</tr>')
        if rows:parts.append('<table border="1">'+''.join(rows)+'</table>')
    if not parts: raise ValueError('상품 상세 본문 미로딩: 고시만으로 성공 처리하지 않음')
    parts.append(notice_table(ctx,product))
    return '<div>'+''.join(parts)+'</div>'

def existing_chrome_profile():
    if sys.platform=='win32':return Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'Google/Chrome/User Data'
    if sys.platform=='darwin':return Path.home()/'Library/Application Support/Google/Chrome'
    return Path.home()/'.config/google-chrome'


def chrome_endpoint_once(profile):
    # Only Chrome's documented opt-in discovery file. Never copy cookies/profiles.
    try:
        lines=(profile/'DevToolsActivePort').read_text('utf-8').splitlines()
        port=int(lines[0]);path=lines[1].strip()
        if not 1<=port<=65535 or not re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+',path):return None
        return 'ws://127.0.0.1:'+str(port)+path
    except (OSError,ValueError,IndexError):return None


def chrome_endpoint(profile):
    for attempt in range(3):
        endpoint=chrome_endpoint_once(profile)
        if endpoint:return endpoint
        if attempt<2:time.sleep(.15)
    return None


@contextmanager
def naver_browser_session(ctx):
    from playwright.sync_api import sync_playwright
    ctx.checkpoint();endpoint=chrome_endpoint(existing_chrome_profile())
    if not endpoint:
        raise RuntimeError('[기존 Chrome 연결 설정 필요] 새 창은 열지 않습니다. 로그인한 Chrome에서 chrome://inspect/#remote-debugging 연결 허용을 켠 뒤 실행하세요.')
    ctx.stage('로그인한 기존 Chrome 연결 · Chrome에 연결 허용 창이 뜨면 직접 허용하세요')
    with sync_playwright() as runtime:
        browser=runtime.chromium.connect_over_cdp(endpoint,timeout=getattr(ctx,'connection_timeout',300000))
        if not browser.contexts:raise RuntimeError('기존 Chrome 기본 세션을 찾지 못했습니다.')
        ctx.log('기존 Chrome 로그인 세션에 연결했습니다. 새 브라우저/프로필을 만들지 않습니다.','SUCCESS')
        try:yield browser.contexts[0]
        finally:ctx.log('수집 연결만 해제했습니다. 기존 Chrome과 로그인 세션은 유지합니다.','INFO')


def wait_access(ctx,page,number,status,captured,timeout=180):
    if status in (404,410):raise ValueError('[상품 페이지 없음] HTTP '+str(status)+' / '+number)
    if status is not None and status>=400 and status not in (401,403,429):raise ValueError('네이버 페이지 응답 오류: '+str(status))
    deadline=time.monotonic()+timeout;announced=False;login_seen=False;login_announced=False;resumed=False
    while time.monotonic()<deadline:
        ctx.checkpoint()
        if page.is_closed():raise ValueError('[수집창 닫힘] 로그인 중에는 수집용 크롬 창을 유지해주세요. 다시 실행하면 같은 프로필을 사용합니다.')
        try:
            parsed=urlsplit(page.url)
            if parsed.hostname in ('nid.naver.com','nid.naver.net'):
                login_seen=True
                if not login_announced:
                    ctx.stage('네이버 로그인 대기 · 수집용 크롬에서 직접 로그인하세요 (최대 3분)')
                    ctx.log('네이버가 로그인을 요구했습니다. 직접 로그인한 뒤 같은 창을 유지하면 원래 상품으로 복귀합니다.','WARNING');login_announced=True;announced=True
            elif login_seen and not resumed and (parsed.hostname in ('naver.com','www.naver.com') or parsed.hostname=='smartstore.naver.com' and parsed.path.rstrip('/')=='/'+STORE):
                ctx.stage('로그인 후 원래 상품으로 복귀: '+number)
                captured.clear();page.goto(SITE+'/'+STORE+'/products/'+number,wait_until='domcontentloaded',timeout=30000)
                resumed=True;continue
            if parsed.hostname=='smartstore.naver.com' and parsed.path=='/'+STORE+'/products/'+number:
                state=state_from(page.content())
                product=next((v for v in (captured.get('product'),state.get('product'),state.get('simpleProductForDetailPage')) if isinstance(v,dict) and str(v.get('id') or '')==number),{})
                if str(product.get('id') or '')==number and product.get('name'):
                    captured.pop('error',None);return state
        except (ValueError,json.JSONDecodeError):
            raise
        except Exception:
            # Page may be navigating while the user completes verification.
            pass
        if not announced:
            ctx.stage('수집용 크롬에서 접속 확인 대기 · 로그인/인증 또는 상품 새로고침 후 자동 계속 (최대 3분)')
            ctx.log('열린 수집용 크롬에서 상품을 정상으로 열어주세요. 접속 제한을 상품 없음으로 처리하지 않습니다. 같은 창은 다음 상품에도 유지됩니다.','WARNING');announced=True
        page.wait_for_timeout(250)
    raise ValueError('[접속 확인 시간 초과] 수집용 크롬에서 상품을 정상으로 연 뒤 다시 실행하세요. 창과 세션은 유지됩니다.')


def snapshot_dir():
    home=Path(os.environ.get('CRAWLER_HUB_HOME') or (Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'CrawlerHub'))
    return home/'browser-profiles'/'jessi_naver_ncbg'/'snapshots'


def saved_browser_product(ctx,number):
    path=snapshot_dir()/(number+'.json')
    if not path.exists():return None
    if path.stat().st_size>8000000:raise ValueError('브라우저 원문 파일 용량 초과')
    data=json.loads(path.read_text('utf-8'))
    expected=SITE+'/'+STORE+'/products/'+number
    if data.get('schema')!='jessi-ncbg-browser-snapshot-v1' or data.get('source_url')!=expected or data.get('store')!=STORE:
        raise ValueError('저장된 브라우저 원문의 상품/스토어 검증 실패')
    collected=datetime.fromisoformat(str(data.get('collected_at','')).replace('Z','+00:00'))
    if collected.tzinfo is None:raise ValueError('브라우저 원문 수집 시각의 시간대 없음')
    age=(datetime.now(timezone.utc)-collected).total_seconds()
    if not -300<=age<=86400:raise ValueError('저장된 브라우저 원문이 24시간을 초과했습니다. 원문을 다시 수집하세요.')
    product=data.get('product') or {};verify_product(product,number)
    if (product.get('channel') or {}).get('url') not in (None,STORE):raise ValueError('브라우저 상품 채널 불일치')
    body=str(data.get('body_html') or '')
    if ctx.enabled('detail_page') and not body:raise ValueError('저장 원문에 상세 본문 없음')
    ctx.snapshot_collected_at=data['collected_at']
    ctx.stage('정상 브라우저에서 확보한 실제 상품 원문 사용: '+number)
    ctx.log('실시간 재조회 대신 정상 브라우저 저장 원문을 사용합니다. 수집시각: '+data['collected_at'],'WARNING')
    return product,body,expected


def parse_notice_markup(markup):
    doc=BeautifulSoup(markup,'html.parser');rows=[]
    for tr in doc.select('tr'):
        cells=tr.find_all(['th','td'],recursive=False)
        if len(cells)>=2:rows.append((cells[0].get_text(' ',strip=True),cells[1].get_text(' ',strip=True)))
    for li in doc.select('li'):
        children=li.find_all(recursive=False)
        if len(children)>=2:rows.append((children[0].get_text(' ',strip=True),children[1].get_text(' ',strip=True)))
    for dt in doc.select('dt'):
        dd=dt.find_next_sibling('dd')
        if dd:rows.append((dt.get_text(' ',strip=True),dd.get_text(' ',strip=True)))
    result={}
    for key,value in rows:
        if key=='재화등의 배송방법에 관한 정보':continue
        if key and value and len(key)<80 and key not in result:result[key]=value
    return result


def enrich_notice(ctx,page,product):
    if notice_value(product,'색상'):return
    has_color=any(str(o.get('optionGroupName','')).strip().casefold() in ('색상','컬러','color','colour') and str(o.get('optionName') or '').strip() for o in product.get('optionStandards') or [])
    if not ctx.enabled('detail_page') and has_color:return
    ctx.checkpoint()
    button=page.get_by_role('button',name='상품정보제공고시 보기',exact=True)
    if not button.count():return
    dialog=None
    try:
        button.click(timeout=7000)
        dialog=page.locator('[role="dialog"]').filter(has_text=re.compile(r'상품정보\s*제공고시')).last
        dialog.wait_for(state='visible',timeout=7000)
        basic=parse_notice_markup(dialog.evaluate('(el)=>el.outerHTML'))
        if basic:product['productInfoProvidedNoticeView']={'basic':basic}
    except Exception as exc:
        ctx.log('상품고시 추가 로딩 미완료: '+str(exc).splitlines()[0],'WARNING')
    finally:
        if dialog is not None:
            try:
                close=dialog.get_by_role('button',name='닫기',exact=True)
                if close.count():close.click(timeout=3000)
            except Exception:pass
        ctx.checkpoint()


def existing_product_tab(browser):
    for page in browser.pages:
        parsed=urlsplit(page.url)
        if parsed.hostname=='smartstore.naver.com' and (parsed.path.rstrip('/')=='/'+STORE or parsed.path.startswith('/'+STORE+'/')):return page
    for page in browser.pages:
        if page.url in ('about:blank','chrome://newtab/','chrome://newtab','chrome://new-tab-page/') or page.url.startswith('chrome://inspect/#remote-debugging'):return page
    raise ValueError('[상품 탭 필요] 기존 Chrome의 현재 탭에서 NC불광 상품 페이지를 열어두세요. 다른 사이트의 탭은 바꾸거나 새 창/탭을 만들지 않습니다.')


def read_product_page(ctx,browser,number):
    url=SITE+'/'+STORE+'/products/'+number
    needs_body=any(ctx.enabled(k) for k in ('detail_page','main_image','additional_images'))
    captured={}
    page=existing_product_tab(browser)
    def receive(response):
        parsed=urlsplit(response.url)
        if parsed.hostname!='smartstore.naver.com':return
        match=re.fullmatch(r'/n/v2/channels/([^/]+)/products/([0-9]+)(/contents/.*)?',parsed.path)
        if not match or match[2]!=number:return
        key='contents' if match[3] else 'product'
        if key=='contents' and not needs_body:return
        if response.status!=200:captured['error']='네이버 상품 응답 HTTP '+str(response.status);return
        try:
            raw=response.body()
            if len(raw)>8000000:raise ValueError('네이버 응답 크기 초과')
            data=json.loads(raw)
            if key=='product' and (data.get('channel') or {}).get('channelUid')!=match[1]:raise ValueError('상품 응답 채널 불일치')
            captured[key]=data
        except Exception as exc:captured['error']=str(exc)
    page.on('response',receive)
    try:
        ctx.stage('네이버 NC불광 상품번호 직접 조회: '+number)
        ctx.checkpoint();response=None
        if page.url.split('?')[0].rstrip('/')!=url:response=page.goto(url,wait_until='domcontentloaded',timeout=30000)
        ctx.checkpoint();status=response.status if response else 200
        state=wait_access(ctx,page,number,status,captured)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            ctx.checkpoint()
            if captured.get('error'):raise ValueError(captured['error'])
            state=state_from(page.content())
            if captured.get('product'):break
            candidate=state.get('product') or state.get('simpleProductForDetailPage') or {}
            if candidate.get('optionCombinations'):captured['product']=candidate;break
            page.wait_for_timeout(100)
        product=next((v for v in (captured.get('product'),state.get('product'),state.get('simpleProductForDetailPage')) if isinstance(v,dict) and str(v.get('id') or '')==number),{})
        verify_product(product,number)
        enrich_notice(ctx,page,product)
        source=''
        if needs_body:
            for _ in range(25):
                ctx.checkpoint()
                if captured.get('error'):raise ValueError(captured['error'])
                source=str((captured.get('contents') or {}).get('renderContent') or '')
                if source:break
                area=page.locator('.se-main-container').first
                if area.count():source=area.evaluate('(el)=>el.outerHTML');break
                page.mouse.wheel(0,800);page.wait_for_timeout(250)
            if not source:raise ValueError('상세 본문 로딩 미완료')
        return product,source,url
    finally:page.remove_listener('response',receive)

def broker_folder():
    home=Path(os.environ.get('CRAWLER_HUB_HOME') or (Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'CrawlerHub'))
    return home/'browser-profiles/jessi_naver_ncbg/existing-connection'


def broker_version():return 'jessi-existing-chrome-v1'

def compatible_broker_versions():return {broker_version(),'8ef34336ce4717c15aff37768062d2690edc491af18d7ee5cb67d1754b648833'}


def broker_info():
    try:
        data=json.loads((broker_folder()/'connection.json').read_text('utf-8'))
        if not isinstance(data.get('port'),int) or not 1<=data['port']<=65535 or not re.fullmatch(r'[A-Za-z0-9_-]{24,100}',data.get('token','')):return None
        return data
    except (OSError,ValueError,TypeError):return None


def broker_request(info,path,payload=None,timeout=5):
    from urllib.request import Request,ProxyHandler
    url='http://127.0.0.1:'+str(info['port'])+path
    data=json.dumps(payload).encode('utf-8') if payload is not None else None
    req=Request(url,data=data,headers={'X-Crawler-Connection':info['token'],'Content-Type':'application/json'})
    with build_opener(ProxyHandler({})).open(req,timeout=timeout) as response:
        raw=response.read(10000000)
    return json.loads(raw)


def broker_alive(info):
    pid=info.get('pid')
    if not isinstance(pid,int) or pid<=0:return False
    try:
        if os.name=='nt':
            import ctypes
            kernel=ctypes.windll.kernel32;kernel.OpenProcess.restype=ctypes.c_void_p
            handle=kernel.OpenProcess(0x1000,False,pid)
            if not handle:return False
            try:
                code=ctypes.c_ulong()
                return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(code))) and code.value==259
            finally:kernel.CloseHandle(ctypes.c_void_p(handle))
        os.kill(pid,0);return True
    except (OSError,ValueError):return False


def ensure_broker(ctx):
    folder=broker_folder();folder.mkdir(parents=True,exist_ok=True);version=broker_version();info=broker_info();launched_at=None
    if info and info.get('ready'):
        try:
            health=broker_request(info,'/health')
            if health.get('ok') and health.get('version') in compatible_broker_versions():return info
            broker_request(info,'/stop',{},timeout=5)
        except Exception:pass
    pending=bool(info and not info.get('ready') and not info.get('error') and info.get('version') in compatible_broker_versions() and broker_alive(info))
    if not pending:
        lock=folder/'starting.lock'
        try:
            handle=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.close(handle)
        except FileExistsError:
            if time.time()-lock.stat().st_mtime>350:lock.unlink(missing_ok=True);raise ValueError('이전 연결 시작 대기가 만료됐습니다. 다시 실행하세요.')
        else:
            try:
                launched_at=time.time()
                flags=getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
                env=dict(os.environ);sdk_dir=str(Path(sys.modules['crawler_rules'].__file__).resolve().parent);env['PYTHONPATH']=sdk_dir+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
                subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--existing-chrome-broker'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags,start_new_session=os.name!='nt',env=env)
            finally:lock.unlink(missing_ok=True)
    ctx.stage('기존 Chrome 연결 승인 대기 · 허용 후 다음 품번에도 같은 연결을 유지합니다')
    deadline=time.monotonic()+330
    while time.monotonic()<deadline:
        ctx.checkpoint();info=broker_info()
        if info and info.get('version') in compatible_broker_versions() and (launched_at is None or info.get('started',0)>=launched_at):
            if info.get('error'):raise ValueError('기존 Chrome 연결 실패: '+info['error'])
            if info.get('ready'):
                try:
                    if broker_request(info,'/health').get('ok'):return info
                except Exception:pass
        time.sleep(.2)
    raise ValueError('Chrome 연결 승인 대기 시간이 지났습니다. 연결 허용 창을 확인해주세요. 새 Chrome 창은 열지 않습니다.')


def broker_collect(ctx,number):
    from concurrent.futures import ThreadPoolExecutor
    info=ensure_broker(ctx);ctx.stage('연결된 기존 Chrome에서 상품 수집: '+number)
    payload={'number':number,'fields':[k for k in ('detail_page','main_image','additional_images','size','price') if ctx.enabled(k)]}
    pool=ThreadPoolExecutor(max_workers=1);future=pool.submit(broker_request,info,'/collect',payload,240)
    try:
        while not future.done():ctx.checkpoint();time.sleep(.15)
        result=future.result()
    finally:pool.shutdown(wait=False,cancel_futures=True)
    for event in result.get('logs',[]):ctx.log(event['message'],event.get('level','INFO'))
    if not result.get('ok'):raise ValueError(result.get('error') or '기존 Chrome 상품 수집 실패')
    product=result['product'];verify_product(product,number)
    return product,result.get('source',''),SITE+'/'+STORE+'/products/'+number


def load_product(ctx,number):
    if os.environ.get('JESSI_NAVER_USE_SAVED')=='1':
        saved=saved_browser_product(ctx,number)
        if saved:return saved
    return broker_collect(ctx,number)



def crawl(ctx):
    ctx.allow_automatic_results=True
    try:
        number=product_number(ctx.product_code);product,source,url=load_product(ctx,number);verify_product(product,number)
        palette,deltas=option_palette(product);name=str(product['name']).strip()
        size_scope=next((c.get('size_scope') for c in palette if c.get('size_scope') and c['size_scope']!='색상별 옵션'),'')
        if size_scope:ctx.log(size_scope+' / 실제 상품 옵션·고시 원문을 보존합니다.','WARNING')
        record=dict(product_code=number,product_name=name,product_url=url,**{'원본 품번':str((product.get('naverShoppingSearchInfo') or {}).get('modelName') or '')})
        if getattr(ctx,'snapshot_collected_at',None):record['원문 수집시각']=ctx.snapshot_collected_at
        if ctx.enabled('size') and size_scope:record['사이즈 확인 범위']=size_scope
        if ctx.enabled('size'):record['size']=','.join(dict.fromkeys(s for c in palette for s in c['size'].split(',') if s))
        if ctx.enabled('price'):record['price']=selected_price(ctx,product,deltas)
        if ctx.enabled('regular_price'):record['regular_price']=None;ctx.log('독립 정상가 출처 미검증: 빈칸','WARNING')
        if ctx.enabled('category'):record['category']=str((product.get('category') or {}).get('wholeCategoryName') or '')
        cache={};photos=[];body=''
        if ctx.enabled('detail_page'):body=detail_markup(ctx,source,product,cache);record['detail_page']=body
        if ctx.enabled('main_image') or ctx.enabled('additional_images'):
            gallery_product=dict(product)
            if not gallery_product.get('productImages'):
                gallery_product['productImages']=[dict(url=u,imageType='REPRESENTATIVE' if i==0 else 'OPTIONAL') for i,u in enumerate([product.get('representativeImageUrl')]+list(product.get('optionalImageUrls') or [])) if u]
            photos=photo_candidates(ctx,gallery_product,source,cache)
        if not photos:
            for color in palette:
                row=dict(record,color=color['color_name'],color_name=color['color_name'],color_code=color['color_code'])
                if ctx.enabled('size'):row['size']=color['size']
                if photos:
                    main,extras,_=gallery_slots([dict(url=u,validation=dict(passed=True)) for u in photos])
                    if ctx.enabled('main_image'):row['main_image']=main
                    if ctx.enabled('additional_images'):row['additional_images']=','.join(extras)
                ctx.emit_result(row)
        else:
            choices=palette if ctx.enabled('size') else [{k:v for k,v in c.items() if k!='size'} for c in palette]
            for url in photos:ctx.emit_unmapped(dict(record),choices,url,shared_detail_html=body)
    except Exception as exc:
        ctx.fail_item(str(exc));raise


def run_existing_chrome_broker():
    import secrets
    from http.server import BaseHTTPRequestHandler,HTTPServer
    folder=broker_folder();folder.mkdir(parents=True,exist_ok=True)
    token=secrets.token_urlsafe(32);version=broker_version();started=time.time()
    class Control:
        fields=set();logs=[];connection_timeout=0
        def checkpoint(self):pass
        def enabled(self,key):return key in self.fields
        def stage(self,message):pass
        def log(self,message,level='INFO'):self.logs.append({'message':str(message),'level':level})
    control=Control()
    class Handler(BaseHTTPRequestHandler):
        browser=None;stop=False
        def log_message(self,*args):pass
        def send_json(self,value,status=200):
            data=json.dumps(value,ensure_ascii=False).encode('utf-8');self.send_response(status);self.send_header('Content-Type','application/json;charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def authorized(self):return self.headers.get('X-Crawler-Connection')==token and self.headers.get('Host')=='127.0.0.1:'+str(self.server.server_port)
        def do_GET(self):
            if not self.authorized():return self.send_json({'error':'접근 불가'},403)
            if self.path=='/health':return self.send_json({'ok':bool(Handler.browser and Handler.browser.browser and Handler.browser.browser.is_connected()),'version':version})
            self.send_json({'error':'잘못된 요청'},404)
        def do_POST(self):
            if not self.authorized():return self.send_json({'error':'접근 불가'},403)
            if self.path=='/stop':
                length=int(self.headers.get('Content-Length','0'))
                if not 0<=length<10000:return self.send_json({'error':'잘못된 요청 크기'},400)
                self.rfile.read(length);Handler.stop=True;return self.send_json({'ok':True})
            if self.path!='/collect':return self.send_json({'error':'잘못된 요청'},404)
            control.logs=[]
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<10000:raise ValueError('잘못된 요청 크기')
                data=json.loads(self.rfile.read(length));number=product_number(data.get('number',''));control.fields=set(data.get('fields') or [])
                product,source,url=read_product_page(control,Handler.browser,number)
                keys=('id','name','channel','category','naverShoppingSearchInfo','optionStandards','options','optionCombinations','productImages','representativeImageUrl','optionalImageUrls','salePrice','discountedSalePrice','benefitsView','productInfoProvidedNoticeView','optionUsable')
                product={k:product[k] for k in keys if k in product}
                return self.send_json({'ok':True,'product':product,'source':source,'logs':control.logs})
            except Exception as exc:return self.send_json({'ok':False,'error':str(exc),'logs':control.logs})
    server=HTTPServer(('127.0.0.1',0),Handler);server.timeout=30
    info={'port':server.server_port,'token':token,'version':version,'started':started,'pid':os.getpid(),'ready':False}
    def save():
        path=folder/'connection.json';temp=path.with_suffix('.tmp');temp.write_text(json.dumps(info),encoding='utf-8');temp.replace(path)
    save()
    try:
        with naver_browser_session(control) as browser:
            Handler.browser=browser;info['ready']=True;save()
            while not Handler.stop:server.handle_request()
    except Exception as exc:info.update(ready=False,error=str(exc));save()
    finally:server.server_close()


if __name__=='__main__' and sys.argv[1:]==['--existing-chrome-broker']:
    run_existing_chrome_broker()
