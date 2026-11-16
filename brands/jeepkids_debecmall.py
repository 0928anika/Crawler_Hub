PORTAL_API_VERSION = 1

import html
import json
import re
from io import BytesIO
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image

SITE = 'https://www.debecmall.co.kr'

def fetch(ctx, route, **params):
    ctx.checkpoint()
    url = SITE + route + '?' + urlencode(params)
    with build_opener().open(Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}), timeout=20) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    ctx.checkpoint()
    if len(data) > 8 * 1024 * 1024: raise ValueError('대백몰 응답 용량 초과')
    value = json.loads(data.decode('utf-8-sig'))
    if not isinstance(value, dict) or not isinstance(value.get('result'), list):
        raise ValueError('대백몰 접근/응답 형식 확인 실패: 검색 결과 없음과 구분')
    return value['result']

def normalize(value):
    return re.sub(r'\s+', '', str(value or '')).upper()

def search(ctx, code):
    found = {}; total = 0
    for page in range(1, 51):
        rows = fetch(ctx, '/api/product/searchProductPage', q=code, sort='undefined', startMoney='', endMoney='', PAGENUM=page, PAGESIZE=20, c='')
        if not rows: break
        total += len(rows)
        for row in rows:
            if normalize(row.get('VENDOR_PRODUCT_CODE')) == code and row.get('BRAND_NAME') == '지프키즈':
                found[str(row['PRODUCT_CODE'])] = row
        if len(rows) < 20 or total >= int(rows[0].get('TOTALCNT') or total): break
    else: raise ValueError('대백몰 검색 페이지 한도 초과: 결과 미완료')
    if not found:
        raise ValueError('대백몰 검색 결과 없음: '+code if not total else '검색 결과는 있으나 지프키즈 정확한 품번 불일치: '+code)
    return list(found.values())

def name_of(row, code):
    name = str(row.get('PRODUCT_NAME') or row.get('LONG_LABEL') or '')
    name = re.sub(r'\[[^\]]*\]', '', name)
    name = re.sub(re.escape(code), '', name, flags=re.I)
    return re.sub(r'\s+', ' ', re.sub(r'\(\s*\)', '', name)).strip()

def palette(rows):
    result = {}
    for row in rows:
        label = str(row.get('SKU_NAME') or '').strip()
        match = re.fullmatch(r'(.+?)\s+([^\s]+)', label)
        if not match: raise ValueError('색상/사이즈 옵션 분리 확인 필요: '+label)
        color, size = match.groups()
        item = result.setdefault(color, dict(color_name=color, color_code='', size=[]))
        if size not in item['size']: item['size'].append(size)
        if row.get('ADDED_PRICE') not in (None, '', 0, '0'):
            raise ValueError('추가금액 옵션: 색상별 판매가 확인 필요')
    if not result: raise ValueError('대백몰 공식 색상/사이즈 옵션 없음')
    return [dict(v, size=','.join(v['size'])) for v in result.values()]

def image_url(raw, kind):
    url = urljoin(SITE, str(raw or ''))
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or p.hostname not in ('www.debecmall.co.kr', 'debecmall.co.kr') or p.username or p.password: return ''
    if not p.path.startswith('/files/'+kind+'/'): return ''
    return 'https://www.debecmall.co.kr'+p.path+('?' + p.query if p.query else '')

def body_urls(attrs):
    urls = []
    for row in attrs:
        if row.get('ATTR_NAME') != '상세설명': continue
        for node in BeautifulSoup(str(row.get('ATTR_VALUE') or ''), 'html.parser').find_all('img'):
            url = image_url(node.get('src'), 'DETAIL')
            if url and url not in urls: urls.append(url)
    return urls

def checked(ctx, url, cache, detail=False):
    if url not in cache:
        result = check_public_image(url, timeout=15, retries=1, max_bytes=64*1024*1024 if detail else 10000000, checkpoint=ctx.checkpoint)
        if result['status'] != 'accessible': raise ValueError('상품 이미지 조회 미완료: '+url+' / '+', '.join(result['reasons']))
        with Image.open(BytesIO(result['data'])) as im:
            if im.format not in ('JPEG', 'PNG', 'WEBP'): raise ValueError('지원하지 않는 원본 이미지 형식')
            if not detail and (min(im.size)<500 or max(im.size)/min(im.size)>1.8): raise ValueError('공식 대표 이미지 규격 확인 필요: '+url)
            im.verify()
        cache[url] = True
    return url

