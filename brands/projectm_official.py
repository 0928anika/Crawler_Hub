PORTAL_API_VERSION = 1

import hashlib
import html
import http.client
import io
import json
import re
import socket
import time
from urllib.parse import urlencode, urljoin, urlsplit
import urllib.error
import urllib.request

from bs4 import BeautifulSoup
from PIL import Image, ImageStat, UnidentifiedImageError
from crawler_rules import gallery_slots, validate_gallery_image

SITE = 'https://www.goodwearmall.com'
SEARCH = 'https://display.goodwearmall.com/search'
IMAGES = 'https://img.goodwearmall.com'
BRAND = 'EBR'
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


def search(ctx, code):
    ctx.stage('프로젝트엠 공개 검색: '+code)
    soup = BeautifulSoup(fetch(ctx,SEARCH+'?'+urlencode({'keyword':code}),6_000_000),'html.parser')
    skus = []
    for anchor in soup.select('a[href]'):
        parsed = urlsplit(urljoin(SEARCH,anchor['href']))
        match = re.fullmatch(r'/product/([A-Za-z0-9]+)/detail',parsed.path)
        if parsed.hostname=='www.goodwearmall.com' and match and match[1].upper().startswith(code):
            sku = match[1].upper()
            if sku not in skus:
                skus.append(sku)
    if not skus:
        raise CollectionError('검색에서 해당 기본 품번 상품 링크 없음: '+code)
    # Search seeds are expanded through every official goods.colorList entry.
    # Color completeness does not depend on the first search page's card limit.
    return skus


def load_product(ctx, sku, code):
    ctx.stage('공식 상품·색상 JSON 확인: '+sku)
    try:
        data = json.loads(fetch(ctx,SITE+'/product/api/view.json?'+urlencode({'godNo':sku})).decode('utf-8-sig'))
        if data.get('result')!='Y':
            raise CollectionError('공개 상품 API 확인 불가: '+sku)
        goods = data['goods'];god = goods['god']
        if goods['sysBrnd']['brndId']!=BRAND or str(god.get('dsgnGrpNo','')).upper()!=code:
            ctx.log('다른 공식 브랜드/기본 품번 제외: '+sku,'INFO')
            return None
        if str(god.get('godNo','')).upper()!=sku:
            raise CollectionError('요청 상품 ID와 응답 ID 불일치: '+sku)
        if not str(god.get('godNm') or '').strip() or not str(god.get('colorCd') or '').strip():
            raise CollectionError('필수 상품명/공식 색상코드 누락: '+sku)
        return goods
    except (KeyError,TypeError,ValueError) as exc:
        raise CollectionError('공식 상품 JSON 형식 변경: '+sku) from exc


def group_products(ctx, code):
    queue = search(ctx,code);seen=set();goods_list=[];errors=[]
    while queue:
        ctx.checkpoint()
        sku = queue.pop(0)
        if sku in seen:
            continue
        seen.add(sku)
        if len(seen)>100:
            raise CollectionError('같은 기본 품번 색상 그래프 100개 한도 초과')
        try:
            goods = load_product(ctx,sku,code)
            if goods is None:
                continue
            goods_list.append(goods)
            for row in goods.get('colorList',[]):
                ctx.checkpoint()
                color = row.get('god') or {}
                if str(color.get('dsgnGrpNo','')).upper()!=code:
                    raise CollectionError('색상 목록의 기본 품번 불일치: '+sku)
                other = str(color.get('godNo') or '').strip().upper()
                if not re.fullmatch(r'[A-Z0-9]+',other):
                    raise CollectionError('색상 목록의 실제 상품 ID 누락/형식 변경')
                if other not in seen and other not in queue:
                    queue.append(other)
        except CollectionError as exc:
            errors.append(str(exc));ctx.fail_item(str(exc))
    if not goods_list:
        raise CollectionError('정확한 기본 품번/브랜드/색상 정보가 없음: '+code)
    return goods_list,errors


def amount(value):
    if isinstance(value,bool):return None
    if isinstance(value,(int,float)):
        return int(value) if value>0 and float(value).is_integer() else None
    text=str(value or '').strip()
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)',text):return None
    value=int(text.replace(',',''))
    return value if value>0 else None


