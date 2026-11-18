PORTAL_API_VERSION = 1

import hashlib
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
BRAND_NO = '1690000447'
VENDOR_NO = 'CN003546'
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
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise CollectionError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt == 2:
                raise CollectionError('통신 실패: %s / %s' % (url, exc)) from exc
        for _ in range(5):
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
        ctx.stage('애니바디 공개 검색 %s페이지' % number)
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


def search(ctx, code):
    result = search_site(ctx, code, SITE)
    if not result:
        raise CollectionError('이랜드몰 검색 결과 0건: '+code)
    official = [url for url in result if parse_qs(urlsplit(url).query).get('lowerVendNo') == [VENDOR_NO]]
    if official:
        ctx.log('공식 판매자 상품 존재: %s 링크만 수집(다른 판매자 제외)' % VENDOR_NO, 'INFO')
        return official
    ctx.log('공식 판매자 상품 없음: 전체 판매자 %s개 링크를 스튜디오컷 검사' % len(result), 'INFO')
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
        if str(main.get('brandNo'))!=BRAND_NO or str(main.get('modelNo','')).strip().upper()!=code:
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
        if not badge or not re.search(r'특가|할인|쿠폰|세일|타임딜|한정딜',next(v for v in badge.groups() if v)):
            break
        name = name[badge.end():]
    # Remove the model first: some sellers put it AFTER the branch suffix.
    for token in ('애니바디','ANYBODY',code):
        name = re.sub(r'(?<![A-Za-z0-9])'+re.escape(token)+r'(?![A-Za-z0-9])','',name,flags=re.I)
    name = re.sub(r'\[\s*\]|\(\s*\)','',name)
    # A branch suffix does not have to start with NC/뉴코아: _충장점,
    # _중동점, [평촌점], (뉴코아 부천점), and bare " 충장점" are all retail
    # annotations. Keep product qualifiers such as (주니어) intact.
    branch = r'(?:(?:NC|뉴코아|이천일|이랜드리테일|이랜드몰|모다아울렛)\s*)?[^\s\[\]()_|/\-]{1,35}(?:지점|점)'
    suffixes = (
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
    accepted=(best>=7 or border_ok) and max(foreground.stddev)>=6
    return accepted,'배경 패치 %s/10, 외곽 테두리 %s/12(4방향 확인)'%(best,border_best)


class ImageRejected(CollectionError):
    """A decoded asset is not a usable product photograph."""


def image_info(ctx, url, cache, detail=False):
    if urlsplit(url).hostname not in ('item.elandrs.com','img-studio.elandmall.com','item-imghub.eland.co.kr') and not (detail and urlsplit(url).hostname == 'file1.partnerbk.com'):
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
            info = (fmt,w,h,hashlib.sha256(raw).hexdigest(),studio_background(image)[0])
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
        raise ImageRejected('손상 또는 이미지가 아닌 응답: '+url) from exc
    cache[key] = info
    return info


NEGATIVE = re.compile(r'저작권|무단.?사용|배너|배송|교환|반품|쿠폰|copyright|banner|shipping|delivery|refund|care_',re.I)
SELLER_PHOTO = re.compile(r'매장.?촬영|직접.?촬영|셀러.?컷|매장.?컷|seller.?photo|store.?photo',re.I)


def studio_product(ctx, main, cache):
    primary = next((row for row in main.get('itemImage', [])
                    if str(row.get('imageDcode')) == '010' and row.get('imagePath')), None)
    if primary is None:
        raise CollectionError('대표사진 주소 없음: 스튜디오컷 판별 불가')
    url = image_url(urljoin('https://item.elandrs.com/', primary['imagePath']))
    result = image_info(ctx, url, cache)
    if not result[4]:
        ctx.log('대표사진 배경이 균일하지 않아 상품 제외: '+url, 'INFO')
    return result[4]


def gallery(ctx, main, cache):
    result, white, complex_count = [],0,0
    for item in main.get('itemImage',[]):
        ctx.checkpoint()
        if str(item.get('imageDcode')) not in ('010','030') or not item.get('imagePath'):
            continue
        url = image_url(urljoin('https://item.elandrs.com/',item['imagePath']))
        label = ' '.join(str(item.get(k) or '') for k in ('imageName','imageFileName'))+' '+unquote(urlsplit(url).path)
        if NEGATIVE.search(label) or re.search(r'사이즈표|실측표|size.?chart|size.?guide|collage|콜라주',label,re.I):
            ctx.log('공통 안내/명시적 셀러 촬영/표 이미지 제외: '+url,'WARNING')
            continue
        try:
            fmt,w,h,digest,plain = image_info(ctx,url,cache)
        except MissingResource as exc:
            ctx.log('갤러리 원본 404 제외: '+str(exc),'WARNING')
            continue
        except ImageRejected as exc:
            ctx.log('사용할 수 없는 갤러리 파일 제외: '+str(exc),'WARNING')
            continue
        if min(w,h)<500 or max(w,h)/min(w,h)>1.8:
            ctx.log('개별 사진 픽셀/비율 기준 제외: %sx%s / %s'%(w,h,url),'WARNING')
            continue
        white += bool(plain)
        complex_count += not plain
        result.append((url,digest))
    if complex_count:
        ctx.log('갤러리 배경 보조 검사: 균일 배경 %s장, 장면형/복잡 배경 %s장. 배경으로 탈락시키지 않고 공식 장면형·디테일 컷 유지. 색상 매핑 시 확인하세요.'%(white,complex_count),'INFO')
    return result


def detail_images(ctx, detail, code, cache):
    desc=detail.get('itemDesc') or {}
    markup=desc.get('itemDescdocumentCon') or ''
    if not markup.strip():raise CollectionError('공식 상품 상세 본문 없음: '+code)
    fragment=BeautifulSoup(html.unescape(markup),'html.parser')
    announce_urls=set()
    for notice in desc.get('itemAnnounceList') or []:
        note=BeautifulSoup(html.unescape(notice.get('itemAnnounceCon') or ''),'html.parser')
        for node in note.select('img'):
            source=node.get('data-src') or node.get('src')
            if source:announce_urls.add(image_url(urljoin(SITE,source)))
    result=[]
    for node in fragment.select('img'):
        ctx.checkpoint()
        source=node.get('ec-data-src') or node.get('data-original') or node.get('data-src') or node.get('src')
        if not source or source.startswith('data:'):continue
        url=image_url(urljoin(SITE,source))
        if url in announce_urls:
            ctx.log('공통 행사/쿠폰 안내 제외(요청 안 함): '+url,'INFO');continue
        name=unquote(urlsplit(url).path.rsplit('/',1)[-1])
        context=name+' '+str(node.get('alt') or '')+' '+str(node.get('title') or '')
        if re.search(r'banner|shipping|delivery|refund|coupon|copyright|배송|교환|반품|쿠폰|저작권',context,re.I):
            ctx.log('공통 배송/행사 안내 제외(요청 안 함): '+url,'INFO');continue
        if urlsplit(url).scheme not in ('http','https'):raise CollectionError('공식 본문 이미지 주소 확인 실패: '+url)
        if urlsplit(url).scheme=='http':url='https:'+url[5:]
        if url in result:continue
        # Filename/code filtering must never remove BASE/GOSI/shared size images.
        info=image_info(ctx,url,cache,detail=True)
        if min(info[1:3])<=1:ctx.log('1픽셀 빈 이미지 제외: '+url,'WARNING');continue
        result.append(url)
    if not result:raise CollectionError('공식 본문 이미지 파싱 결과 없음: '+code)
    return result


def crawl(ctx):
    code = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9]+(?:-[A-Z0-9]+)*',code):
        ctx.fail_item('색상코드 없는 기본 품번 형식을 확인하세요')
        raise CollectionError('잘못된 입력 품번')
    if not(ctx.enabled('main_image') or ctx.enabled('additional_images')):
        ctx.fail_item('수동 매핑은 대표/추가이미지 중 하나 이상 필요')
        raise CollectionError('사진 수집 미선택')
    try:
        links = search(ctx,code)
    except CollectionError as exc:
        ctx.fail_item(str(exc));raise
    products,errors,cache = [],[],{}
    diagnostics=[]
    rejected = 0
    for link in links:
        ctx.checkpoint()
        try:
            ctx.stage('공식 브랜드·품번 확인: '+link)
            data = product(ctx,link,code,diagnostics=diagnostics)
            if data is None:
                continue
            main = data['itemMain']
            if str(main['lowerVendNo']) != VENDOR_NO and not studio_product(ctx, main, cache):
                rejected += 1
                continue
            name = clean_name(main['itemName'],code)
            if not name:
                raise CollectionError('정리 후 필수 상품명 없음')
            palette,actual = colors(ctx,options(ctx,main))
            record = {'product_code':code,'product_name':name,'product_url':link,'원본 상품명':main['itemName'],'판매자번호':str(main['lowerVendNo'])}
            record['원본 옵션'] = '\n'.join(str(row.get('itemOptionName') or '') for row in actual)
            optional_values(ctx,main,actual,record)
            photos = gallery(ctx,main,cache)
            if not photos:
                raise CollectionError('유효 개별 상품 사진 0장: '+link)
            body = detail_images(ctx,data,code,cache) if ctx.enabled('detail_page') and not products else []
            products.append((record,palette,photos,body))
        except (CollectionError,KeyError,UnicodeError) as exc:
            message = link+' / '+str(exc)
            ctx.fail_item(message);errors.append(message)
    if not products:
        if diagnostics:message=' / '.join(dict.fromkeys(diagnostics))
        elif errors:message='공식 검색 상품은 있으나 수집 처리 실패: '+code
        elif rejected:message='검색 결과는 있으나 대표사진 스튜디오컷 조건 통과 상품 없음: '+code
        elif links:message='검색 결과가 있으나 지정 브랜드/품번 검증 불일치: '+code
        else:message='공식 검색에서 지정 판매자 상품 조회 안 됨(전체 사이트 미등록으로 단정하지 않음): '+code
        ctx.fail_item(message)
        raise CollectionError(message+(' / '+'; '.join(errors) if errors else ''))
    palette,photos,bodies,digests = [],[],[],set()
    for record,colors_list,images,body in products:
        for color in colors_list:
            if color not in palette:
                palette.append(color)
        for url,digest in images:
            ctx.checkpoint()
            if digest not in digests:
                digests.add(digest);photos.append((url,record['product_url']))
        for url in body:
            if url not in bodies:
                bodies.append(url)
    record = {'product_code':code,'product_name':products[0][0]['product_name'],'원본 상품명':'\n'.join(dict.fromkeys(p[0]['원본 상품명'] for p in products)),'판매자번호':','.join(dict.fromkeys(p[0]['판매자번호'] for p in products))}
    record['원본 옵션'] = '\n'.join(dict.fromkeys(line for p in products for line in str(p[0].get('원본 옵션') or '').splitlines() if line))
    for field in ('size','price','regular_price','category'):
        if ctx.enabled(field):
            if field == 'size':
                record[field] = ','.join(dict.fromkeys(value for p in products for value in str(p[0].get(field) or '').split(',') if value)) or None
            else:
                record[field] = common([p[0].get(field) for p in products])
            if record[field] is None:
                ctx.log(field+' 공통값 미확정: 빈칸으로 수집 계속','WARNING')
    if ctx.enabled('detail_page'):
        markup = ''.join('<img src="%s" alt="상품 상세" style="max-width:100%%;height:auto">'%html.escape(u,quote=True) for u in bodies)
        if len(markup)>32767:
            ctx.fail_item('공통 상세 HTML Excel 셀 한도 초과')
            raise CollectionError('공통 상세 HTML Excel 셀 한도 초과')
        record['detail_page'] = markup
    for url,link in photos:
        ctx.checkpoint()
        ctx.emit_unmapped(dict(record,product_url=products[0][0]['product_url']),palette,url,shared_detail_html=record.get('detail_page',''))
    if errors:
        raise CollectionError('일부 링크 실패. 성공 후보 보존, 입력 행 미완료: '+'; '.join(errors))
    ctx.log('%s: %s개 링크, %s개 색상, %s장 사진 수집'%(code,len(products),len(palette),len(photos)),'SUCCESS')
