PORTAL_API_VERSION = 1
"""STYLE24(한세공식몰 style24.com) 전용 품번 크롤러 — 브랜드 공통, 자동 색상 매핑 / URL 방식.

버커루·NBA키즈·컬처콜·모이몰른·리바이스키즈 등 STYLE24에 입점한 모든 브랜드의 상품을
STYLE24 상품코드(goodsCd)로 직접 수집한다. 브랜드관 소속은 제한하지 않고, 수집한 브랜드는
'브랜드' 열에 공식 ProductGroup.brand 값 그대로 남긴다.

입력
- 기본 품번(goodsCd) → 공식 .opt_color 실제 링크로 연결된 전체 색상
- 품번_색상코드(예: K263TS150P_BL) → 그 색상만
- 상품 링크(https://www.style24.com/goods/detail/form?goodsCd=...&colorCd=...) → colorCd가 있으면 그 색상만

수집(2026-11-26 실사이트 확인: 버커루 B264JP510P·NBA키즈 K263TS150P 등)
- 품번 확인: ld+json ProductGroup.productGroupID == 입력 품번
- 색상: 현재 색상코드(.opt_color a.on / hasVariant) + 한글 색상명(.opt_color .color).
  영문 색상명은 컬러 안내(.pd-core__color-item)의 이미지와 색상 칩 이미지가 같은 경우만 '영문 색상' 열에.
- 사이즈: .opt_size input[name=opt][optcd1=색상]의 optcd2(품절 포함)
- 판매가/정상가: .desc_info .price_blk .sale_price / .org_price (사이즈별 추가금이 다르면 판매가 빈칸)
- 이미지: goodsImgList에 등록된 해당 goodsCd·colorCd 갤러리 파일만(없으면 파일명 품번_색상_번호).
  crawler_rules 규격 검사 통과 컷만 대표/추가 5슬롯.
- 상세: 본문 이미지 + 사이즈정보 레이어 '표준 사이즈' 탭 표(카테고리에 맞는 표만, 못 고르면 전부)
  + 상품고시정보 표. 측정 가이드 탭·없는 자료는 생략.
"""
import hashlib,html,json,re
from urllib.error import HTTPError
from urllib.parse import parse_qs,quote,urlencode,urljoin,urlsplit,unquote
from urllib.request import Request,urlopen
from bs4 import BeautifulSoup
from crawler_rules import check_public_image,validate_gallery_image,gallery_slots

REVISION='2026-11-26-style24-item-1'
SITE='https://www.style24.com'
HOSTS={'www.style24.com','style24.com','m.style24.com'}
IMAGE_HOST='image.style24.com'
CODE=re.compile(r'[A-Z0-9]{6,20}')
COLOR=re.compile(r'[A-Z0-9]{2,3}')


class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


def fetch(ctx,url,method='GET'):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in HOSTS:raise CrawlError('STYLE24 주소가 아닙니다: '+url)
    for attempt in range(2):
        ctx.checkpoint()
        try:
            request=Request(quote(url,safe=':/?&=%+#'),data=b'' if method=='POST' else None,headers={'User-Agent':'Mozilla/5.0'},method=method)
            with urlopen(request,timeout=20) as r:
                if urlsplit(r.geturl()).hostname not in HOSTS:raise CrawlError('STYLE24 밖으로 이동했습니다: '+r.geturl())
                data=r.read(6000001)
                if len(data)>6000000:raise CrawlError('STYLE24 HTML 크기 한도 초과')
                return BeautifulSoup(data,'html.parser'),r.geturl()
        except HTTPError as exc:
            if exc.code in (400,401,403,404,405,410,429) or attempt:raise CrawlError(f'HTTP {exc.code}: {url}') from exc
        except OSError as exc:
            if attempt:raise CrawlError('통신 오류: '+url+': '+str(exc)) from exc
        finally:ctx.checkpoint()
        ctx.log('1/1 재시도: '+url,'WARNING')
    raise CrawlError('요청 실패: '+url)


def text(node):return node.get_text(' ',strip=True) if node else ''


