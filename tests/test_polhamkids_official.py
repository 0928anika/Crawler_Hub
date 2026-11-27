"""폴햄키즈(굿웨어몰 KBR) 오프라인 검사 — 2026-11-27 실사이트 PKF4DP2016 응답 구조를 본뜸."""
import importlib.util
import io
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app')]
from PIL import Image  # noqa: E402

BASE = 'PKF4DP2016'
COLORS = [('DIN', '다크 인디고'), ('MIN', '미디움 인디고'), ('DGR', '다크 그레이')]


def load():
    spec = importlib.util.spec_from_file_location('polhamkids_test', ROOT / 'brands/polhamkids_official.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png(url):
    tint = zlib.crc32(url.encode()) & 0xFFFFFF
    buf = io.BytesIO()
    Image.new('RGB', (1000, 1200), ((tint >> 16) & 255, (tint >> 8) & 255, tint & 255)).save(buf, 'JPEG')
    return buf.getvalue()


def goods(code, name, brand='KBR'):
    sku = BASE + code
    return dict(result='Y', goods=dict(
        god=dict(godNo=sku, dsgnGrpNo=BASE, brndGrpId=brand, colorCd=code, colorNm=name, cvrPrc=69900,
                 godNm='여아) 기모 와이드핏 데님 팬츠', godSaleSectCd='SALE_PROGRS', godDetailCont=''),
        colorList=[dict(god=dict(godNo=BASE + c, dsgnGrpNo=BASE)) for c, _ in COLORS],
        dspGodPrc=dict(salePrc=25900), godCaUseYn='Y',
        godImgList=[dict(imgTurn=100, imgUseYn='Y', imgUrl=f'/goods/{sku}_M.jpg')]
                   + [dict(imgTurn=200 + n, imgUseYn='Y', imgUrl=f'/goods/{sku}_{n}.jpg') for n in range(1, 5)],
        cnsgnSingleGoditmList=[dict(optValNm1=s) for s in ('120', '130', '140')],
        gdSizeLktb='<table class="size-table"><thead><tr><th>사이즈</th><th>120(120)</th></tr></thead>'
                   '<tbody><tr><td>허리</td><td onclick="x()">27</td></tr></tbody></table>',
        acmsrImgUrl='/goods/realsize/pants.jpg', godDetailList=[]))


class PolhamKidsTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.rows, self.failures, self.searches = [], [], []
        brand = {'value': 'KBR'}
        self.brand = brand

        def api(ctx, path, params):
            if 'getTotalSearch' in path:
                self.searches.append(params['searchWord'])
                rows = [dict(GOD_NO=BASE + c, BRND_ID='KBR') for c, _ in COLORS] + [dict(GOD_NO='PHX0000000BK', BRND_ID='PBR')]
                return dict(godList=rows if params['searchWord'] == BASE else [], godPaging=dict(totalPage=1),
                            searchDTO=dict(searchWord=params['searchWord']))
            code = params['godNo'][len(BASE):]
            return goods(code, dict(COLORS).get(code, code), brand['value'])

        def fetch(ctx, path, params=None):
            sku = path.split('/')[2]
            source = (f'<meta property="og:url" content="https://www.goodwearmall.com/product/{sku}/detail">'
                      "<script>var key = 'stdCtgryNm_1'; var value = '신성통상';"
                      "var key = 'stdCtgryNm_2'; var value = '유아동의류'; var key = 'stdCtgryNm_3'; var value = '여아의류';</script>")
            return source, 'https://www.goodwearmall.com' + path

        self.m.api, self.m.fetch = api, fetch
        self.m.check_public_image = lambda url, **kw: dict(status='accessible', url=url, final_url=url, data=png(url),
                                                           reasons=[], warnings=[], width=1000, height=1200, format='JPEG')

    def ctx(self, code, selected=('size', 'price', 'regular_price', 'category', 'main_image', 'additional_images', 'detail_page')):
        test = self

        class C:
            product_code = code
            def enabled(self, key): return key in selected
            def checkpoint(self): pass
            def stage(self, *_): pass
            def log(self, *_): pass
            def emit_result(self, row): test.rows.append(row)
            def fail_item(self, message): test.failures.append(message)
        return C()

    def test_all_colors_of_polham_kids_group(self):
        self.m.crawl(self.ctx('pkf4dp2016'))
        self.assertEqual([r['color_code'] for r in self.rows], ['DIN', 'MIN', 'DGR'])
        row = self.rows[0]
        self.assertEqual((row['product_code'], row['product_name'], row['color_name']), (BASE, '여아) 기모 와이드핏 데님 팬츠', '다크 인디고'))
        self.assertEqual((row['price'], row['regular_price'], row['size']), (25900, 69900, '120,130,140'))
        self.assertEqual(row['category'], '유아동의류 > 여아의류')
        self.assertTrue(row['main_image'].endswith('PKF4DP2016DIN_M.jpg'))
        self.assertIn('실측사이즈', row['detail_page'])
        self.assertIn('/goods/realsize/pants.jpg', row['detail_page'])
        self.assertNotIn('onclick', row['detail_page'])
        self.assertFalse(self.failures)

    def test_requested_color_only(self):
        self.m.crawl(self.ctx('PKF4DP2016_MIN'))
        self.assertEqual([r['color_code'] for r in self.rows], ['MIN'])

    def test_polham_adult_group_is_not_polham_kids(self):
        self.brand['value'] = 'PBR'
        with self.assertRaises(ValueError):
            self.m.crawl(self.ctx(BASE))
        self.assertFalse(self.rows)
        self.assertTrue(any('폴햄키즈' in f for f in self.failures))

    def test_not_found(self):
        with self.assertRaises(ValueError):
            self.m.crawl(self.ctx('PKX0000000'))
        self.assertTrue(any('검색 결과 없음' in f for f in self.failures))


if __name__ == '__main__':
    unittest.main()
