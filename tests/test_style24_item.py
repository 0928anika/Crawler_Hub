"""STYLE24 전용(브랜드 공통) 크롤러 — 저장된 버커루 실페이지 + NBA키즈 형태 합성 검사."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'brands')]
import style24_item as c  # noqa: E402
from test_leviskids import C  # noqa: E402

FIX = ROOT / 'tests/fixtures/buckaroo'
BASE = 'B254Z1020P'


def source():
    return c.BeautifulSoup((FIX / 'B254Z1020P_DH.html').read_bytes(), 'html.parser')


def popup():
    return c.BeautifulSoup((FIX / 'size.html').read_bytes(), 'html.parser')


class Style24ItemTests(unittest.TestCase):
    def test_input_forms(self):
        self.assertEqual(c.parse_input('b254z1020p'), (BASE, None))
        self.assertEqual(c.parse_input('B254Z1020P_DH'), (BASE, 'DH'))
        self.assertEqual(c.parse_input('https://www.style24.com/goods/detail/form?goodsCd=K263TS150P&colorCd=BL&ithrCd=IN15_01'),
                         ('K263TS150P', 'BL'))
        self.assertEqual(c.parse_input('https://m.style24.com/goods/detail/form?goodsCd=K263TS150P'), ('K263TS150P', None))
        for bad in ('B254Z1020P_', 'https://www.style24.com/display/brand/main/form?brandGroupNo=2143',
                    'https://example.com/goods/detail/form?goodsCd=B254Z1020P'):
            with self.assertRaises(c.CrawlError):
                c.parse_input(bad)

    def test_identity_any_brand_with_brand_column(self):
        doc = source()
        row = c.identity(doc, BASE)
        self.assertEqual((row['product_name'], row['color_code'], row['브랜드']), ('기모 쭈리 후드 티셔츠', 'DH', 'BUCKAROO'))
        self.assertIn(BASE, row['원본 상품명'])
        other = c.BeautifulSoup(str(doc).replace('BUCKAROO', 'NBA KIDS'), 'html.parser')
        self.assertEqual(c.identity(other, BASE)['브랜드'], 'NBA KIDS')                   # 브랜드관 제한 없음
        with self.assertRaisesRegex(c.CrawlError, '품번 확인 실패'):
            c.identity(doc, 'OTHER12345')
        self.assertEqual(set(c.color_links(doc, BASE)), {'DH', '1M', 'BK', 'WI'})

    def test_clean_name_strips_brand_prefixes(self):
        self.assertEqual(c.clean_name('NBA키즈 피그먼트 긴팔티셔츠 (K263TS150P)', 'K263TS150P', 'NBA KIDS'), '피그먼트 긴팔티셔츠')
        self.assertEqual(c.clean_name('엔비에이키즈 오로라 책가방', 'K271AB600P', 'NBA KIDS'), '오로라 책가방')
        self.assertEqual(c.clean_name('BUCKAROO 데님 팬츠(B254Z1020P)', BASE, 'BUCKAROO'), '데님 팬츠')
        self.assertEqual(c.clean_name('버커루 데님 팬츠', BASE, 'BUCKAROO'), '데님 팬츠')

    def test_english_color_only_when_chip_matches(self):
        doc = source()
        self.assertEqual(c.english_color(doc, BASE, 'DH'), 'DARK KHAKI')
        self.assertEqual(c.english_color(doc, BASE, 'ZZ'), '')

    def test_sizes_prices_and_images(self):
        doc = source()
        self.assertTrue(c.sizes(doc, 'DH'))
        self.assertEqual(c.prices(C(), doc, 'DH'), (22900, 69000))
        urls = c.product_images(doc, BASE, 'DH')
        self.assertEqual(len(urls), 5)
        self.assertTrue(all('_DH_' in u for u in urls))

    def test_size_table_picks_category_boxes(self):
        with patch.object(c, 'fetch', return_value=(popup(), c.SITE)) as request:
            markup = c.size_table(C(), source(), BASE, 'DH', {})              # 카테고리: 여성 > 티셔츠 → 여성 상의만
        tables = c.BeautifulSoup(markup, 'html.parser').select('table')
        self.assertEqual(len(tables), 1)
        self.assertIn('여성 상의', markup)
        self.assertNotIn('남성 하의', markup)
        self.assertIn('표준 사이즈', markup)
        self.assertEqual(request.call_args.kwargs['method'], 'POST')
        boxes = popup().select('.tab_cont_wrap > .tab_cont')[0].select('.size_tbl_box')
        self.assertEqual(len(c.pick_boxes(boxes, '유아동 > 가방')), 4)                  # 못 고르면 전부
        self.assertEqual(len(c.pick_boxes(boxes, '남성 > 데님 팬츠')), 1)

    def test_nba_style_headings_match_clothing(self):
        html = ''.join(f'<div class="size_tbl_box"><h6>{h}</h6><table><tr><td>1</td></tr></table></div>' for h in ('의류/언더', '신발', '모자'))
        boxes = c.BeautifulSoup(html, 'html.parser').select('.size_tbl_box')
        picked = c.pick_boxes(boxes, 'NBA KIDS > 유아동 > 키즈 남아 > 티셔츠/셔츠')
        self.assertEqual([b.h6.get_text() for b in picked], ['의류/언더'])
        self.assertEqual([b.h6.get_text() for b in c.pick_boxes(boxes, '유아동 > 신발')], ['신발'])

    def test_detail_has_body_size_and_notice(self):
        with patch.object(c, 'fetch', return_value=(popup(), c.SITE)), patch.object(c, 'checked', return_value=True):
            markup = c.detail(C(), source(), BASE, 'DH', [], {})
        self.assertIn('_DH_', markup)
        self.assertIn('사이즈정보', markup)
        self.assertIn('상품고시정보', markup)

    def test_crawl_metadata_only_and_requested_color(self):
        ctx = C(['size', 'price', 'regular_price', 'category'])
        ctx.product_code = 'B254Z1020P_DH'
        with patch.object(c, 'fetch', return_value=(source(), c.SITE + '/goods/detail/form?goodsCd=B254Z1020P')), \
                patch.object(c, 'checked', side_effect=AssertionError('images disabled')):
            c.crawl(ctx)
        self.assertEqual(len(ctx.rows), 1)
        row = ctx.rows[0]
        self.assertEqual((row['color_code'], row['브랜드'], row['영문 색상'], row['price']), ('DH', 'BUCKAROO', 'DARK KHAKI', 22900))
        self.assertTrue(row['category'])
        self.assertFalse(ctx.failures)

    def test_missing_color_fails_clearly(self):
        ctx = C([])
        ctx.product_code = 'B254Z1020P_ZZ'
        with patch.object(c, 'fetch', return_value=(source(), c.SITE)), self.assertRaisesRegex(c.CrawlError, '지정 색상 없음'):
            c.crawl(ctx)
        self.assertTrue(ctx.failures)


if __name__ == '__main__':
    unittest.main()