def parse_input(raw):
    """(기본 품번, 지정 색상 또는 None)."""
    raw=str(raw or '').strip()
    if raw.lower().startswith(('http://','https://')):
        p=urlsplit(raw);q=parse_qs(p.query)
        if p.hostname not in HOSTS or not q.get('goodsCd'):raise CrawlError('STYLE24 상품 링크(goodsCd 포함)가 아닙니다: '+raw)
        base=q['goodsCd'][0].strip().upper();color=(q.get('colorCd') or [''])[0].strip().upper() or None
        if not CODE.fullmatch(base) or (color and not COLOR.fullmatch(color)):raise CrawlError('링크의 상품코드/색상코드 형식 오류: '+raw)
        return base,color
    m=re.fullmatch(r'([A-Z0-9]{6,20})(?:[_-]([A-Z0-9]{2,3}))?',raw.upper())
    if not m:raise CrawlError('STYLE24 품번, 품번_색상코드 또는 상품 링크를 입력하세요: '+raw)
    return m[1],m[2]


def ld_entries(doc):
    for node in doc.select('script[type="application/ld+json"]'):
        try:value=json.loads(node.get_text())
        except ValueError:continue
        for entry in value if isinstance(value,list) else [value]:
            if isinstance(entry,dict):yield entry


def brand_of(group,doc):
    brand=group.get('brand')
    name=str((brand or {}).get('name','') if isinstance(brand,dict) else brand or '').strip()
    if not name:name=re.sub(r'\s*브랜드\s*홈.*$','',text(doc.select_one('.brand_box'))).strip()
    return name


# 상품명 앞에 붙는 한글 브랜드 표기(상품명 정리용. 브랜드 판정에는 쓰지 않음)
ALIASES={'NBAKIDS':['NBA키즈','NBA 키즈','엔비에이키즈','엔비에이 키즈'],'BUCKAROO':['버커루'],'LEVISKIDS':['리바이스키즈','리바이스 키즈'],
         'MOIMOLN':['모이몰른'],'CULTURECALL':['컬처콜'],'TELEMBOR':['테렘보르'],'DODOM':['도도엠']}


def clean_name(name,base,brand):
    key=re.sub(r'[^A-Z0-9]','',brand.upper())
    tokens=[base]+sorted({t for t in [brand,re.sub(r'\s+','',brand)]+ALIASES.get(key,[]) if t},key=len,reverse=True)
    first=(re.match(r'[A-Za-z0-9]+',brand) or [''])[0]
    if first:name=re.sub(r'^\s*'+re.escape(first)+r'[가-힣]+\s+','',name,flags=re.I)   # 'NBA키즈 …' 같은 앞머리 표기
    for token in tokens:
        name=re.sub(r'(?<![A-Za-z0-9가-힣])'+re.escape(token)+r'(?![A-Za-z0-9가-힣])','',name,flags=re.I)
    name=re.sub(r'\(\s*\)|\[\s*\]','',name)
    return re.sub(r'\s+',' ',name).strip(' -_/')


def identity(doc,base):
    groups=[v for v in ld_entries(doc) if v.get('@type')=='ProductGroup' and str(v.get('productGroupID','')).upper()==base]
    if len(groups)!=1:raise CrawlError('[품번 확인 실패] STYLE24 상품정보(ProductGroup)에 입력 품번이 없습니다: '+base)
    group=groups[0]
    current=doc.select_one('.opt_color a.on[href]')
    if current:code=(parse_qs(urlsplit(current['href']).query).get('colorCd') or [''])[0].upper()
    else:
        match=re.search(r'params\.colorCd\s*=\s*"([A-Za-z0-9]+)"',str(doc))
        rows=group.get('hasVariant') or []
        code=match[1].upper() if match else (parse_qs(urlsplit(rows[0].get('url','')).query).get('colorCd') or [''])[0].upper() if len(rows)==1 else ''
    if not COLOR.fullmatch(code):raise CrawlError('현재 색상코드 확인 실패')
    variants=[v for v in group.get('hasVariant') or [] if (parse_qs(urlsplit(v.get('url','')).query).get('colorCd') or [''])[0].upper()==code]
    color=text(doc.select_one('.opt_color .color')) or (str(variants[0].get('color') or '') if variants else '')
    if not color:raise CrawlError('색상명 없음: '+code)
    brand=brand_of(group,doc)
    original=text(doc.select_one('.desc_info .title_blk .name')) or str(group.get('name') or '')
    name=clean_name(original,base,brand)
    if not name:raise CrawlError('정리 후 상품명 없음')
    return dict(product_code=base,product_name=name,color=color,color_name=color,color_code=code,브랜드=brand,**{'원본 상품명':original})


