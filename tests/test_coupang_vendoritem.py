"""쿠팡 viid 크롤러 — 2026-11-27 사용자 Chrome에서 읽은 실제 페이지 값(버커루 B241KT550P)으로 해석 검사.

브라우저 연결(read_page)은 대체하고, EXTRACT_JS가 돌려주는 모양 그대로의 데이터를 넣는다.
"""
import importlib.util
import io
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app')]
from PIL import Image  # noqa: E402

IMG = '//image.coupangcdn.com/image/vendor_inventory/'
NAVY = IMG + '6549/3b448fb252bc59a04750294ba7560a108571f5c21abc66b5fe4b7d636208.jpg'
WHITE = IMG + '7690/17776807def0f444af77542b6d2e84d9ada7891325a968db8cde257f4414.jpg'


def load():
    spec = importlib.util.spec_from_file_location('coupang_test', ROOT / 'brands/coupang_vendoritem.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PAGE_GAP = 0
    return module


def page(viid='92971716055', not_selling=False):
    """사용자 Chrome에서 /vp/products/0?vendorItemId=92971716055 를 열었을 때 읽힌 값."""
    return {
        'url': 'https://www.coupang.com/vp/products/0?vendorItemId=' + viid,
        'atf': {'title': '버커루 BC [24년신상] 봄 가을 간절기 레이어드 하기 좋은 여성 꽈배기 V넥 베스트 B241KT550P',
                'productId': 7921593691, 'brand': '버커루',
                'leafCategoryInfo': {'name': '조끼/베스트', 'parentsCategoryNames': ['상의류', '여성의류', '패션의류', 'KAN', 'HOME']},
                'options': {
                    'optionRows': [
                        {'name': '사이즈', 'attributes': [{'valueId': '1514325469', 'name': 'XS', 'selected': False},
                                                       {'valueId': '1301649153', 'name': 'XS(85)', 'selected': viid == '92971716055'},
                                                       {'valueId': '48849201', 'name': 'S', 'selected': viid == '96402909989'}]},
                        {'name': '색상', 'attributes': [{'valueId': '1997158754', 'name': 'WHITE(00)', 'selected': viid == '96402909989'},
                                                      {'valueId': '3008235596', 'name': '네이비', 'selected': viid == '92971716055'}]}],
                    'attributeVendorItemMap': {
                        '1514325469:1997158754': {'vendorItemId': 91753983125, 'itemId': 21763689962, 'itemName': '버커루 여성 A/C/N 꽈배기 V넥 VEST B241KT550P',
                                                  'soldOut': False, 'invalid': False, 'images': [WHITE], 'price': ['79,000', '29,000', '29,000']},
                        '1301649153:3008235596': {'vendorItemId': 92971716055, 'itemId': 21763689954,
                                                  'itemName': '버커루 BC [24년신상] 봄 가을 간절기 레이어드 하기 좋은 여성 꽈배기 V넥 베스트 B241KT550P',
                                                  'soldOut': False, 'invalid': False, 'images': [NAVY], 'price': ['79,000', '35,880', '33,720']},
                        '48849201:1997158754': {'vendorItemId': 96402909989 if viid == '96402909989' else 91753983132, 'itemId': 21763689950,
                                                'itemName': '버커루 여성 A/C/N 꽈배기 V넥 VEST B241KT550P', 'invalid': viid == '96402909989',
                                                'images': [WHITE], 'price': None if viid == '96402909989' else ['79,000', '29,000', '29,000']}}}},
        'brief': [] if not_selling else [['제품 소재', '라벨참조'], ['색상', '00(WHITE),06(NAVY)'], ['치수', 'XS(85),S(90)'],
                                         ['제조자(수입자)', '라벨참조'], ['제조국', '라벨참조'], ['세탁방법 및 취급시 주의사항', '라벨참조']],
        'detail': [] if not_selling else ['http://image1.coupangcdn.com/image/vendor_inventory/6549/3b448fb252bc59a04750294ba7560a108571f5c21abc66b5fe4b7d636208.jpg',
                                          'http://image1.coupangcdn.com/image/vendor_inventory/2164/edcaf37d7b3b19b332bc3c86eea437bb40b0c9b6c5256bd29304dab5f8f3.jpg'],
        'crumbs': ['쿠팡 홈', '패션의류/잡화', '남녀 공용 의류', '니트류/조끼', '스웨터/니트'],
        'mpn': 'B241KT550P', 'coupangIds': ['7921593691', '21763689954'], 'notSelling': not_selling, 'blocked': False}


def jpg(url):
    tint = zlib.crc32(url.encode()) & 0xFFFFFF
    buf = io.BytesIO()
    Image.new('RGB', (1000, 1000), ((tint >> 16) & 255, (tint >> 8) & 255, tint & 255)).save(buf, 'JPEG')
    return buf.getvalue()


class Ctx:
    def __init__(self, code, selected=('size', 'price', 'regular_price', 'category', 'main_image', 'additional_images', 'detail_page')):
        self.product_code, self.selected, self.rows, self.failures, self.logs = code, set(selected), [], [], []
    def enabled(self, key): return key in self.selected
    def checkpoint(self): pass
    def stage(self, *_): pass
    def log(self, message, *_): self.logs.append(message)
    def emit_result(self, row): self.rows.append(row)
    def fail_item(self, message): self.failures.append(message)


class CoupangTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.m.check_public_image = lambda url, **kw: dict(status='accessible', url=url, final_url=url, data=jpg(url),
                                                           reasons=[], warnings=[], width=1000, height=1000, format='JPEG')
        self.opened = []

    def run_with(self, code, data):
        self.m.read_viid = lambda ctx, viid: (self.opened.append(viid), data)[1]
        ctx = Ctx(code)
        self.m.crawl(ctx)
        return ctx

    def test_input_forms(self):
        self.assertEqual(self.m.parse_input(' 96402909989 '), '96402909989')
        self.assertEqual(self.m.parse_input('https://www.coupang.com/vp/products/7921593691?vendorItemId=96402909989&itemId=1'), '96402909989')
        for bad in ('https://www.coupang.com/vp/products/7921593691', 'abc', 'https://example.com/?vendorItemId=123456'):
            with self.assertRaises(self.m.CrawlError):
                self.m.parse_input(bad)

    def test_selling_viid_full_record(self):
        ctx = self.run_with('https://www.coupang.com/vp/products/7921593691?vendorItemId=92971716055', page())
        self.assertEqual(self.opened, ['92971716055'])
        row = ctx.rows[0]
        self.assertEqual((row['product_code'], row['color'], row['size']), ('92971716055', '네이비', 'XS(85)'))
        self.assertEqual((row['price'], row['regular_price'], row['쿠폰 적용가']), (35880, 79000, 33720))
        self.assertEqual((row['쿠팡 상품번호'], row['itemId'], row['모델번호'], row['판매 상태']), ('7921593691', '21763689954', 'B241KT550P', '판매 중'))
        self.assertEqual(row['category'], '패션의류/잡화 > 남녀 공용 의류 > 니트류/조끼 > 스웨터/니트')
        self.assertEqual(row['main_image'], 'https:' + NAVY)
        self.assertIn('https://image1.coupangcdn.com/image/vendor_inventory/2164/', row['detail_page'])     # http → https
        self.assertIn('필수 표기 정보', row['detail_page'])
        self.assertIn('00(WHITE),06(NAVY)', row['detail_page'])
        self.assertEqual(row['product_url'], 'https://www.coupang.com/vp/products/7921593691?vendorItemId=92971716055')
        self.assertFalse(ctx.failures)

    def test_not_selling_viid_kept_with_status(self):
        ctx = self.run_with('96402909989', page('96402909989', not_selling=True))
        row = ctx.rows[0]
        self.assertEqual((row['color'], row['size'], row['판매 상태'], row['price']), ('WHITE(00)', 'S', '판매 종료', None))
        self.assertEqual(row['detail_page'], '')
        self.assertTrue(any('판매 종료' in m for m in ctx.logs))

    def test_viid_missing_from_map_uses_selected_options(self):
        data = page()
        data['atf']['options']['attributeVendorItemMap']['1301649153:3008235596']['vendorItemId'] = 1
        ctx = self.run_with('92971716055', data)
        self.assertEqual((ctx.rows[0]['color'], ctx.rows[0]['size']), ('네이비', 'XS(85)'))
        self.assertTrue(any('화면에서 선택된 옵션' in m for m in ctx.logs))

    def test_blocked_or_empty_page_fails_clearly(self):
        data = dict(page(), blocked=True)
        with self.assertRaisesRegex(self.m.CrawlError, '접근 제한'):
            self.run_with('92971716055', data)
        with self.assertRaisesRegex(self.m.CrawlError, '상품 데이터 없음'):
            self.run_with('92971716055', dict(page(), atf=None))

    def test_image_url_normalized_to_original(self):
        self.assertEqual(self.m.image_url('//thumbnail.coupangcdn.com/thumbnails/remote/640x640ex/image/vendor_inventory/a/b.jpg'),
                         'https://image.coupangcdn.com/image/vendor_inventory/a/b.jpg')
        self.assertEqual(self.m.image_url('https://evil.example/image/a.jpg'), '')

    def test_extract_script_is_present(self):
        for token in ('__next_f', 'atfData', '#itemBrief', '.product-detail-content', 'attributeVendorItemMap'):
            self.assertIn(token, self.m.EXTRACT_JS)


FAKE_PLAYWRIGHT = """
import json, os
from pathlib import Path
COUNT = Path(os.environ['FAKE_CDP_COUNT'])

class Response:
    status = 200

class Page:
    def goto(self, url, **kw):
        self.url = url
        return Response()
    def wait_for_function(self, *a, **kw):
        pass
    def evaluate(self, script):
        return {'url': self.url, 'script': 'attributeVendorItemMap' in script}
    def close(self):
        pass

class Context:
    def set_default_timeout(self, v): pass
    def set_default_navigation_timeout(self, v): pass
    def new_page(self): return Page()

class Browser:
    contexts = [Context()]
    def is_connected(self): return True

class Chromium:
    def connect_over_cdp(self, endpoint, timeout=0, no_defaults=False):
        COUNT.write_text(str(int(COUNT.read_text() or 0) + 1) if COUNT.exists() else '1')
        return Browser()

class Runtime:
    chromium = Chromium()
    def start(self): return self
    def stop(self): pass

def sync_playwright():
    return Runtime()
"""


class KeeperTest(unittest.TestCase):
    """viid마다 Chrome에 새로 붙지 않고(허용 창 반복 방지) 도우미 연결 하나로 여러 행을 읽는지."""

    def test_one_chrome_connection_for_many_viids(self):
        import os
        import tempfile
        m = load()
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'fake' / 'playwright').mkdir(parents=True)
            (folder / 'fake' / 'playwright' / '__init__.py').write_text('', 'utf-8')
            (folder / 'fake' / 'playwright' / 'sync_api.py').write_text(FAKE_PLAYWRIGHT, 'utf-8')
            (folder / 'keeper').mkdir()
            old = {k: os.environ.get(k) for k in ('PYTHONPATH', 'FAKE_CDP_COUNT')}
            os.environ['PYTHONPATH'] = str(folder / 'fake')
            os.environ['FAKE_CDP_COUNT'] = str(folder / 'count.txt')
            m.chrome_endpoint = lambda profile: 'ws://127.0.0.1:9222/devtools/browser/abc'
            m.keeper_dir = lambda: folder / 'keeper'
            state = folder / 'keeper' / 'state.json'
            try:
                ctx = Ctx('1')
                first = m.read_viid(ctx, '92971716055')
                second = m.read_viid(Ctx('2'), '91753983125')
                self.assertEqual(first['url'], 'https://www.coupang.com/vp/products/0?vendorItemId=92971716055')
                self.assertTrue(first['script'])
                self.assertEqual(second['url'][-11:], '91753983125')
                self.assertEqual((folder / 'count.txt').read_text(), '1')
                self.assertTrue(any('한 번' in message for message in ctx.logs))
            finally:
                if state.exists():
                    m.keeper_call(__import__('json').loads(state.read_text('utf-8')), '/quit')
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == '__main__':
    unittest.main()
