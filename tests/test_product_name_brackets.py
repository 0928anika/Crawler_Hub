import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from crawler_rules import clean_product_name
class ProductNameTests(unittest.TestCase):
 def test_preserve_meaningful_brackets_at_both_ends(self):
  for name in ['기모 맨투맨 (브러시드)','(기모) 맨투맨','팬츠 [와이드]','[여성] 팬츠','니트 (울 [캐시미어])']:
   with self.subTest(name=name):self.assertEqual(clean_product_name(name,[],[]),name)
 def test_remove_only_empty_pairs_left_by_brand_and_code_removal(self):
  self.assertEqual(clean_product_name('[GIORDANO] 니트 (울) (01026957)',['GIORDANO'],['01026957']),'니트 (울)')
  self.assertEqual(clean_product_name('지오다노 맨투맨 [세미오버] - 01026957',['지오다노'],['01026957']),'맨투맨 [세미오버]')
if __name__=='__main__':unittest.main()
