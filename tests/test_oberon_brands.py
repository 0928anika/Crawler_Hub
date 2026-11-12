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

NEW = ['adhoc_official', 'bangbang_official', 'decoand_official', 'dawnfield_official', 'disneygolf_smfashion',
       'renomakids_official', 'minimum_official', 'adidas_official', 'brentwood_kolonmall', 'ping_bucketstore', 'ecolier_soiemall']


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
    records = [dict(dict(zip(headers, d['row'])), _unmapped=d.get('unmapped')) for d in rows]
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
                self.assertNotIn('emit_local_image(', code)
                if meta['mapping_mode'] == 'manual':
                    self.assertTrue(meta.get('hybrid_mapping'))
                    self.assertNotIn('mapping_verified=False', code)
                self.assertNotIn('import requests', code)


class Cafe24Hybrid(unittest.TestCase):
    def test_every_cafe24_brand_splits_linked_and_unlinked_photos(self):
        for key in ['adhoc_official', 'bangbang_official', 'decoand_official', 'dawnfield_official', 'disneygolf_smfashion', 'renomakids_official', 'minimum_official']:
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
        module = load('dawnfield_official')
        site = module.CONFIG['site']
        rows, failures, error, net = run(module, 'dawnfield_official', 'ZZ9999', ALL,
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
        module = load('brentwood_kolonmall')
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
        rows, failures, error, net = run(module, 'brentwood_kolonmall', 'BRUAW26451', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['색상명'], rows[0]['색상코드'], rows[0]['품번']), ('BLACK', 'BK', 'BRUAW26451'))
        self.assertEqual(rows[0]['정상가'], 450000)
        detail = rows[0]['상세페이지 HTML']
        self.assertLess(detail.index('diagram.jpg'), detail.index('<table', detail.index('사이즈 정보')))

    def test_bucketstore_color_code_and_notice(self):
        module = load('ping_bucketstore')
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
        rows, failures, error, net = run(module, 'ping_bucketstore', '111E3TI001', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['색상코드'], row['사이즈'], row['판매가'], row['정상가'], row['카테고리']), ('BK', '095,100', 79000, 99000, 'MEN > 티셔츠'))
        self.assertIn('<th>소재</th><td>폴리 90% 스판 10%</td>', row['상세페이지 HTML'])
        self.assertEqual(row['상품명'], '남성 긴팔 이너')

    def test_ecolier_brand_scope_and_manual_candidates(self):
        module = load('ecolier_soiemall')
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
        rows, failures, error, net = run(module, 'ecolier_soiemall', '25D0901', ALL, routes)
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['_unmapped'] for r in rows))
        self.assertFalse(any('/list/' in u for u in net.images))
        self.assertEqual(rows[0]['사이즈'], '55,66,77')
        self.assertIsNone(rows[0]['정상가'])
        self.assertEqual(rows[0]['상품명'], '플레어 원피스')
        self.assertIn('<th>제조국</th><td>대한민국</td>', rows[0]['상세페이지 HTML'])
        self.assertNotIn('top1', rows[0]['상세페이지 HTML'])


if __name__ == '__main__':
    unittest.main()