def color_links(doc,base):
    links={}
    for a in doc.select('.opt_color a[href]'):
        p=urlsplit(urljoin(SITE,a['href']));q=parse_qs(p.query)
        code=(q.get('colorCd') or [''])[0].upper()
        if p.hostname in HOSTS and p.path=='/goods/detail/form' and [g.upper() for g in q.get('goodsCd',[])]==[base] and COLOR.fullmatch(code):
            links.setdefault(code,SITE+'/goods/detail/form?'+urlencode({'goodsCd':base,'colorCd':code}))
    return links


def english_color(doc,base,code):
    """컬러 안내 이미지와 해당 색상 칩 이미지가 같을 때만 영문 원문. 아니면 ''."""
    paths=set()
    for a in doc.select('.opt_color a[href]'):
        q=parse_qs(urlsplit(urljoin(SITE,a['href'])).query)
        if (q.get('colorCd') or [''])[0].upper()!=code:continue
        paths.update(urlsplit(urljoin(SITE,i['src'])).path.split('/gio/')[0] for i in a.select('img[src]'))
    names={text(item.select_one('.pd-core__color-name')) for item in doc.select('.pd-core__color-item')
           if any(urlsplit(urljoin(SITE,i['src'])).path.split('/gio/')[0] in paths for i in item.select('img[src]'))}
    names={n for n in names if n and re.search(r'[A-Za-z]',n) and not re.search(r'[가-힣]',n)}
    return names.pop() if len(names)==1 else ''


def sizes(doc,code):
    values=[]
    for n in doc.select('.opt_size input[name="opt"][optcd2]'):
        if (n.get('optcd1') or code).upper()!=code:continue
        value=n['optcd2'].strip()
        if value and value not in values:values.append(value)
    return ','.join(values)


def money(value):
    value=re.sub(r'[,\s원]','',str(value or ''))
    return int(value) or None if re.fullmatch(r'\d+',value) else None


def prices(ctx,doc,code):
    nodes=[n for n in doc.select('.opt_size input[name="opt"]') if (n.get('optcd1') or code).upper()==code]
    extras={money(n.get('addprice')) or 0 for n in nodes}
    if any(not re.fullmatch(r'\d+(?:\.0+)?',n.get('addprice','0') or '0') for n in nodes) or len(extras)>1:
        ctx.log('사이즈별 추가금이 달라 판매가 빈칸','WARNING');sale=None
    else:
        sale=money(text(doc.select_one('.desc_info .price_blk .sale_price em')))
        if sale and extras:sale+=next(iter(extras))
    normal=money(text(doc.select_one('.desc_info .price_blk .org_price em')))
    if normal is None:ctx.log('독립 정상가 없음(할인 없음 또는 미표기): 정상가 빈칸','WARNING')
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
    urls=[];pattern=re.compile(re.escape(base)+'_'+re.escape(code)+r'_(?:\d+|[A-Z]\d+)\.',re.I)
    declared=set();has_index=False
    for node in doc.find_all('script',src=False):
        source=node.get_text();match=re.search(r'\bgoodsImgList\s*=\s*',source)
        if not match:continue
        try:data,_=json.JSONDecoder().raw_decode(source[match.end():])
        except ValueError:continue
        if not isinstance(data,list):continue
        has_index=True
        for row in data:
            if isinstance(row,dict) and str(row.get('goodsCd','')).upper()==base and str(row.get('colorCd','')).upper()==code and row.get('sysImgNm'):
                declared.add('/Upload/ProductImage/'+str(row['sysImgNm']).lstrip('/'))
    for img in doc.select('.thumb_list_wrap img[data-thumbnail="goodsDetailImageWeb"], img[data-thumbnail="goodsDetailImageWeb"]'):
        url=image_url(img.get('src'))
        path=unquote(urlsplit(url).path).split('/gio/',1)[0]
        owned=path in declared if has_index and declared else bool(pattern.search(path))
        if url and owned and url not in urls:urls.append(url)
    return urls


