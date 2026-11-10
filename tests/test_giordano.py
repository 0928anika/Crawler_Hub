import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'brands')]
import giordano_official as c


class Context:
    def __init__(self, fields=(), code='05586901'):
        self.fields, self.product_code = fields, code
        self.rows, self.failures, self.logs = [], [], []
    def checkpoint(self): return None
    def enabled(self, key): return key in self.fields
    def stage(self, message): return None
    def log(self, message, level='INFO'): self.logs.append((level, message))
    def emit_result(self, row): self.rows.append(row)
    def fail_item(self, message): self.failures.append(message)


class GiordanoTests(unittest.TestCase):
    def setUp(self):
        self.doc = c.parse((ROOT / 'tests/fixtures/giordano/05586901.html').read_text(encoding='utf-8'))

    def test_style_and_colorchip_full_sku_are_independent_of_brand_title(self):
        self.assertEqual(c.identity(self.doc, '05586901')['product_name'], '부클 머플러')
        self.assertIsNone(c.identity(self.doc, '05356951'))
        self.doc.select_one('input[name=product_name]')['value'] = '지오다노 NEW 부클 머플러 05586901'
        self.assertEqual(c.identity(self.doc, '05586901')['product_name'], '부클 머플러')
        self.assertEqual([v['color_code'] for v in c.colors(self.doc, '05586901')], ['01','02','03','04','05','06'])
        self.doc.select_one('.color_option img')['src'] = '/_data/product/colorchip_auto/9999999901.jpg'
        with self.assertRaises(ValueError): c.colors(self.doc, '05586901')

    def test_explicit_selling_and_independent_consumer_price_exclude_coupon(self):
        ctx = Context()
        self.assertEqual(c.price(ctx, self.doc), 29800)
        self.assertEqual(c.price(ctx, self.doc, regular=True), 29800)
        area = self.doc.select_one('#detail .wrap_prd div.info')
        for value in ('', '0', '가격문의', 'USD 25', ',,,', '29,80'):
            area['data-normal_prc'] = value
            self.assertIsNone(c.price(ctx, self.doc, regular=True))
            self.assertEqual(c.price(ctx, self.doc), 29800)
        foreign = c.parse(str(self.doc).replace('var currency = "원"', 'var currency = "USD"'))
        self.assertIsNone(c.price(ctx, foreign))

    def test_optional_requests_and_price_gates(self):
        for fields in ((), ('price',), ('regular_price',), ('price', 'regular_price')):
            with self.subTest(fields=fields):
                ctx = Context(fields)
                with patch.object(c, 'sizes') as sizes, patch.object(c, 'gallery_urls') as photos, patch.object(c, 'detail_urls') as details, patch.object(c, 'ajax') as ajax:
                    c.collect(ctx, self.doc, c.SITE + '/shop/detail.php?pno=verified', '05586901', None, set(), {})
                    for mock in (sizes, photos, details, ajax): mock.assert_not_called()
                self.assertEqual(len(ctx.rows), 6)
                for row in ctx.rows:
                    self.assertTrue(row['product_url'].startswith('https://'))
                    for key in ('price', 'regular_price'):
                        self.assertEqual(key in row, key in fields)

    def test_missing_regular_price_does_not_fail_colors(self):
        self.doc.select_one('#detail .wrap_prd div.info')['data-normal_prc'] = ''
        ctx = Context(('regular_price',))
        self.assertEqual(c.collect(ctx, self.doc, c.SITE + '/shop/detail.php?pno=verified', '05586901', None, set(), {}), [])
        self.assertEqual(len(ctx.rows), 6)
        self.assertTrue(all(r['regular_price'] is None for r in ctx.rows))
        self.assertFalse(ctx.failures)

    def test_sizes_exclude_nonexistent_sku_but_keep_soldout_and_leading_zero(self):
        doc = c.parse('<form name="prdFrm"><a class="optChipSet2 soldout" data="1">065 (품절)</a><a class="optChipSet2" data="2">070</a><a class="optChipSet2" data="3">075</a></form>')
        with patch.object(c, 'ajax', return_value='@2') as ajax:
            self.assertEqual(c.sizes(Context(), doc, '46602'), '065,075')
            ajax.assert_called_once_with(unittest.mock.ANY, 'getOptionStockSKU', item_no='@46602')

    def test_details_keep_original_notice_and_size_without_care_or_tables(self):
        # Real source fixture plus nested wrappers; no destructive tree operations.
        full = c.parse((ROOT / 'tests/fixtures/giordano/05586901.html').read_text(encoding='utf-8'))
        urls = c.detail_urls(full, c.SITE)
        self.assertFalse(any('ch.jpg' in u for u in urls))
        synthetic = c.parse('<div id="detail"><div class="detail_info"><div class="wing-detail-more-contents"><div><span><img src="/_gio_on/2026/05586901.jpg"></span></div></div></div><div id="det_size_info"><div class="size_img"><img src="/_gio_on/2026/05586901size.jpg"></div></div></div><div class="prd_detail_content_gio"><img data-layer_title="상품정보고시" src="/_gio_on/2026/05586901info.jpg"><img data-layer_title="소재정보" src="/_gio_on/2026/05586901ch.jpg"></div>')
        urls = c.detail_urls(synthetic, c.SITE)
        self.assertEqual(len(urls), 3)
        with patch.object(c, 'public_image', return_value={'status':'accessible'}):
            output = c.detail(Context(), urls, {})
        self.assertEqual(output.count('<img'), 3)
        self.assertNotIn('<table', output)
        self.assertEqual(c.detail(Context(), [], {}), '')

    def test_color_request_is_official_option_id_not_array_position(self):
        response = {'html':'<div class="slides"><img src="/_data/attach/a.jpg"><img src="/_data/attach/review.jpg"><img src="https://outside.example/a.jpg"></div>'}
        with patch.object(c, 'ajax', return_value=json.dumps(response)) as ajax:
            urls = c.gallery_urls(Context(), self.doc, '46603')
        self.assertEqual(urls, [c.SITE + '/_data/attach/a.jpg'])
        self.assertEqual(ajax.call_args.kwargs['ino'], '46603')
        self.assertEqual(ajax.call_args.kwargs['hash'], 'DF4EAD7F6553991E50D44444718EBEB7')

    def test_partial_color_failure_keeps_other_results(self):
        ctx = Context(('size',))
        with patch.object(c, 'sizes', side_effect=[ValueError('network'), 'FREE', 'FREE', 'FREE', 'FREE', 'FREE']):
            failures = c.collect(ctx, self.doc, c.SITE + '/shop/detail.php?pno=verified', '05586901', None, set(), {})
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(ctx.rows), 5)
        self.assertEqual(ctx.rows[0]['color_code'], '02')

    def test_both_delimiters_and_no_optional_fields(self):
        for value in ('05586901_02', '05586901-02'):
            ctx = Context(code=value)
            with patch.object(c, 'search', return_value=[c.SITE + '/shop/detail.php?pno=test']), patch.object(c, 'fetch', return_value=(str(self.doc), c.SITE + '/shop/detail.php?pno=test')):
                c.crawl(ctx)
            self.assertEqual([r['color_code'] for r in ctx.rows], ['02'])

    def test_search_ignores_sort_links_and_follows_official_more(self):
        first = '<a href="/shop/search_result.php?search_str=05586901&amp;sort=1">sort</a><a href="/shop/detail.php?pno=a">a</a><div class="more_count"><span class="total">2</span></div>'
        more = json.dumps({'content':'<a href="/shop/detail.php?pno=b">b</a>'})
        with patch.object(c, 'fetch', side_effect=[(first,c.SITE + '/shop/search_result.php?search_str=05586901'),(more,c.SITE + '/main/exec.php')]) as fetch:
            self.assertEqual(len(c.search(Context(), '05586901')), 2)
            self.assertEqual(fetch.call_count, 2)
            self.assertIn('module_page=2', fetch.call_args.args[1])

    def test_named_campaign_uses_original_bracketed_title(self):
        for name in ('NEW [박현지] 울캐시 코트', '[변우석] 코튼 자켓', 'NEW [문가영] 니트'):
            self.assertTrue(c.named_campaign(name))
        for name in ('부클 머플러', '[주니어] 니트', '[여성] 코트', '[온라인] 자켓'):
            self.assertFalse(c.named_campaign(name))

    def test_clean_title_keeps_original_for_background_filter(self):
        for raw, expected in [('NEW [박현지] 울캐시 발마칸 코트', '울캐시 발마칸 코트'), ('[변우석] 코튼 트윌 풀집 자켓', '코튼 트윌 풀집 자켓'), ('new [문가영] 니트', '니트'), ('RENEW 니트', 'RENEW 니트')]:
            with self.subTest(raw=raw):
                self.doc.select_one('input[name=product_name]')['value'] = raw
                row = c.identity(self.doc, '05586901')
                self.assertEqual(row['product_name'], expected)
                self.assertEqual(row['원본 상품명'], raw)
        self.doc.select_one('input[name=product_name]')['value'] = 'NEW [박현지] 울캐시 발마칸 코트'
        ctx = Context(('main_image',))
        with patch.object(c, 'gallery_urls', return_value=[]), patch.object(c, 'gallery', return_value=('', [], '')) as gallery:
            c.collect(ctx, self.doc, c.SITE + '/shop/detail.php?pno=verified', '05586901', '01', set(), {})
            self.assertTrue(gallery.call_args.kwargs['filter_background'])
        self.assertEqual(ctx.rows[0]['product_name'], '울캐시 발마칸 코트')

    def test_real_scene_backgrounds_are_rejected_and_plain_shots_remain(self):
        folder = ROOT / 'tests/fixtures/giordano/background'
        for name in ('a_0','a_1','a_2','a_3','b_0','b_8'):
            with self.subTest(name=name):
                self.assertFalse(c.uniform_background((folder / (name + '.png')).read_bytes())[0])
        for name in ('a_4','a_5','b_1','b_6'):
            with self.subTest(name=name):
                self.assertTrue(c.uniform_background((folder / (name + '.png')).read_bytes())[0])

    def test_background_exclusions_cannot_reenter_main_or_extra_padding(self):
        folder = ROOT / 'tests/fixtures/giordano/background'
        scene, plain = (folder / 'b_8.png').read_bytes(), (folder / 'b_1.png').read_bytes()
        ctx = Context()
        with patch.object(c, 'public_image', side_effect=[{'data': scene}, {'data': plain}]), patch.object(c, 'validate_gallery_image', return_value={'passed': True}):
            main, extras, warning = c.gallery(ctx, ['https://example.com/scene.jpg', 'https://example.com/plain.jpg'], '01', {}, filter_background=True)
        self.assertEqual(main, 'https://example.com/plain.jpg')
        self.assertEqual(extras, [main] * 5)
        self.assertTrue(warning)
        with patch.object(c, 'public_image', return_value={'data': scene}), patch.object(c, 'validate_gallery_image') as validate:
            main, extras, warning = c.gallery(ctx, ['https://example.com/scene.jpg'], '01', {}, filter_background=True)
            validate.assert_not_called()
        self.assertFalse(main)
        self.assertTrue(warning)

    def test_unnamed_product_skips_background_analysis(self):
        with patch.object(c, 'public_image', return_value={'data': b'image'}), patch.object(c, 'uniform_background') as background, patch.object(c, 'validate_gallery_image', return_value={'passed': True}):
            c.gallery(Context(), ['https://example.com/a.jpg'], '01', {})
            background.assert_not_called()


if __name__ == '__main__':
    unittest.main()
