import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import agabang_eland_studio as c
class Context:
 product_code='01O11761003'
 def __init__(self):self.rows=[];self.failures=[]
 def enabled(self,k):return k in ('main_image','additional_images','size','price','detail_page')
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def fail_item(self,m):self.failures.append(m)
 def emit_unmapped(self,r,colors,url,**kw):self.rows.append((r,colors,url))
class Tests(unittest.TestCase):
 def setUp(self):
  self.data=json.loads((ROOT/'tests/fixtures/agabang_eland/2301719026.json').read_text(encoding='utf8'))
  self.opts=json.loads((ROOT/'tests/fixtures/agabang_eland/2301719026-options.json').read_text(encoding='utf8'))
  self.url=c.SITE+'/i/item?itemNo=2301719026&lowerVendNo='+c.VENDOR
 def test_registration(self):
  from registry import get_brand
  m=get_brand('agabang_eland_studio');self.assertTrue(m['available']);self.assertEqual(c.VENDOR,'LV22035111');self.assertEqual(c.BRAND_NO,'1690000462');self.assertEqual(m['additional_image_count'],9)
 def test_official_priority(self):
  doc=c.BeautifulSoup('<span data-searchresult-total>2</span><a class="ui-item__link" href="/i/item?itemNo=1&lowerVendNo=LV00000001">a</a><a class="ui-item__link" href="/i/item?itemNo=2&lowerVendNo='+c.VENDOR+'">b</a>','html.parser')
  with patch.object(c,'page',return_value=(doc,c.SITE)):self.assertEqual(c.search(Context(),'CODE'),[c.SITE+'/i/item?itemNo=2&lowerVendNo='+c.VENDOR])
 def test_product_identity(self):
  def page(*a):return c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=self.data))+';</script>','html.parser'),self.url
  with patch.object(c,'page',side_effect=page):
   self.assertIsNotNone(c.product_once(Context(),self.url,'01O11761003'))
   self.assertIsNone(c.product_once(Context(),self.url,'WRONG'))
   self.data['itemMain']['brandNo']='wrong';self.assertIsNone(c.product_once(Context(),self.url,'01O11761003'))
 def test_model_without_colour_suffix_requires_full_title_match(self):
  self.data['itemMain']['modelNo']='01O117610'
  def page(*a):return c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=self.data))+';</script>','html.parser'),self.url
  with patch.object(c,'page',side_effect=page):
   self.assertIsNotNone(c.product_once(Context(),self.url,'01O11761003'))
   self.assertIsNone(c.product_once(Context(),self.url,'01O11761004'))
 def test_nextmom_original_and_common_banners(self):
  self.assertTrue(c.original_detail('https://item.nextmom.co.kr/magaline_img/goods/agabang/2025/01S75200502.jpg','01S752005'))
  self.assertFalse(c.original_detail('https://item.nextmom.co.kr/magaline_img/goods/agabang/2025/01S75200602.jpg','01S752005'))
  with patch.object(c,'image_info',return_value=True),patch.object(c,'fetch',side_effect=AssertionError('banner requested')):parts=c.detail(Context(),self.data,'01O11761003',{})
  self.assertEqual(len(parts),1);self.assertIn('01O11761003.jpg',parts[0][1]);self.assertNotIn('event',parts[0][1]);self.assertNotIn('bottom',parts[0][1])
 def test_size_only_soldout_and_single_detail(self):
  for row in self.opts:row['soldoutYn']='Y'
  ctx=Context()
  with patch.object(c,'search',return_value=[self.url]),patch.object(c,'product',return_value=(self.data,self.url)),patch.object(c,'options',return_value=self.opts),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/test.jpg','hash')]),patch.object(c,'detail',return_value=[('photo','<img src="https://item.nextmom.co.kr/body.jpg">')]):c.crawl(ctx)
  self.assertFalse(ctx.failures);self.assertEqual(ctx.rows[0][1][0]['color_name'],'O/WHITE');self.assertEqual(ctx.rows[0][1][0]['color_code'],'');self.assertEqual(ctx.rows[0][1][0]['size'],'75,80')
 def test_non_official_requires_studio_gate(self):
  self.data['itemMain']['lowerVendNo']='LV00000001';ctx=Context()
  with patch.object(c,'search',return_value=[self.url]),patch.object(c,'product',return_value=(self.data,self.url)),patch.object(c,'studio_product',return_value=False),patch.object(c,'gallery',side_effect=AssertionError('non-studio gallery')):
   with self.assertRaises(c.CrawlError):c.crawl(ctx)
  self.assertFalse(ctx.rows)
if __name__=='__main__':unittest.main()
