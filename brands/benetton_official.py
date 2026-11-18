PORTAL_API_VERSION = 1

import hashlib
import html
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs
from urllib.request import Request, urlopen
from bs4 import BeautifulSoup, NavigableString, Comment
from crawler_rules import check_public_image, validate_gallery_image, gallery_slots

SITE='https://benettonmall.com'
HOSTS={'benettonmall.com','www.benettonmall.com'}


def fetch(ctx,url,payload=None):
    if urlsplit(url).scheme!='https' or urlsplit(url).hostname not in HOSTS:raise ValueError('베네통 공식 주소가 아닙니다.')
    for attempt in range(2):
        ctx.checkpoint()
        try:
            request=Request(url,data=json.dumps(payload).encode() if payload is not None else None,headers={'User-Agent':'Mozilla/5.0','Content-Type':'application/json','Accept':'application/json,text/html'})
            with urlopen(request,timeout=20) as response:
                if urlsplit(response.geturl()).hostname not in HOSTS:raise ValueError('공식몰 외부로 이동했습니다.')
                raw=response.read(6_000_001)
            if len(raw)>6_000_000:raise ValueError('공식 응답 용량 초과')
            return raw
        except (HTTPError,URLError,TimeoutError,OSError) as exc:
            if attempt:raise ValueError('공식 조회 실패: '+url+' / '+str(exc)) from exc
            ctx.log('공식 조회 재시도: '+str(exc),'WARNING')


def inline_json(doc,name):
    for script in doc.select('script:not([src])'):
        match=re.search(r'\b(?:let|const|var)\s+'+re.escape(name)+r'\s*=\s*',script.get_text())
        if match:
            return json.JSONDecoder().raw_decode(script.get_text()[match.end():])[0]
    raise ValueError('공식 데이터 확인 실패: '+name)


def product(ctx,pid,base):
    if not re.fullmatch(r'\d+',pid):raise ValueError('공식 상품 식별자 형식 오류')
    url=SITE+'/product/view?'+urlencode({'productcode':pid})
    doc=BeautifulSoup(fetch(ctx,url),'html.parser');data=inline_json(doc,'productInfo')
    if str(data.get('prodcode','')).upper()!=base:return None
    if str(data.get('productcode',''))!=pid:raise ValueError('상품 페이지 식별자 불일치')
    if data.get('shop_div_code')!='B':raise ValueError('베네통 브랜드 상품이 아닙니다.')
    color=str(data.get('colorcode') or '').strip().upper()
    if not color:raise ValueError('공식 색상코드 없음')
    checked=doc.select_one('.chk-color input[checked]')
    if checked and checked.get('id')!='colorcode_'+color:raise ValueError('선택 색상과 상품 데이터 불일치')
    return doc,data,url


def search(ctx,base):
    links=[];seen=set();count=None
    for page in range(100):
        params=dict(tbl='',shopcode='B',brand='',code='',keywd=base,prod_division_type='',price='',min_price='',max_price='',color='',sortlist='recent',size='',gender='',isOutlet='',page=page,limit=40)
        data=json.loads(fetch(ctx,SITE+'/search/product',params))
        if not isinstance(data,dict) or not isinstance(data.get('result'),list):raise ValueError('공식 검색 응답 형식 오류')
        total=int(data['count'])
        if count is None:count=total
        if total!=count:raise ValueError('검색 중 상품 수가 변경되었습니다. 다시 수집하세요.')
        rows=data['result'];fresh=0
        for row in rows:
            pid=str(row.get('productcode') or '')
            if pid in seen:continue
            seen.add(pid);fresh+=1
            if str(row.get('prodcode','')).upper()==base and row.get('shop_div_code')=='B':links.append(pid)
        if (page+1)*40>=total:return links
        if not rows or not fresh:raise ValueError('공식 검색 페이지 진행 실패')
    raise ValueError('공식 검색 페이지 한도 초과')


def image_url(raw):
    if not raw or raw.startswith('data:'):return ''
    url=urljoin(SITE,raw);parts=urlsplit(url)
    allowed=(parts.hostname=='img.benettonmall.com' and parts.path.startswith('/PARTS/')) or (parts.hostname in HOSTS and parts.path.startswith('/data/shopimages/'))
    return url if parts.scheme=='https' and allowed and not parts.username and not parts.password else ''


