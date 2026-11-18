import importlib.util
import itertools
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('intercrew_test',ROOT/'brands/intercrew_official.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/intercrew'
def source():return c.parse((FIX/'product.html').read_bytes())
class Context:
    product_code='ITA3EL05A'
    def __init__(self,selected=()):self.selected=selected;self.rows=[];self.failures=[];self.logs=[]
    def checkpoint(self):pass
    def stage(self,*args):pass
    def log(self,*args):self.logs.append(args)
    def enabled(self,key):return key in self.selected
    def emit_result(self,row):self.rows.append(row)
    def fail_item(self,message):self.failures.append(message)

class IntercrewTests(unittest.TestCase):
    def test_actual_identity_and_full_code(self):
        for query in ['ITA3EL05A','ITA3EL05ADG']:
            record,sku=c.identity(source(),query)
            self.assertEqual((record['color_name'],record['color_code']),('D그레이','DG'))
            self.assertEqual(sku,'ITA3EL05ADG')
            self.assertEqual(record['product_name'],'다트 볼륨 올밴딩 딥 그레이 데님 팬츠')
        with self.assertRaises(c.CrawlError):c.identity(source(),'ITA3EL05ABK')

    def test_no_guessed_mapping_for_multicolor_page(self):
        doc=source();option=doc.new_tag('option',value='블랙');option.string='블랙'
        doc.select_one('select[option_title="색상"]').append(option)
        with self.assertRaises(c.CrawlError):c.identity(doc,'ITA3EL05A')

    def test_search_reads_actual_cards(self):
        with patch.object(c,'fetch',return_value=(FIX/'search.html').read_bytes()):
            links=c.search(Context(),'ITA3EL05A')
        self.assertEqual(len(links),1);self.assertIn('/8634/',links[0])

    def test_lazy_body_retains_size_notice_and_excludes_delivery(self):
        ctx=Context();doc=source();urls=c.detail_urls(doc,c.SITE)
        self.assertEqual(len(urls),5)
        self.assertTrue(any('info_size' in url for url in urls))
        def picture(ctx,url,cache):return dict(width=900,height=1 if url.endswith('_01_model.jpg') else 1660)
        with patch.object(c,'image',side_effect=picture) as loader:result=c.detail(ctx,doc,c.SITE,{})
        self.assertEqual(len(loader.call_args_list),5)
        parsed=c.parse(result);self.assertEqual(len(parsed.select('img')),4)
        self.assertFalse(parsed.select('table'))
        self.assertIn('info_size.jpg',result);self.assertIn('fit_fabric.jpg',result)
        self.assertNotIn('delivery',result);self.assertNotIn('_01_model.jpg',result)

    def test_price_combinations_and_unselected_loaders(self):
        for selling,normal in itertools.product([False,True],repeat=2):
            ctx=Context([k for k,v in [('price',selling),('regular_price',normal),('size',True)] if v])
            with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=8634']),patch.object(c,'fetch',return_value=(FIX/'product.html').read_bytes()),patch.object(c,'gallery',side_effect=AssertionError('unselected gallery')),patch.object(c,'detail',side_effect=AssertionError('unselected detail')):
                c.crawl(ctx)
            self.assertFalse(ctx.failures);row=ctx.rows[0]
            self.assertEqual(row.get('price'),53900 if selling else None)
            self.assertEqual(row.get('regular_price'),89900 if normal else None)
            self.assertEqual('regular_price' in row,normal)
            self.assertEqual('price' in row,selling)
            self.assertEqual(row['size'],'28(71),30(76),32(81),34(86)')

    def test_regular_price_absent_zero_invalid_nonfatal(self):
        for value in [None,'0','문의']:
            ctx=Context();doc=source();node=doc.select_one('#span_product_price_custom')
            if value is None:node.decompose()
            else:node.string=value
            self.assertIsNone(c.regular_price(ctx,doc));self.assertTrue(ctx.logs)
        with patch.object(c,'amount',side_effect=RuntimeError('parse error')):
            self.assertIsNone(c.regular_price(Context(),source()))

    def test_no_search_result_is_failure(self):
        ctx=Context()
        with patch.object(c,'search',return_value=[]):
            with self.assertRaises(c.CrawlError):c.crawl(ctx)
        self.assertTrue(ctx.failures)

    def test_body_transient_error_not_silently_skipped(self):
        with patch.object(c,'image',side_effect=c.CrawlError('TLS timeout')):
            with self.assertRaises(c.CrawlError):c.detail(Context(),source(),c.SITE,{})

if __name__=='__main__':unittest.main()
