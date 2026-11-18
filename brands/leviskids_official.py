PORTAL_API_VERSION = 1
import hashlib,html,io,json,re
from urllib.error import HTTPError
from urllib.parse import parse_qs,quote,urlencode,urljoin,urlsplit,unquote
from urllib.request import Request,urlopen
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image,validate_gallery_image,gallery_slots
SITE='https://www.style24.com'
HOSTS={'www.style24.com','style24.com'}
IMAGE_HOST='image.style24.com'
# V263JK135P GN의 세 벌 합성 01컷을 실제 시각 확인한 원본 지문.
KNOWN_COMPOSITES={'c0ec46928f8531586ef57172d8b311c6e68bc0f4b0f95c04699147801a78dbdc'}


def fetch(ctx,url,method='GET'):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in HOSTS:raise ValueError('Style24 공식 주소 확인 실패')
    for attempt in range(2):
        ctx.checkpoint()
        try:
            request=Request(quote(url,safe=':/?&=%+#'),data=b'' if method=='POST' else None,headers={'User-Agent':'Mozilla/5.0'},method=method)
            with urlopen(request,timeout=20) as r:
                if urlsplit(r.geturl()).hostname not in HOSTS:raise ValueError('공식몰 외부로 이동했습니다.')
                data=r.read(6000001)
                if len(data)>6000000:raise ValueError('공식 HTML 크기 한도 초과')
                return BeautifulSoup(data,'html.parser'),r.geturl()
        except HTTPError as exc:
            if exc.code in (400,401,403,404,405,410,429) or attempt:raise
        except OSError:
            if attempt:raise
        finally:ctx.checkpoint()


def text(node):return node.get_text(' ',strip=True) if node else ''


def ld_entries(doc):
    for node in doc.select('script[type="application/ld+json"]'):
        try:value=json.loads(node.get_text())
        except ValueError:continue
        for entry in value if isinstance(value,list) else [value]:
            if isinstance(entry,dict):yield entry


def identity(doc,base):
    groups=[v for v in ld_entries(doc) if v.get('@type')=='ProductGroup' and v.get('productGroupID')==base]
    if len(groups)!=1:raise ValueError('[품번 확인 실패] 공식 ProductGroup에 입력 품번이 없습니다: '+base)
    group=groups[0];brand=re.sub(r'[^A-Z가-힣]','',str((group.get('brand') or {}).get('name','')).upper())
    if brand not in ('LEVISKIDS','리바이스키즈'):raise ValueError('공식 상품 브랜드가 리바이스키즈가 아닙니다: '+brand)
    current=doc.select_one('.opt_color a.on[href]')
    if current:
        code=parse_qs(urlsplit(current['href']).query).get('colorCd',[''])[0]
    else:
        rows=group.get('hasVariant') or [];code=parse_qs(urlsplit(rows[0].get('url','')).query).get('colorCd',[''])[0] if len(rows)==1 else ''
    if not re.fullmatch(r'[A-Z0-9]{2,3}',code):raise ValueError('공식 선택 색상코드 확인 실패')
    variants=[v for v in group.get('hasVariant',[]) if parse_qs(urlsplit(v.get('url','')).query).get('colorCd')==[code]]
    if len(variants)!=1:raise ValueError('상품/색상 구조화 데이터 연결 확인 실패')
    color=text(doc.select_one('.opt_color .color')) or str(variants[0].get('color') or '')
    if not color:raise ValueError('공식 색상명 없음')
    name=text(doc.select_one('.desc_info .title_blk .name')) or str(group.get('name') or '')
    name=re.sub(r'^\s*(?:리바이스\s*키즈|LEVI[’\x27]?S\s*KIDS)\s*','',name,flags=re.I)
    name=re.sub(r'(?<![A-Z0-9])'+re.escape(base)+r'(?![A-Z0-9])','',name,flags=re.I).strip(' -_/')
    if not name:raise ValueError('정리 후 상품명 없음')
    return dict(product_code=base,product_name=name,color=color,color_name=color,color_code=code)


def color_links(doc,base):
    links={}
    for a in doc.select('.opt_color a[href]'):
        p=urlsplit(urljoin(SITE,a['href']));q=parse_qs(p.query)
        code=q.get('colorCd',[''])[0]
        if p.hostname in HOSTS and p.path=='/goods/detail/form' and q.get('goodsCd')==[base] and re.fullmatch(r'[A-Z0-9]{2,3}',code):
            links[code]=SITE+'/goods/detail/form?'+urlencode({'goodsCd':base,'colorCd':code})
    return links


def sizes(doc,code):
    values=[]
    for n in doc.select('.opt_size input[name="opt"][optcd2]'):
        if n.get('optcd1')!=code:continue
        value=n['optcd2'].strip()
        if value and value not in values:values.append(value)
    return ','.join(values)


