import importlib.util
import itertools
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('allaboutnew_test',ROOT/'brands/allaboutnew_official.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/allaboutnew'
def source(name='product'):return c.parse((FIX/(name+'.html')).read_bytes())
class Context:
    product_code='ANA3DC01A'
    def __init__(self,selected=()):self.selected=selected;self.rows=[];self.failures=[];self.logs=[]
    def checkpoint(self):pass
    def stage(self,*args):pass
    def log(self,*args):self.logs.append(args)
    def enabled(self,key):return key in self.selected
    def emit_result(self,row):self.rows.append(row)
    def fail_item(self,message):self.failures.append(message)

class AllaboutnewTests(unittest.TestCase):
    def test_actual_normal_body_uses_shared_official_image_host(self):
        doc=source('ANA4UP02ABR');urls=c.detail_urls(doc,c.SITE)
        self.assertEqual(len(urls),4)
        self.assertTrue(all(c.urlsplit(url).hostname=='intercrewkk.mycafe24.com' for url in urls))
        self.assertTrue(all(c.urlsplit(url).hostname in c.IMAGE_HOSTS for url in urls))
        with patch.object(c,'gallery_candidates',side_effect=AssertionError('normal body must not fall back')),patch.object(c,'image',return_value=dict(width=900,height=2000)):
            result=c.detail(Context(),doc,c.SITE,{})
        self.assertEqual([n['src'] for n in c.parse(result).select('img')],urls)
        self.assertFalse(c.parse(result).select('table'))

    def test_real_heading_color_not_incorrect_chip(self):
        record,sku=c.identity(source(),'ANA3DC01A')
        self.assertEqual((record['product_name'],record['color_name'],record['color_code']),('빈티지 솔트 워싱 집업 가디건','차콜','CC'))
        self.assertIn('D/BEIGE',c.text(source().select_one('.color')))
        other,sku=c.identity(source('682'),'ANA3DC01A')
        self.assertEqual((other['color_name'],other['color_code']),('D베이지','DE'))
        with self.assertRaises(c.CrawlError):c.identity(source(),'ANA3DC01ADE')

    def test_brand_is_verified_against_primary_schema(self):
        doc=source()
        for n in doc.select('script'):n.decompose()
        with self.assertRaises(c.CrawlError):c.identity(doc,'ANA3DC01A')

    def test_real_search_returns_two_color_links(self):
        with patch.object(c,'fetch',return_value=(FIX/'search.html').read_bytes()):links=c.search(Context(),'ANA3DC01A')
        self.assertEqual(len(links),2)
        self.assertTrue(any('/683/' in u for u in links));self.assertTrue(any('/682/' in u for u in links))

    def test_button_sizes_keep_disabled(self):
        doc=source();doc.select_one('[option_title="사이즈"] li')['class']=['ec-product-soldout']
        self.assertEqual(c.sizes(doc),'95,100,105')

    def test_empty_body_uses_every_shot_without_slot_padding(self):
        urls=[c.SITE+'/image'+str(i)+'.jpg' for i in range(8)]
        ctx=Context()
        with patch.object(c,'gallery_candidates',return_value=[dict(url=u) for u in urls]),patch.object(c,'image',return_value=dict(width=1000,height=1000)):
            result=c.detail(ctx,source(),c.SITE,{})
        self.assertEqual([n['src'] for n in c.parse(result).select('img')],urls)
        self.assertFalse(c.parse(result).select('table'));self.assertTrue(ctx.logs)

    def test_detail_fallback_does_not_drop_tall_product_photos(self):
        def picture(ctx,url,cache):return dict(raw=b'image',width=1000,height=2100,digest=url)
        with patch.object(c,'image',side_effect=picture),patch.object(c,'validate_gallery_image',return_value=dict(passed=False,reasons=['ratio'])):
            shots=c.gallery_candidates(Context(),source(),c.SITE,{},for_detail=True)
        self.assertEqual(len(shots),7)

    def test_existing_body_has_priority_and_keeps_size_notice_image(self):
        doc=source();area=doc.select_one('#prdDetail')
        urls=[c.SITE+'/detail/body.jpg',c.SITE+'/detail/info_size.jpg']
        for url in urls:
            n=doc.new_tag('img');n['ec-data-src']=url;area.append(n)
        with patch.object(c,'gallery_candidates',side_effect=AssertionError('must not replace existing body')),patch.object(c,'image',return_value=dict(width=900,height=1800)):
            result=c.detail(Context(),doc,c.SITE,{})
        self.assertEqual([n['src'] for n in c.parse(result).select('img')],urls)
        self.assertFalse(c.parse(result).select('table'))

    def test_missing_structure_is_not_an_empty_body(self):
        doc=source();doc.select_one('#prdDetail').decompose()
        with self.assertRaises(c.CrawlError):c.detail(Context(),doc,c.SITE,{})

    def test_broken_existing_body_is_not_replaced_by_shots(self):
        doc=source();n=doc.new_tag('img',src=c.SITE+'/actual.jpg');doc.select_one('#prdDetail').append(n)
        for exc in [c.MissingResource('404'),c.CrawlError('timeout')]:
            with patch.object(c,'image',side_effect=exc),patch.object(c,'gallery_candidates',side_effect=AssertionError('fallback forbidden')):
                with self.assertRaises(c.CrawlError):c.detail(Context(),doc,c.SITE,{})

    def test_price_combinations_never_use_max_discount(self):
        for selling,normal in itertools.product([False,True],repeat=2):
            ctx=Context([k for k,v in [('price',selling),('regular_price',normal)] if v])
            with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=683']),patch.object(c,'fetch',return_value=(FIX/'product.html').read_bytes()),patch.object(c,'gallery',side_effect=AssertionError('unselected')),patch.object(c,'detail',side_effect=AssertionError('unselected')):
                c.crawl(ctx)
            self.assertFalse(ctx.failures);row=ctx.rows[0]
            self.assertEqual(row.get('price'),129900 if selling else None)
            self.assertEqual(row.get('regular_price'),129900 if normal else None)
            self.assertEqual('price' in row,selling);self.assertEqual('regular_price' in row,normal)

    def test_missing_regular_price_stays_blank(self):
        for value in [None,'0 KRW','문의']:
            doc=source();n=doc.select_one('#span_product_price_custom');ctx=Context()
            if value is None:n.decompose()
            else:n.string=value
            self.assertIsNone(c.regular_price(ctx,doc));self.assertTrue(ctx.logs)

if __name__=='__main__':unittest.main()