def gallery_urls(doc,data):
    result=[];prefix=str(data['prodcode'])+str(data['colorcode'])+'_'
    for node in doc.select('.prd-view-gallery .gallery-thumbs a[rel]'):
        relation=node['rel'];relation=' '.join(relation) if isinstance(relation,list) else relation
        match=re.search(r"largeimage:\s*['\"]([^'\"]+)['\"]",relation)
        url=image_url(match[1]) if match else ''
        if url and urlsplit(url).path.rsplit('/',1)[-1].startswith(prefix) and url not in result:result.append(url)
    if not result:
        for node in doc.select('.prd-view-gallery a.gallery-zoom[href]'):
            url=image_url(node['href'])
            if url and urlsplit(url).path.rsplit('/',1)[-1].startswith(prefix) and url not in result:result.append(url)
    return result


def image_info(ctx,url,cache):
    if url not in cache:cache[url]=check_public_image(url,timeout=15,retries=1,checkpoint=ctx.checkpoint)
    result=cache[url]
    if result['status']!='accessible':
        ctx.log('공식 이미지 확인 불가: '+url+' / '+', '.join(result.get('reasons',[])),'WARNING')
        return None
    return result


def gallery(ctx,doc,data,cache):
    accepted=[];seen=set()
    for url in gallery_urls(doc,data):
        ctx.checkpoint();info=image_info(ctx,url,cache)
        if not info:continue
        valid=validate_gallery_image(info['data'],role='product_shot',mapping_verified=True,single_shot_verified=True,mapping_evidence='공식 색상별 상품 페이지 prodcode·colorcode 및 갤러리 파일명 일치',content_evidence='공식 상품 갤러리 largeimage 원본, 리뷰/추천 영역 제외')
        digest=hashlib.sha256(info['data']).hexdigest()
        if valid['passed'] and digest not in seen:accepted.append(dict(url=url,validation=valid));seen.add(digest)
        elif not valid['passed']:ctx.log('상품사진 규격 제외: '+url+' / '+', '.join(valid['reasons']),'WARNING')
        if len(accepted)>=6:break
    main,extra,warning=gallery_slots(accepted)
    if warning:ctx.log(warning,'WARNING')
    if not main:raise ValueError('규격을 통과한 공식 색상 상품사진 없음: '+data['colorcode'])
    return main,extra


def money(ctx,value,label):
    text=str(value or '').replace(',','').strip()
    if re.fullmatch(r'\d+',text) and int(text)>0:return int(text)
    ctx.log(label+' 누락/0/형식 미확인: 빈칸','WARNING');return None


def render(ctx,node,cache):
    if isinstance(node,Comment):return ''
    if isinstance(node,NavigableString):return html.escape(str(node))
    if not getattr(node,'name',None):return ''
    if node.name in ('script','style','button','iframe','form','video','noscript'):return ''
    if node.name=='input':return html.escape(node.get('value',''))
    if node.name=='img':
        url=image_url(node.get('data-src') or node.get('src') or '')
        if not url or not image_info(ctx,url,cache):return ''
        return '<img src="'+html.escape(url,quote=True)+'" alt="'+html.escape(node.get('alt',''),quote=True)+'" style="max-width:100%;height:auto">'
    body=''.join(render(ctx,child,cache) for child in node.children)
    if node.name not in ('div','section','p','span','h2','h3','h4','ul','ol','li','table','thead','tbody','tr','th','td','figure','figcaption','br','strong','em','b'):return body
    if node.name=='br':return '<br>'
    attrs=''
    for key in ('colspan','rowspan'):
        if node.name in ('th','td') and str(node.get(key,'')).isdigit():attrs+=' '+key+'="'+str(node[key])+'"'
    if node.name=='table':attrs+=' style="border-collapse:collapse;width:100%"'
    if node.name in ('th','td'):attrs+=' style="border:1px solid #ccc;padding:8px"'
    return '<'+node.name+attrs+'>'+body+'</'+node.name+'>'


