import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('jeep_lotteon_test',ROOT/'brands/jeep_lotteon.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/jeep_lotteon'
class C:
    def checkpoint(self):return None
    def log(self,*args):return None
class LotteStudioTests(unittest.TestCase):
    def test_actual_studio_store_and_corner_badge_examples(self):
        for name,expected in [('studio.jpg',True),('seller.jpg',False),('corner_badge.png',False)]:
            with self.subTest(name=name):
                with Image.open(FIX/name) as pic:
                    self.assertEqual(c.studio(pic.convert('RGB').resize((500,500)))[0],expected)
    def test_search_does_not_admit_badge_wrong_brand_or_recommendations(self):
        rows=[{'key':str(i),'pdId':str(i),'brandId':brand,'pdName':name,**extra} for i,brand,name,extra in [(1,c.BRAND,'지프 JQ6JPU441',{}),(2,c.BRAND,'지프 JQ6JPU441',{'emblemInfo':{'emblemName':'롯데백화점위크'}}),(3,'P3784','지프키즈 JQ6JPU441',{}),(4,c.BRAND,'지프 JQ6JPU4410',{})]]
        with patch.object(c,'json_get',return_value={'itemList':rows,'total':4}):result=c.search(C(),'JQ6JPU441')
        self.assertEqual([r['key'] for r in result],['1'])
    def test_source_keeps_existing_size_text_table_and_excludes_banner(self):
        source='<div id="llis-root"><img src="https://example.com/a.png"><p>원문 본문</p><img src="https://example.com/product.jpg"><table><tr><th>사이즈</th><td>100</td></tr></table><p>측정 오차 1cm</p></div>'
        def info(ctx,url,cache):return {'digest':url,'image':Image.new('RGB',(800,500))}
        with tempfile.TemporaryDirectory() as folder,patch.object(c,'fetch',return_value=source.encode()),patch.object(c,'image',side_effect=info),patch.object(c,'ocr',side_effect=['이 상품은 신세계라이브쇼핑 상품입니다','지프 상품 상세']):
            result=c.body_markup(C(),{'descInfo':{'epnJsn':[{'pdEpnTypCd':'DSCRP','dtlFileRteNm':'/LO/1/','dtlFileNm':'DSCRP_1'}]}},{},{},Path(folder))
        self.assertNotIn('a.png',result);self.assertIn('product.jpg',result)
        self.assertEqual(result.count('<table'),1);self.assertIn('측정 오차 1cm',result)
        self.assertNotIn('상품정보고시',result)
    def test_long_common_service_banner_is_excluded_but_product_remains(self):
        source='<div id="llis-root"><img src="https://example.com/a.png"><img src="https://example.com/product.jpg"></div>'
        def info(ctx,url,cache):return {'digest':url,'image':Image.new('RGB',(800,3300) if url.endswith('a.png') else (800,800))}
        with tempfile.TemporaryDirectory() as folder,patch.object(c,'fetch',return_value=source.encode()),patch.object(c,'image',side_effect=info),patch.object(c,'ocr',side_effect=['사이즈 가이드 고객 서비스 구매 전 배송 안내 교환 반품 안내 고객센터','상품 상세']):
            result=c.body_markup(C(),{'descInfo':{'epnJsn':[{'pdEpnTypCd':'DSCRP','dtlFileRteNm':'/LO/1/','dtlFileNm':'DSCRP_1'}]}},{},{},Path(folder))
        self.assertNotIn('a.png',result);self.assertIn('product.jpg',result)

    def test_windows_ocr_recognizes_both_user_banner_examples(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ['shopping_banner.png','delivery_banner.png']:
                with Image.open(FIX/name) as pic:words=c.ocr(C(),pic.convert('RGB'),Path(folder),{})
                self.assertRegex(words,c.BANNER)
    def test_palette_keeps_disabled_colors_without_guessing_codes(self):
        data={'optionInfo':{'optionList':[{'title':'색상','options':[{'label':'블랙','disabled':True},{'label':'베이지'}]}]}}
        self.assertEqual(c.palette(data),[{'color_name':'블랙','color_code':''},{'color_name':'베이지','color_code':''}])
if __name__=='__main__':unittest.main()
