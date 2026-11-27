PORTAL_API_VERSION = 1
"""Under Armour Korea official SFCC product/variation data, isolated Chrome."""
import html
import io
import json
import re
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image, gallery_slots
from detail_store import excel_detail
from regular_price import amount

SITE = 'https://www.underarmour.co.kr'
HOSTS = ('www.underarmour.co.kr', 'underarmour.co.kr')
IMAGE_HOSTS = ('underarmour.scene7.com',)
API = SITE + '/on/demandware.store/Sites-KR-Site/ko_KR/'


def input_code(value):
    match = re.fullmatch(r'([0-9]{7})(?:[-_]([0-9]{3}))?', str(value).strip())
    if not match:
        raise ValueError('품번 형식: 1373882 또는 1373882-001')
    return match[1], match[2]


def public_url(value, images=False):
    url = urljoin(SITE, str(value or ''))
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in (IMAGE_HOSTS if images else HOSTS) or parsed.username or parsed.password:
        raise ValueError('공식 출처가 아닌 주소: ' + url)
    return url


def fetch_json(ctx, page, url):
    ctx.checkpoint()
    url = public_url(url)
    data = page.evaluate("""async url => {
        const controller = new AbortController();const timer = setTimeout(()=>controller.abort(),20000);
        try {const r=await fetch(url,{signal:controller.signal});
            const text=await r.text();return {status:r.status,text:text};}
        finally {clearTimeout(timer);}
    }""", url)
    ctx.checkpoint()
    if data['status'] != 200:
        raise ValueError('공식 상품 응답 HTTP ' + str(data['status']) + ': ' + url)
    if len(data['text']) > 8000000:
        raise ValueError('공식 상품 응답 용량 초과')
    try:
        return json.loads(data['text'])
    except ValueError:
        raise ValueError('공식 JSON 응답이 아닙니다: 접근 제한 또는 로딩 오류') from None


def variation_url(url, style, color, length=None):
    parsed = urlsplit(public_url(url))
    values = dict(parse_qsl(parsed.query, keep_blank_values=True))
    values.update(pid=style, quantity='1')
    values['dwvar_' + style + '_color'] = color
    values.pop('dwvar_' + style + '_size', None)
    if length is not None:
        values['dwvar_' + style + '_length'] = length
    return parsed._replace(query=urlencode(values)).geturl()


def attributes(product, key):
    return next((a.get('values') or [] for a in product.get('variationAttributes') or []
                 if a.get('id') == key or a.get('attributeId') == key), [])


def verify_product(product, style, color):
    custom = product.get('custom') or {}
    if str(custom.get('style') or custom.get('masterID') or product.get('id')) != style:
        raise ValueError('요청 품번과 공식 상품 응답 불일치')
    if str(custom.get('color') or '') != color:
        raise ValueError('요청 색상과 공식 상품 응답 불일치')
    if not str(product.get('productName') or '').strip():
        raise ValueError('공식 상품명 없음')


def sizes(product):
    return list(dict.fromkeys(str(v.get('displayValue') or v.get('value') or '').strip()
                              for v in attributes(product, 'size') if v.get('displayValue') or v.get('value')))


def product_images(product):
    groups = product.get('images') or {}
    rows = groups.get('pdpMainDesktop') or groups.get('pdpZoomDesktop') or []
    return list(dict.fromkeys(public_url(r.get('url') or r.get('absURL'), images=True)
                              for r in rows if r.get('url') or r.get('absURL')))


def checked_images(ctx, urls, cache):
    accepted = []
    for url in urls:
        ctx.checkpoint()
        if url not in cache:
            cache[url] = check_public_image(url, timeout=12, retries=1, max_bytes=10000000, checkpoint=ctx.checkpoint)
        result = cache[url]
        if result['status'] == 'unknown':
            raise ValueError('상품 이미지 조회 미완료: ' + url)
        if result['status'] != 'accessible':
            ctx.log('상품 이미지 접근 불가: ' + url, 'WARNING');continue
        with Image.open(io.BytesIO(result['data'])) as im:
            valid = (im.format in ('JPEG','PNG','WEBP') and not getattr(im,'is_animated',False)
                     and min(im.size)>=500 and max(im.size)/min(im.size)<=1.8)
        if not valid:
            ctx.log('상품 이미지 규격 미달: ' + url, 'WARNING');continue
        accepted.append(url)
    return accepted


def size_markup(data):
    # Use the chart linked by this exact product, never guess by category.
    doc = BeautifulSoup(str(data.get('sizechartTable') or ''), 'html.parser')
    tables = ''.join(str(t) for t in doc.select('table'))
    if not tables:
        return ''
    notes = [n.get_text(' ',strip=True) for n in doc.select('p') if n.get_text(' ',strip=True)]
    return '<h3>사이즈 가이드</h3>' + tables + ''.join('<p>'+html.escape(n)+'</p>' for n in notes)


def detail_markup(product, photos, chart, description_html=''):
    custom = product.get('custom') or {}
    parts = ['<img src="' + html.escape(u,quote=True) + '">' for u in photos]
    doc = BeautifulSoup(description_html, 'html.parser')
    lead = doc.select_one('.b-product_description-content')
    if lead and lead.get_text(' ',strip=True):
        parts.append('<p>'+html.escape(lead.get_text(' ',strip=True))+'</p>')
    if not lead:
        for field in ('shortDescription', 'longDescription'):
            value = product.get(field)
            if isinstance(value, str) and value.strip():
                text = BeautifulSoup(value, 'html.parser').get_text(' ', strip=True)
                if text:parts.append('<p>'+html.escape(text)+'</p>')
    for key, label in [('dna','기능 소개'),('specs','제품 정보'),('fitCare','핏 및 관리 방법')]:
        rows = [str(v).strip() for v in custom.get(key) or [] if str(v).strip()]
        if rows:
            parts.append('<h3>'+label+'</h3><ul>'+''.join('<li>'+html.escape(v)+'</li>' for v in rows)+'</ul>')
    # Preserve actual disclosure tables when supplied; do not invent absent notices.
    parts.extend(str(t) for t in doc.select('table'))
    if chart:
        parts.append(chart)
    value, overflow = excel_detail(''.join(parts))
    if overflow:
        raise ValueError('상세 HTML이 엑셀 셀 한도를 초과했습니다.')
    return value


