PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

import hashlib
import base64
import subprocess
import tempfile
import time
from pathlib import Path
import html
import io
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, unquote, quote
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image, ImageStat, ImageChops, ImageFilter, UnidentifiedImageError

SITE = 'https://www.elandmall.co.kr'
VENDOR = 'LV16003596'
BRAND_NO = '1690000333'
POLICY = {'checked': '2026-11-05', 'source': 'https://marketplace.coupang.com/mba/listing',
          'route': 'WING 웹/엑셀', 'min_side': 500, 'max_bytes': 10000000,
          'formats': ('JPEG', 'PNG', 'WEBP'), 'max_aspect': 1.8,
          'note': '정지 WebP와 비율 1.8은 프로젝트 정책. 등록 승인 보장은 아님.'}
# 실제 조사: 클라비스 CVTWG1108N 공개 검색·상품·옵션.
# 공식 판매자 우선. 공식이 없을 때만 전체 판매자의 스튜디오컷을 검사한다.
# 실제 Hub 화면, 최종 Excel, WING 업로드는 별도 검증 대상이다.


class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


def fetch(ctx, url, limit=6000000):
    if urlsplit(url).scheme not in ('http', 'https'):
        raise CrawlError('HTTP 이미지/페이지 주소가 아님: ' + url)
    url=quote(url,safe=":/?#[]@!$&'()*+,;=%")
    for attempt in range(3):
        ctx.checkpoint()
        failure = None
        try:
            with build_opener().open(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as r:
                final = r.geturl()
                data = r.read(limit + 1)
                content_type = r.headers.get('Content-Type', '')
                if len(data) > limit:
                    raise CrawlError('응답 용량 초과: ' + url)
                return data, final, content_type
        except HTTPError as e:
            failure = f'HTTP {e.code}: {url}'
            if e.code in (400, 401, 403, 404, 410, 429):
                raise CrawlError(failure) from e
        except (URLError, OSError, TimeoutError) as e:
            failure = f'통신 오류: {url}: {e}'
        finally:
            ctx.checkpoint()
        if attempt == 2:
            raise CrawlError(failure)
        ctx.log(f'{attempt + 1}/2 재시도: ' + failure, 'WARNING')
    raise CrawlError('요청 실패: ' + url)


def page(ctx, url):
    data, final, _ = fetch(ctx, url)
    if urlsplit(final).hostname != 'www.elandmall.co.kr':
        raise CrawlError('지정 쇼핑몰 밖으로 이동: ' + final)
    return BeautifulSoup(data.decode('utf-8-sig'), 'html.parser'), final


def search(ctx, code):
    links, seen, signatures = [], set(), set()
    page_no = 1
    while page_no <= 30:
        ctx.stage(f'이랜드몰 품번 검색 {page_no}페이지')
        soup, _ = page(ctx, SITE + '/s/srch?' + urlencode(
            {'q': code, 'size': 100, 'page': page_no, 'from': (page_no - 1) * 100}))
        total_tag = soup.select_one('[data-searchresult-total]')
        if total_tag is None or not re.fullmatch(r'[\d,]+', total_tag.get_text(strip=True)):
            raise CrawlError('검색 결과 건수 없음: 오류/인증 화면 또는 사이트 변경')
        total = int(total_tag.get_text(strip=True).replace(',', ''))
        current = []
        for a in soup.select('a.ui-item__link[href]'):
            u = urljoin(SITE, a['href'])
            p = urlsplit(u)
            q = parse_qs(p.query)
            if p.hostname != 'www.elandmall.co.kr' or p.path != '/i/item' or not q.get('itemNo'):
                continue
            key = (q['itemNo'][0], q.get('lowerVendNo', [''])[0])
            if key not in current:
                current.append(key)
            if u not in links:
                links.append(u)
        signature = tuple(current)
        if total and (not current or signature in signatures):
            raise CrawlError('검색 페이지가 반복되거나 비어 있음: 누락 방지를 위해 중단')
        signatures.add(signature)
        seen.update(current)
        if len(seen) >= total:
            official=[u for u in links if parse_qs(urlsplit(u).query).get('lowerVendNo')==[VENDOR]]
            ctx.log('공식 판매자 상품 우선: 공식만 수집' if official else '공식 판매자 상품 없음: 모든 판매자 스튜디오컷 검사', 'INFO')
            return official or links
        page_no += 1
    raise CrawlError('검색 30페이지 초과')


class ProductDataMissing(CrawlError):
    """Successful HTML response temporarily omitted itemDetailJson."""


def product_once(ctx, url, code):
    soup, final = page(ctx, url)
    for script in soup.find_all('script', src=False):
        value = script.string or script.get_text()
        m = re.search(r'const\s+itemDetailJson\s*=\s*', value)
        if not m:
            continue
        try:
            d = json.JSONDecoder().raw_decode(value[m.end():])[0]['itemDetail']
            main = d['itemMain']
        except (ValueError, KeyError, TypeError) as e:
            raise CrawlError('상품 JSON 파싱 실패: ' + url) from e
        expected_vendor=parse_qs(urlsplit(url).query).get('lowerVendNo',[''])[0]
        if str(main.get('lowerVendNo')) != expected_vendor:
            ctx.log('다른 판매자 제외: ' + final, 'INFO')
            return None
        if str(main.get('brandNo')) != BRAND_NO:
            ctx.log('다른 브랜드 제외: '+str(main.get('brandNo')), 'INFO')
            return None
        if str(main.get('modelNo', '')).strip().upper() != code:
            ctx.log('다른 공식 품번 제외: ' + str(main.get('modelNo')) + ': ' + final, 'INFO')
            return None
        if str(main.get('itemNo')) != parse_qs(urlsplit(url).query).get('itemNo', [''])[0]:
            raise CrawlError('조회 상품 ID와 응답 상품 ID 불일치')
        if not main.get('itemName'):
            raise CrawlError('상품명 누락: ' + final)
        clean_name(main['itemName'], code)
        ctx.log('품번/판매자 확인: ' + final, 'SUCCESS')
        return d, final
    raise ProductDataMissing('공식 상품 JSON 없음: ' + url)


def product(ctx, url, code):
    # The official page intermittently omits its JSON even while the item exists.
    # Retry this precise failure; malformed data and identity errors stay strict.
    for attempt in range(3):
        ctx.checkpoint()
        try:
            return product_once(ctx, url, code)
        except ProductDataMissing:
            if attempt == 2:
                raise
            ctx.log('상품 JSON 일시 누락: 공식 상세 재조회 '+str(attempt+1)+'/2 / '+url, 'WARNING')
        finally:
            ctx.checkpoint()


def options(ctx, main, selected=None):
    params = {'itemNo': main['itemNo'], 'lowerVendNo': main['lowerVendNo'], 'itemTcode': main['itemTcode']}
    if selected is not None:
        params['itemOptionNoList'] = str(selected)
    raw, final, _ = fetch(ctx, SITE + '/v1/item/option/api?' + urlencode(params))
    if urlsplit(final).hostname != 'www.elandmall.co.kr':
        raise CrawlError('옵션 API가 다른 호스트로 이동')
    try:
        data = json.loads(raw)
        if str(data['resultCode']) != '200':
            raise CrawlError('옵션 API 오류: ' + str(data.get('resultMessage')))
        rows = data['data']['itemOption']
        if not isinstance(rows, list) or not rows:
            raise CrawlError('공식 옵션 후보 없음')
        if any(str(o.get('itemNo')) != str(main['itemNo']) for o in rows):
            raise CrawlError('옵션 상품 ID 불일치')
        own=[o for o in rows if str(o.get('lowerVendNo'))==str(main['lowerVendNo'])]
        if len(own)!=len(rows):ctx.log('다른 판매자 옵션 제외: '+str(len(rows)-len(own))+'개','WARNING')
        if not own:raise CrawlError('해당 판매자 옵션 없음')
        return own
    except (ValueError, KeyError, TypeError) as e:
        raise CrawlError('옵션 JSON 구조 변경') from e



def display_color(row):
    return bool(re.fullmatch(r'AA\s+대표(?:칼라|컬러|색상)',str(row.get('itemOptionName') or '').strip(),re.I))

def color_rows(rows):
    result = []
    for o in rows:
        if display_color(o):continue
        if str(o.get('itemOptionTcode')) not in ('10','99'):
            raise CrawlError('첫 옵션이 색상인 상품만 지원: 사이트 옵션 구조 확인 필요')
        label = str(o.get('itemOptionName') or '').strip()
        if not label:
            raise CrawlError('공식 색상명 누락')
        # 공식 옵션의 00 WHITE 형식. DB의 itemOptionNo는 색상코드로 쓰지 않는다.
        m = re.fullmatch(r'\(([A-Z0-9]{2})\)\s*(.+)', label) or re.fullmatch(r'([A-Z0-9]{2})\s+(.+)', label)
        result.append({'color_name': m[2] if m else label, 'color_code': m[1] if m else ''})
    if not result:raise CrawlError('실제 색상 옵션 없음: 표시용 대표색상 제외')
    return result


def clean_name(name, code):
    for token in ('클라비스', 'CLAVIS', code):
        name = re.sub(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])', '', name, flags=re.I)
    name = re.sub(r'\[\s*\]|\(\s*\)', '', name)
    name = re.sub(r'\s+', ' ', name).strip(' _-')
    if not name:
        raise CrawlError('정리 후 실제 상품명이 없음')
    return name