def amount(ctx, value, label):
    if str(value or '').isdigit() and int(value)>0: return int(value)
    ctx.log(label+' 없음/형식 미확인: 빈칸', 'WARNING')
    return None

def crawl(ctx):
    code = normalize(ctx.product_code)
    if not re.fullmatch(r'[A-Z]{2}\d[A-Z]{3}\d{3}', code): raise ValueError('품번 형식 확인 필요: 예 KR3TPU247')
    ctx.allow_automatic_results = True
    try:
        ctx.stage('지프키즈 대백몰 품번 검색: '+code)
        products = search(ctx, code); cache = {}; seen = set()
        for candidate in products:
            rows = fetch(ctx, '/api/product/prdDetail', productCode=candidate['PRODUCT_CODE'])
            if len(rows)!=1: raise ValueError('대백몰 상품 상세 확인 실패')
            row = rows[0]
            if normalize(row.get('VENDOR_PRODUCT_CODE')) != code or row.get('BRAND_NAME') != '지프키즈': raise ValueError('상세 품번/브랜드 불일치')
            pid = row['PRODUCT_ID']; colors = palette(fetch(ctx, '/api/product/listSkuInfo', productId=pid, saleStatus=row.get('SALE_STATUS','N')))
            name = name_of(row, code)
            if not name: raise ValueError('공식 상품명 없음')
            common = dict(product_code=code, product_name=name, product_url=SITE+'/?'+urlencode(dict(am='prd/dm_prd_detail', Lang='kr', code=row['PRODUCT_CODE'])), **{'원본 상품명':row.get('LONG_LABEL') or row.get('PRODUCT_NAME','')})
            if ctx.enabled('price'): common['price'] = amount(ctx, row.get('SALE_PRICE'), '판매가')
            if ctx.enabled('regular_price'):
                common['regular_price'] = None; ctx.log('독립 정상가 표시 미확인: 빈칸', 'WARNING')
            if ctx.enabled('category'):
                cats=fetch(ctx, '/api/common/select', resultType='list', sqlId='pool.getUpperCategory', tree_path=row.get('TREE_PATH',''), tree_item_depth=1)
                common['category']=' > '.join(str(c.get('TREE_ITEM_LABEL') or '') for c in cats)
            detail = ''
            if ctx.enabled('detail_page'):
                attrs=fetch(ctx, '/api/common/select', sqlId='product.prd_attr', resultType='list', productId=pid)
                urls=body_urls(attrs)
                detail='<div>'+''.join('<p><img src="'+html.escape(checked(ctx,u,cache,True),quote=True)+'" alt="상품 상세정보"></p>' for u in urls)+'</div>' if urls else ''
                if not urls: ctx.log('상품 본문 이미지 없음: 사이즈표/고시를 임의 생성하지 않음', 'WARNING')
                common['detail_page']=detail
            photos=[]
            if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                images=fetch(ctx, '/api/product/prdProductImage', product_code=row['PRODUCT_CODE'])
                for item in sorted(images,key=lambda v:int(v.get('SEQ') or 0)):
                    url=image_url(item.get('SIZE_600'), 'PRODUCT')
                    if url and url not in photos: photos.append(checked(ctx,url,cache))
                if not photos: raise ValueError('공식 상품 대표/추가 이미지 없음')
            if len(colors)>1 and photos:
                if ctx.enabled('size'): common['size']=','.join(dict.fromkeys(s for c in colors for s in c['size'].split(',')))
                choices=[dict(c) if ctx.enabled('size') else {k:v for k,v in c.items() if k!='size'} for c in colors]
                for url in photos: ctx.emit_unmapped(dict(common),choices,url,shared_detail_html=detail)
                ctx.log('공식 색상별 이미지 연결 없음: 수동 매핑으로 전달', 'INFO')
            else:
                for color in colors:
                    if color['color_name'] in seen: continue
                    record=dict(common,color=color['color_name'],color_name=color['color_name'],color_code='')
                    if ctx.enabled('size'): record['size']=color['size']
                    if ctx.enabled('main_image'): record['main_image']=photos[0] if photos else ''
                    if ctx.enabled('additional_images'): record['additional_images']=','.join(photos[1:6] or photos[:1])
                    ctx.emit_result(record);seen.add(color['color_name'])
        ctx.log('지프키즈 대백몰 수집 완료', 'SUCCESS')
    except Exception as exc:
        ctx.fail_item(str(exc));raise
