"""Oberon 신규 브랜드 오프라인 검사: 실제 SDK CrawlContext + 네트워크 대역(fixture).

실사이트 응답이 아니라 각 사이트 구조를 본뜬 최소 fixture다. SDK 호출 형식, 선택 항목 생략,
혼합 매핑(자동/수동) 분리, 고시·사이즈 표 재구성, 실패 계약만 검증한다.
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from crawler_sdk import CrawlContext
from PIL import Image

NEW = ['adhoc_official', 'bangbang_official', 'decoand_official', 'crocodile_official', 'disneygolf_official',
       'renomakids_official', 'minimum_official', 'adidas_official', 'brentwood_official', 'ping_official', 'ecolier_official',
       'jdx_official', 'sfit_official', 'elcanto_official', 'riverclassy_favlook', 'zensai_official', 'mountia_official',
       'lenic_official', 'lecoqgolf_official', 'underarmour_official', 'millet_official', 'fila_official',
       'redface_official']


def load(key):
    spec = importlib.util.spec_from_file_location(key + '_oberon_test', ROOT / 'brands' / (key + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def image(url):
    color = zlib.crc32(url.encode()) & 0xFFFFFF
    size = (300, 300) if 'small' in url else (600, 2000) if 'long' in url else (800, 800)
    buffer = io.BytesIO()
    Image.new('RGB', size, ((color >> 16) & 255, (color >> 8) & 255, color & 255)).save(buffer, 'PNG')
    return buffer.getvalue(), size


class Network:
    def __init__(self, module, routes):
        self.routes, self.calls, self.images = routes, [], []
        module.fetch = self.fetch
        module.check_public_image = self.check

    def fetch(self, ctx, url, hosts, **kw):
        self.calls.append(url)
        for key, value in self.routes.items():
            if url == key or (key.endswith('*') and url.startswith(key[:-1])):
                if isinstance(value, Exception):
                    raise value
                body = value if isinstance(value, bytes) else (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)).encode()
                return body, url, 'text/html; charset=utf-8'
        raise AssertionError('예상하지 않은 요청: ' + url)

    def check(self, url, **kw):
        self.images.append(url)
        if '404' in url:
            return dict(status='inaccessible', url=url, final_url=url, data=None, reasons=['공개 GET 거부: HTTP 404'], warnings=[])
        data, (w, h) = image(url)
        return dict(status='accessible', url=url, final_url=url, data=data, width=w, height=h, format='PNG', reasons=[], warnings=[])


PRESENTATION = __import__('re').compile(r'\s(?:style|border|cellpadding|cellspacing|align|width)="[^"]*"')


def plain_html(record):
    """상세 HTML 비교용: 허브 excel_detail이 덧붙이는 표시용 속성(style 등)만 지우고 내용·구조(rowspan 등)는 그대로 비교."""
    for key in ('상세페이지 HTML',):
        if isinstance(record.get(key), str):
            record[key] = PRESENTATION.sub('', record[key]).replace('/>', '>')
    unmapped = record.get('_unmapped')
    if isinstance(unmapped, dict) and isinstance(unmapped.get('shared_detail_html'), str):
        unmapped['shared_detail_html'] = PRESENTATION.sub('', unmapped['shared_detail_html']).replace('/>', '>')
    return record


def run(module, key, code, selected, routes):
    meta = json.loads((ROOT / 'brands' / (key + '.json')).read_text(encoding='utf-8'))
    net = Network(module, routes)
    events, error = [], None
    with tempfile.TemporaryDirectory() as folder:
        ctx = CrawlContext('unused', Path(folder) / 'result.xlsx', 0, selected, meta['output_headers'], lambda: None,
                           lambda kind, **data: events.append((kind, data)), input_item={'품번': code},
                           mapping_mode=meta['mapping_mode'], image_mode=meta['image_mode'])
        try:
            module.crawl(ctx)
            ctx.finish()
        except Exception as exc:
            error = exc
    rows = [d for k, d in events if k == 'row' and not d.get('header')]
    headers = [d['row'] for k, d in events if k == 'row' and d.get('header')][-1]
    records = [plain_html(dict(dict(zip(headers, d['row'])), _unmapped=d.get('unmapped'))) for d in rows]
    failures = [d['message'] for k, d in events if k == 'item_failure']
    return records, failures, error, net


CAFE24_SEARCH = '<div class="xans-search-result"><ul><li id="anchorBoxId_101"></li><li id="anchorBoxId_102"></li></ul></div>'
STOCK = {
    'P1': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': ['블랙(BK)', '090'], 'option_price': 39000, 'is_display': 'T', 'is_selling': 'T'},
    'P2': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': ['블랙(BK)', '095'], 'option_price': 39000, 'is_display': 'T', 'is_selling': 'F'},
    'P3': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': ['화이트(WH)', '090'], 'option_price': 39000, 'is_display': 'T'},
    'P4': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': ['화이트(WH)', '095'], 'option_price': 39000, 'is_display': 'T'},
}
CAFE24_PRODUCT = '''<html><head><meta property="og:image" content="https://shop.example/web/product/big/main.jpg">
<link rel="canonical" href="https://{host}/product/tee/101/"></head><body>
<script>var product_name = '[사은품증정] 데일리 반팔티 TS1001'; var option_stock_data = '{stock}';</script>
<div class="infoArea"><table><tr><th>상품명</th><td>[사은품증정] 데일리 반팔티 TS1001</td></tr></table>
<span id="span_product_price_text">39,000원</span><span id="span_product_price_custom">59,000원</span></div>
<div class="xans-product-headcategory"><ol><li>홈</li><li>상의</li><li>반팔</li></ol></div>
<div class="xans-product-image"><img class="BigImage" src="//{host}/web/product/medium/m.jpg">
<div class="xans-product-addimage"><img src="//{host}/web/product/extra/small/extra1.jpg"><img src="//{host}/web/product/extra/small/extra2.jpg"></div></div>
<ul option_title="색상"><li option_value="블랙(BK)" link_image="//{host}/web/product/option_button/black.jpg"></li><li option_value="화이트(WH)"></li></ul>
<div id="prdDetail" class="xans-product-additional"><div class="guide_contents cont"><img src="//{host}/detail/d1.jpg"><img src="//{host}/common/top.jpg"><img src="//{host}/detail/bn_pay.jpg"><img src="//{host}/detail/d404.jpg"></div></div>
<table id="desctable"><tr><td>소재</td><td>면 100%</td></tr><tr><td>제조국</td><td>대한민국</td></tr><tr><td>세탁방법 및 취급시 주의사항</td><td>단독 손세탁</td></tr></table>
</body></html>'''
OTHER_PRODUCT = '<html><body><script>var product_name = \'다른 상품 TS10010\';</script></body></html>'


def cafe24_routes(module, single=False):
    site, host = module.CONFIG['site'], module.CONFIG['hosts'][0]
    stock = {k: v for k, v in STOCK.items() if not single or v['option_value_orginal'][0] == '블랙(BK)'}
    page = CAFE24_PRODUCT.format(host=host, stock=json.dumps(stock, ensure_ascii=False).replace('"', '\\"'))
    if single:
        page = page.replace('<li option_value="화이트(WH)"></li>', '')
    return {site + '/product/search.html?keyword=TS1001': CAFE24_SEARCH,
            site + '/product/detail.html?product_no=101': page,
            site + '/product/detail.html?product_no=102': OTHER_PRODUCT,
            site + '/product/image_zoom2.html?product_no=101': '<html></html>'}


ALL = ['size', 'price', 'regular_price', 'category', 'main_image', 'additional_images', 'detail_page']


class Contract(unittest.TestCase):
    def test_registered_with_hub_contract(self):
        for key in NEW:
            with self.subTest(key=key):
                meta = json.loads((ROOT / 'brands' / (key + '.json')).read_text(encoding='utf-8'))
                code = (ROOT / 'brands' / meta['script']).read_text(encoding='utf-8')
                self.assertTrue(code.startswith('PORTAL_API_VERSION = 1\n'))
                self.assertEqual(meta['execution_level'], 1)
                self.assertIn('판매가', meta['output_headers'])
                self.assertIn('정상가', meta['output_headers'])
                self.assertIn('상품 링크', meta['output_headers'])
                if meta.get('image_mode') != 'local':
                    self.assertNotIn('emit_local_image(', code)
                else:
                    self.assertEqual((meta['mapping_mode'], meta['image_color_mapping']), ('manual', 'manual'))
                    self.assertNotIn('emit_unmapped(', code)
                if meta['mapping_mode'] == 'manual' and meta.get('image_mode') != 'local':
                    self.assertTrue(meta.get('hybrid_mapping'))
                    self.assertNotIn('mapping_verified=False', code)
                self.assertNotIn('import requests', code)


class Cafe24Hybrid(unittest.TestCase):
    def test_every_cafe24_brand_splits_linked_and_unlinked_photos(self):
        for key in ['adhoc_official', 'bangbang_official', 'decoand_official', 'crocodile_official', 'disneygolf_official', 'renomakids_official', 'minimum_official', 'lenic_official']:
            with self.subTest(key=key):
                module = load(key)
                rows, failures, error, net = run(module, key, 'TS1001', ALL, cafe24_routes(module))
                self.assertIsNone(error, failures)
                auto = [r for r in rows if not r['_unmapped']]
                manual = [r for r in rows if r['_unmapped']]
                self.assertEqual([r['색상코드'] for r in auto], ['BK'])
                self.assertTrue(auto[0]['대표이미지'].endswith('/option_button/black.jpg'))
                self.assertEqual(len(auto[0]['추가이미지'].split(',')), 5)
                self.assertEqual(auto[0]['사이즈'], '090,095')  # 품절 095 유지, 앞자리 0 보존
                self.assertEqual(auto[0]['판매가'], 39000)
                self.assertEqual(auto[0]['정상가'], 59000)
                self.assertEqual(auto[0]['상품명'], '데일리 반팔티')
                self.assertEqual(auto[0]['상품 링크'], 'https://' + module.CONFIG['hosts'][0] + '/product/tee/101/')
                self.assertGreaterEqual(len(manual), 3)
                self.assertTrue(all(len(r['_unmapped']['colors']) == 2 for r in manual))
                self.assertEqual(manual[0]['사이즈'], '090,095')
                detail = auto[0]['상세페이지 HTML']
                self.assertIn('/detail/d1.jpg', detail)
                self.assertNotIn('/common/', detail)
                self.assertNotIn('bn_pay', detail)
                self.assertNotIn('d404', detail)
                if module.CONFIG.get('notice'):
                    self.assertIn('<h3>상품정보고시</h3>', detail)
                    self.assertIn('<th>세탁방법 및 취급시 주의사항</th><td>단독 손세탁</td>', detail)
                self.assertEqual(manual[0]['_unmapped']['shared_detail_html'], detail)

    def test_single_color_page_is_fully_automatic(self):
        module = load('bangbang_official')
        rows, failures, error, net = run(module, 'bangbang_official', 'TS1001', ALL, cafe24_routes(module, single=True))
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['_unmapped'])
        self.assertEqual(rows[0]['색상'], '블랙 (BK)')

    def test_unselected_optional_items_make_no_dedicated_requests(self):
        module = load('renomakids_official')
        rows, failures, error, net = run(module, 'renomakids_official', 'TS1001', [], cafe24_routes(module))
        self.assertIsNone(error, failures)
        self.assertEqual(net.images, [])
        self.assertFalse(any('image_zoom2' in u for u in net.calls))
        self.assertEqual(sorted(r['색상코드'] for r in rows), ['BK', 'WH'])
        self.assertNotIn('상세페이지 HTML', rows[0] if rows[0].get('상세페이지 HTML') else {})

    def test_link_failure_keeps_other_rows_and_fails_input(self):
        module = load('adhoc_official')
        routes = cafe24_routes(module)
        routes[module.CONFIG['site'] + '/product/detail.html?product_no=102'] = module.CrawlError('HTTP 404: x')
        rows, failures, error, net = run(module, 'adhoc_official', 'TS1001', ['main_image'], routes)
        self.assertTrue(rows)
        self.assertIsNotNone(error)
        self.assertTrue(failures)

    def test_no_match_is_not_a_silent_success(self):
        module = load('crocodile_official')
        site = module.CONFIG['site']
        rows, failures, error, net = run(module, 'crocodile_official', 'ZZ9999', ALL,
                                         {site + '/product/search.html?keyword=ZZ9999': '<div class="xans-search-result"></div>'})
        self.assertEqual(rows, [])
        self.assertIsNotNone(error)
        self.assertTrue(failures)


ADIDAS = {'id': 'JZ3160', 'name': '아디다스 삼바 OG', 'attribute_list': {'color': 'Core Black / Cloud White', 'size_chart_id': 'SC1'},
          'meta_data': {'canonical': '//www.adidas.co.kr/samba-og/JZ3160.html'},
          'view_list': [{'type': 'standard', 'image_url': 'https://assets.adidas.com/images/w_500,f_auto,q_auto/a1/JZ3160_01.jpg'},
                        {'type': 'videoasset', 'image_url': 'https://assets.adidas.com/videos/v.mp4'},
                        {'type': 'standard', 'image_url': 'https://assets.adidas.com/images/w_500,f_auto,q_auto/a2/JZ3160_02.jpg'}],
          'variation_list': [{'size': '230'}, {'size': '240'}], 'pricing_information': {'currentPrice': 139000},
          'breadcrumb_list': [{'text': '신발'}, {'text': '오리지널스'}],
          'product_certificates': {'materialInformation': '갑피: 천연가죽', 'legalCountryofOrigin': '베트남'}}


class Others(unittest.TestCase):
    def test_adidas_article_is_one_color_and_missing_regular_price_is_blank(self):
        module = load('adidas_official')
        routes = {'https://www.adidas.co.kr/api/products/JZ3160': ADIDAS,
                  'https://www.adidas.co.kr/size-chart/api/size-chart/SC1*': module.CrawlError('HTTP 404: chart')}
        rows, failures, error, net = run(module, 'adidas_official', 'JZ3160', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['상품명'], '삼바 OG')
        self.assertEqual(row['색상명'], 'Core Black / Cloud White')
        self.assertIsNone(row['정상가'])
        self.assertNotIn(',f_auto', row['대표이미지'])
        self.assertIn('/images/w_1000/', row['대표이미지'])
        self.assertIn('<th>제조국</th><td>베트남</td>', row['상세페이지 HTML'])
        self.assertFalse(any('videos' in u for u in net.images))

    def test_brentwood_color_products_and_size_figure_before_table(self):
        module = load('brentwood_official')
        product = {'code': 'BRUAW26451BK', 'baseProductCode': 'BRUAW26451', 'name': '브렌우드 울 블레이저', 'style': 'BLACK',
                   'images': [{'imageLZ': {'url': 'https://img.kolonmall.com/a_LZ.jpg'}}, {'imageLZ': {'url': 'https://img.kolonmall.com/b_LZ.jpg'}}],
                   'variantOptions': [{'variantOptionQualifiers': [{'qualifier': 'size', 'value': '095'}]}],
                   'price': {'price': 300000, 'wishPrice': 450000}, 'fabric': '울 100%',
                   'sizeSpecs': [{'useRegnNm': '총장', 'sortSeq': 1, 'sizeSpecs': [{'legacySize': '095', 'sizeSpecVal': '72'}]}],
                   'diagramUseYn': True, 'productDiagram': {'imageUrl': 'https://img.kolonmall.com/diagram.jpg'}}

        def page(data):
            return 'self[Symbol.for("ApolloSSRDataTransport")] ??= []).push(' + json.dumps({'rehydrate': {'x': {'data': data}}}) + ')</script>'
        routes = {'https://www.kolonmall.com/Brands/BRENTWOOD/Search?keyword=BRUAW26451': page({'products': {'results': [{'code': 'BRUAW26451BK'}, {'code': 'BRUAW264519'}]}}),
                  'https://www.kolonmall.com/Brands/BRENTWOOD/Product/BRUAW26451BK': page({'productDetail': {'product': product}})}
        rows, failures, error, net = run(module, 'brentwood_official', 'BRUAW26451', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['색상명'], rows[0]['색상코드'], rows[0]['품번']), ('BLACK', 'BK', 'BRUAW26451'))
        self.assertEqual(rows[0]['정상가'], 450000)
        detail = rows[0]['상세페이지 HTML']
        self.assertLess(detail.index('diagram.jpg'), detail.index('<table', detail.index('사이즈 정보')))

    def test_bucketstore_color_code_and_notice(self):
        module = load('ping_official')
        goods = {'goodsName': 'PING 남성 긴팔 이너', 'brandName': 'PING',
                 'optionList': [{'goodsCode': '111E3TI001_BK', 'option1Name': '블랙', 'option2List': [{'option2Name': '095'}, {'option2Name': '100'}]}],
                 'imageList': ['https://image.bucketstore.com/goods/1.jpg', 'https://image.bucketstore.com/goods/2.jpg']}
        rsc = '1:{"goodsDetail":' + json.dumps(goods) + ',"actualMeasurementSizeList":[{"option2Name":"095","총장":"70"}]}'
        routes = {'https://bucketstore.com/bucketapi/goods/search/v4*': {'content': {'body': [
                      {'code': '111E3TI001_BK', 'name': 'x', 'brandName': 'PING', 'price': {'tag': 99000, 'real': 79000}},
                      {'code': '111E3TI0012_BK', 'name': 'other'}]}},
                  'https://bucketstore.com/bucketapi/goods/lambda*': [{'itemkind': {'category1': 'MEN', 'category2': '티셔츠'}}],
                  'https://bucketstore.com/goods/detail/111E3TI001_BK': rsc,
                  'https://bucketstore.com/bucketapi/goods/111E3TI001_BK/detail*': {'data': {'goodsDescription': '<img src="https://image.bucketstore.com/desc/d1.jpg">',
                      'notificationInfoList': [{'item': '소재', 'content': '폴리 90%\r\n스판 10%'}]}}}
        rows, failures, error, net = run(module, 'ping_official', '111E3TI001', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['색상코드'], row['사이즈'], row['판매가'], row['정상가'], row['카테고리']), ('BK', '095,100', 79000, 99000, 'MEN > 티셔츠'))
        self.assertIn('<th>소재</th><td>폴리 90% 스판 10%</td>', row['상세페이지 HTML'])
        self.assertEqual(row['상품명'], '남성 긴팔 이너')

    def test_ecolier_brand_scope_and_manual_candidates(self):
        module = load('ecolier_official')
        search = ('<div class="goods_list_cont"><div class="item_cont"><a href="../goods/goods_view.php?goodsNo=1001"></a>'
                  '<span class="item_brand">에꼴리에</span><strong class="item_name">플레어 원피스 (25D0901)</strong></div>'
                  '<div class="item_cont"><a href="../goods/goods_view.php?goodsNo=1002"></a><span class="item_brand">소이</span>'
                  '<strong class="item_name">원피스 (25D0901)</strong></div>'
                  '<div class="item_cont"><a href="../goods/goods_view.php?goodsNo=1003"></a><span class="item_brand">에꼴리에</span>'
                  '<strong class="item_name">원피스 (25D09010)</strong></div></div>')
        view = ('<div class="item_detail_tit"><h3>[에꼴리에] 플레어 원피스 (25D0901)</h3></div>'
                '<div class="item_detail_list"><dl><dt>모델번호</dt><dd>25D0901</dd></dl></div>'
                '<input name="set_goods_price" value="129000.00"><input name="set_goods_fixedPrice" value="0.00">'
                '<select name="optionNo_0"><option value="">=컬러 선택=</option><option value="블랙">블랙</option><option value="네이비">네이비</option></select>'
                '<img src="https://cdn.soiemall.co.kr/data/goods/1001/image/magnify/m1.jpg"><img src="https://cdn.soiemall.co.kr/data/goods/1001/image/magnify/m2.jpg">'
                '<img src="https://cdn.soiemall.co.kr/data/goods/1001/image/list/l1.jpg">'
                '<div class="detail_explain_box"><img src="https://cdn.soiemall.co.kr/editor/d1.jpg"><img src="https://cdn.soiemall.co.kr/top1.jpg"></div>'
                '<div class="detail_info_box"><table><tr><th>치수</th><td>55, 66, 77</td><th>제조국</th><td>대한민국</td></tr></table></div>')
        routes = {'https://www.soiemall.co.kr/goods/goods_search.php?keyword=25D0901&page=1': search,
                  'https://www.soiemall.co.kr/goods/goods_search.php?keyword=25D0901&page=2': '<div></div>',
                  'https://www.soiemall.co.kr/goods/goods_view.php?goodsNo=1001': view}
        rows, failures, error, net = run(module, 'ecolier_official', '25D0901', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['_unmapped'] for r in rows))
        self.assertFalse(any('/list/' in u for u in net.images))
        self.assertEqual(rows[0]['사이즈'], '55,66,77')
        self.assertIsNone(rows[0]['정상가'])
        self.assertEqual(rows[0]['상품명'], '플레어 원피스')
        detail=module.soup(rows[0]['상세페이지 HTML'])
        self.assertEqual(detail.find('th',string='제조국').find_next_sibling('td').get_text(strip=True),'대한민국')
        self.assertNotIn('top1', rows[0]['상세페이지 HTML'])


def single_page(host, name, color, extra=''):
    stock = {'P1': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': [color, '090'], 'is_display': 'T'}}
    return ('<html><head><meta property="og:image" content="https://%s/web/product/big/%s.jpg"></head><body>'
            "<script>var product_name = '%s'; var option_stock_data = '%s';</script>"
            '<span id="span_product_price_text">10,000원</span>%s</body></html>'
            % (host, color, name, json.dumps(stock, ensure_ascii=False).replace('"', '\\"'), extra))


class SecondBatch(unittest.TestCase):
    def test_jdx_follows_color_pages_and_full_code_selects_one(self):
        module = load('jdx_official')
        site = module.CONFIG['site']
        routes = {site + '/product/search.html?keyword=X1WDX4131': CAFE24_SEARCH.replace('<li id="anchorBoxId_102"></li>', ''),
                  site + '/product/detail.html?product_no=101': single_page('jdx.co.kr', '골프 팬츠 (X1WDX4131BK) - JDX', '블랙', '<a href="/product/golf/x1wdx4131wh/102/">WH</a>'),
                  site + '/product/detail.html?product_no=102': single_page('jdx.co.kr', '골프 팬츠 (X1WDX4131WH) - JDX', '화이트'),
                  site + '/product/image_zoom2.html?product_no=101': '<html></html>', site + '/product/image_zoom2.html?product_no=102': '<html></html>'}
        rows, failures, error, net = run(module, 'jdx_official', 'X1WDX4131', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual([(r['색상명'], r['색상코드'], r['품번']) for r in rows], [('블랙', 'BK', 'X1WDX4131'), ('화이트', 'WH', 'X1WDX4131')])
        self.assertEqual(rows[0]['상품명'], '골프 팬츠')
        rows, failures, error, net = run(module, 'jdx_official', 'X1WDX4131WH', [], routes)
        self.assertIsNone(error, failures)
        self.assertEqual([r['색상코드'] for r in rows], ['WH'])

    def test_sfit_code_in_image_filename_and_combined_option(self):
        module = load('sfit_official')
        site = module.CONFIG['site']
        stock = {'P1': {'option_name_original': ['색상/사이즈'], 'option_value_orginal': ['블랙(BK)/95(3)'], 'is_display': 'T'},
                 'P2': {'option_name_original': ['색상/사이즈'], 'option_value_orginal': ['블랙(BK)/100(0)'], 'is_display': 'T'}}
        page = ('<html><head><meta property="og:image" content="https://sfit.co.kr/web/product/big/SFT001_1.jpg"></head><body>'
                "<script>var product_name = '데일리 팬츠'; var option_stock_data = '" + json.dumps(stock, ensure_ascii=False).replace('"', '\\"') + "';</script></body></html>")
        routes = {site + '/product/search.html?keyword=SFT001': CAFE24_SEARCH.replace('<li id="anchorBoxId_102"></li>', ''),
                  site + '/product/detail.html?product_no=101': page, site + '/product/image_zoom2.html?product_no=101': '<html></html>'}
        rows, failures, error, net = run(module, 'sfit_official', 'SFT001', ['size', 'main_image'], routes)
        self.assertIsNone(error, failures)
        self.assertEqual((rows[0]['색상명'], rows[0]['색상코드'], rows[0]['사이즈']), ('블랙', 'BK', '95,100'))

    def test_elcanto_multicolor_goes_to_manual_with_notice_table(self):
        module = load('elcanto_official')
        ld = {'@type': 'Product', 'mpn': 'LCMC93', 'name': '엘칸토 로퍼', 'category': '여성(대) > 로퍼(중)', 'offers': {'price': 89000}}
        page = ('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False) + '</script>'
                '<div class="thumb-info"><span class="price">89,000원</span><del>129,000원</del>'
                '<div class="originImgArea"><img src="https://www.elcanto.co.kr/shopimages/a1.jpg"><img src="https://www.elcanto.co.kr/shopimages/a2.jpg"></div></div>'
                '<select name="optionlist[]" label="색상"></select><select name="optionlist[]" label="사이즈"></select>'
                "<script>var optionJsonData = {0:{opt_values:'블랙,230',sto_state:'SALE'},1:{opt_values:'블랙,240',sto_state:'SOLDOUT'},2:{opt_values:'브라운,230',sto_state:'SALE'}};\n</script>"
                '<div class="prd-detail"><img src="https://www.elcanto.co.kr/detail/d1.jpg"><img src="https://www.elcanto.co.kr/design/cs_1.jpg">'
                '<table><tr><th>소재</th><td>소가죽</td></tr><tr><th>제조국</th><td>대한민국</td></tr></table></div>')
        search = '<div class="prdListArea"><dl class="item-list"><a class="prdLink" href="/shop/shopdetail.html?branduid=55">x</a><dd class="name">로퍼 LCMC93</dd></dl></div>'
        routes = {'https://www.elcanto.co.kr/shop/shopbrand.html?search=LCMC93': search,
                  'https://www.elcanto.co.kr/shop/shopdetail.html?branduid=55': page}
        rows, failures, error, net = run(module, 'elcanto_official', 'LCMC93', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['_unmapped'] for r in rows))
        self.assertEqual(rows[0]['상품명'], '로퍼')
        self.assertEqual((rows[0]['판매가'], rows[0]['정상가'], rows[0]['카테고리']), (89000, 129000, '여성 > 로퍼'))
        self.assertEqual(rows[0]['사이즈'], '')  # 색상별 사이즈가 달라 공통값 없음
        self.assertIn('<th>소재</th><td>소가죽</td>', rows[0]['상세페이지 HTML'])
        self.assertNotIn('cs_1', rows[0]['상세페이지 HTML'])

    def test_riverclassy_uses_official_color_text_and_fails_without_it(self):
        module = load('riverclassy_favlook')
        def page(name):
            return ('<div class="thumb-wrap"><ul class="thumbnail"><img src="https://www.favlook.co.kr/shopimages/x1.jpg"></ul></div>'
                    '<div class="thumb-info"><div class="info"><p class="brand">리버클래시</p><h3 class="main_name">' + name + '</h3>'
                    '<p class="model_name">LMD51854</p><span class="sellprice"><em>59,000</em></span><span class="tagprice"><strike>99,000</strike></span>'
                    '</div></div><script>var optionJsonData = {0:{opt_values:\'55\'},1:{opt_values:\'66\'}};\n</script>')
        search = '<div class="item-list"><dl class="item"><a href="/shop/shopdetail.html?branduid=7">x</a>LMD51854</dl><dl class="item"><a href="/shop/shopdetail.html?branduid=8">y</a>LMD51854</dl></div>'
        routes = {'https://www.favlook.co.kr/shop/shopbrand.html?search=LMD51854': search,
                  'https://www.favlook.co.kr/shop/shopdetail.html?branduid=7': page('울 코트 블랙'),
                  'https://www.favlook.co.kr/shop/shopdetail.html?branduid=8': page('울 코트')}
        rows, failures, error, net = run(module, 'riverclassy_favlook', 'LMD51854', ['size', 'price', 'regular_price', 'main_image'], routes)
        self.assertEqual([(r['색상명'], r['사이즈'], r['판매가'], r['정상가']) for r in rows], [('블랙', '55,66', 59000, 99000)])
        self.assertIsNotNone(error)
        self.assertTrue(any('색상 표기가 없어' in f for f in failures))

    def test_zensai_sizes_only_requested_when_selected(self):
        module = load('zensai_official')
        ld = {'@type': 'Product', 'name': '[네이비] 남성 바람막이 (ZAD4JP403NV)', 'sku': 'ZAD4JP403NV'}
        page = ('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False) + '</script>'
                '<div id="prod_image_list"><img xoriginal="https://cdn-optimized.imweb.me/upload/a.jpg"></div>'
                '<span class="real_price">199,000원</span><span class="sale_price">259,000원</span>')
        routes = {'https://www.zensai.co.kr/search?keyword=ZAD4JP403': '<a href="/shop_view/?idx=11">남성 바람막이 (ZAD4JP403NV)</a>',
                  'https://www.zensai.co.kr/shop_view/?idx=11': page,
                  'https://www.zensai.co.kr/shop/load_option.cm': {'option_html': '<div class="dropdown-item"><a class="_requireOption">95</a></div><div class="dropdown-item"><a class="_requireOption">100 (품절)</a></div>'}}
        rows, failures, error, net = run(module, 'zensai_official', 'ZAD4JP403', ['price', 'regular_price', 'main_image'], routes)
        self.assertIsNone(error, failures)
        self.assertFalse(any('load_option' in u for u in net.calls))
        self.assertEqual((rows[0]['색상명'], rows[0]['색상코드'], rows[0]['상품명'], rows[0]['판매가'], rows[0]['정상가']), ('네이비', 'NV', '남성 바람막이', 199000, 259000))
        self.assertTrue(rows[0]['대표이미지'].startswith('https://cdn.imweb.me/'))
        rows, failures, error, net = run(module, 'zensai_official', 'ZAD4JP403', ['size'], routes)
        self.assertEqual(rows[0]['사이즈'], '95,100 (품절)')


class ThirdBatch(unittest.TestCase):
    def test_lecoq_variant_images_per_color_and_exact_code(self):
        module = load('lecoqgolf_official')
        base = 'https://dk-on.com/LECOQGOLF/product/GQ422LDJO5/'
        variant = lambda cc, name: {'url': base + cc, 'color': name, 'image': ['//img.dk-on.com/p/GQ422LDJO5_' + cc + '_N01.jpg', '//img.dk-on.com/p/GQ422LDJO5_' + cc + '_N02.jpg', '//img.dk-on.com/p/GQ422LDJO5_' + cc + '_D01.jpg'],
                                    'offers': {'price': 159000, 'priceSpecification': [{'name': '정상가', 'price': 229000}]}}
        ld = {'@type': 'ProductGroup', 'name': '[르꼬끄골프] 여성 다운 점퍼', 'category': '블랙위크 > 여성 > 아우터', 'material': '나일론 100%',
              'hasVariant': [variant('CREM', '크림'), variant('BLCK', '블랙')]}
        page = ('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False) + '</script>'
                '<input data-prod-cd="GQ422LDJO5" data-color-cd="CREM" data-size-cd="085"><input data-prod-cd="GQ422LDJO5" data-color-cd="CREM" data-size-cd="090" disabled>'
                '<input data-prod-cd="GQ422LDJO5" data-color-cd="BLCK" data-size-cd="090"><input data-prod-cd="GQ999" data-color-cd="BLCK" data-size-cd="100">'
                '<div class="tbl-size-guide"><table><tr><th>사이즈</th><th>가슴</th></tr><tr><td>085</td><td>98</td></tr></table></div>')
        search = '<div id="prodList"><a href="/LECOQGOLF/product/GQ422LDJO5/CREM">a</a><a href="/LECOQGOLF/product/GQ422LDJO5X/CREM">b</a></div>'
        routes = {'https://dk-on.com/LECOQGOLF/search?keyword=GQ422LDJO5': search, base + 'CREM': page}
        rows, failures, error, net = run(module, 'lecoqgolf_official', 'GQ422LDJO5', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual([(r['색상명'], r['색상코드'], r['사이즈']) for r in rows], [('크림', 'CREM', '085,090'), ('블랙', 'BLCK', '090')])
        self.assertTrue(rows[1]['대표이미지'].endswith('_BLCK_N01.jpg'))
        self.assertNotIn('CREM', rows[1]['추가이미지'] + rows[1]['상세페이지 HTML'])
        self.assertEqual((rows[0]['상품명'], rows[0]['판매가'], rows[0]['정상가'], rows[0]['카테고리']), ('여성 다운 점퍼', 159000, 229000, '여성 > 아우터'))
        detail = rows[0]['상세페이지 HTML']
        self.assertIn('_CREM_D01.jpg', detail)
        self.assertLess(detail.index('상품정보고시'), detail.index('사이즈 정보'))
        rows, failures, error, net = run(module, 'lecoqgolf_official', 'GQ422LDJO5_BLCK', ['price'], routes)
        self.assertIsNone(error, failures)
        self.assertEqual([r['색상코드'] for r in rows], ['BLCK'])

    def test_underarmour_color_pages_use_coded_images(self):
        module = load('underarmour_official')
        site = 'https://www.underarmour.co.kr'
        url = site + '/ko-kr/p/tops/1370494.html'
        def page(active, sizes):
            ld = [{'@type': 'Product', 'name': 'UA 테크 반팔 티셔츠', 'audience': {'GenderType': 'male'}, 'offers': {'price': 39000},
                   'image': ['//underarmour.scene7.com/is/image/Underarmour/V5-1370494-001_FC?rp=x', '//underarmour.scene7.com/is/image/Underarmour/V5-1370494-001_BC',
                             '//underarmour.scene7.com/is/image/Underarmour/V5-1370494-100_FC']},
                  {'@type': 'BreadcrumbList', 'itemListElement': [{'name': '홈'}, {'name': '상의'}]}]
            sw = ''.join('<a class="js-swatch-link%s" data-attr-value="%s" title="%s 컬러 선택"></a>' % (' m-active' if c == active else '', c, n) for c, n in (('001', 'Black'), ('100', 'White')))
            sz = ''.join('<button class="js-size-select%s" aria-label="size" data-attr-value="%s"></button>' % (' disabled' if s == 'XL' else '', s) for s in sizes)
            return ('<script type="application/ld+json">' + json.dumps(ld) + '</script>' + sw + sz +
                    '<span class="b-price-value m-actual" content="39000"></span><a href="/on/demandware.store/Sites-KR-Site/ko_KR/Product-SizeChart?cid=sizechart-men-tops">표</a>')
        routes = {site + '/ko-kr/search/?q=1370494&lang=ko_KR': '<section class="b-products_grid-tile" data-pid="1370494"><a href="/ko-kr/p/tops/1370494.html?x=1">t</a></section>',
                  url: page('001', ['M', 'XL']), url + '?dwvar_1370494_color=100': page('100', ['L']),
                  site + '/on/demandware.store/Sites-KR-Site/ko_KR/Product-SizeChart?cid=sizechart-men-tops': {'html': '<table><tr><th>사이즈</th><th>가슴</th></tr><tr><td>M</td><td>100</td></tr></table>'}}
        rows, failures, error, net = run(module, 'underarmour_official', '1370494', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual([(r['색상'], r['사이즈'], r['정상가']) for r in rows], [('Black (001)', 'M,XL', None), ('White (100)', 'L', None)])
        self.assertTrue(rows[0]['대표이미지'].endswith('V5-1370494-001_FC?wid=1000&hei=1250&fmt=jpg&qlt=90'))
        self.assertNotIn('-100_', rows[0]['추가이미지'] + rows[0]['상세페이지 HTML'])
        self.assertEqual(rows[0]['카테고리'], '남성 > 상의')
        self.assertIn('사이즈 정보', rows[0]['상세페이지 HTML'])
        rows, failures, error, net = run(module, 'underarmour_official', '1370494', ['price'], routes)
        self.assertFalse(any('SizeChart' in u for u in net.calls))

    def test_millet_follows_color_chips_and_needs_code_evidence(self):
        module = load('millet_official')
        site = 'https://www.millet.co.kr'
        def page(code, notice_color='WHITE(10), BLACK(16)'):
            return ('<div class="item-detail-info"><div class="cate"><span>MILLET</span><span>' + code + '</span></div>'
                    '<div class="item-detail-info-name"><span class="name">밀레 남성 티셔츠</span></div></div>'
                    '<div class="item-detail-price"><del>89,000</del><div class="price-info"><span class="price">59,000</span></div></div>'
                    '<div class="color-items"><a href="/front/product/22864">b</a><a href="/front/product/22863">w</a></div>'
                    '<div class="size-choice"><input name="size" value="95" optqty="3"><input name="size" value="100" optqty="0" disabled></div>'
                    '<div class="item-img-thumb"><img src="/upload/' + code + '_1.jpg"><img src="/upload/' + code + '_2.jpg"></div>'
                    '<div class="item-datail-cont"><img src="/upload/d_' + code + '.jpg"><div class="swiper-slide"><img src="/upload/rec.jpg"></div></div>'
                    '<table><tr><th>제품소재</th><td>폴리 100%</td></tr><tr><th>색상</th><td>' + notice_color + '</td></tr></table>')
        routes = {site + '/front/product/product_list.ajax': '<li><a href="/front/product/22864">밀레 티셔츠 MTSSB970</a></li>',
                  site + '/front/product/22864': page('MTSSB97016'), site + '/front/product/22863': page('MTSSB97010')}
        rows, failures, error, net = run(module, 'millet_official', 'MTSSB970', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual([(r['품번'], r['색상'], r['사이즈'], r['판매가'], r['정상가']) for r in rows],
                         [('MTSSB970', 'BLACK (16)', '95,100', 59000, 89000), ('MTSSB970', 'WHITE (10)', '95,100', 59000, 89000)])
        self.assertIn('MTSSB97010_1', rows[1]['대표이미지'])
        self.assertNotIn('rec.jpg', rows[0]['상세페이지 HTML'])
        self.assertIn('<th>제품소재</th><td>폴리 100%</td>', rows[0]['상세페이지 HTML'])
        routes[site + '/front/product/22863'] = page('MTSSB97010', 'BLACK(16)')
        rows, failures, error, net = run(module, 'millet_official', 'MTSSB970', ['price'], routes)
        self.assertEqual([r['색상코드'] for r in rows], ['16'])
        self.assertIsNotNone(error)
        self.assertTrue(any('추측하지 않음' in f for f in failures))

    def test_fila_direct_handle_one_color_with_notice(self):
        module = load('fila_official')
        site = 'https://www.fila.co.kr'
        ld = {'@type': 'Product', 'name': '휠라 로고 반팔 티셔츠', 'size': ['090', '095', '100'],
              'offers': [{'name': '095', 'price': '39000', 'availability': 'InStock'}, {'name': '090', 'price': '39000', 'availability': 'OutOfStock'}]}
        page = ('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False) + '</script>'
                '<script data-selected-variant>{"compare_at_price": 5900000}</script>'
                '<div id="x__main"><p>상품코드 : FS263PO01X001001</p><p>색상 : 001 (BLACK)</p>'
                '<div class="product__media-gallery"><img src="//www.fila.co.kr/cdn/shop/files/1100FS263PO01X001001_01.jpg?v=1"><img src="/cdn/shop/files/1100FS263PO01X001001_02.jpg"></div></div>'
                '<div id="model_slider_1"><img src="/cdn/shop/files/1100FS263PO01X001001_M1.jpg"><img src="/cdn/shop/files/1100FS263PO01X001071_M1.jpg"></div>'
                '<div class="accordion"><h2>상품정보고시</h2><div class="sub__content"><div class="sub__content-heading">소재</div><div class="sub__content-text">면 100%</div></div></div>')
        routes = {site + '/products/1100fs263po01x001001': page}
        rows, failures, error, net = run(module, 'fila_official', 'FS263PO01X001001', ALL, routes)
        self.assertIsNone(error, failures)
        r = rows[0]
        self.assertEqual((r['품번'], r['색상명'], r['색상코드'], r['사이즈'], r['판매가'], r['정상가']), ('FS263PO01X001', 'BLACK', '001', '090,095', 39000, 59000))
        self.assertTrue(r['대표이미지'].endswith('_01.jpg'))
        self.assertIn('001001_M1', r['상세페이지 HTML'])
        self.assertNotIn('001071', r['상세페이지 HTML'])
        self.assertIn('<th>소재</th><td>면 100%</td>', r['상세페이지 HTML'])
        routes = {site + '/products/1100fs263po01x001': module.CrawlError('HTTP 404: x'),
                  site + '/search/suggest.json?q=FS263PO01X001&resources%5Btype%5D=product&resources%5Blimit%5D=10':
                      {'resources': {'results': {'products': [{'handle': '1100fs263po01x001001'}, {'handle': '1100fs263po01x0010019'}]}}},
                  site + '/products/1100fs263po01x001001': page}
        rows, failures, error, net = run(module, 'fila_official', 'FS263PO01X001', ['price'], routes)
        self.assertIsNone(error, failures)
        self.assertEqual([r['색상코드'] for r in rows], ['001'])


def redface_strip():
    """흰 배경 통이미지: 색상명 띠 → 단독 컷 → 나란한 2컷 → 실측표(듬성한 선) 순서."""
    from PIL import ImageDraw
    img = Image.new('RGB', (1400, 2600), 'white')
    d = ImageDraw.Draw(img)
    d.rectangle((600, 120, 800, 145), fill=(20, 20, 20))        # 색상명 문구(얇은 띠, 단독 컷에 가장 가까움) → 단독 컷
    d.rectangle((600, 1060, 800, 1090), fill=(20, 20, 20))      # 색상명 문구 → 나란한 2컷
    d.rectangle((400, 180, 1000, 980), fill=(30, 60, 120))       # 단독 컷 600x800
    d.rectangle((80, 1150, 640, 1850), fill=(150, 30, 30))       # 왼쪽 컷 560x700
    d.rectangle((760, 1150, 1320, 1850), fill=(30, 120, 60))     # 오른쪽 컷
    for y in range(2000, 2500, 50):                              # 실측표: 듬성한 격자선
        d.line((100, y, 1300, y), fill=(0, 0, 0), width=2)
    for x in range(100, 1301, 200):
        d.line((x, 2000, x, 2450), fill=(0, 0, 0), width=2)
    buf = io.BytesIO(); img.save(buf, 'JPEG', quality=90)
    return buf.getvalue()


def solid(size, color):
    buf = io.BytesIO(); Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


class RedfaceLocalCrop(unittest.TestCase):
    def run_redface(self, selected, extra_routes=None, ocr=None):
        module = load('redface_official')
        if ocr is not None:
            module.ocr_tiles = ocr
        stock = {'P1': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': ['블랙(BK)', '090'], 'option_price': 189000, 'is_display': 'T'},
                 'P2': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': ['블랙(BK)', '095'], 'option_price': 189000, 'is_selling': 'F'},
                 'P3': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': ['네이비(NV)', '090'], 'option_price': 189000},
                 'P4': {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': ['네이비(NV)', '095'], 'option_price': 189000}}
        page = ('<html><head><meta property="og:image" content="https://theredface.com/web/product/big/main.jpg">'
                '<link rel="canonical" href="https://theredface.com/product/x/4413/"></head><body>'
                "<script>var product_name = '[사은품증정] 레드페이스 루미 구스 헤비 다운 우먼 재킷 REWFJKWBF560'; var option_stock_data = '"
                + json.dumps(stock, ensure_ascii=False).replace('"', '\\"') + "';</script>"
                '<div class="infoArea"><span id="span_product_price_text">189,000원</span><span id="span_product_price_custom">389,000원</span></div>'
                '<div class="xans-product-headcategory"><ol><li>홈</li><li>여성</li><li>다운</li></ol></div>'
                '<div class="xans-product-addimage"><img src="//theredface.com/web/product/extra/small/ex1.jpg"></div>'
                '<ul option_title="색상"><li option_value="블랙(BK)" link_image="//theredface.com/web/product/option_button/bk.jpg"></li><li option_value="네이비(NV)"></li></ul>'
                '<div id="prdDetail"><img src="/web/upload/common/top_banner.jpg"><img ec-data-src="/web/product_detail/REWFJKWBF560_strip.jpg">'
                '<table><tr><th>제품소재</th><td>나일론 100%</td></tr><tr><th>제조국</th><td>베트남</td></tr><tr><th>세탁방법 및 취급시 주의사항</th><td>드라이클리닝</td></tr></table>'
                '<table><tr><th>사이즈</th><th>가슴</th><th>총장</th></tr><tr><td>090</td><td rowspan="2">100</td><td>68</td></tr><tr><td>095</td><td>70</td></tr></table>'
                '</div></body></html>')
        routes = {
            'https://theredface.com/product/search.html?keyword=REWFJKWBF560':
                '<div class="xans-search-result"><ul class="prdList"><li id="anchorBoxId_4413"><a href="/product/루미-재킷-rewfjkwbf560/4413/category/1/display/1/">루미 재킷 REWFJKWBF560</a></li>'
                '<li><a href="/product/x-rewfjkwbf5600/9999/">비슷한 품번 REWFJKWBF5600</a></li></ul></div>',
            'https://theredface.com/product/%EB%A3%A8%EB%AF%B8-%EC%9E%AC%ED%82%B7-rewfjkwbf560/4413/category/1/display/1/': page,
            'https://theredface.com/web/product/option_button/bk.jpg': solid((800, 800), (10, 10, 10)),
            'https://theredface.com/web/product/big/main.jpg': solid((1000, 1000), (60, 60, 160)),
            'https://theredface.com/web/product/extra/big/ex1.jpg': solid((900, 1200), (90, 40, 40)),
            'https://theredface.com/web/product_detail/REWFJKWBF560_strip.jpg': redface_strip(),
        }
        routes.update(extra_routes or {})
        calls = []

        def request(ctx, url, limit=0, optional=False, site_only=True, timeout=25):
            url = module.public_url(url)
            calls.append(url)
            if url not in routes:
                if optional:
                    return None
                raise module.CrawlError('HTTP 404: ' + url)
            value = routes[url]
            if isinstance(value, Exception):
                raise module.CrawlError(str(value))  # 이 실행에서 불러온 모듈의 예외로 재현
            return value if isinstance(value, bytes) else value.encode()
        module.request = request
        meta = json.loads((ROOT / 'brands' / 'redface_official.json').read_text(encoding='utf-8'))
        events, error = [], None
        with tempfile.TemporaryDirectory() as folder:
            ctx = CrawlContext('unused', Path(folder) / 'result.xlsx', 0, selected, meta['output_headers'], lambda: None,
                               lambda kind, **data: events.append((kind, data)), input_item={'품번': 'REWFJKWBF560'},
                               mapping_mode=meta['mapping_mode'], image_mode=meta['image_mode'])
            try:
                module.crawl(ctx)
                ctx.finish()
            except Exception as exc:
                error = exc
        rows = [d for k, d in events if k == 'row' and not d.get('header')]
        headers = [d['row'] for k, d in events if k == 'row' and d.get('header')][-1]
        records = [plain_html(dict(dict(zip(headers, d['row'])), _unmapped=d.get('unmapped'))) for d in rows]
        failures = [d['message'] for k, d in events if k == 'item_failure']
        return records, failures, error, calls

    def test_cuts_mapping_and_shared_detail(self):
        rows, failures, error, calls = self.run_redface(ALL)
        self.assertIsNone(error, failures)
        crops = [json.loads(r['크롭 좌표']) for r in rows if r['크롭 좌표']]
        self.assertEqual(len(rows), 6)            # link_image + 대표 + 추가 + 통이미지 3컷
        self.assertEqual(len(crops), 3)           # 문구 띠·실측표는 제외
        for l, t, r, b in crops:
            self.assertGreaterEqual(min(r - l, b - t), 500)
            self.assertLessEqual(max(r - l, b - t) / min(r - l, b - t), 1.8)
            self.assertLess(b, 2000)               # 실측표 영역 미포함
        mapped = [r for r in rows if r['_unmapped'].get('preassigned_color_id')]
        self.assertEqual(len(mapped), 1)
        self.assertIn('link_image', mapped[0]['_unmapped']['mapping_evidence'])
        self.assertTrue(all(len(r['_unmapped']['colors']) == 2 for r in rows))
        r = rows[0]
        self.assertEqual((r['상품명'], r['사이즈'], r['판매가'], r['정상가'], r['카테고리']), ('루미 구스 헤비 다운 우먼 재킷', '090,095', 189000, 389000, '여성 > 다운'))
        detail = r['상세페이지 HTML']
        self.assertIn('REWFJKWBF560_strip.jpg', detail)
        self.assertNotIn('top_banner', detail)
        self.assertIn('<th>제조국</th><td>베트남</td>', detail)
        self.assertIn('rowspan="2"', detail)
        self.assertTrue(all(x['상세페이지 HTML'] == detail for x in rows))
        self.assertFalse(any('9999' in u for u in calls))  # 비슷한 품번 미요청

    def test_ocr_label_maps_only_unique_official_color(self):
        seen = []
        def fake_ocr(ctx, tiles, work):
            seen.append(len(tiles))
            return {0: 'BLACK', 1: '블랙 / 네이비'}   # 두 번째 띠는 두 색 동시 일치 → 미매핑
        rows, failures, error, calls = self.run_redface(ALL, ocr=fake_ocr)
        self.assertIsNone(error, failures)
        self.assertEqual(seen, [2])
        ocr_rows = [r for r in rows if r['OCR 근거']]
        self.assertEqual(len(ocr_rows), 1)
        self.assertIn('BLACK=블랙', ocr_rows[0]['OCR 근거'])
        self.assertTrue(ocr_rows[0]['_unmapped'].get('preassigned_color_id'))
        crops = [r for r in rows if r['크롭 좌표']]
        self.assertEqual(sum(1 for r in crops if r['_unmapped'].get('preassigned_color_id')), 1)

    def test_match_color_rules(self):
        module = load('redface_official')
        colors = [{'color_name': '블랙', 'color_code': ''}, {'color_name': '카키', 'color_code': ''}, {'color_name': '아이보리', 'color_code': ''}]
        self.assertEqual(module.match_color('KHAKI', colors)[0]['color_name'], '카키')
        self.assertEqual(module.match_color('아이보리컬러', colors)[0]['color_name'], '아이보리')
        self.assertIsNone(module.match_color('블랙 카키', colors)[0])
        self.assertIsNone(module.match_color('NEW ARRIVAL', colors)[0])
        self.assertIsNone(module.match_color('BLACKISH', colors)[0])

    def test_slow_image_is_skipped_and_slug_page_used_first(self):
        module = load('redface_official')
        rows, failures, error, calls = self.run_redface(ALL, extra_routes={
            'https://theredface.com/web/product/extra/big/ex1.jpg': module.CrawlError('네트워크 오류 timed out: x')})
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 5)                        # 실패한 추가컷 1장만 빠지고 나머지는 계속
        self.assertFalse(any('detail.html' in u for u in calls))  # 검증된 상품 링크로 바로 열고 fallback 미사용

    def test_word_labels_map_each_column_and_thin_seams_split(self):
        from PIL import ImageDraw
        module = load('redface_official')
        img = Image.new('RGB', (2000, 4200), 'white')
        d = ImageDraw.Draw(img)
        for cx in (350, 1000, 1650):                       # BLACK / KHAKI / IVORY 글자(한 줄, 열마다)
            d.rectangle((cx - 80, 100, cx + 80, 130), fill=(20, 20, 20))
        for cx, c in ((350, (30, 30, 30)), (1000, (110, 110, 110)), (1650, (210, 200, 190))):
            d.rectangle((cx - 290, 220, cx + 290, 1000), fill=c)
        d.rectangle((900, 1200, 1100, 1230), fill=(20, 20, 20))   # DETAIL VIEW
        d.rectangle((300, 1350, 1700, 2350), fill=(150, 140, 135))  # 3px 흰 선으로만 붙은 두 컷
        d.rectangle((300, 2353, 1700, 3353), fill=(140, 130, 125))
        photos, labels = module.segment(img)
        self.assertEqual(len(photos), 5)
        words = [labels[k[0][0]][1][k[0][1]] for _, k in photos[:3]]
        self.assertEqual([round((w[0] + w[2]) / 2) for w in words], [350, 1000, 1650])
        self.assertEqual(photos[3][1][0], photos[4][1][0])    # 디테일 두 컷은 같은 글자(DETAIL VIEW)가 가장 가까운 후보
        self.assertLess(photos[3][0][3], 2353)
        self.assertGreaterEqual(photos[4][0][1], 2350)
        below = Image.new('RGB', (1000, 2400), 'white')    # 글자가 사진 '아래'에 붙은 상품(REEGMKX24101 형태)
        d = ImageDraw.Draw(below)
        d.rectangle((250, 100, 750, 800), fill=(30, 40, 90))         # 네이비 컷
        d.rectangle((420, 840, 580, 870), fill=(20, 20, 20))         # 네이비(NA) — 바로 아래
        d.rectangle((250, 1050, 750, 1750), fill=(150, 150, 150))    # 그레이 컷
        d.rectangle((420, 1790, 580, 1820), fill=(20, 20, 20))       # 그레이(GR) — 바로 아래
        photos, labels = module.segment(below)
        self.assertEqual([near[0][0] for _, near in photos], [0, 1])  # 각 사진은 가장 가까운(아래) 글자가 1순위

    def test_fuzzy_match_against_official_options(self):
        module = load('redface_official')
        colors = [{'color_name': '블랙', 'color_code': ''}, {'color_name': '카키', 'color_code': ''}, {'color_name': '아이보리', 'color_code': ''}]
        self.assertEqual(module.match_color('BLAGK', colors)[0]['color_name'], '블랙')
        self.assertEqual(module.match_color('IV0RY', colors)[0]['color_name'], '아이보리')
        self.assertIsNone(module.match_color('FRONT VIEW', colors)[0])
        self.assertIsNone(module.match_color('BLACK KHAKI', colors)[0])

    def test_optional_fields_skipped(self):
        rows, failures, error, calls = self.run_redface(['main_image'])
        self.assertIsNone(error, failures)
        self.assertTrue(rows)
        self.assertTrue(all(r.get('상세페이지 HTML') in (None, '') and r.get('사이즈') in (None, '') for r in rows))

    def test_no_image_fields_is_explicit_failure(self):
        rows, failures, error, calls = self.run_redface(['price'])
        self.assertIsNotNone(error)
        self.assertFalse(any('detail.html' in u for u in calls))


if __name__ == '__main__':
    unittest.main()
