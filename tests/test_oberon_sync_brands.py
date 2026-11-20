"""오베론에서 옮긴 5개 브랜드(마운티아·젠사이·리버클래시·에스핏·하프클럽 품번) develop용 오프라인 검사.

오베론 tests/test_oberon_brands.py의 같은 fixture·검사를 그대로 옮김.

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

NEW = ['mountia_official', 'zensai_official', 'riverclassy_favlook', 'sfit_official', 'halfclub_item']


def load(key):
    spec = importlib.util.spec_from_file_location(key + '_sync_test', ROOT / 'brands' / (key + '.py'))
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
                if meta['mapping_mode'] == 'manual' and meta.get('image_mode') != 'local' and key != 'halfclub_item':
                    self.assertTrue(meta.get('hybrid_mapping'))
                if key == 'halfclub_item':                  # 순수 수동 매핑: 자동 결과·색상 추측 없음
                    self.assertNotIn('emit_result(', code)
                    self.assertNotIn('mapped_color', code)
                    self.assertNotIn('mapping_verified=False', code)
                self.assertNotIn('import requests', code)



class MovedFromOberon(unittest.TestCase):
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




if __name__ == '__main__':
    unittest.main()
