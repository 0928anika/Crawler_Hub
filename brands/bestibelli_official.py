PORTAL_API_VERSION = 1

import hashlib
import html
import io
import json
import re
import time
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image

SITE = 'https://brand.naver.com'
STORE = 'bestibelli'
POLICY = dict(checked_on='2026-11-12', source='https://marketplace.coupang.com/mba/listing',
              min_side=500, max_bytes=10000000, max_aspect=1.8, formats=['JPEG','PNG','WEBP'],
              note='정지 WebP는 사용자 등록 환경 정책. 비율은 프로젝트 보조 기준.')
# 실제 공식 검색 q=BPDJX7626 -> 13763163321, 모델명과 channelUid 대조.
# 일반 Edge 브라우저가 받은 공개 product 응답을 사용한다. 인증정보/쿠키 주입 없음.
# 색상은 optionCombinations/options, 사진은 productImages. 색감으로 배정하지 않는다.


def state_from(source):
    doc = BeautifulSoup(source,'html.parser')
    for node in doc.find_all('script'):
        text = node.get_text()
        match = re.search(r'window\.__PRELOADED_STATE__\s*=\s*',text)
        if match:
            raw = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',lambda m:'null' if m[0]=='undefined' else m[0],text[match.end():])
            data = json.JSONDecoder().raw_decode(raw)[0]
            if (data.get('channel') or {}).get('brandUrl') != STORE:
                raise ValueError('지정한 베스띠벨리 네이버 공식 스토어의 응답이 아닙니다.')
            return data
    raise ValueError('공개 스토어 데이터가 없습니다. 접근 제한/페이지 구조를 확인하세요.')


def navigate(ctx,page,url):
    ctx.checkpoint()
    response = page.goto(url,wait_until='domcontentloaded',timeout=30000)
    ctx.checkpoint()
    if response and response.status in (401,403,429):
        raise ValueError('네이버 접근 제한 HTTP '+str(response.status)+' / 제한을 우회하지 않고 중단합니다.')
    if response and response.status >= 400:
        raise ValueError('네이버 페이지 HTTP '+str(response.status)+' / '+url)
    parsed = urlsplit(page.url)
    if parsed.hostname != 'brand.naver.com' or not parsed.path.startswith('/'+STORE+'/'):
        raise ValueError('지정한 공식 스토어 외부로 이동했습니다.')


def discover(ctx,page,code):
    pending = [SITE+'/'+STORE+'/search?'+urlencode({'q':code})]
    visited, seen, found = set(),set(),{}
    channel_uid = None
    models = set()
    total = None
    while pending:
        url = pending.pop(0)
        if url in visited:continue
        if len(visited)>=30:raise ValueError('검색 페이지 안전 한도 초과')
        visited.add(url);navigate(ctx,page,url)
        state = state_from(page.content());search = state.get('keywordSearch') or {}
        channel_uid = (state.get('channel') or {}).get('channelUid')
        rows = search.get('simpleProducts')
        if search.get('keyword','').upper()!=code or not isinstance(rows,list) or not isinstance(search.get('totalCount'),int):
            raise ValueError('공식 품번 검색 응답/검색어 확인 실패')
        if total is not None and total!=search['totalCount']:
            raise ValueError('검색 중 상품 수가 변경됐습니다. 다시 실행하세요.')
        total = search['totalCount']
        for row in rows:
            number = str(row.get('id') or '')
            if not number.isdecimal():raise ValueError('공식 검색 상품번호 누락')
            seen.add(number)
            model = str((row.get('naverShoppingSearchInfo') or {}).get('modelName') or '').strip().upper()
            models.add(model or '모델명 미표시')
            if model==code:
                found[number] = SITE+'/'+STORE+'/products/'+number
        if len(seen)>=total:break
        for href in page.locator('a[href]').evaluate_all('(ns)=>ns.map(n=>n.href)'):
            parsed = urlsplit(href);q = parse_qs(parsed.query)
            if parsed.hostname=='brand.naver.com' and parsed.path=='/'+STORE+'/search' and q.get('q')==[code] and href not in visited:
                pending.append(href)
        if not pending:raise ValueError('검색 결과는 더 있으나 다음 페이지 링크를 확인할 수 없습니다. 일부만 완료하지 않습니다.')
    if not found:
        if total==0:
            raise ValueError('[검색 결과 0건] '+code+' / 네이버 공식 스토어 검색에 정상 접근했으나 응답 상품 수가 0건입니다.')
        raise ValueError('[품번 일치 실패] '+code+' / 검색 '+str(total)+'건은 조회됐으나 공식 모델명이 일치하지 않습니다. 조회 모델명: '+', '.join(sorted(models)))
    ctx.log('공식 검색 확인: '+code+' / 검색 '+str(total)+'건, 정확한 모델명 일치 '+str(len(found))+'건','INFO')
    if not channel_uid:raise ValueError('공식 스토어 채널 식별자 누락')
    return found,channel_uid


