import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('yale',ROOT/'brands/yale_official.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/yale'
def doc(kind='product'):return BeautifulSoup((FIX/(kind+'.html')).read_bytes(),'html.parser')
class Context:
 def __init__(self):self.logs=[]
 def log(self,*args):self.logs.append(args)
 def checkpoint(self):pass
class YaleTests(unittest.TestCase):
 def test_search_both_color_pages(self):
  with patch.object(c,'fetch',return_value=(doc('search'),c.SITE)):
   self.assertEqual(set(c.search(Context(),'YEW4FW201A')),{'17344','17343'})
 def test_identity_and_sizes(self):
  d=doc();r=c.identity(d,'17344','YEW4FW201A',True)
  self.assertEqual((r['product_name'],r['color'],r['color_code']),('헤리티지 댄 투웨이 부츠','블랙','BK'))
  self.assertEqual(c.options(d,'사이즈','17344'),['230','240','250'])
  with self.assertRaises(c.ProductMismatch):c.identity(d,'17344','YEW4FW202A',True)
 def test_independent_prices_and_gallery_scope(self):
  d=doc();ctx=Context()
  self.assertEqual(c.price(ctx,d),129000);self.assertEqual(c.regular_price(ctx,d),129000)
  d.select_one('#span_product_price_custom').decompose();self.assertIsNone(c.regular_price(ctx,d))
  urls=c.gallery_urls(d,'17344',c.SITE)
  self.assertEqual(len(urls),5);self.assertFalse(any('/small/' in u for u in urls))
 def test_detail_preserves_declared_body_on_temporary_failure(self):
  ctx=Context()
  with patch.object(c,'checked',side_effect=c.ImageLookupError('reset')):
   result=c.detail(ctx,doc(),c.SITE,{})
  parsed=BeautifulSoup(result,'html.parser');self.assertEqual(len(parsed.select('img')),7)
  self.assertFalse(parsed.select('table'));self.assertNotIn('24_INTRO',result)
  self.assertTrue(ctx.logs)
 def test_explicit_zero_is_not_access_failure(self):
  d=BeautifulSoup((FIX/'search_BFF1JK301A.html').read_bytes(),'html.parser')
  with patch.object(c,'fetch',return_value=(d,c.SITE)):
   self.assertEqual(c.search(Context(),'BFF1JK301A'),{})
  with patch.object(c,'fetch',return_value=(BeautifulSoup('Access denied','html.parser'),c.SITE)):
   with self.assertRaises(ValueError):c.search(Context(),'BFF1JK301A')
 def test_category_ignores_empty_category_key(self):
  d=BeautifulSoup('<script>CAFE24.FRONT_JS_CONFIG_SHOP = {"oCategoryInfo":{"":{"category_name":""},"1":{"category_name":"OUTER"},"2":{"category_name":"DOWN"}}};</script>','html.parser')
  self.assertEqual(c.category(Context(),d),'OUTER > DOWN')
 def test_summary_uses_individual_color_pages(self):
  d=BeautifulSoup((FIX/'search_YGS1JK304A.html').read_bytes(),'html.parser')
  with patch.object(c,'fetch',return_value=(d,c.SITE)):
   self.assertEqual(set(c.search(Context(),'YGS1JK304A')),{'18204','18203','18202'})
 def test_sep_body_kept_without_intro_and_common_end(self):
  d=BeautifulSoup((FIX/'sep_detail.html').read_bytes(),'html.parser')
  with patch.object(c,'checked',return_value=True):result=c.detail(Context(),d,c.SITE,{})
  urls=[n['src'] for n in BeautifulSoup(result,'html.parser').select('img')]
  self.assertEqual(len(urls),6)
  self.assertTrue(all('/sep/25.03.17/SFS1SH201ABR/' in u for u in urls))
  self.assertNotIn('sep_top',result);self.assertNotIn('sep_end',result)
  self.assertEqual(c.image_url('https://unknown.example/sep/product.jpg',c.SITE),'')
if __name__=='__main__':unittest.main()
