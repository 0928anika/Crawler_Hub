PORTAL_API_VERSION = 1
import hashlib, html, io, json, re, time, base64, binascii
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, unquote
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image, gallery_slots
SITE = 'https://brand.naver.com'
STORE = 'crocs'
REVISION = '2026-11-16-crocs-naver-4-no-delivery-size-table'
POLICY = dict(min_side=500,max_bytes=10000000,max_aspect=1.8,formats=['JPEG','PNG','WEBP'])


def state_from(source):
    doc = BeautifulSoup(source,'html.parser')
    for node in doc.find_all('script'):
        text = node.get_text()
        match = re.search(r'window\.__PRELOADED_STATE__\s*=\s*',text)
        if match:
            raw = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',lambda m:'null' if m[0]=='undefined' else m[0],text[match.end():])
            data = json.JSONDecoder().raw_decode(raw)[0]
            if (data.get('channel') or {}).get('brandUrl') != STORE:
                raise ValueError('지정한 크록스 네이버 공식 스토어의 응답이 아닙니다.')
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


def money(value):
    if isinstance(value,bool):return None
    try:
        number=float(value)
        return int(number) if number>0 and number.is_integer() else None
    except (ValueError,TypeError,OverflowError):return None


def public_image(ctx,url,cache,detail=False):
    parsed = urlsplit(url)
    external=parsed.hostname in ('toossa.com','www.toossa.com') and unquote(parsed.path).startswith('/goodsimg/ilban/Crocs/')
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
    if not accepted:raise ValueError('공식 갤러리에서 규격을 통과한 상품 사진이 없습니다.')
    return accepted

def identity(product, number, code, channel_uid):
    model=str((product.get('naverShoppingSearchInfo') or {}).get('modelName') or '').strip().upper()
    if str(product.get('id'))!=number or (product.get('channel') or {}).get('channelUid')!=channel_uid or model!=code:
        raise ValueError('공식 스토어/상품번호/모델명 불일치: '+code)
    suffix=code.rsplit('-',1)[1]
    groups=product.get('options') or []
    size_pos=next((i+1 for i,g in enumerate(groups) if str(g.get('groupName','')).strip().upper() in ('사이즈','SIZE')),None)
    labels=[];sizes=[];deltas=[]
    for row in product.get('optionCombinations') or []:
        values=[str(row.get('optionName'+str(i+1)) or '').strip() for i in range(len(groups)) if i+1!=size_pos]
        label=' '.join(v for v in values if v)
        if label and label not in labels:labels.append(label)
        size=str(row.get('optionName'+str(size_pos)) or '').strip() if size_pos else ''
        if size and size not in sizes:sizes.append(size)
        deltas.append(row.get('price'))
    if len(labels)!=1:raise ValueError('색상별 모델의 단일 색상 옵션 확인 실패: '+str(labels))
    match=re.fullmatch(r'(.+?)\s*\(([A-Za-z0-9]{3})\)',labels[0])
    if match and match[2].upper()!=suffix:raise ValueError('모델 색상코드와 공식 옵션 불일치: '+labels[0])
    # Model identity and single-color scope are already verified above.
    # Some official pages publish the name alone (e.g. 아미 그린).
    color=match[1].strip() if match else labels[0].strip()
    name=re.sub(r'\[\s*크록스\s*(?:본사|공식)?\s*\]',' ',str(product.get('name') or ''),flags=re.I)
    name=re.sub(r'(?<![A-Z0-9])'+re.escape(code)+r'(?![A-Z0-9])',' ',name,flags=re.I)
    name=re.sub(r'\s*'+re.escape(color)+r'\s*$','',name)
    name=re.sub(r'\s+',' ',name).strip(' -_/')
    if not name:raise ValueError('정리 후 상품명 없음')
    return dict(product_code=code,product_name=name,color=color,color_name=color,color_code=suffix),sizes,deltas