def crawl(ctx):
    from playwright.sync_api import sync_playwright
    try:
        style, requested = input_code(ctx.product_code)
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel='chrome',headless=True)
            try:
                page = browser.new_page(locale='ko-KR')
                ctx.stage('언더아머 공식 상품 조회: ' + style)
                response = page.goto(SITE+'/ko-kr/p/'+style+'.html',wait_until='domcontentloaded',timeout=30000)
                doc = BeautifulSoup(page.content(),'html.parser')
                root = doc.select_one('.product-detail[data-pid]')
                if not response or response.status >= 400 or not root or root.get('data-pid') != style:
                    raise ValueError('공식 상품 페이지 확인 실패: '+style+' / HTTP '+str(response.status if response else '응답 없음'))
                canonical = public_url(page.url)
                colors = {}
                for node in root.select('[data-attr="color"] a[data-attr-value]'):
                    code = str(node.get('data-attr-value') or '')
                    if re.fullmatch(r'[0-9]{3}',code):
                        colors.setdefault(code,dict(url=node.get('data-attr-url'),name=re.sub(r'\s*컬러 선택$','',node.get('title') or '').strip()))
                if requested:
                    colors = {k:v for k,v in colors.items() if k==requested}
                if not colors:
                    raise ValueError('공식 색상 옵션 없음: '+str(requested or style))
                crumbs = list(dict.fromkeys(n.get_text(' ',strip=True) for n in root.select('.b-breadcrumbs-link') if n.get_text(' ',strip=True)))
                chart_cache, cache, failures = {}, {}, []
                for color, item in colors.items():
                    ctx.checkpoint();ctx.stage('언더아머 색상 수집: '+style+'-'+color)
                    try:
                        if not item['url']:
                            raise ValueError('공식 색상 조회 주소 없음')
                        url = variation_url(item['url'],style,color)
                        data = fetch_json(ctx,page,url);product = data.get('product') or {};verify_product(product,style,color)
                        size_values = sizes(product)
                        lengths = attributes(product,'length')
                        if ctx.enabled('size') and len(lengths)>1:
                            size_values = []
                            for length in lengths:
                                length_id = str(length.get('value') or length.get('id') or '')
                                varied = fetch_json(ctx,page,variation_url(url,style,color,length_id)).get('product') or {}
                                verify_product(varied,style,color)
                                size_values.extend(str(length.get('displayValue') or length_id)+'/'+s for s in sizes(varied))
                        custom = product.get('custom') or {}
                        name = re.sub(r'^(?:언더아머|UNDER ARMOUR)\s*','',product['productName'],flags=re.I).strip()
                        subheader = str(custom.get('subHeader') or '').strip()
                        if subheader and subheader not in name:
                            name += ' '+subheader
                        name = re.sub(r'(?<![0-9])'+re.escape(style)+r'(?:[-_][0-9]{3})?(?![0-9])','',name).strip()
                        record = dict(product_code=style,product_name=name,color=item['name'] or custom.get('colorway') or color,
                            color_name=item['name'] or custom.get('colorway') or '',color_code=color,
                            product_url=canonical.split('?')[0]+'?'+urlencode({'dwvar_'+style+'_color':color}))
                        if ctx.enabled('size'):record['size']=','.join(dict.fromkeys(size_values))
                        prices = product.get('price') or {}
                        if ctx.enabled('price'):record['price']=amount((prices.get('sales') or {}).get('value'))
                        if ctx.enabled('regular_price'):record['regular_price']=amount((prices.get('list') or {}).get('value'))
                        if ctx.enabled('category'):record['category']=' > '.join(crumbs)
                        photos = []
                        if any(ctx.enabled(k) for k in ('main_image','additional_images','detail_page')):
                            photos=checked_images(ctx,product_images(product),cache)
                        if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                            if not photos:raise ValueError('규격을 통과한 공식 상품 사진 없음')
                            main,extra,_=gallery_slots([dict(url=u,validation=dict(passed=True)) for u in photos])
                            if ctx.enabled('main_image'):record['main_image']=main
                            if ctx.enabled('additional_images'):record['additional_images']=','.join(extra)
                        if ctx.enabled('detail_page'):
                            chart_id = product.get('sizeChartId');chart = ''
                            if chart_id:
                                if chart_id not in chart_cache:
                                    chart_cache[chart_id]=size_markup(fetch_json(ctx,page,API+'Product-SizeChart?'+urlencode({'cid':chart_id})))
                                chart=chart_cache[chart_id]
                            record['detail_page']=detail_markup(product,photos,chart,data.get('productCustomDescriptionHtml') or '')
                        ctx.emit_result(record)
                    except Exception as exc:
                        failures.append(style+'-'+color+': '+str(exc));ctx.fail_item(failures[-1])
                if failures:
                    raise RuntimeError('일부 색상 수집 미완료: '+' / '.join(failures))
            finally:
                browser.close()
    except Exception as exc:
        if not locals().get('failures'):ctx.fail_item(str(exc))
        raise
