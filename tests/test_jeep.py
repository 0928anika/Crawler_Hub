import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
spec=importlib.util.spec_from_file_location('jeep_test',ROOT/'brands/jeep_official.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class C:
    def log(self,*a):return None
class JeepTests(unittest.TestCase):
    def setUp(self):self.doc=c.parse((ROOT/'tests/fixtures/jeep/JR4JPU492BK.html').read_text(encoding='utf-8'))
    def test_adult_identity_and_exact_model(self):
        row=c.identity(self.doc,'JR4JPU492')
        self.assertEqual(row['product_name'],'루비콘 볼륨 다운점퍼')
        self.assertEqual(row['color_code'],'BK');self.assertEqual(row['color_name'],'')
        self.assertIsNone(c.identity(self.doc,'JR4JPU491'))
    def test_kids_not_mixed_into_adult_results(self):
        doc=c.parse((ROOT/'tests/fixtures/jeepkids/KR3TPU051_0.html').read_text(encoding='utf-8'))
        self.assertIsNone(c.identity(doc,'KR3TPU051'))
    def test_actual_discounted_selling_price_and_fallback(self):
        self.assertEqual(c.price(C(),self.doc),231200)
        for tr in self.doc.select('.infoArea tr'):
            head=tr.find('th')
            if head and head.get_text(strip=True)=='할인판매가':tr.decompose()
        self.assertEqual(c.price(C(),self.doc),289000)
    def test_actual_gallery_details_and_sizes(self):
        raw=c.field(self.doc,'상품명')
        self.assertEqual(c.gallery_urls(self.doc,c.SITE,raw),['https://jeepwear.co.kr/web/product/big/202611/a659b4353ec40019fefca801d55d97e5.jpg'])
        details=c.detail_urls(self.doc,c.SITE)
        self.assertEqual(len(details),4);self.assertTrue(details[0].endswith('492bk-1.jpg'))
        self.assertEqual(c.sizes(self.doc),'S,M,L,XL,2XL')
        self.assertEqual(c.categories(self.doc),['겨울 아우터'])
if __name__=='__main__':unittest.main()