def checked(ctx,url,cache,detail=False):
    key=(url,detail)
    if key not in cache:
        shared=cache.get((url,False))
        cache[key]=shared if detail and shared and shared['status']=='accessible' else check_public_image(url,timeout=12,retries=1,max_bytes=64*1024*1024 if detail else 10000000,checkpoint=ctx.checkpoint)
    value=cache[key]
    if value['status']=='unknown':raise CrawlError('이미지 통신 미완료: '+url+' / '+', '.join(value['reasons']))
    if value['status']!='accessible':
        ctx.log('이미지 없음/접근불가: '+url,'WARNING');return None
    return value


def gallery(ctx,urls,base,code,cache):
    if not urls:raise CrawlError('해당 색상의 갤러리 이미지를 찾지 못했습니다: '+base+' / '+code)
    accepted=[];seen=set()
    for url in urls:
        value=checked(ctx,url,cache)
        if not value:continue
        digest=hashlib.sha256(value['data']).hexdigest()
        valid=validate_gallery_image(value['data'],role='product_shot',mapping_verified=True,single_shot_verified=True,
            mapping_evidence='STYLE24 goodsCd '+base+' / colorCd '+code+' / 같은 색상 상품 갤러리(goodsImgList)',
            content_evidence='STYLE24 상품 갤러리 영역. 리뷰·추천·가이드 제외, 형식/픽셀/용량/비율 검사.')
        if valid['passed'] and digest not in seen:accepted.append(dict(url=url,validation=valid));seen.add(digest)
        elif not valid['passed']:ctx.log('갤러리 규격 제외: '+url+' / '+', '.join(valid['reasons']),'WARNING')
        if len(accepted)>=6:break
    if not accepted:raise CrawlError('규격을 통과한 상품컷 없음')
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


def body_images(doc):
    urls=[]
    for node in doc.select('.pd-core__wrap img,.pd-core__product-view img,.pd-core__detail img,#goodsDescHtml img,[id^="goodsDescHtml_"] img'):
        if node.find_parent(class_='logo'):continue
        url=image_url(node.get('data-src') or node.get('src'))
        if url and url not in urls:urls.append(url)
    return urls


def family(category_path):
    """카테고리로 사이즈표 종류와 성별 추정. (종류, 성별)"""
    kind=None
    if re.search(r'신발|운동화|샌들|슬리퍼|스니커즈|부츠',category_path):kind='신발'
    elif re.search(r'모자|비니|캡|햇',category_path):kind='모자'
    elif re.search(r'팬츠|바지|스커트|쇼츠|레깅스|데님|진',category_path):kind='하의'
    elif re.search(r'티셔츠|맨투맨|상의|점퍼|자켓|재킷|코트|후드|니트|셔츠|베스트|원피스|가디건|패딩|아우터|블라우스',category_path):kind='상의'
    gender='여성' if re.search(r'여성|여아|우먼',category_path) else '남성' if re.search(r'남성|남아|맨즈',category_path) else None
    return kind,gender


def pick_boxes(boxes,category_path):
    kind,gender=family(category_path)
    if not kind:return boxes
    def heading(box):return text(box.find(['h6','h5','h4']))
    matched=[b for b in boxes if kind in heading(b) or (kind in ('상의','하의') and '의류' in heading(b))]
    if gender:
        narrowed=[b for b in matched if gender in heading(b)]
        matched=narrowed or matched
    return matched or boxes


