import importlib.util
import itertools
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,MagicMock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('stco_test',ROOT/'brands/stco_official.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/stco'

def source(code='DDSXD01CCD'):
    return c.parse((FIX/(code+'.html')).read_bytes())

class Context:
    product_code='DDSXD01CCD'
    def __init__(self,selected=()):self.selected=selected;self.rows=[];self.failures=[];self.logs=[]
    def enabled(self,key):return key in self.selected
    def checkpoint(self):pass
    def stage(self,*args):pass
    def log(self,*args):self.logs.append(args)
    def fail_item(self,message):self.failures.append(message)
    def emit_result(self,row):self.rows.append(row)

class StcoTests(unittest.TestCase):
    def test_empty_banner_directory_does_not_fail_real_body(self):
        doc=source('SEJXC01NSB');urls=c.body_urls(doc,c.SITE)
        self.assertEqual(len(urls),5)
        self.assertTrue(urls[0].endswith('SEJXC01NSB_t.jpg'))
        self.assertTrue(urls[-1].endswith('SIZESEJXF01.jpg'))
        with patch.object(c,'image',return_value={'width':950,'height':1000}) as loader:
            result=c.detail(Context(),doc,c.SITE,{})
        self.assertEqual([call.args[1] for call in loader.call_args_list],urls)
        self.assertNotIn('src="https://image.codigallery.com/promotion/"',result)
        self.assertEqual(len(c.parse(result).select('table tr')),8)

    def test_tls_timeout_retries_fresh_connection(self):
        ctx=Context();first=MagicMock();second=MagicMock()
        first.open.side_effect=c.urllib.error.URLError(TimeoutError('TLS handshake timed out'))
        response=second.open.return_value.__enter__.return_value
        response.headers={};response.read1.side_effect=[b'official response',b'']
        with patch.object(c,'public_opener',side_effect=[first,second]) as factory,patch.object(c.time,'sleep'):
            self.assertEqual(c.fetch(ctx,c.SITE+'/Search?searchWord=SDSXD54CSA'),b'official response')
        self.assertEqual(factory.call_count,2)
        self.assertEqual(second.open.call_args.kwargs['timeout'],15)
        self.assertTrue(any('재시도' in log[0] for log in ctx.logs))

    def test_tls_failure_is_bounded(self):
        opener=MagicMock();opener.open.side_effect=c.urllib.error.URLError(TimeoutError('TLS handshake timed out'))
        with patch.object(c,'public_opener',return_value=opener),patch.object(c.time,'sleep'):
            with self.assertRaises(c.CrawlError):c.fetch(Context(),c.SITE)
        self.assertEqual(opener.open.call_count,3)

    def test_notice_identity_and_korean_color(self):
        for sku,color,code in [('DDSXD01CCD','베이지','CCD'),('JDSXZ83CSB','다크 그레이','CSB')]:
            row=c.identity(source(sku),sku)
            self.assertEqual((row['color'],row['color_name'],row['color_code']),(color,color,code))
        with self.assertRaises(c.CrawlError):c.identity(source(),'DJPXD02CSB')

    def test_coordinate_gallery_excluded(self):
        doc=source();self.assertTrue(doc.select('.coordOption'))
        urls=c.gallery_urls(doc,c.SITE,'DDSXD01CCD')
        self.assertEqual(len(urls),6)
        self.assertTrue(all('/main/DDSXD01CCD_' in u for u in urls))
        self.assertFalse(any('DJPXD02' in u or 'DKTXD04' in u for u in urls))

    def test_sizes_include_zero_stock_and_ignore_coordinates(self):
        doc=source();node=doc.select_one('.listOption input[data-prd_no="DDSXD01CCD"]')
        node['data-qty']='0';node['disabled']='disabled'
        self.assertEqual(c.sizes(doc,'DDSXD01CCD'),'95,100,105,110')

    def test_complete_body_includes_shared_size_image(self):
        doc=source();urls=c.body_urls(doc,c.SITE)
        self.assertEqual(len(urls),6)
        self.assertTrue(urls[0].endswith('DIEMS_brand_bn.jpg'))
        self.assertTrue(urls[-1].endswith('SIZEDDSXF04.jpg'))
        with patch.object(c,'image',return_value={'width':950,'height':1000}) as fetch:
            result=c.detail(Context(),doc,c.SITE,{})
        self.assertEqual([call.args[1] for call in fetch.call_args_list],urls)
        parsed=c.parse(result)
        self.assertEqual([i['src'] for i in parsed.select('img')],urls)
        self.assertEqual(len(parsed.select('table')),1)
        self.assertEqual(len(parsed.select('table tr')),8)
        self.assertEqual(parsed.find_all(['img','table'])[-1].name,'table')
        self.assertIn('폴리에스터 65%, 레이온 35%',result)

    def test_notice_text_and_escaping(self):
        doc=source();area=doc.select_one('#targetProdInfoNotice')
        p=doc.new_tag('p');p.string='추가 안내 <보관> & 세탁';area.append(p)
        value=c.notice_html(doc)
        self.assertIn('추가 안내 &lt;보관&gt; &amp; 세탁',value)

    def test_missing_notice_fails_instead_of_fabricating(self):
        doc=source();doc.select_one('#targetProdInfoNotice').decompose()
        with self.assertRaises(c.CrawlError):c.notice_html(doc)

    def test_no_real_body_fails_even_when_files_removed(self):
        with patch.object(c,'image',side_effect=c.MissingResource('404')):
            with self.assertRaises(c.CrawlError):c.detail(Context(),source(),c.SITE,{})

    def test_removed_files_and_one_pixel_placeholders_keep_remaining_body(self):
        removed={'SSJXC61CSD':'SSJXC61CSD_0.jpg','SSJXC63CSK':'SSJXC63CSK_t.jpg'}
        for sku,missing in removed.items():
            ctx=Context()
            def loader(ctx,url,cache):
                if url.endswith(missing):raise c.MissingResource('404')
                return {'width':950,'height':1 if url.endswith('SSJXC63CSK_0.jpg') else 1000}
            with patch.object(c,'image',side_effect=loader):result=c.detail(ctx,source(sku),c.SITE,{})
            images=[node['src'] for node in c.parse(result).select('img')]
            self.assertEqual(len(images),4 if sku=='SSJXC61CSD' else 3)
            self.assertTrue(any(url.endswith(sku+'_1.jpg') for url in images))
            self.assertFalse(any(url.endswith(missing) for url in images))
            self.assertTrue(any('SIZESSJXF' in url for url in images))
            self.assertEqual(len(c.parse(result).select('table tr')),8)
            self.assertTrue(ctx.logs)

    def test_transient_body_error_is_not_discarded(self):
        with patch.object(c,'image',side_effect=c.CrawlError('TLS timeout')):
            with self.assertRaises(c.CrawlError):c.detail(Context(),source(),c.SITE,{})

    def test_search_ignores_header_recommendations_and_accepts_base(self):
        raw=(FIX/'search.html').read_bytes()+b'<a href="/Detail/Index/JDSXZ83CSB">header</a>'
        with patch.object(c,'fetch',return_value=raw):
            self.assertEqual([sku for sku,url in c.search(Context(),'DDSXD01')],['DDSXD01CCD'])

    def test_absent_search_card_still_tries_direct_input(self):
        with patch.object(c,'fetch',return_value=b'<html></html>'):
            self.assertEqual(c.search(Context(),'ANYINPUT'),[('ANYINPUT',c.SITE+'/detail/index/ANYINPUT')])

    def test_optional_prices_all_combinations(self):
        raw=(FIX/'JDSXZ83CSB.html').read_bytes()
        for selling,normal in itertools.product([False,True],repeat=2):
            ctx=Context([key for key,enabled in [('price',selling),('regular_price',normal)] if enabled]);ctx.product_code='JDSXZ83CSB'
            with patch.object(c,'search',return_value=[('JDSXZ83CSB',c.SITE+'/detail/index/JDSXZ83CSB')]),patch.object(c,'fetch',return_value=raw),patch.object(c,'gallery',side_effect=AssertionError('unselected gallery')),patch.object(c,'detail',side_effect=AssertionError('unselected detail')):
                c.crawl(ctx)
            self.assertFalse(ctx.failures);self.assertEqual(len(ctx.rows),1)
            row=ctx.rows[0]
            self.assertEqual(row.get('price'),44900 if selling else None)
            self.assertEqual(row.get('regular_price'),59900 if normal else None)
            self.assertEqual('price' in row,selling);self.assertEqual('regular_price' in row,normal)

    def test_invalid_regular_price_is_blank_with_warning(self):
        for value in [None,'0','문의','59,900원부터']:
            doc=source('JDSXZ83CSB');node=doc.select_one('.priceOld')
            if value is None:node.decompose()
            else:node.string=value
            ctx=Context();self.assertIsNone(c.regular_price(ctx,doc));self.assertTrue(ctx.logs)
            self.assertEqual(c.amount(c.text(doc.select_one('.priceNew'))),44900)

    def test_full_price_is_not_copied_to_regular_price(self):
        ctx=Context();doc=source()
        self.assertEqual(c.amount(c.text(doc.select_one('.priceNew'))),59900)
        self.assertIsNone(c.regular_price(ctx,doc))

if __name__=='__main__':unittest.main()
