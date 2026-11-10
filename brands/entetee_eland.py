PORTAL_API_VERSION = 1

import hashlib
import html
import http.client
import io
import json
import re
import socket
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
import urllib.error
import urllib.request

from bs4 import BeautifulSoup
from PIL import Image, ImageStat, UnidentifiedImageError

SITE = 'https://www.elandmall.co.kr'
BRAND_NO = '1690000448'
MAX_BYTES = 10_000_000


class CollectionError(RuntimeError):
    """Expected transport, source-format or collection failure."""


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


def page(ctx, url):
    return BeautifulSoup(fetch(ctx, url, 6_000_000).decode('utf-8-sig'), 'html.parser')


def search(ctx, code):
    links, seen, signatures = {}, set(), set()
    for number in range(1, 101):
        ctx.stage('앙떼떼 공개 검색 %s페이지' % number)
        soup = page(ctx, SITE+'/s/srch?'+urlencode(
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
            if parsed.hostname!='www.elandmall.co.kr' or parsed.path!='/i/item':
                continue
            if not params.get('itemNo') or not params.get('lowerVendNo'):
                continue
            key = params['itemNo'][0],params['lowerVendNo'][0]
            if key not in current:
                current.append(key)
            # NC branch vendors also carry the same verified brand. Never limit
            # the search to CN003623 or require a brand substring in the title.
            links[key] = SITE+'/i/item?'+urlencode({'itemNo':key[0],'lowerVendNo':key[1]})
        signature = tuple(current)
        if total and (not current or signature in signatures):
            raise CollectionError('검색 페이지 반복/비어 있음: 누락 방지 중단')
        signatures.add(signature)
        seen.update(current)
        if len(seen)>=total:
            return list(links.values())
    raise CollectionError('검색 100페이지 안전 한도 초과: 미완료')


def product(ctx, url, code):
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
            ctx.log('다른 브랜드/공식 품번 제외: '+url,'INFO')
            return None
        params = parse_qs(urlsplit(url).query)
        if str(main.get('itemNo'))!=params['itemNo'][0] or str(main.get('lowerVendNo'))!=params['lowerVendNo'][0]:
            raise CollectionError('조회 상품/판매자와 응답 ID 불일치: '+url)
        if not main.get('itemName'):
            raise CollectionError('필수 상품명 없음: '+url)
        return detail
    raise CollectionError('공식 상품 JSON 없음: '+url)


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
        for row in rows:
            if str(row.get('itemNo'))!=str(main['itemNo']) or str(row.get('lowerVendNo'))!=str(main['lowerVendNo']):
                raise CollectionError('옵션 상품/판매자 ID 불일치')
        return rows
    except (KeyError,ValueError,TypeError) as exc:
        raise CollectionError('공식 옵션 JSON 형식 변경') from exc


def colors(ctx, rows):
    palette, actual = [], []
    for row in rows:
        ctx.checkpoint()
        if str(row.get('itemOptionTcode'))!='10':
            raise CollectionError('확인되지 않은 첫 옵션 유형: 색상 후보 확인 불가')
        label = str(row.get('itemOptionName') or '').strip()
        if not label:
            raise CollectionError('공식 색상 옵션명 누락')
        if re.fullmatch(r'AA\s+대표(?:칼라|컬러|색상)',label,re.I):
            ctx.log('사이트 표시용 대표칼라 옵션 제외: '+label,'INFO')
            continue
        match = re.fullmatch(r'([A-Z0-9]{2})\s+(.+)',label,re.I)
        color = {'color_name':match[2] if match else label,'color_code':match[1] if match else ''}
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
        record['regular_price'] = money(main.get('orgSellprice'))
        if record['regular_price'] is None:
            ctx.log('정상가 orgSellprice 누락/0/형식 불명확: 빈칸으로 수집 계속','WARNING')
        ctx.checkpoint()
    if ctx.enabled('category'):
        record['category'] = str(main.get('depth4StandardCategoryName') or '').strip() or None
    if not (ctx.enabled('size') or ctx.enabled('price')):
        return
    size_groups, price_values = [], []
    for color in actual:
        ctx.checkpoint()
        children = options(ctx,main,color['itemOptionNo'])
        if any(str(row.get('itemOptionTcode'))!='20' for row in children):
            raise CollectionError('확인되지 않은 색상 하위 사이즈 옵션 유형')
        if ctx.enabled('size'):
            names = list(dict.fromkeys(str(row.get('itemOptionName') or '').strip() for row in children))
            size_groups.append(names if names and all(names) else None)
        if ctx.enabled('price'):
            # The official main sellprice and all actual size/colour sellPrice
            # values must agree before one common manual-mapping price is used.
            price_values.append(money(color.get('sellPrice')))
            price_values.extend(money(row.get('sellPrice')) for row in children)
    if ctx.enabled('size'):
        valid = bool(size_groups) and all(v and set(v)==set(size_groups[0] or []) for v in size_groups)
        record['size'] = ','.join(size_groups[0]) if valid else None
        if not valid:
            ctx.log('색상별 공통 사이즈 미확정: 빈칸','WARNING')
    if ctx.enabled('price'):
        record['price'] = common([money(main.get('sellprice'))]+price_values)
        if record['price'] is None:
            ctx.log('공식 판매가/옵션별 판매가 공통값 미확정: 빈칸','WARNING')


def clean_name(name, code):
    # Store-name suffixes are not part of the garment name. Keep meaningful
    # parentheses such as (주니어); only empty brand/code brackets are removed.
    name = re.sub(r'_(?:NC|뉴코아|이천일)[^_\s]{0,20}점\s*$','',name)
    for token in ('앙떼떼','ENTETEE',code):
        name = re.sub(r'(?<![A-Za-z0-9])'+re.escape(token)+r'(?![A-Za-z0-9])','',name,flags=re.I)
    name = re.sub(r'\[\s*\]|\(\s*\)','',name)
    return re.sub(r'\s+',' ',name).strip(' _-')


def white_background(image):
    # A corroborating gallery signal only. A seasonal model set or fabric detail
    # may have a complex background; never reject such images for this metric.
    im = image.convert('RGB').resize((120,120))
    count = 0
    for box in ((0,0,15,15),(105,0,120,15),(0,45,12,65),(108,45,120,65),
                (0,90,12,110),(108,90,120,110),(0,105,15,120),(105,105,120,120)):
        stat = ImageStat.Stat(im.crop(box))
        count += min(stat.mean)>=225 and max(stat.stddev)<=18
    return count>=5


class ImageRejected(CollectionError):
    """A decoded asset is not a usable product photograph."""


def image_info(ctx, url, cache):
    if url in cache:
        return cache[url]
    raw = fetch(ctx,url,MAX_BYTES)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt,w,h = image.format,image.width,image.height
            if fmt not in ('JPEG','PNG','WEBP') or getattr(image,'is_animated',False):
                raise ImageRejected('정지 JPEG/PNG/WebP가 아닌 이미지: '+url)
            if w*h>40_000_000:
                raise ImageRejected('이미지 해독 픽셀 한도 초과: '+url)
            image.load()
            info = (fmt,w,h,hashlib.sha256(raw).hexdigest(),white_background(image))
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
        raise ImageRejected('손상 또는 이미지가 아닌 응답: '+url) from exc
    cache[url] = info
    return info


NEGATIVE = re.compile(r'저작권|무단.?사용|배너|배송|교환|반품|쿠폰|copyright|banner|shipping|delivery|refund|care_',re.I)
SELLER_PHOTO = re.compile(r'매장.?촬영|직접.?촬영|셀러.?컷|매장.?컷|seller.?photo|store.?photo',re.I)


def gallery(ctx, main, cache):
    result, white, complex_count = [],0,0
    for item in main.get('itemImage',[]):
        ctx.checkpoint()
        if str(item.get('imageDcode')) not in ('010','030') or not item.get('imagePath'):
            continue
        url = urljoin('https://item.elandrs.com/',item['imagePath'])
        label = ' '.join(str(item.get(k) or '') for k in ('imageName','imageFileName'))+' '+urlsplit(url).path
        if NEGATIVE.search(label) or SELLER_PHOTO.search(label) or re.search(r'사이즈표|실측표|size.?chart|size.?guide|collage|콜라주',label,re.I):
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
        ctx.log('갤러리 배경 보조 검사: 흰 배경 %s장, 장면형/복잡 배경 %s장. 첫 사진만으로 탈락시키지 않고 본사 장면형·디테일 컷 유지. 색상 매핑 시 확인하세요.'%(white,complex_count),'INFO' if white else 'WARNING')
    return result


def detail_images(ctx, detail, code, cache):
    desc = detail.get('itemDesc') or {}
    markup = desc.get('itemDescdocumentCon') or ''
    if not markup.strip():
        ctx.log('공식 상품 본문 없음: 상세 빈칸으로 계속','WARNING')
        return []
    fragment = BeautifulSoup(html.unescape(markup),'html.parser')
    result = []
    for image in fragment.find_all('img'):
        ctx.checkpoint()
        url = urljoin(SITE,image.get('src') or image.get('data-src') or '')
        if not url or url.startswith('data:'):
            continue
        parsed = urlsplit(url)
        name = parsed.path.rsplit('/',1)[-1]
        context = name+' '+str(image.get('alt') or '')+' '+str(image.get('title') or '')
        if NEGATIVE.search(context) or SELLER_PHOTO.search(context):
            ctx.log('공통 배너/저작권/셀러 안내 제외 (요청 안 함): '+url,'INFO')
            continue
        studio = parsed.hostname=='img-studio.elandmall.com' and parsed.path.startswith('/Winstudio/')
        product = bool(re.fullmatch(re.escape(code)+r'(?:[_-][A-Za-z0-9]+)?\.(?:jpg|jpeg|png|webp)',name,re.I))
        size = bool(re.fullmatch(r'AT[A-Za-z0-9_-]*SIZE[A-Za-z0-9_-]*\.(?:jpg|jpeg|png|webp)',name,re.I))
        cdn = parsed.hostname=='item.elandrs.com' and parsed.path.startswith('/r/image/item/')
        if not ((studio and (product or size)) or cdn):
            raise CollectionError('상품 본문/공통 안내 구분 미확인 이미지: '+url)
        # Observed official HTTPS endpoint serves the original HTTP-authored URL.
        if parsed.scheme=='http':
            url = 'https:'+url[5:]
        try:
            image_info(ctx,url,cache)
        except MissingResource as exc:
            ctx.log('존재하지 않는 상세 원본 404 제외: '+str(exc),'WARNING')
            continue
        if url not in result:
            result.append(url)
    if not result:
        raise CollectionError('본문이 있지만 허용된 상품 이미지 파싱 결과 없음')
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
    for link in links:
        ctx.checkpoint()
        try:
            ctx.stage('공식 브랜드·품번 확인: '+link)
            data = product(ctx,link,code)
            if data is None:
                continue
            main = data['itemMain']
            name = clean_name(main['itemName'],code)
            if not name:
                raise CollectionError('정리 후 필수 상품명 없음')
            palette,actual = colors(ctx,options(ctx,main))
            record = {'product_code':code,'product_name':name,'product_url':link}
            optional_values(ctx,main,actual,record)
            photos = gallery(ctx,main,cache)
            if not photos:
                raise CollectionError('유효 개별 상품 사진 0장: '+link)
            body = detail_images(ctx,data,code,cache) if ctx.enabled('detail_page') else []
            products.append((record,palette,photos,body))
        except (CollectionError,KeyError,UnicodeError) as exc:
            message = link+' / '+str(exc)
            ctx.fail_item(message);errors.append(message)
    if not products:
        message = '정확한 앙떼떼 브랜드/품번과 색상·사진을 확인할 수 없음: '+code
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
    record = {'product_code':code,'product_name':products[0][0]['product_name']}
    for field in ('size','price','regular_price','category'):
        if ctx.enabled(field):
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
        ctx.emit_unmapped(dict(record,product_url=link),palette,url,shared_detail_html=record.get('detail_page',''))
    if errors:
        raise CollectionError('일부 링크 실패. 성공 후보 보존, 입력 행 미완료: '+'; '.join(errors))
    ctx.log('%s: %s개 링크, %s개 색상, %s장 사진 수집'%(code,len(products),len(palette),len(photos)),'SUCCESS')