def prices(ctx, product, deltas, has_original_label):
    base=money(product.get('discountedSalePrice')) or money((product.get('benefitsView') or {}).get('discountedSalePrice')) or money(product.get('salePrice'))
    unique=set(deltas)
    sale=None
    if len(unique)==1 and isinstance(next(iter(unique)),(int,float)):
        extra=next(iter(unique));sale=int(base+extra) if base and base+extra>0 else None
    else:ctx.log('사이즈별 추가금이 다르거나 불명확하여 판매가 빈칸','WARNING')
    original=money(product.get('salePrice'))
    normal=original if has_original_label and original and base and original>base else None
    if normal is None and ctx.enabled('regular_price'):ctx.log('독립 할인 전 가격 확인 불가: 정상가 빈칸','WARNING')
    return sale,normal


def cell(value):
    if isinstance(value,dict):return '<br>'.join(html.escape(str(k))+': '+cell(v) for k,v in value.items())
    if isinstance(value,list):return '<br>'.join(cell(v) for v in value)
    return html.escape(str(value if value is not None else ''))


def notice_table(ctx, product):
    values=(product.get('productInfoProvidedNoticeView') or {}).get('basic') or {}
    values={k:v for k,v in values.items() if not re.fullmatch(r'A\s*/?\s*S\s*책임자와\s*전화번호',str(k),re.I)}
    if not values:
        ctx.log('공식 상품정보 고시 없음: 표 생략','WARNING');return ''
    return '<h3>상품정보제공고시</h3><table border="1" cellpadding="8" style="width:100%;border-collapse:collapse"><tbody>'+''.join('<tr><th>'+html.escape(str(k))+'</th><td>'+cell(v)+'</td></tr>' for k,v in values.items())+'</tbody></table>'


def body_original(value):
    """Use declared originals; decode Naver proxy paths only for known public hosts."""
    url=str(value or '').strip()
    if url.startswith('//'):url='https:'+url
    parsed=urlsplit(url)
    if parsed.hostname=='proxy-smartstore.naver.net' and parsed.path.startswith('/img/'):
        token=unquote(parsed.path[len('/img/'):])
        try:
            decoded=base64.b64decode(token+'='*((-len(token))%4),validate=True).decode('utf-8')
        except (ValueError,UnicodeError,binascii.Error):return ''
        url=decoded if decoded.startswith(('http://','https://')) else 'https://'+decoded
    if url.startswith('http://'):url='https://'+url[7:]
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or '..' in unquote(parsed.path).split('/'):return ''
    if parsed.hostname=='shop-phinf.pstatic.net':return url
    if parsed.hostname in ('toossa.com','www.toossa.com') and unquote(parsed.path).startswith('/goodsimg/ilban/Crocs/'):return url
    return ''


def detail(ctx, product, contents, cache):
    doc=BeautifulSoup(contents.get('renderContent') or '','html.parser');parts=[];seen=set();body_count=0;excluded_body_count=0
    model=str((product.get('naverShoppingSearchInfo') or {}).get('modelName') or '').strip().upper();base=model.split('-',1)[0]
    for component in doc.select('.se-component'):
        if 'se-image' in component.get('class',[]):
            anchor=component.select_one('[data-linkdata]');node=component.select_one('img')
            try:declared=json.loads(anchor.get('data-linkdata','{}')) if anchor else {}
            except ValueError:declared={}
            url=body_original(declared.get('src')) or body_original(node.get('data-src') if node else '') or body_original(node.get('src') if node else '')
            if not url:continue
            p=urlsplit(url)
            if '크록스_오프로드_스포츠_클로그pdp_full' in unquote(p.path).casefold():
                ctx.log('배송 안내가 포함된 통짜 상세 이미지 전체 제외: '+url,'INFO')
                excluded_body_count+=1
                continue
            if p.hostname in ('toossa.com','www.toossa.com'):
                filename=unquote(p.path).rsplit('/',1)[-1]
                if not base or not re.match(re.escape(base)+r'(?:[-_.]|$)',filename,re.I):continue
            if re.search(r'notice|banner|coupon|event|crocs_img|size[_-]|shipping|delivery|리뷰|배송|고시|사이즈',unquote(p.path),re.I):continue
            if url in seen:continue
            seen.add(url)
            result=public_image(ctx,url,cache,detail=True)
            if not result:raise ValueError('공식 본문 이미지 조회 실패: '+url)
            parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="max-width:100%;height:auto"></p>');body_count+=1
        elif 'se-text' in component.get('class',[]):
            for paragraph in component.select('p'):
                text=paragraph.get_text(' ',strip=True).replace('\u200b','').strip()
                if text:parts.append('<p>'+html.escape(text)+'</p>')
    if not body_count and not excluded_body_count:raise ValueError('상세 본문 이미지 수집 0장: 고시·사이즈표만으로 성공 처리하지 않습니다.')
    parts.append(notice_table(ctx,product))
    return '<div>'+''.join(parts)+'</div>'


def crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    if not re.fullmatch(r'\d{5,6}-[A-Z0-9]{3}',code):raise ValueError('품번-색상코드로 입력하세요. 예: 202651-02S')
    from playwright.sync_api import sync_playwright
    failures=[];completed=set()
    try:
        with sync_playwright() as engine:
            try:browser=engine.chromium.launch(headless=True,channel='msedge')
            except Exception as exc:raise ValueError('Microsoft Edge 및 Playwright 실행 환경이 필요합니다: '+str(exc)) from exc
            with browser:
                page=browser.new_page();page.set_default_timeout(15000)
                def route(request):
                    p=urlsplit(request.request.url)
                    if request.request.resource_type in ('image','media','font'):return request.abort()
                    if p.hostname=='brand.naver.com' and p.path.startswith('/n/'):
                        main=bool(re.fullmatch(r'/n/v2/channels/[^/]+/products/\d+',p.path))
                        content=ctx.enabled('detail_page') and '/products/' in p.path and '/contents/' in p.path
                        if not main and not content:return request.abort()
                    return request.continue_()
                page.route('**/*',route)
                ctx.stage('크록스 네이버 공식 검색: '+code)
                links,channel_uid=discover(ctx,page,code)
                for number,url in links.items():
                    captured={};expected='/n/v2/channels/'+channel_uid+'/products/'+number
                    def receive(response):
                        path=urlsplit(response.url).path
                        key='product' if path==expected else 'contents' if path.startswith(expected+'/contents/') and ctx.enabled('detail_page') else None
                        if key is None:return
                        try:
                            data=response.body()
                            if response.status!=200:raise ValueError('공식 '+key+' 응답 HTTP '+str(response.status))
                            if len(data)>8000000:raise ValueError('공식 응답 크기 한도 초과')
                            captured[key]=json.loads(data)
                        except Exception as exc:captured['error']=str(exc)
                    page.on('response',receive)
                    try:
                        ctx.stage('공식 상품 조회: '+number);navigate(ctx,page,url)
                        product=wait_data(ctx,page,captured,'product')
                        record,sizes,deltas=identity(product,number,code,channel_uid)
                        if record['color_code'] in completed:continue
                        record['product_url']=page.url
                        if ctx.enabled('size'):record['size']=','.join(sizes)
                        if ctx.enabled('price') or ctx.enabled('regular_price'):
                            sale,normal=prices(ctx,product,deltas,'할인 전 가격' in page.locator('body').inner_text())
                            if ctx.enabled('price'):record['price']=sale
                            if ctx.enabled('regular_price'):record['regular_price']=normal
                        if ctx.enabled('category'):record['category']=str((product.get('category') or {}).get('wholeCategoryName') or '')
                        cache={}
                        if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                            urls=gallery(ctx,product,cache)
                            main,extras,warning=gallery_slots([dict(url=u,validation=dict(passed=True)) for u in urls])
                            if warning:ctx.log(warning,'WARNING')
                            if ctx.enabled('main_image'):record['main_image']=main
                            if ctx.enabled('additional_images'):record['additional_images']=','.join(extras)
                        if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,product,wait_data(ctx,page,captured,'contents'),cache)
                        ctx.emit_result(record);completed.add(record['color_code'])
                    except Exception as exc:
                        message=url+' / '+str(exc);ctx.fail_item(message);failures.append(message)
                    finally:page.remove_listener('response',receive)
        if failures:raise RuntimeError('일부 상품 수집 미완료: '+' / '.join(failures))
        if not completed:raise ValueError('정확한 공식 모델의 수집 결과 없음: '+code)
    except Exception as exc:
        if not failures:ctx.fail_item(str(exc))
        raise