def clean_name(name, code, sku):
    for token in ('프로젝트엠','PROJECT M',sku,code):
        name=re.sub(r'(?<![A-Za-z0-9])'+re.escape(token)+r'(?![A-Za-z0-9])','',name,flags=re.I)
    return re.sub(r'\s+',' ',re.sub(r'\(\s*\)|\[\s*\]','',name)).strip(' _-')


def category(ctx, sku):
    text=fetch(ctx,SITE+'/product/'+sku+'/detail').decode('utf-8-sig')
    pairs=re.findall(r"var key = '(stdCtgryNm_\d+)';\s*var value = '([^']*)';",text)
    values=[html.unescape(value) for _,value in sorted(pairs,key=lambda pair:int(pair[0].rsplit('_',1)[1])) if value]
    if not values:ctx.log('공식 분류 메타데이터 없음: '+sku,'WARNING')
    return ' > '.join(values) or None


def picture(ctx,url,cache,gallery=False):
    if url in cache:
        if not gallery or cache[url].get('gallery_checked'):
            return cache[url]
        # A URL first seen in a detail-only role needs one palette validation
        # if a later colour explicitly uses it as an active gallery photograph.
    raw=fetch(ctx,url,MAX_BYTES)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            fmt,w,h=image.format,image.width,image.height
            if fmt not in ('JPEG','PNG','WEBP') or getattr(image,'is_animated',False):
                raise CollectionError('정지 JPEG/PNG/WebP가 아닌 이미지: '+url)
            if w*h>40_000_000:raise CollectionError('이미지 해독 크기 초과')
            image.load()
            # Very small centred paper/tag against a white sheet is not a
            # usable garment shot. Retain it in original shared detail instead.
            small_label=False
            if gallery:
                sample=image.convert('L').resize((120,160))
                mask=sample.point(lambda v:255 if v<230 else 0)
                box=mask.getbbox()
                small_label=bool(box and (box[2]-box[0])*(box[3]-box[1])/(120*160)<.20)
            info={'url':url,'digest':hashlib.sha256(raw).hexdigest(),'format':fmt,'width':w,'height':h,
                  'small_label':small_label,'gallery_checked':gallery}
            if gallery and min(w,h)>=500 and max(w,h)/min(w,h)<=1.8 and not small_label:
                # Mapping is independently verified by the API's exact godNo
                # and colorCd. THNAIL roles and the original inspected gallery
                # provide content-role evidence; dimensions alone do not.
                info['validation']=validate_gallery_image(raw,role='product_shot',mapping_verified=True,
                    single_shot_verified=True,mapping_evidence='goods.god.godNo/colorCd + goods.godImgList',
                    content_evidence='THNAIL active individual product/model gallery; no chart/banner/label metadata')
            else:
                info['validation']={'passed':False,'reasons':['상품컷 픽셀/비율 또는 작은 종이/라벨 신호']}
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
        raise CollectionError('실제 이미지 해독 실패: '+url) from exc
    cache[url]=info
    return info


NEGATIVE=re.compile(r'배송|교환|반품|저작권|무단.?사용|배너|banner|shipping|refund|copyright|care_',re.I)


def gallery_assets(ctx, goods, cache, need_gallery, need_detail):
    result=[]
    sku=goods['god']['godNo'];code=goods['god']['colorCd']
    for row in goods.get('godImgList',[]):
        ctx.checkpoint()
        if row.get('imgTpCd')!='THNAIL' or not row.get('imgUrl'):continue
        turn=int(row.get('imgTurn') or 0)
        active=row.get('imgUseYn')=='Y' or turn==100
        detail_role=101<=turn<=215
        if not ((need_gallery and active) or (need_detail and detail_role)):continue
        url=urljoin(IMAGES,row['imgUrl'])
        if NEGATIVE.search(urlsplit(url).path):
            ctx.log('공통 안내 이미지 제외 (요청 안 함): '+url,'INFO');continue
        # Only an explicit source URL is used; no _01/_02 URL guessing.
        try:info=picture(ctx,url,cache,gallery=bool(need_gallery and active))
        except MissingResource as exc:
            ctx.log('원본 갤러리 404 제외: '+str(exc),'WARNING');continue
        result.append(dict(info,sku=sku,color=code,active=active,detail_role=detail_role))
    return result