def money(value):
    value=re.sub(r'[,\s원]','',str(value or ''))
    return int(value) or None if re.fullmatch(r'\d+',value) else None


def prices(ctx,doc,code):
    nodes=doc.select('.opt_size input[name="opt"][optcd1="'+code+'"]')
    extras={money(n.get('addprice')) or 0 for n in nodes}
    # addprice=0 is an explicit zero; arbitrary malformed values are not accepted.
    if any(not re.fullmatch(r'\d+',n.get('addprice','')) for n in nodes) or len(extras)>1:
        ctx.log('사이즈 추가금 불명확: 판매가 빈칸','WARNING');sale=None
    else:
        sale=money(text(doc.select_one('.desc_info .price_blk .sale_price em')))
        if sale and extras:sale+=next(iter(extras))
    normal=money(text(doc.select_one('.desc_info .price_blk .org_price em')))
    if normal is None:ctx.log('공식 독립 정상가 없음: 빈칸','WARNING')
    return sale,normal


def category(doc):
    for row in ld_entries(doc):
        if row.get('@type')=='BreadcrumbList':
            return ' > '.join(str(n.get('name','')) for n in sorted(row.get('itemListElement') or [],key=lambda n:n.get('position',0))[1:-1])
    return ''


def image_url(raw):
    if not raw:return ''
    url=urljoin(SITE,raw);p=urlsplit(url)
    if p.scheme!='https' or p.hostname!=IMAGE_HOST or p.username or p.password or '..' in unquote(p.path).split('/'):return ''
    if not p.path.startswith(('/Upload/ProductImage/','/Upload/editor/')):return ''
    return quote(url,safe=':/?&=%+#')


def product_images(doc,base,code):
    urls=[];identity=re.compile(re.escape(base)+'_'+re.escape(code)+r'_(?:\d+|[BCD]\d+)\.',re.I)
    declared=set();has_index=False
    for node in doc.find_all('script',src=False):
        source=node.get_text();match=re.search(r'\bgoodsImgList\s*=\s*',source)
        if not match:continue
        data,_=json.JSONDecoder().raw_decode(source[match.end():])
        if not isinstance(data,list):raise ValueError('공식 goodsImgList 형식 변경')
        has_index=True
        for row in data:
            if row.get('goodsCd')==base and row.get('colorCd')==code and row.get('sysImgNm'):
                declared.add('/Upload/ProductImage/'+str(row['sysImgNm']).lstrip('/'))
    for img in doc.select('.thumb_list_wrap img[data-thumbnail="goodsDetailImageWeb"]'):
        url=image_url(img.get('src'))
        path=unquote(urlsplit(url).path).split('/gio/',1)[0]
        # 공식 색상 PB에 파일명 DB가 연결되는 상품도 있다. 파일명 코드를 추측하지 않는다.
        owned=path in declared if has_index else bool(identity.search(path))
        if url and owned and url not in urls:urls.append(url)
    return urls


def checked(ctx,url,cache,detail=False):
    key=(url,detail)
    if key not in cache:
        shared=cache.get((url,False))
        cache[key]=shared if detail and shared and shared['status']=='accessible' else check_public_image(url,timeout=12,retries=1,max_bytes=64*1024*1024 if detail else 10000000,checkpoint=ctx.checkpoint)
    value=cache[key]
    if value['status']=='unknown':raise ValueError('공식 이미지 통신 미완료: '+url+' / '+', '.join(value['reasons']))
    if value['status']!='accessible':
        ctx.log('원본 이미지 없음/접근불가: '+url,'WARNING');return None
    return value


def gallery(ctx,urls,base,code,cache):
    if not urls:raise ValueError('공식 상품·색상의 갤러리 주소를 확인할 수 없음: '+base+' / '+code)
    accepted=[];seen=set()
    for url in urls:
        value=checked(ctx,url,cache)
        if not value:continue
        digest=hashlib.sha256(value['data']).hexdigest()
        if digest in KNOWN_COMPOSITES:
            ctx.log('실제 확인된 여러 벌 합성컷 제외: '+url,'INFO');continue
        valid=validate_gallery_image(value['data'],role='product_shot',mapping_verified=True,single_shot_verified=True,
            mapping_evidence='공식 goodsCd '+base+' / colorCd '+code+' / 동일 색상 .thumb_list_wrap 상품 갤러리',
            content_evidence='공식 제품 이미지 영역. 리뷰·추천·가이드 분리, 실제 형식/픽셀/용량/비율 검사. 확인된 3벌 합성컷 제외.')
        if valid['passed'] and digest not in seen:accepted.append(dict(url=url,validation=valid));seen.add(digest)
        elif not valid['passed']:ctx.log('갤러리 규격 제외: '+url+' / '+', '.join(valid['reasons']),'WARNING')
        if len(accepted)>=6:break
    if not accepted:raise ValueError('규격을 통과한 공식 개별 상품컷 없음')
    return gallery_slots(accepted)