def wait_data(ctx,page,captured,key):
    deadline = time.monotonic()+25
    while key not in captured:
        ctx.checkpoint()
        if captured.get('error'):raise ValueError(captured['error'])
        if time.monotonic()>deadline:raise ValueError('공식 '+key+' 응답 로딩 시간 초과')
        page.wait_for_timeout(100)
    return captured[key]


def color_rows(product):
    groups = product.get('options') or []
    position = next((i+1 for i,v in enumerate(groups) if str(v.get('groupName','')).strip().upper() in ('색상','컬러','COLOR')),None)
    if position is None:raise ValueError('공식 색상 옵션 그룹 확인 실패')
    rows = product.get('optionCombinations') or []
    labels = list(dict.fromkeys(str(row.get('optionName'+str(position)) or '').strip() for row in rows))
    labels = [label for label in labels if label]
    if not labels:raise ValueError('공식 색상 후보 없음')
    colors = []
    for label in labels:
        match = re.fullmatch(r'([A-Z0-9]+)\((.+)\)',label)
        colors.append(dict(color_code=match[1],color_name=match[2]) if match else dict(color_code='',color_name=label))
    return colors,position,labels


def common_sizes(ctx,product,color_position,labels):
    position = next((i+1 for i,v in enumerate(product.get('options') or []) if str(v.get('groupName','')).strip().upper() in ('사이즈','SIZE')),None)
    if position is None:
        ctx.log('공식 사이즈 옵션 없음','WARNING');return ''
    values = []
    for color in labels:
        sizes = list(dict.fromkeys(str(row.get('optionName'+str(position)) or '') for row in product.get('optionCombinations',[]) if row.get('optionName'+str(color_position))==color))
        values.append([s for s in sizes if s])
    if values and all(value==values[0] for value in values):return ','.join(values[0])
    ctx.log('색상별 사이즈가 달라 수동 매핑의 공통 사이즈는 빈칸으로 저장합니다.','WARNING')
    return ''


def money(value):
    if isinstance(value,bool):return None
    try:
        number=float(value)
        return int(number) if number>0 and number.is_integer() else None
    except (ValueError,TypeError,OverflowError):return None


def product_name(value,code):
    value=re.sub(r'[\[(]\s*'+re.escape(code)+r'\s*[\])]', ' ', value, flags=re.I)
    value=re.sub(r'(?<![A-Z0-9_-])'+re.escape(code)+r'(?![A-Z0-9_-])',' ',value,flags=re.I)
    for alias in ('베스띠벨리','BESTIBELLI'):
        value=re.sub(r'(?<![A-Za-z0-9가-힣])'+re.escape(alias)+r'(?![A-Za-z0-9가-힣])',' ',value,flags=re.I)
    value=re.sub(r'\[\s*\]|\(\s*\)',' ',value)
    return re.sub(r'\s+',' ',value).strip(' _-/')


def common_price(ctx,product):
    values = {row.get('price') for row in product.get('optionCombinations',[])}
    if values!={0}:
        ctx.log('옵션 추가금이 있거나 불명확하여 공통 판매가는 빈칸으로 저장합니다.','WARNING');return None
    benefits = product.get('benefitsView') or {}
    value = benefits.get('discountedSalePrice') if 'discountedSalePrice' in benefits else product.get('salePrice')
    result = money(value)
    if result is None:ctx.log('공식 현재 판매가 확인 불가: 빈칸','WARNING')
    return result


def public_image(ctx,url,cache,detail=False):
    parsed = urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('shop-phinf.pstatic.net','images.styleid.co.kr'):
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


def gallery(ctx,product,cache):
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
    if not accepted:raise ValueError('수동 매핑할 유효한 개별 상품 사진이 없습니다.')
    return accepted


def detail(ctx,product,contents,code,cache):
    parts,seen=[],set()
    doc=BeautifulSoup(contents.get('renderContent') or '','html.parser')
    for node in doc.select('img'):
        url=node.get('data-src') or node.get('src') or ''
        parsed=urlsplit(url)
        # 실제 상품 폴더만 채택. event, styleid_public/info의 공통/배송/관리 이미지는 요청하지 않음.
        if parsed.hostname!='images.styleid.co.kr' or not parsed.path.startswith('/swnetworks/styleid_img/bb/') or '/'+code+'/' not in parsed.path:continue
        if url in seen:continue
        seen.add(url)
        if public_image(ctx,url,cache,detail=True):parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="max-width:100%;height:auto"></p>')
    if not seen:ctx.log('품번과 연결된 공식 상세 이미지 없음: 해당 부분 생략','WARNING')
    value='<div>'+''.join(parts)+'</div>' if parts else ''
    if len(value.encode('utf-16-le'))//2>32767:raise ValueError('상세 HTML Excel 셀 한도 초과')
    return value


def crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{4,29}',code):raise ValueError('기본 품번만 입력하세요. 수동 색상 매핑 방식입니다.')
    if not (ctx.enabled('main_image') or ctx.enabled('additional_images')):raise ValueError('수동 매핑은 대표 또는 추가이미지 선택이 필요합니다.')
    from playwright.sync_api import sync_playwright
    failures=[];records=[];palette=[];images=[];fragments=[];cache={}
    try:
        with sync_playwright() as engine:
            # 이 PC에서 일반 Edge 공개 접속 검증. 로그인 프로필/인증 쿠키를 사용하지 않는다.
            try:browser=engine.chromium.launch(headless=True,channel='msedge')
            except Exception as exc:raise ValueError('Microsoft Edge와 Playwright 실행 환경이 필요합니다: '+str(exc)) from exc
            with browser:
                page=browser.new_page();page.set_default_timeout(15000)
                def route(request):
                    parsed=urlsplit(request.request.url);path=parsed.path
                    if request.request.resource_type in ('image','media','font'):return request.abort()
                    if parsed.hostname=='brand.naver.com' and path.startswith('/n/'):
                        main=bool(re.fullmatch(r'/n/v2/channels/[^/]+/products/\d+',path))
                        content=ctx.enabled('detail_page') and '/products/' in path and '/contents/' in path
                        if not main and not content:return request.abort()
                    return request.continue_()
                page.route('**/*',route)
                ctx.stage('베스띠벨리 네이버 공식 품번 검색: '+code)
                links,channel_uid=discover(ctx,page,code)
                for number,url in links.items():
                    captured={}
                    def receive(response):
                        path=urlsplit(response.url).path
                        expected='/n/v2/channels/'+channel_uid+'/products/'+number
                        key='product' if path==expected else 'contents' if path.startswith(expected+'/contents/') and ctx.enabled('detail_page') else None
                        if not key:return
                        try:
                            data=response.body()
                            if len(data)>6000000:raise ValueError('공식 응답 크기 한도 초과')
                            if response.status!=200:raise ValueError('공식 '+key+' HTTP '+str(response.status))
                            captured[key]=json.loads(data)
                        except Exception as exc:captured['error']=str(exc)
                    page.on('response',receive)
                    try:
                        ctx.stage('공식 상품 조회: '+number);navigate(ctx,page,url)
                        product=wait_data(ctx,page,captured,'product')
                        if str(product.get('id'))!=number or (product.get('channel') or {}).get('channelUid')!=channel_uid or str((product.get('naverShoppingSearchInfo') or {}).get('modelName') or '').strip().upper()!=code:
                            raise ValueError('상품번호/공식 모델명/스토어 불일치')
                        colors,color_position,labels=color_rows(product)
                        for color in colors:
                            if color not in palette:palette.append(color)
                        record=dict(product_code=code,product_name=product_name(str(product.get('name') or ''),code),product_url=page.url)
                        if not record['product_name']:raise ValueError('공식 상품명 누락')
                        if ctx.enabled('size'):record['size']=common_sizes(ctx,product,color_position,labels)
                        if ctx.enabled('price'):record['price']=common_price(ctx,product)
                        if ctx.enabled('regular_price'):
                            record['regular_price']=None;ctx.log('독립 정상가 필드 미확인: salePrice를 정상가로 대체하지 않습니다.','WARNING')
                        if ctx.enabled('category'):record['category']=str((product.get('category') or {}).get('wholeCategoryName') or '')
                        if ctx.enabled('detail_page'):
                            markup=detail(ctx,product,wait_data(ctx,page,captured,'contents'),code,cache)
                            if markup and markup not in fragments:fragments.append(markup)
                        for image in gallery(ctx,product,cache):
                            if image not in images:images.append(image)
                        records.append(record)
                    except Exception as exc:
                        failures.append('상품 '+number+' / '+str(exc))
                    finally:page.remove_listener('response',receive)
    except Exception as exc:
        ctx.fail_item(str(exc));raise
    if records and images and palette:
        output=dict(records[0])
        for field in ('size','price','category'):
            if ctx.enabled(field) and any(r.get(field)!=records[0].get(field) for r in records):
                output[field]=None if field=='price' else '';ctx.log('상품 링크별 '+field+' 값이 달라 공통 값은 빈칸','WARNING')
        if ctx.enabled('detail_page'):
            output['detail_page']=''.join(fragments)
            if len(output['detail_page'].encode('utf-16-le'))//2>32767:failures.append('공통 상세 HTML Excel 셀 한도 초과')
        if not any('셀 한도' in reason for reason in failures):
            for image in images:
                ctx.checkpoint();ctx.emit_unmapped(output,palette,image,shared_detail_html=output.get('detail_page',''))
    elif not failures:failures.append('상품 사진 또는 공식 색상 후보 수집 결과 없음')
    if failures:
        for reason in failures:ctx.fail_item(reason)
        raise RuntimeError('일부 상품 수집 미완료: '+' / '.join(failures))