def clean_table(markup):
    soup=BeautifulSoup(markup,'html.parser')
    for node in soup.select('script,style,iframe,object,form'):node.decompose()
    tables=[]
    for table in soup.find_all('table'):
        rows=table.find_all('tr')
        if len(rows)<2 or not any(len(row.find_all(['th','td'],recursive=False))>=2 for row in rows):
            continue
        output=['<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">']
        def row_html(row):
            cells=[]
            for cell in row.find_all(['th','td'],recursive=False):
                attrs=''
                for attr in ('rowspan','colspan'):
                    value=str(cell.get(attr) or '')
                    if re.fullmatch(r'[1-9]\d?',value):attrs+=' '+attr+'="'+value+'"'
                cells.append('<'+cell.name+attrs+'>'+html.escape(cell.get_text(' ',strip=True))+'</'+cell.name+'>')
            return '<tr>'+''.join(cells)+'</tr>'
        for section in table.children:
            if getattr(section,'name',None) in ('thead','tbody','tfoot'):
                output.append('<'+section.name+'>')
                output.extend(row_html(row) for row in section.find_all('tr',recursive=False))
                output.append('</'+section.name+'>')
            elif getattr(section,'name',None)=='tr':output.append(row_html(section))
        output.append('</table>');tables.append(''.join(output))
    return tables


def body_fragment(goods):
    # Exactly the same source selection as productform.init.godCont. Do not
    # import notices, care or AFF_NTFC_DETAIL_HTML (separate product notice).
    value=''
    if goods.get('godCaUseYn')=='Y':
        value=next((r.get('godDetailCont') or '' for r in goods.get('godDetailList',[])
                    if r.get('godDetailTpCd')=='GOD_DETAIL_ASSTN'),'')
    return value or goods['god'].get('godDetailCont') or ''