def notice_table(ctx,doc):
    pairs=[]
    for box in doc.select('.area_infotbl'):
        if text(box.select_one('.title'))!='상품고시정보':continue
        for row in box.select('table tr'):
            cells=row.find_all(['th','td'],recursive=False)
            if len(cells)==2:
                key,value=(text(n) for n in cells)
                if key and value:pairs.append((key,value))
    if not pairs:ctx.log('상품고시정보 없음: 표 생략','WARNING');return ''
    return '<h3>상품고시정보</h3><table border="1"><tbody>'+''.join('<tr><th>'+html.escape(k)+'</th><td>'+html.escape(v)+'</td></tr>' for k,v in pairs)+'</tbody></table>'


def size_images(ctx,doc,base,code,cache):
    if not doc.select_one('#btn_infoSize_pop'):
        ctx.log('사이즈정보 버튼 없음: 생략','WARNING');return []
    # 공식 cfGoodsSizeInfo가 호출하는 읽기 전용 POST 레이어.
    try:popup,_=fetch(ctx,SITE+'/goods/sizeInfo/layer/'+base+'/'+code+'/null',method='POST')
    except HTTPError as exc:
        if exc.code not in (404,410):raise
        ctx.log('공식 사이즈정보 없음: 생략','WARNING');return []
    tabs=popup.select('.tab_cont_wrap > .tab_cont')
    if not tabs:ctx.log('사이즈정보 표준 사이즈 영역 없음: 생략','WARNING');return []
    urls=[]
    # 첫 탭 표준 사이즈만. 두 번째 탭 측정 가이드의 img_sizeinfo는 요청하지 않는다.
    for img in tabs[0].select('img'):
        url=image_url(img.get('src'))
        if url and url not in urls and checked(ctx,url,cache,detail=True):urls.append(url)
    if not urls:ctx.log('공식 사이즈표 이미지 없음: 생략','WARNING')
    return urls


def detail(ctx,doc,base,code,urls,cache):
    parts=[]
    for n in doc.select('.pd-core__description p,.pd-core__description li'):
        if text(n):parts.append('<p>'+html.escape(text(n))+'</p>')
    # 상품 갤러리 원본은 본문에도 유지한다(합성 소개컷은 상세에서는 허용).
    for url in urls:
        if checked(ctx,url,cache,detail=True):parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세"></p>')
    for url in size_images(ctx,doc,base,code,cache):parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="사이즈 정보"></p>')
    parts.append(notice_table(ctx,doc))
    return '<div>'+''.join(parts)+'</div>'


def crawl(ctx):
    raw=str(ctx.product_code).strip().upper();m=re.fullmatch(r'([A-Z0-9]{6,20})(?:[_-]([A-Z0-9]{2,3}))?',raw)
    if not m:raise ValueError('리바이스키즈 품번 또는 품번_GN 형식으로 입력하세요.')
    base,requested=m.groups();ctx.stage('리바이스키즈 공식 품번 조회: '+base)
    try:
        first,final=fetch(ctx,SITE+'/goods/detail/form?'+urlencode({'goodsCd':base}))
        first_record=identity(first,base);links=color_links(first,base)
        if not links:links={first_record['color_code']:final}
        if requested and requested not in links:raise ValueError('지정 공식 색상 없음: '+requested)
    except Exception as exc:ctx.fail_item(str(exc));raise
    errors=[]
    for code,url in links.items():
        if requested and requested!=code:continue
        try:
            doc=first if code==first_record['color_code'] else fetch(ctx,url)[0]
            row=identity(doc,base)
            if row['color_code']!=code:raise ValueError('요청 색상과 실제 상품 색상 불일치')
            row['product_url']=url
            if ctx.enabled('size'):row['size']=sizes(doc,code)
            if ctx.enabled('price') or ctx.enabled('regular_price'):
                sale,normal=prices(ctx,doc,code)
                if ctx.enabled('price'):row['price']=sale
                if ctx.enabled('regular_price'):row['regular_price']=normal
            if ctx.enabled('category'):row['category']=category(doc)
            cache={};urls=product_images(doc,base,code) if any(ctx.enabled(k) for k in ('main_image','additional_images','detail_page')) else []
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main,extras,warning=gallery(ctx,urls,base,code,cache)
                if warning:ctx.log(warning,'WARNING')
                if ctx.enabled('main_image'):row['main_image']=main
                if ctx.enabled('additional_images'):row['additional_images']=','.join(extras)
            if ctx.enabled('detail_page'):row['detail_page']=detail(ctx,doc,base,code,urls,cache)
            ctx.emit_result(row)
        except Exception as exc:message=code+' / '+str(exc);ctx.fail_item(message);errors.append(message)
    if errors:raise RuntimeError('일부 색상 수집 실패: '+' / '.join(errors))
