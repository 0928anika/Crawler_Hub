import sys,json,copy,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import ilodilo_boribori as c
FIX=ROOT/'tests/fixtures/ilodilo_boribori'
def sample(number='438239571'):return json.loads((FIX/(number+'.json')).read_text('utf-8'))
class C:
 product_code='IDJP26W50'
 def __init__(self):self.rows=[];self.errors=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return True
 def emit_unmapped(self,*a,**kw):self.rows.append((a,kw))
 def fail_item(self,m):self.errors.append(m)
class IlodiloBoriboriTests(unittest.TestCase):
 def test_real_body_only_keeps_product_and_size_images(self):
  d=sample()['data'];urls=c.body_images(d,'IDJP26W50');self.assertEqual(len(urls),2)
  self.assertIn('ID23-SIZE',urls[0]);self.assertIn('IDJP26W50_',urls[1])
 def test_opaque_seller_notices_and_named_banners_never_enter_body(self):
  d=sample()['data'];d['productDesc']['prdDescContClob']+='<img src="https://example.com/KakaoTalk_notice.jpg"><img src="https://example.com/IDJP26W50_banner.jpg" data-filename="ID-BANNER23.jpg"><img src="https://example.com/notice.jpg">'
  self.assertEqual(c.body_images(d,'IDJP26W50'),c.body_images(sample()['data'],'IDJP26W50'))
 def test_unknown_body_is_not_assumed_to_be_product_content(self):
  d=sample()['data'];d['productDesc']['prdDescContClob']='<img src="https://example.com/KakaoTalk_123.jpg">'
  with self.assertRaises(c.CollectionError):c.body_images(d,'IDJP26W50')
 def test_nonstudio_cover_excludes_link_even_if_label_closeup_passes(self):
  d=sample();ctx=C()
  with patch.object(c,'search',return_value=(['1'],1)),patch.object(c,'product',return_value=(d['data'],d['price'],'link')),patch.object(c,'checked_photo',side_effect=lambda ctx,url:'_basic_' not in url),self.assertRaises(c.CollectionError):c.crawl(ctx)
  self.assertEqual(ctx.rows,[])
 def test_rejected_extra_never_enters_candidates_and_body_is_single_link(self):
  d=sample();ctx=C()
  with patch.object(c,'search',return_value=(['1'],1)),patch.object(c,'product',return_value=(d['data'],d['price'],'link')),patch.object(c,'checked_photo',side_effect=lambda ctx,url:'_basic_' in url):c.crawl(ctx)
  self.assertTrue(ctx.rows);self.assertTrue(all('_basic_' in row[0][2] for row in ctx.rows));self.assertEqual(ctx.rows[0][0][0]['detail_page'].count('<img'),2)
 def test_names_remove_brand_model_and_region(self):
  self.assertEqual(c.clean_name('일로딜로_썸머 팬츠 IDTJ2650Z_괴정점','IDTJ2650Z'),'썸머 팬츠')
if __name__=='__main__':unittest.main()