def detail(ctx,doc,data,cache):
    sections=[]
    for selector in ('#prd-detail-info .opt-feature-inner','#prd-detail-info .prd-pics','#prd-detail-info .prd-info-list','#prd-detail-info .prd-info-clean','#prd-detail-size'):
        source=doc.select_one(selector)
        if source is None:
            ctx.log('상세 구성 요소 없음: '+selector,'WARNING');continue
        fragment=BeautifulSoup(str(source),'html.parser')
        for material in fragment.select('.tbl-material'):
            rows=[]
            for row in material.select('tr'):
                label=row.find('th');selected=row.select_one('span.on')
                if label and selected:rows.append('<tr><th>'+html.escape(label.get_text(' ',strip=True))+'</th><td>'+html.escape(selected.get_text(' ',strip=True))+'</td></tr>')
            replacement=BeautifulSoup('<table>'+''.join(rows)+'</table>','html.parser')
            material.replace_with(replacement)
        markup=render(ctx,fragment,cache)
        if markup:sections.append(markup)
    markup='<div>'+''.join(sections)+'</div>'
    if len(markup.encode('utf-16-le'))//2>32767:raise ValueError('상세 HTML 엑셀 셀 한도 초과')
    return markup


def crawl(ctx):
    raw=str(ctx.product_code).strip();direct=raw.startswith('https://')
    if direct:
        if urlsplit(raw).hostname not in HOSTS or urlsplit(raw).path!='/product/view':raise ValueError('공식 상품 링크 형식 오류')
        pid=parse_qs(urlsplit(raw).query).get('productcode',[''])[0]
        doc=BeautifulSoup(fetch(ctx,SITE+'/product/view?'+urlencode({'productcode':pid})),'html.parser');base=str(inline_json(doc,'productInfo')['prodcode']).upper();links=[pid]
    else:
        base=raw.upper()
        if not re.fullmatch(r'[A-Z0-9]{6,20}',base):raise ValueError('기본 품번을 입력하세요. 예: BACTF2661')
        links=search(ctx,base)
    if not links:ctx.fail_item('공식 품번 검색 결과 없음: '+base);raise ValueError('공식 품번 검색 결과 없음: '+base)
    pending=list(links);seen=set();colors=set();errors=[]
    while pending:
        pid=pending.pop(0)
        if pid in seen:continue
        seen.add(pid);ctx.checkpoint()
        try:
            result=product(ctx,pid,base)
            if result is None:continue
            doc,data,url=result;code=data['colorcode'];ctx.stage('베네통 '+base+' / '+code)
            for node in doc.select('.colorlist-set[data-productcode]'):
                linked=node['data-productcode']
                if linked not in seen and linked not in pending:pending.append(linked)
            if code in colors:continue
            color=str(data.get('colorcode_val') or '').split('_',1)[-1].strip()
            if not color or color.startswith('#'):
                selected=doc.select_one('.chk-color input[checked]')
                color=selected.parent.get_text(' ',strip=True) if selected else code
            record=dict(product_code=base,product_name=data['productname'].strip(),product_url=url,color=color,color_name=color,color_code=code)
            if ctx.enabled('size'):
                values=list(dict.fromkeys(n.get('data-opt-code','').strip() for n in doc.select('.prd-size-select[data-opt-code]') if n.get('data-opt-code')))
                if not values:values=[v for v in str(data.get('sizecd') or '').split('@#') if v]
                record['size']=','.join(values)
                if not values:ctx.log('공식 사이즈 옵션 없음: '+code,'WARNING')
            if ctx.enabled('price'):record['price']=money(ctx,data.get('sellprice'),'판매가')
            if ctx.enabled('regular_price'):record['regular_price']=money(ctx,data.get('consumerprice'),'정상가')
            if ctx.enabled('category'):
                try:record['category']=' > '.join(v for v in inline_json(doc,'categoryName') if v)
                except ValueError:record['category']='';ctx.log('공식 카테고리 확인 불가: 빈칸','WARNING')
            cache={}
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                main,extra=gallery(ctx,doc,data,cache)
                if ctx.enabled('main_image'):record['main_image']=main
                if ctx.enabled('additional_images'):record['additional_images']=','.join(extra)
            if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,doc,data,cache)
            ctx.emit_result(record);colors.add(code)
        except (ValueError,KeyError,TypeError) as exc:
            message=base+' / '+pid+' / '+str(exc);ctx.fail_item(message);errors.append(message)
    if errors:raise ValueError('일부 색상 수집 미완료: '+'; '.join(errors))
    if not colors:ctx.fail_item('정확한 공식 품번 일치 상품 없음: '+base);raise ValueError('정확한 공식 품번 일치 상품 없음: '+base)
    ctx.log(base+' / '+str(len(colors))+'색상 수집 완료','SUCCESS')