def text_size_table(fragment):
    # Conservative fallback for explicit labelled text, not guessed values.
    text=BeautifulSoup(fragment,'html.parser').get_text('\n',strip=True)
    if not re.search(r'사이즈|실측|SIZE',text,re.I):return []
    measurements=r'어깨너비|어깨넓이|어깨|가슴둘레|가슴단면|가슴|허리둘레|허리단면|허리|엉덩이둘레|허벅지둘레|소매길이|소매|총기장|총장|밑위|밑단'
    rows=[];headers=None
    for line in text.splitlines():
        match=re.match(r'^([A-Z]{1,4}(?:\(\d+\))?|\d{2,3})\s*[:：]\s*(.+)$',line)
        if not match:continue
        values=re.findall(r'('+measurements+r')\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(cm|mm)?',match[2],re.I)
        if len(values)<2:continue
        labels=[v[0] for v in values]
        if headers is None:headers=labels
        if labels!=headers:raise CollectionError('텍스트 사이즈표의 치수 항목이 행마다 달라 확인 필요')
        rows.append([match[1]]+[v[1]+((' '+v[2]) if v[2] else '') for v in values])
    if len(rows)<2:return []
    markup='<table><thead><tr>'+''.join('<th>'+html.escape(v)+'</th>' for v in ['사이즈']+headers)+'</tr></thead><tbody>'
    markup+=''.join('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'
    return clean_table(markup)


def size_material(ctx,goods,cache):
    markup=goods.get('gdSizeLktb') or ''
    source='goods.gdSizeLktb'
    if not markup:
        for kind in ('SIZE_LKTB','MOBILE_SIZE_LKTB'):
            value=next((r.get('godDetailCont') or '' for r in goods.get('godDetailList',[]) if r.get('godDetailTpCd')==kind),'')
            if value:markup=value;source='godDetailList.'+kind;break
    fragment=body_fragment(goods)
    if markup:
        tables=clean_table(markup)
        if not tables:raise CollectionError('공식 사이즈표가 있으나 파싱 결과 없음: '+source)
    else:
        body=BeautifulSoup(fragment,'html.parser')
        for node in body.select('script,style,iframe'):node.decompose()
        tables=[]
        for table in body.find_all('table'):
            text=table.get_text(' ',strip=True)
            if re.search(r'사이즈|SIZE',text,re.I) and re.search(r'가슴|허리|총장|기장|어깨|소매',text):
                tables.extend(clean_table(str(table)))
        if not tables:tables=text_size_table(str(body))
    diagram=goods.get('acmsrImgUrl') or ''
    if not diagram:
        body=BeautifulSoup(fragment,'html.parser')
        for image in body.find_all('img'):
            url=image.get('src') or image.get('data-src') or ''
            if re.search(r'/realsize/|측정그림|사이즈그림',url+' '+str(image.get('alt') or ''),re.I):
                diagram=url;break
    if diagram:
        diagram=urljoin(IMAGES,diagram);picture(ctx,diagram,cache)
    if not tables:ctx.log('공식 실측표 없음: 별도 생성하지 않고 나머지 상세 계속','WARNING')
    return diagram,tables


def detail_markup(ctx, products, assets, cache):
    parts=[];digests=set();urls=set()
    # Preserve official product detail, with all colour images together. It is
    # independent of the final main/additional selection. Mixed-colour images
    # and original labels remain here, never a reconstructed notice table.
    for goods in products:
        fragment=BeautifulSoup(body_fragment(goods),'html.parser')
        for node in fragment.select('script,style,iframe,object,form'):node.decompose()
        for image in fragment.find_all('img'):
            url=urljoin(SITE,image.get('src') or image.get('data-src') or '')
            if not url or NEGATIVE.search(urlsplit(url).path+' '+str(image.get('alt') or '')):continue
            if '/realsize/' in urlsplit(url).path:continue
            info=picture(ctx,url,cache)
            if info['digest'] not in digests:
                digests.add(info['digest']);urls.add(url)
                parts.append('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="max-width:100%;height:auto">')
    for item in assets:
        ctx.checkpoint()
        if item['detail_role'] and item['digest'] not in digests:
            digests.add(item['digest']);urls.add(item['url'])
            parts.append('<img src="'+html.escape(item['url'],quote=True)+'" alt="상품 상세" style="max-width:100%;height:auto">')
    diagrams=[];tables=[]
    for goods in products:
        diagram,table_list=size_material(ctx,goods,cache)
        if diagram and diagram not in diagrams:diagrams.append(diagram)
        for table in table_list:
            if table not in [t for _,t in tables]:tables.append((goods['god']['colorCd'],table))
    if diagrams or tables:
        parts.append('<h3>실측사이즈</h3>')
        # Unit and measurement note are present in the official popupSize.html.
        parts.append('<p>단위 : cm</p>')
        for url in diagrams:
            parts.append('<img src="'+html.escape(url,quote=True)+'" alt="사이즈 측정 그림" style="max-width:100%;height:auto">')
        for color,table in tables:
            if len(tables)>1:parts.append('<h4>'+html.escape(color)+' 실측사이즈</h4>')
            parts.append(table)
        parts.append('<p>소재 특성 및 측정 방법에 따라 약 1~3cm의 오차가 발생할 수 있으며, 이는 불량 사유에 해당하지 않는 점 참고 부탁드립니다.</p>')
    markup=''.join(parts)
    if not markup:raise CollectionError('공식 상품 본문/사이즈 자료 모두 없음')
    if len(markup)>32767:raise CollectionError('정리한 공통 상세 HTML Excel 셀 한도 초과')
    return markup


def crawl(ctx):
    value=str(ctx.product_code).strip().upper()
    match=re.fullmatch(r'([A-Z0-9]+)(?:[_-]([A-Z0-9]+))?',value)
    if not match:
        ctx.fail_item('기본품번 또는 기본품번_공식색상코드를 입력하세요')
        raise CollectionError('품번 형식 오류')
    code,wanted=match[1],match[2]
    try:products,errors=group_products(ctx,code)
    except CollectionError as exc:ctx.fail_item(str(exc));raise
    selected=[g for g in products if not wanted or str(g['god']['colorCd']).upper()==wanted]
    if not selected:
        ctx.fail_item('해당 공식 색상코드 상품 없음: '+str(wanted))
        raise CollectionError('색상코드 확인 불가')
    need_gallery=ctx.enabled('main_image') or ctx.enabled('additional_images')
    need_detail=ctx.enabled('detail_page')
    cache={};assets=[]
    if need_gallery or need_detail:
        for goods in products:
            try:assets.extend(gallery_assets(ctx,goods,cache,need_gallery,need_detail))
            except CollectionError as exc:
                message=goods['god']['godNo']+' / '+str(exc);ctx.fail_item(message);errors.append(message)
    # Same bytes under multiple color IDs cannot prove one-color assignment.
    owners={}
    for item in assets:
        if item['active']:owners.setdefault(item['digest'],set()).add(item['color'])
    shared={digest for digest,colors in owners.items() if len(colors)>1}
    if shared and need_gallery:ctx.log('여러 색상에 공통인 사진 %s종: 자동 색상 갤러리에서는 제외하고 공통 상세에만 유지'%len(shared),'WARNING')
    markup=''
    if need_detail:
        try:markup=detail_markup(ctx,products,assets,cache)
        except CollectionError as exc:ctx.fail_item(str(exc));raise
    emitted=0;seen_colors=set()
    for goods in selected:
        ctx.checkpoint();god=goods['god'];sku=god['godNo'];color=str(god['colorCd'])
        try:
            if color in seen_colors:raise CollectionError('같은 색상코드의 상품 ID 중복: '+color)
            seen_colors.add(color)
            name=clean_name(god['godNm'],code,sku)
            if not name:raise CollectionError('정리 후 상품명 없음')
            color_name=str(god.get('colorNm') or '').strip()
            if color_name==color:color_name=''
            record={'product_code':code,'product_name':name,'color':color_name or color,
                    'color_name':color_name,'color_code':color,'product_url':SITE+'/product/'+sku+'/detail'}
            if ctx.enabled('size'):
                names=list(dict.fromkeys(str(row.get('itmNm') or '').strip() for row in goods.get('godItmExList',[])))
                record['size']=','.join(name for name in names if name)
            if ctx.enabled('price'):
                record['price']=amount((goods.get('dspGodPrc') or {}).get('salePrc'))
                if record['price'] is None:ctx.log('공식 판매가 누락/형식 불명확: '+sku,'WARNING')
            if ctx.enabled('regular_price'):
                ctx.checkpoint();record['regular_price']=amount(god.get('cvrPrc'))
                if record['regular_price'] is None:ctx.log('정상가 cvrPrc 누락/0/형식 불명확: 빈칸으로 계속 / '+sku,'WARNING')
                ctx.checkpoint()
            if ctx.enabled('category'):record['category']=category(ctx,sku)
            if need_gallery:
                candidates=[];seen=set()
                for item in assets:
                    if item['sku']!=sku or not item['active'] or item['digest'] in seen or item['digest'] in shared:continue
                    seen.add(item['digest'])
                    if item['validation']['passed']:
                        candidates.append({'url':item['url'],'validation':item['validation']})
                    else:ctx.log('갤러리 제외: '+str(item['validation'].get('reasons'))+' / '+item['url'],'WARNING')
                main,extras,warning=gallery_slots(candidates)
                if warning:ctx.log(warning,'WARNING')
                if not main:raise CollectionError('색상이 확인된 유효 개별 상품컷 0장: '+sku)
                if ctx.enabled('main_image'):record['main_image']=main
                if ctx.enabled('additional_images'):record['additional_images']=','.join(extras)
            if need_detail:record['detail_page']=markup
            ctx.emit_result(record);emitted+=1
        except CollectionError as exc:
            message=sku+' / '+str(exc);ctx.fail_item(message);errors.append(message)
    if not emitted:
        ctx.fail_item('완료된 공식 색상 결과 없음: '+code)
        raise CollectionError('수집 결과 0행')
    if errors:raise CollectionError('일부 색상/링크 실패. 성공 결과 보존, 입력 행 미완료: '+'; '.join(errors))
    ctx.log(code+': '+str(emitted)+'개 공식 색상 수집 완료','SUCCESS')
