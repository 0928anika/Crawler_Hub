import json,copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import happyland_eland_studio as c
class Ctx:
 product_code='HL2672SE10'
 def __init__(self):self.rows=[];self.failures=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return k in ('main_image','additional_images','detail_page','size','price')
 def fail_item(self,m):self.failures.append(m)
 def emit_unmapped(self,r,colors,url,**kw):self.rows.append((r,colors,url))
class Tests(unittest.TestCase):
 def setUp(self):
  self.data=json.loads((ROOT/'tests/fixtures/happyland_eland/HL2672SE10.json').read_text(encoding='utf8'));self.url=c.SITE+'/i/item?itemNo=2610620879&lowerVendNo='+c.VENDOR
 def test_registration(self):
  from registry import get_brand
  m=get_brand('happyland_eland_studio');self.assertTrue(m['available']);self.assertEqual(c.VENDOR,'LV24008647');self.assertEqual(c.BRAND_NO,'1700008270');self.assertEqual(m['additional_image_count'],9)
 def test_official_priority_and_all_fallback_links(self):
  def doc(vendors):return c.BeautifulSoup('<span data-searchresult-total>2</span>'+''.join('<a class="ui-item__link" href="/i/item?itemNo='+str(i)+'&lowerVendNo='+v+'">x</a>' for i,v in enumerate(vendors)),'html.parser')
  with patch.object(c,'page',return_value=(doc(['LV00000001',c.VENDOR]),c.SITE)):links=c.search(Ctx(),'HL2672SE10')
  self.assertEqual(len(links),1);self.assertIn(c.VENDOR,links[0])
  with patch.object(c,'page',return_value=(doc(['LV00000001','LV00000002']),c.SITE)):self.assertEqual(len(c.search(Ctx(),'HL2672SE10')),2)
 def test_identity_checks_brand_model_vendor(self):
  def page(*a):return c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=self.data))+';</script>','html.parser'),self.url
  with patch.object(c,'page',side_effect=page):
   self.assertIsNotNone(c.product_once(Ctx(),self.url,'HL2672SE10'));self.assertIsNone(c.product_once(Ctx(),self.url,'WRONG'))
   self.data['itemMain']['brandNo']='OTHER';self.assertIsNone(c.product_once(Ctx(),self.url,'HL2672SE10'))
 def test_product_size_images_remain_banners_never_requested(self):
  with patch.object(c,'image_info',return_value=True) as check,patch.object(c,'fetch',side_effect=AssertionError('banner fetched')):parts=c.detail(Ctx(),self.data,'HL2672SE10',{})
  self.assertEqual(len(parts),2);self.assertTrue(any('notice_size.jpg' in p[1] for p in parts));self.assertFalse(any('/notice/notice.jpg' in call.args[1] for call in check.call_args_list))
 def test_named_seller_shipping_image_cannot_be_trusted(self):
  self.data['itemDesc']['itemDescdocumentCon']='<img src="https://pub-020c219c6a054e3ab4d8a4c1f8bd4a5c.r2.dev/h/2026/HL2672SE10_shipping.jpg">'
  with patch.object(c,'image_info',side_effect=AssertionError('shipping image requested')):self.assertEqual(c.detail(Ctx(),self.data,'HL2672SE10',{}),[])
 def test_all_studio_sellers_but_only_one_detail(self):
  ctx=Ctx();products=[]
  for i in range(2):
   data=copy.deepcopy(self.data);data['itemMain']['lowerVendNo']='LV0000000'+str(i+1);products.append((data,self.url+'&source='+str(i)))
  def opts(ctx,main,selected=None):
   if selected is None:return [dict(itemOptionNo='color1',itemOptionName='(01)Pink',itemOptionTcode='10')]
   return [dict(itemOptionName='80',itemOptionTcode='20',sellPrice=10000,soldoutYn='Y')]
  with patch.object(c,'search',return_value=[p[1] for p in products]),patch.object(c,'product',side_effect=products),patch.object(c,'studio_product',return_value=True),patch.object(c,'options',side_effect=opts),patch.object(c,'detail',return_value=[('photo','<img src="https://img/body.jpg">')]) as detail:
   # Use distinct hashes so both sellers' photos survive deduplication.
   with patch.object(c,'gallery',side_effect=[[('https://item.elandrs.com/0.jpg','hash0')],[('https://item.elandrs.com/1.jpg','hash1')]]):c.crawl(ctx)
  self.assertFalse(ctx.failures);self.assertEqual(len(ctx.rows),2);self.assertEqual(detail.call_count,1);self.assertEqual(ctx.rows[0][1][0]['size'],'80')
 def test_non_studio_fallback_is_rejected(self):
  ctx=Ctx();self.data['itemMain']['lowerVendNo']='LV00000001'
  with patch.object(c,'search',return_value=[self.url]),patch.object(c,'product',return_value=(self.data,self.url)),patch.object(c,'studio_product',return_value=False),patch.object(c,'gallery',side_effect=AssertionError('nonstudio gallery')):
   with self.assertRaises(c.CrawlError):c.crawl(ctx)
  self.assertFalse(ctx.rows)
if __name__=='__main__':unittest.main()