def image_key(url):
    p = urlsplit(html.unescape(url))
    return (p.hostname or '').lower(), p.path.lower()


def content_signal(im):
    # OCR/의미 인식이 아닌 보수적 보조 검사. 작은 글자 성분이 지배적인
    # 라벨/고시와 전폭 흰 구분선으로 분리된 합성 사진을 제외한다.
    im = im.convert('RGB')
    im.thumbnail((480, 480))
    w, h = im.size
    px = list(im.getdata())
    dark = {i for i, rgb in enumerate(px) if max(rgb) < 170}
    total_dark = len(dark)
    small_count = small_area = 0
    while dark:
        start = dark.pop()
        stack, area = [start], 0
        x0 = x1 = start % w
        y0 = y1 = start // w
        while stack:
            i = stack.pop()
            x, y = i % w, i // w
            area += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for nx, ny in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                j = ny * w + nx
                if 0 <= nx < w and 0 <= ny < h and j in dark:
                    dark.remove(j)
                    stack.append(j)
        if 2 <= area <= 120 and x1-x0 < 32 and y1-y0 < 20:
            small_count += 1
            small_area += area
    if small_count >= 150 and 0 < total_dark < w*h*.16 and small_area > total_dark*.35:
        return '작은 글자 성분이 지배적인 라벨/고시/측정 안내 의심'
    # 밝은 배경 전체가 아닌 내부 구분선 양쪽에 독립 사진 영역이 있는지 확인.
    gray = im.convert('L')
    gray.thumbnail((120, 120))
    for g in (gray, gray.transpose(Image.Transpose.ROTATE_90)):
        a, b = g.size
        values = list(g.getdata())
        for y in range(b//3, b*2//3):
            if sum(v >= 252 for v in values[y*a:(y+1)*a]) < a*.99:
                continue
            upper, lower = values[max(0,y-b//4)*a:y*a], values[(y+1)*a:min(b,y+b//4)*a]
            if upper and lower and sum(v < 220 for v in upper)/len(upper) > .18 and sum(v < 220 for v in lower)/len(lower) > .18:
                return '전폭 흰 구분선 양쪽에 독립 사진 영역: 콜라주 의심'
    return ''


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
    # Pale brick/tile walls have similar average colors but repeated fine edges.
    # Require genuinely smooth matching background patches, not just matching means.
    fine=image.convert('RGB').resize((400,400));gray=fine.convert('L')
    texture=ImageChops.difference(gray,gray.filter(ImageFilter.GaussianBlur(2)))
    clean=[]
    boxes=[(.02,.05,.10,.15),(.90,.05,.98,.15),(.02,.85,.10,.95),(.90,.85,.98,.95)]
    boxes += [(x,y,x+.08,y+.1) for y in (.20,.45,.70) for x in (.02,.90)]
    for x1,y1,x2,y2 in boxes:
        box=(int(x1*400),int(y1*400),int(x2*400),int(y2*400))
        stat=ImageStat.Stat(fine.crop(box))
        if max(stat.stddev)<=14 and ImageStat.Stat(texture.crop(box)).mean[0]<=1.0:clean.append(stat.mean)
    smooth=max((sum(sum((a-b)**2 for a,b in zip(mean,center))<=35**2 for mean in clean) for center in clean),default=0)
    accepted=(best>=7 or border_ok) and smooth>=5 and max(foreground.stddev)>=6
    return accepted,'배경 패치 %s/10, 외곽 %s/12, 무늬 없는 배경 %s/10(필요5)'%(best,border_best,smooth)


def image_info(ctx, url, cache):
    if url in cache:
        return cache[url]
    try:
        data, final, content_type = fetch(ctx, url, POLICY['max_bytes'])
        with Image.open(io.BytesIO(data)) as im:
            fmt, w, h = im.format, im.width, im.height
            animated = getattr(im, 'is_animated', False)
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            signal = content_signal(im)
            plain = studio_background(im)[0]
        ctx.log(f'이미지 {fmt} {w}x{h}, {len(data)}bytes, animated={animated}, HTTP={content_type}: {url}', 'INFO')
        info = (fmt, w, h, animated, hashlib.sha256(data).hexdigest(), signal, plain)
    except (CrawlError, UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        ctx.log(f'이미지 제외: {url}: {e}', 'WARNING')
        info = None
    cache[url] = info
    return info



def studio_product(ctx,main,cache):
    primary=next((row for row in main.get('itemImage',[]) if str(row.get('imageDcode'))=='010' and row.get('imagePath')),None)
    if primary is None:raise CrawlError('상품 사진 조회 실패: 대표사진 주소 없음')
    url=urljoin('https://item.elandrs.com/',primary['imagePath']);info=image_info(ctx,url,cache)
    if not info:raise CrawlError('상품 사진 조회 실패: 대표사진 접근/해독 불가 / '+url)
    passed=info[6] and not info[5]
    if not passed:ctx.log('스튜디오컷 조건 미달 대표사진: '+url,'INFO')
    return passed

def gallery(ctx, main, cache):
    urls = []
    for item in main.get('itemImage', []):
        ctx.checkpoint()
        if str(item.get('imageDcode')) not in ('010', '030') or not item.get('imagePath'):
            continue
        url = urljoin('https://item.elandrs.com/', item['imagePath'])
        label = ' '.join(str(item.get(k) or '') for k in ('imageName', 'imageFileName'))
        if re.search(
            r'배너|콜라주|사이즈|측정|고시|스와치|로고|banner|collage|measurement|size.?chart|size.?guide|care_', label + ' ' + urlsplit(url).path, re.I):
            ctx.log('갤러리 제외 신호: ' + url, 'WARNING')
            continue
        info = image_info(ctx, url, cache)
        if not info:
            continue
        fmt, w, h, animated, digest, signal, plain = info
        if str(main['lowerVendNo']) != VENDOR and not plain:
            ctx.log('비균일 배경 셀러 사진 제외: '+url, 'INFO')
            continue
        if signal:
            ctx.log('갤러리 제외: ' + signal + ': ' + url, 'WARNING')
            continue
        if fmt not in POLICY['formats'] or animated or min(w, h) < 500 or max(w, h) / min(w, h) > 1.8:
            ctx.log('갤러리 형식/픽셀/애니메이션/비율 기준 탈락: ' + url, 'WARNING')
            continue
        urls.append((url, digest))
    return urls


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
            elif not isinstance(results[owner],Exception):results[owner]+=tile['text']+'\n'
        return results
    except (ValueError,KeyError,TypeError) as exc:
        raise CrawlError('OCR 결과 형식 확인 실패') from exc


BANNER = re.compile(r'banner|shipping|delivery|refund|coupon|copyright|notice|매장|배송|교환|반품|쿠폰|저작권|고객센터|문의사항|상담시간|구매전|구매안내',re.I)


def original_detail(url,code):
    p=urlsplit(url);name=unquote(p.path.rsplit('/',1)[-1])
    return p.hostname=='img-studio.elandmall.com' and p.path.startswith('/Winstudio/') and bool(
        re.fullmatch(re.escape(code)+r'(?:[_-][A-Za-z0-9]+)?\.(?:jpe?g|png|webp)',name,re.I) or
        re.fullmatch(r'CV[A-Za-z0-9_-]*SIZE[A-Za-z0-9_-]*\.(?:jpe?g|png|webp)',name,re.I))


def detail(ctx,d,code,cache):
    desc=d.get('itemDesc') or {};doc=BeautifulSoup(html.unescape(desc.get('itemDescdocumentCon') or ''),'html.parser')
    announced=set()
    for notice in desc.get('itemAnnounceList') or []:
        fragment=BeautifulSoup(html.unescape(notice.get('itemAnnounceCon') or ''),'html.parser')
        for node in fragment.select('img[src]'):announced.add(image_key(urljoin(SITE,node['src'])))
    trusted=[];other=[];order={}
    for node in doc.select('img'):
        raw=node.get('ec-data-src') or node.get('data-src') or node.get('src') or ''
        if not raw or raw.startswith('data:'):continue
        url=urljoin(SITE,raw);p=urlsplit(url)
        label=unquote(p.path)+' '+str(node.get('alt') or '')+' '+str(node.get('title') or '')
        if image_key(url) in announced or BANNER.search(label):
            ctx.log('셀러/공통 안내 배너 제외: '+url, 'INFO');continue
        if p.scheme not in ('http','https'):continue
        if p.scheme=='http':url='https:'+url[5:]
        order.setdefault(url,len(order))
        if original_detail(url,code):
            if url not in trusted:trusted.append(url)
        elif p.hostname in ('item.elandrs.com','item-imghub.eland.co.kr'):
            if url not in other:other.append(url)
        else:ctx.log('본문 이미지 출처 확인 불가: '+url,'WARNING')
    # Keep brand originals and only separately verified product extras.
    parts=[]
    if trusted:
        for url in trusted:
            ctx.checkpoint()
            if image_info(ctx,url,cache):parts.append(('photo','<img src="'+html.escape(url,quote=True)+'">'))
    # UUID files have no meaningful filename: inspect all image tiles before use.
    for offset in range(0,len(other),4):
        batch=[]
        for url in other[offset:offset+4]:
            ctx.checkpoint()
            try:
                raw,_,_=fetch(ctx,url,64*1024*1024)
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format not in ('JPEG','PNG','WEBP') or getattr(im,'is_animated',False):raise CrawlError('정지 상세 이미지 아님')
                    if im.width*im.height>80_000_000:raise CrawlError('상세 이미지 픽셀 한도 초과')
                    im.load();batch.append((url,im.copy()))
            except (CrawlError,OSError,UnidentifiedImageError,Image.DecompressionBombError) as exc:ctx.log('상세 이미지 제외: '+str(exc),'WARNING')
        if not batch:continue
        try:texts=ocr_many(ctx,[image for _,image in batch])
        except (CrawlError,OSError,subprocess.SubprocessError) as exc:
            ctx.log('상세 OCR 검사 실패: 미검사 이미지 제외 / '+str(exc),'WARNING');continue
        for (url,image),text in zip(batch,texts):
            if isinstance(text,Exception) or BANNER.search(re.sub(r'\s+','',text)):
                ctx.log('셀러 안내/검사 실패 상세 이미지 제외: '+url,'INFO');continue
            if trusted and not (re.search(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',text,re.I) and studio_background(image)[0]):
                ctx.log('본사 원본 외 추가 이미지의 품번/스튜디오 근거 미확인: '+url,'INFO');continue
            parts.append(('photo','<img src="'+html.escape(url,quote=True)+'">'))
    parts.sort(key=lambda pair:order[html.unescape(BeautifulSoup(pair[1],'html.parser').img['src'])])
    return parts


def crawl(ctx):
    code = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*', code):
        ctx.fail_item('색상 접미사 없는 기본 품번을 입력하세요.')
        raise CrawlError('기본 품번 형식 오류')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):
        ctx.fail_item('수동 매핑은 대표 또는 추가이미지 선택이 필요합니다.')
        raise CrawlError('매핑용 이미지 수집 미선택')
    errors, products, colors, candidates, sizes, prices, categories = [], [], [], [], [], [], []
    cache, pieces = {}, []
    detail_source = None
    matched = rejected = 0
    def failed(message):
        errors.append(message)
        ctx.fail_item(message)
    try:
        links = search(ctx, code)
        if not links:raise CrawlError('품번 검색 결과 없음: '+code+' / 이랜드몰 검색 0건')
    except CrawlError as e:
        failed(str(e))
        raise
    for url in links:
        ctx.checkpoint()
        ctx.stage('공식 품번/판매자 및 옵션 확인')
        try:
            found = product(ctx, url, code)
            if found is None:
                continue
            d, final = found
            main = d['itemMain']
            matched += 1
            if str(main['lowerVendNo']) != VENDOR and not studio_product(ctx,main,cache):
                rejected += 1
                ctx.log('스튜디오컷 없음(상품 제외): '+code+' / '+final,'INFO')
                continue
            opts = [row for row in options(ctx, main) if not display_color(row)]
            cs = color_rows(opts)
            products.append((main, final))
            photos=gallery(ctx, main, cache)
            if not photos:
                products.pop()
                rejected += 1
                if any(cache[url] is None for row in main.get('itemImage',[]) if row.get('imagePath') for url in [urljoin('https://item.elandrs.com/',row['imagePath'])] if url in cache):
                    failed('상품 사진 조회 실패: '+code+' / 통과 사진이 없고 이미지 접근/해독 오류 발생 / '+final)
                ctx.log('통과한 스튜디오 상품 사진 없음: '+url,'INFO')
                continue
            candidates.extend(photos)
            for c in cs:
                if c not in colors:colors.append(c)
            if ctx.enabled('category'):
                categories.append(str(main.get('depth4StandardCategoryName') or ''))
            if ctx.enabled('price') or ctx.enabled('size'):
                for opt in opts:
                    ctx.checkpoint()
                    child = options(ctx, main, opt['itemOptionNo'])
                    if any(str(o.get('itemOptionTcode')) not in ('20','99') for o in child):
                        raise CrawlError('2단계 색상/사이즈 외 옵션 구조는 미지원')
                    if ctx.enabled('size'):
                        names = list(dict.fromkeys(str(o.get('itemOptionName') or '').strip() for o in child))
                        if not names or not all(names):
                            raise CrawlError('사이즈 옵션명 누락')
                        sizes.append(names)
                    if ctx.enabled('price'):
                        values = [o.get('sellPrice') for o in child]
                        if any(not isinstance(v, (int, float)) or v < 0 for v in values):
                            raise CrawlError('사이즈별 판매가 누락')
                        prices.extend(values)
            if ctx.enabled('detail_page') and detail_source is None:
                body=detail(ctx,d,code,cache)
                if body:
                    pieces=body
                    detail_source=final
                    ctx.log('상세는 이 상품 한 곳만 사용: '+final,'INFO')
            if str(main['lowerVendNo']) == VENDOR:
                ctx.log('공식 상품 한 곳 수집 완료: 다른 상품은 수집하지 않음', 'INFO')
                break
        except CrawlError as e:
            message=str(e)
            failed(message if url in message else message + ': ' + url)
    if (not products or not colors) and not errors:
        if matched:
            failed('스튜디오컷 없음: '+code+' / 정확한 품번 상품 '+str(matched)+'개 조회, 대표/개별 사진 조건 미달 '+str(rejected)+'개')
        else:
            failed('정확한 품번 일치 상품 없음: '+code+' / 검색 결과 '+str(len(links))+'개가 있으나 브랜드·품번 검증 불일치')
    unique, hashes = [], set()
    for url, digest in candidates:
        ctx.checkpoint()
        if digest not in hashes:
            hashes.add(digest)
            unique.append(url)
    if not unique and not errors:
        failed('통과한 매핑용 개별 상품 사진이 0장입니다.')
    if products and colors and unique:
        main, final = products[0]
        record = {'product_code': code, 'product_name': clean_name(main['itemName'], code), 'product_url': detail_source or final}
        regular_records = []
        for product_main, _ in products:
            value = {}
            collect_regular_price(ctx, value, product_main, 'eland')
            regular_records.append(value)
        merge_regular_prices(ctx, record, regular_records)
        if ctx.enabled('size'):
            same = bool(sizes) and all(v == sizes[0] for v in sizes)
            record['size'] = ','.join(sizes[0]) if same else ''
            if not same:
                ctx.log('색상/상품 링크별 사이즈가 달라 공통 사이즈는 빈 값', 'WARNING')
        if ctx.enabled('price'):
            record['price'] = prices[0] if prices else None
            if len(set(prices)) > 1:
                ctx.log(f'판매가 차이: 첫 지정 상품/옵션 판매가 {prices[0]}원 선택. 확인한 가격={sorted(set(prices))}. 가격 차이는 성공 처리.', 'WARNING')
        if ctx.enabled('category'):
            record['category'] = categories[0] if categories and len(set(categories)) == 1 else ''
        if ctx.enabled('detail_page'):
            try:
                body = list(dict.fromkeys(markup for _, markup in pieces))
                markup = ''.join(body)
                if not markup:
                    failed('상세페이지 수집 실패: '+code+' / 스튜디오컷 '+str(len(unique))+'장은 있으나 배너를 제외한 안전한 상세 원본을 수집하지 못함')
                if len(markup.encode('utf-16-le')) // 2 > 32767:
                    raise CrawlError('상세 HTML이 Excel 셀 32767자 한도 초과')
                record['detail_page'] = markup
            except CrawlError as e:
                failed(str(e))
        ctx.stage(f'수동 매핑 후보 {len(unique)}장 전달')
        for url in unique:
            ctx.checkpoint()
            ctx.emit_unmapped(dict(record), colors, url, shared_detail_html=record.get('detail_page', ''))
    if errors:
        raise CrawlError(' / '.join(dict.fromkeys(errors)))
