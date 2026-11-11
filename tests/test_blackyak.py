import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import blackyak_official as c
FIX=ROOT/'tests/fixtures/blackyak'
URL=c.SITE+'/blackyak/shop/detail.php?pno=sample'


class Context:
    def __init__(self,fields=(),code='1BYGJFV002'):
        self.fields,self.product_code=fields,code
        self.rows,self.errors,self.logs=[],[],[]
    def checkpoint(self):return None
    def enabled(self,key):return key in self.fields
    def stage(self,message):return None
    def log(self,*args):self.logs.append(args)
    def fail_item(self,message):self.errors.append(message)
    def emit_result(self,record):self.rows.append(record)


class BlackyakTests(unittest.TestCase):
    def setUp(self):
        self.sources={color:(FIX/(color+'.html')).read_text(encoding='utf-8') for color in ('FB','DY')}
        self.doc=c.parse(self.sources['FB'])

    def test_full_official_code_and_color_are_independent_of_brand_in_title(self):
        row=c.identity(self.doc,'1BYGJFV002')
        self.assertEqual(row['color_code'],'FB')
        self.assertEqual(row['color_name'],'FRENCH BLUE')
        self.assertEqual(row['공식 전체 품번'],'1BYGJFV002FB')
        self.assertIsNone(c.identity(self.doc,'1BYJKFV006'))
        self.doc.select_one('.prd-info__name').string='블랙야크 남성 자켓 1BYGJFV002FB'
        self.assertEqual(c.identity(self.doc,'1BYGJFV002')['product_name'],'남성 자켓')

    def test_size_buttons_keep_leading_zeros_and_sold_out(self):
        n=self.doc.select_one('.prd-info__size-chip');n['data-qty']='0';n['disabled']='disabled'
        self.assertEqual(c.sizes(self.doc),'095,100,105,110')

    def test_explicit_prices_are_separate_and_invalid_regular_price_is_optional(self):
        ctx=Context()
        self.assertEqual(c.price(ctx,self.doc),679200)
        self.assertEqual(c.price(ctx,self.doc,True),799000)
        for value in ('0','','가격문의',',,,','799,00'):
            self.doc.select_one('.prd-info__price-original .prd-info__price-num').string=value
            self.assertIsNone(c.price(ctx,self.doc,True))
            self.assertEqual(c.price(ctx,self.doc),679200)
        self.doc.select_one('.prd-info__price-original').decompose()
        self.assertIsNone(c.price(ctx,self.doc,True));self.assertFalse(ctx.errors)

    def test_gallery_excludes_other_areas_and_uses_hidden_detail_cuts_too(self):
        urls=c.gallery_urls(self.doc,URL)
        self.assertEqual(len(urls),14)
        self.assertTrue(all('/1BYGJFV002_FB/' in u for u in urls))
        self.assertEqual(len(set(urls)),14)

    def test_second_product_uses_its_own_actual_gallery_without_image_allowlist(self):
        doc=c.parse((FIX/'OTHER_FB.html').read_text(encoding='utf-8'))
        self.assertEqual(c.identity(doc,'1BYJKFV006')['공식 전체 품번'],'1BYJKFV006FB')
        urls=c.gallery_urls(doc,URL)
        self.assertEqual(len(urls),16)
        self.assertTrue(all('/1BYJKFV006_FB/' in u for u in urls))

    def test_detail_combines_original_shots_notice_text_and_one_exact_size_table(self):
        ctx=Context()
        with patch.object(c,'public_image',return_value={'status':'accessible'}):
            result=c.detail(ctx,self.doc,c.gallery_urls(self.doc,URL),{})
        doc=c.parse(result)
        self.assertEqual(len(doc.select('img')),14)
        self.assertEqual(len(doc.select('table')),1)
        self.assertEqual([n.get_text(strip=True) for n in doc.select('table th,table td')],[n.get_text(strip=True) for n in self.doc.select('.prd-modal-size__table th,.prd-modal-size__table td')])
        expected=c.notice_pairs(self.doc)
        self.assertEqual(len(doc.select('p strong')),len(expected))
        for label,value in expected:
            self.assertIn(label,doc.get_text());self.assertIn(value,doc.get_text())
        self.assertIn('단위 : 의류/용품(cm), 신발(mm)',doc.get_text())
        self.assertNotIn('배송 및 교환',doc.get_text())

    def test_table_renderer_preserves_spans_and_safely_handles_nested_wrappers(self):
        table=c.parse('<table onclick="bad()"><tbody><tr><th rowspan="2">SIZE</th><td colspan="2"><span><b>095 &amp; 100</b><script>bad()</script></span></td></tr></tbody></table>').table
        result=c.table_html(table)
        self.assertIn('rowspan="2"',result);self.assertIn('colspan="2"',result)
        self.assertIn('095 &amp; 100',result)
        self.assertNotIn('onclick',result);self.assertNotIn('script',result);self.assertNotIn('bad()',result)
        self.assertIn('095<br>100',c.table_html(c.parse('<table><tr><td>095<br>100</td></tr></table>').table))

    def test_timed_out_shot_does_not_discard_verified_detail(self):
        good = 'https://picweb.blackyak.com/front.jpg'
        late = 'https://picweb.blackyak.com/back.jpg'
        cache = {good: {'status': 'accessible'}, late: {'status': 'unknown', 'reasons': ['timed out']}}
        ctx = Context()
        result = c.detail(ctx, self.doc, [good, late], cache)
        self.assertIn(good, result)
        self.assertNotIn(late, result)
        self.assertIn('상품정보제공고시', result)
        self.assertIn('<table', result)
        self.assertTrue(any('생략' in message and late in message for message, *_ in ctx.logs))
        with self.assertRaisesRegex(ValueError, '상품컷 확인 실패'):
            c.detail(ctx, self.doc, [late], cache)

    def test_missing_notice_and_guide_do_not_prevent_detail(self):
        for n in self.doc.select('.prd-notice__accordion,.prd-modal-size__table'):n.decompose()
        ctx=Context()
        with patch.object(c,'public_image',return_value={'status':'accessible'}):
            result=c.detail(ctx,self.doc,['http://picweb.blackyak.com/photo.jpg'],{})
        self.assertIn('<img',result);self.assertNotIn('<table',result);self.assertTrue(ctx.logs)

    def test_no_optional_fields_skip_all_image_price_size_and_detail_work(self):
        ctx=Context()
        with patch.object(c,'search',return_value=[URL]),patch.object(c,'fetch',return_value=(self.sources['FB'],URL)),patch.object(c,'linked_colors',return_value=[]),patch.object(c,'gallery_urls') as gallery,patch.object(c,'sizes') as size,patch.object(c,'price') as price,patch.object(c,'detail') as detail:
            c.crawl(ctx)
            for mock in (gallery,size,price,detail):mock.assert_not_called()
        self.assertEqual(len(ctx.rows),1)
        self.assertEqual(ctx.rows[0]['product_url'],URL)

    def test_independent_price_selection(self):
        for fields in ((),('price',),('regular_price',),('price','regular_price')):
            with self.subTest(fields=fields):
                ctx=Context(fields)
                with patch.object(c,'search',return_value=[URL]),patch.object(c,'fetch',return_value=(self.sources['FB'],URL)),patch.object(c,'linked_colors',return_value=[]):c.crawl(ctx)
                self.assertEqual('price' in ctx.rows[0],'price' in fields)
                self.assertEqual('regular_price' in ctx.rows[0],'regular_price' in fields)

    def test_detail_only_collects_shots_but_not_gallery_or_size_option(self):
        ctx=Context(('detail_page',))
        with patch.object(c,'search',return_value=[URL]),patch.object(c,'fetch',return_value=(self.sources['FB'],URL)),patch.object(c,'linked_colors',return_value=[]),patch.object(c,'prefetch'),patch.object(c,'public_image',return_value={'status':'accessible'}),patch.object(c,'gallery') as gallery,patch.object(c,'sizes') as sizes:
            c.crawl(ctx);gallery.assert_not_called();sizes.assert_not_called()
        self.assertIn('<table',ctx.rows[0]['detail_page'])
        self.assertNotIn('main_image',ctx.rows[0])

    def test_full_sku_and_both_separators_select_exact_color(self):
        for code in ('1BYGJFV002DY','1BYGJFV002_DY','1bygjfv002-dy'):
            with self.subTest(code=code):
                ctx=Context(code=code)
                with patch.object(c,'search',return_value=[URL]),patch.object(c,'fetch',return_value=(self.sources['DY'],URL)),patch.object(c,'linked_colors',return_value=[]):c.crawl(ctx)
                self.assertEqual([r['color_code'] for r in ctx.rows],['DY'])

    def test_partial_color_error_keeps_completed_color_and_marks_item_failed(self):
        ctx=Context()
        with patch.object(c,'search',return_value=[URL,URL.replace('sample','bad')]),patch.object(c,'fetch',side_effect=[(self.sources['FB'],URL),ValueError('network')]),patch.object(c,'linked_colors',return_value=[]):
            with self.assertRaises(RuntimeError):c.crawl(ctx)
        self.assertEqual(len(ctx.rows),1);self.assertTrue(ctx.errors)


if __name__=='__main__':unittest.main()