def size_table(ctx,doc,base,code,cache):
    if not doc.select_one('#btn_infoSize_pop'):
        ctx.log('사이즈정보 없음: 표 생략','WARNING');return ''
    try:popup,_=fetch(ctx,SITE+'/goods/sizeInfo/layer/'+base+'/'+code+'/null',method='POST')
    except CrawlError as exc:
        if not re.search(r'HTTP (404|410)',str(exc)):raise
        ctx.log('사이즈정보 없음: 표 생략','WARNING');return ''
    tabs=popup.select('.tab_cont_wrap > .tab_cont')
    names=[text(t) for t in popup.select('.tab_nav li')]
    if not tabs:return ''
    index=next((i for i,n in enumerate(names) if '실측' in n),next((i for i,n in enumerate(names) if '표준' in n),0))
    standard=tabs[min(index,len(tabs)-1)];label=names[index] if index<len(names) else '표준 사이즈'
    parts=[]
    def render_row(cells):
        result=[]
        for cell in cells:
            attrs=''
            for key in ('rowspan','colspan'):
                value=str(cell.get(key,''))
                if value.isdigit() and 0<int(value)<=50:attrs+=' '+key+'="'+value+'"'
            result.append('<'+cell.name+attrs+'>'+html.escape(text(cell))+'</'+cell.name+'>')
        return '<tr>'+''.join(result)+'</tr>'
    for box in pick_boxes(standard.select('.size_tbl_box'),category(doc)):
        left=box.select_one('.head_t table');right=box.select_one('.body_t table')
        tables=[]
        if left is not None and right is not None:
            labels=left.select('tr');values=right.select('tr')
            if len(labels)!=len(values):raise CrawlError('사이즈정보 행 연결 불일치: '+base)
            rows=[render_row(a.find_all(['th','td'],recursive=False)+b.find_all(['th','td'],recursive=False)) for a,b in zip(labels,values)]
            tables=[''.join(rows)]
        else:
            for table in box.select('table'):
                tables.append(''.join(render_row(row.find_all(['th','td'],recursive=False)) for row in table.select('tr')))
        if tables:
            title=text(box.find(['h6','h5','h4']));unit=text(box.select_one('.size_unit'))
            if title:parts.append('<h4>'+html.escape(title)+'</h4>')
            if unit:parts.append('<p>'+html.escape(unit)+'</p>')
            parts.extend('<table border="1" cellpadding="8" cellspacing="0" style="width:100%;border-collapse:collapse"><tbody>'+rows+'</tbody></table>' for rows in tables)
    for img in standard.select('img'):
        url=image_url(img.get('src'))
        if url and checked(ctx,url,cache,detail=True):parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="사이즈 정보"></p>')
    if not parts:
        ctx.log('사이즈 표 없음: 생략','WARNING');return ''
    notes=[text(n) for n in standard.select('.size_footer li') if text(n)]
    parts.extend('<p>'+html.escape(note)+'</p>' for note in notes)
    return '<h3>사이즈정보 · '+html.escape(label)+'</h3>'+''.join(parts)


def detail(ctx,doc,base,code,urls,cache):
    parts=[]
    for n in doc.select('.pd-core__description p,.pd-core__description li'):
        if text(n):parts.append('<p>'+html.escape(text(n))+'</p>')
    for url in body_images(doc) or urls:
        if checked(ctx,url,cache,detail=True):parts.append('<p><img src="'+html.escape(url,quote=True)+'" alt="상품 상세"></p>')
    parts.append(size_table(ctx,doc,base,code,cache))
    parts.append(notice_table(ctx,doc))
    markup='<div>'+''.join(parts)+'</div>'
    if len(markup.encode('utf-16-le'))//2>32767:raise CrawlError('상세 HTML이 Excel 셀 한도(32,767자) 초과')
    return markup


def crawl(ctx):
    ctx.log('STYLE24 전용 크롤러 '+REVISION,'INFO')
    try:
        base,requested=parse_input(ctx.product_code)
        ctx.stage('STYLE24 품번 조회: '+base)
        first,final=fetch(ctx,SITE+'/goods/detail/form?'+urlencode({'goodsCd':base}))
        first_record=identity(first,base);links=color_links(first,base)
        if not links:links={first_record['color_code']:final}
        if requested and requested not in links:raise CrawlError('지정 색상 없음: '+base+'_'+requested+' (있는 색상: '+', '.join(links)+')')
    except CrawlError as exc:ctx.fail_item(str(exc));raise
    ctx.log(f"{first_record['브랜드'] or '브랜드 미상'} / {first_record['product_name']} / 색상 {len(links)}개",'INFO')
    errors=[];emitted=0
    for code,url in links.items():
        if requested and requested!=code:continue
        try:
            ctx.stage('색상 수집: '+code)
            doc=first if code==first_record['color_code'] else fetch(ctx,url)[0]
            row=identity(doc,base)
            if row['color_code']!=code:raise CrawlError('요청 색상과 실제 상품 색상 불일치')
            row['product_url']=url;row['영문 색상']=english_color(doc,base,code)
            if ctx.enabled('size'):
                row['size']=sizes(doc,code)
                if not row['size']:ctx.log('사이즈 옵션 없음: '+code,'WARNING')
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
            ctx.emit_result(row);emitted+=1
        except CrawlError as exc:message=base+'_'+code+': '+str(exc);ctx.fail_item(message);errors.append(message)
    if errors:raise CrawlError(('일부 ' if emitted else '')+'색상 수집 실패: '+' / '.join(errors))
