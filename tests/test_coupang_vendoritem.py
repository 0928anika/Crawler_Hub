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
        from contextlib import contextmanager

        @contextmanager
        def browser(ctx):
            yield 'context'
        self.m.existing_browser = browser
        self.m.read_page = lambda ctx, context, viid: (self.opened.append(viid), data)[1]
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


if __name__ == '__main__':
    unittest.main()
