import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))

class Context:
    product_code='TGTM26T01'
    def checkpoint(self):return None
    def log(self,*a):return None
    def stage(self,*a):return None
    def fail_item(self,*a):return None
    def enabled(self,key):return key=='main_image'
    def emit_unmapped(self,*a,**kw):self.output.append((a,kw))
    def __init__(self):self.output=[]

class VendorVersionTests(unittest.TestCase):
    def test_official_inaccessible_size_banner_preserves_actual_body(self):
        for c in self.modules():
            if c.VENDOR_NO!='LV17008491':continue
            urls=['http://img-studio.elandmall.com/Winstudio/TG-24-2BANNER.jpg','http://img-studio.elandmall.com/Winstudio/TGAK26A04.jpg','http://img-studio.elandmall.com/Winstudio/TG25-SIZE-1BANNER.jpg']
            data={'itemDesc':{'itemDescdocumentCon':''.join('<img src="'+u+'"><br>' for u in urls),'itemProvideMntList':[{'itemProvideMntArtiName':'종류','itemProvideMntArtiCon':'백팩'}]}}
            def load(ctx,url):
                if url==urls[-1]:raise c.UnavailableSource('HTTP 403')
                return (900,3000,'JPEG')
            with patch.object(c,'image_info',side_effect=load):result=''.join(c.detail(Context(),BeautifulSoup('','html.parser'),data))
            self.assertIn(urls[1],result);self.assertIn('백팩',result);self.assertNotIn(urls[-1],result)
            with patch.object(c,'image_info',side_effect=c.CrawlError('TLS timeout')):
                with self.assertRaises(c.CrawlError):c.detail(Context(),BeautifulSoup('','html.parser'),data)

    def test_official_zero_search_reports_search_not_image_failure(self):
        for c in self.modules():
            if c.VENDOR_NO!='LV17008491':continue
            ctx=Context();ctx.product_code='TGAK25A04'
            page=BeautifulSoup('<em data-searchresult-total>0</em><div id="searchPageList"></div>','html.parser')
            with patch.object(c,'soup',return_value=page),patch.object(c,'api') as api,patch.object(c,'gallery') as gallery:
                with self.assertRaises(c.CrawlError) as error:c.crawl(ctx)
            self.assertIn('검색 결과 0건',str(error.exception))
            self.assertNotIn('수동 매핑 불가',str(error.exception))
            api.assert_not_called();gallery.assert_not_called()

    def test_official_other_store_is_not_reported_as_global_absence(self):
        for c in self.modules():
            if c.VENDOR_NO!='LV17008491':continue
            page=BeautifulSoup('<em data-searchresult-total>1</em><div id="searchPageList"><div data-item-section><a class="ui-item__link" href="/i/item?itemNo=2502724265&amp;lowerVendNo=LV19015442"></a></div></div>','html.parser')
            with patch.object(c,'soup',return_value=page):
                with self.assertRaises(c.CrawlError) as error:c.discover(Context(),'TGAK25A04')
            self.assertIn('검색 결과는 있으나',str(error.exception));self.assertIn(c.VENDOR_NO,str(error.exception))

    def modules(self):
        for name in ['tdg_eland_ncgn','tdg_eland_official']:
            spec=importlib.util.spec_from_file_location(name,ROOT/'brands'/(name+'.py'))
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            yield module
    def test_discovery_filters_id_even_without_branch_name(self):
        for c in self.modules():
            with self.subTest(vendor=c.VENDOR_NO):
                page=BeautifulSoup('<span data-searchresult-total>2</span><div id="searchPageList">'+''.join('<div data-item-section><a class="ui-item__link" href="/i/item?itemNo='+str(i)+'&amp;lowerVendNo='+vendor+'"><span class="ui-item__name">일반 상품명</span></a></div>' for i,vendor in [(1,c.VENDOR_NO),(2,'OTHER')])+'</div>','html.parser')
                with patch.object(c,'soup',return_value=page):links=c.discover(Context(),'TGTM26T01')
                self.assertEqual(len(links),1);self.assertIn('itemNo=1',links[0]);self.assertIn(c.VENDOR_NO,links[0])
    def test_detail_revalidates_vendor_brand_and_model(self):
        for c in self.modules():
            main={'itemNo':'1','lowerVendNo':c.VENDOR_NO,'brandNo':c.BRAND_NO,'modelNo':'TGTM26T01','itemName':'일반 팬츠 TGTM26T01','brandName':'더데이걸'}
            for field,bad in [('lowerVendNo','OTHER'),('brandNo','OTHER'),('modelNo','OTHER'),('itemNo','2')]:
                with self.subTest(module=c.__name__,field=field):
                    wrong=dict(main);wrong[field]=bad
                    with patch.object(c,'discover',return_value=['https://www.elandmall.co.kr/i/item?itemNo=1&lowerVendNo='+c.VENDOR_NO]),patch.object(c,'soup',return_value=BeautifulSoup('','html.parser')),patch.object(c,'parse_item',return_value={'itemMain':wrong}),patch.object(c,'api') as api:
                        with self.assertRaises(c.CrawlError):c.crawl(Context())
                        api.assert_not_called()
    def test_valid_vendor_accepts_title_without_branch_marker(self):
        for c in self.modules():
            ctx=Context();main={'itemNo':'1','lowerVendNo':c.VENDOR_NO,'brandNo':c.BRAND_NO,'modelNo':'TGTM26T01','itemName':'일반 팬츠 TGTM26T01','brandName':'더데이걸'}
            with patch.object(c,'discover',return_value=['https://www.elandmall.co.kr/i/item?itemNo=1&lowerVendNo='+c.VENDOR_NO]),patch.object(c,'soup',return_value=BeautifulSoup('','html.parser')),patch.object(c,'parse_item',return_value={'itemMain':main}),patch.object(c,'api',return_value=[{'itemOptionTcode':'10','itemOptionName':'(10) 블랙'}]),patch.object(c,'optional_values',return_value=({},[])),patch.object(c,'gallery',return_value=['https://example.com/a.jpg']):
                c.crawl(ctx)
            self.assertEqual(len(ctx.output),1)
            self.assertEqual(ctx.output[0][0][0]['product_name'],'일반 팬츠')

if __name__=='__main__':unittest.main()
