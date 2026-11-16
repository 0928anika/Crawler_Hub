import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'brands'))
import benetton_halfclub_studio as c
FIX=ROOT/'tests/fixtures/benetton_halfclub'
class Context:
 def __init__(self,code='BAKPC3661',fields=()):self.product_code=code;self.fields=fields;self.rows=[];self.errors=[]
 def checkpoint(self):pass
 def log(self,*a):pass
 def stage(self,*a):pass
 def enabled(self,k):return k in self.fields
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,r,colors,url,shared_detail_html=''):self.rows.append((r,colors,url))
class Tests(unittest.TestCase):
 def setUp(self):self.data=json.loads((FIX/'product.json').read_text(encoding='utf-8'));self.texts=json.loads((FIX/'ocr.json').read_text(encoding='utf-8'))
 def test_required_documents_and_excluded_notices(self):
  for key in ['16','17','18']:self.assertFalse(c.banner_text(self.texts[key]),key)
  for key in ['1','excluded0','excluded1']:self.assertTrue(c.banner_text(self.texts[key]),key)
 def test_stock_number_connects_sizes_without_color_guess(self):
  colors=c.palette(self.data['productInfo'],True);self.assertEqual([x['color_name'] for x in colors],['BE','MG']);self.assertTrue(all(x['size']=='OS' for x in colors));self.assertTrue(all(x['color_code']=='' for x in colors))
 def test_search_filters_exact_brand_and_code(self):
  payload=(FIX/'search.json').read_bytes()
  with patch.object(c,'fetch',return_value=payload):self.assertEqual(c.search(Context(),'BAKPC3661'),['442037474','442060720','442953268'])
 def test_background_rejects_nonuniform_scene(self):
  blank=Image.new('RGB',(720,960),'#eee');ImageDraw.Draw(blank).rectangle((180,100,540,850),fill='#333')
  self.assertTrue(c.studio_background(blank)[0])
  noise=Image.effect_noise((720,960),90).convert('RGB');self.assertFalse(c.studio_background(noise)[0])
 def test_detail_documents_kept_in_order_banner_removed(self):
  im=Image.new('RGB',(700,900),'white');texts=iter([self.texts['1']]+['']*14+[self.texts['16'],self.texts['17'],self.texts['18']])
  with patch.object(c,'inspected',side_effect=lambda *a:(None,im,next(texts))),patch.object(c,'studio_background',return_value=(True,'')):
   result=c.details(Context(),self.data['productInfo'],{})
  self.assertNotIn('shoplinker/1.png',result);self.assertIn('/Z.jpg',result);self.assertIn('%EC%8A%A4%ED%81%AC%EB%A6%B0',result);self.assertIn('shoplinker/2.png',result);self.assertNotIn('<table',result)
 def test_regular_price_missing_is_not_failure(self):
  self.assertIsNone(c.positive(0));self.assertIsNone(c.positive('문의'));self.assertEqual(c.positive(279000),279000)
 def test_no_optional_fields_avoids_detail_and_prices(self):
  ctx=Context();im=Image.new('RGB',(720,960),'#eee');ImageDraw.Draw(im).rectangle((180,100,540,850),fill='#333')
  with patch.object(c,'search',return_value=['442037474','442060720','442953268']),patch.object(c,'page',return_value=self.data),patch.object(c,'inspected',return_value=(None,im,'')),patch.object(c,'details') as detail:c.crawl(ctx)
  detail.assert_not_called();self.assertTrue(ctx.rows);self.assertNotIn('regular_price',ctx.rows[0][0])
 def test_actual_sdk_contract(self):
  import tempfile
  sys.path.insert(0,str(ROOT/'app'))
  from crawler_sdk import CrawlContext
  definition=json.loads((ROOT/'brands/benetton_halfclub_studio.json').read_text(encoding='utf-8'))
  im=Image.new('RGB',(720,960),'#eee');ImageDraw.Draw(im).rectangle((180,100,540,850),fill='#333')
  with tempfile.TemporaryDirectory() as folder:
   ctx=CrawlContext(Path(folder)/'in.xlsx',Path(folder)/'out.xlsx',0,['main_image','size','price','regular_price'],definition['output_headers'],lambda:None,lambda *a,**k:None,input_item={'품번':'BAKPC3661'},mapping_mode='manual')
   with patch.object(c,'search',return_value=['442037474']),patch.object(c,'page',return_value=self.data),patch.object(c,'inspected',return_value=(None,im,'')):c.crawl(ctx)
   ctx.finish();self.assertTrue((Path(folder)/'out.xlsx').is_file())
 def test_nonstudio_product_not_emitted(self):
  ctx=Context();im=Image.effect_noise((720,960),90).convert('RGB')
  with patch.object(c,'search',return_value=['442037474','442060720','442953268']),patch.object(c,'page',return_value=self.data),patch.object(c,'inspected',return_value=(None,im,'')):
   with self.assertRaises(c.CollectionError):c.crawl(ctx)
  self.assertFalse(ctx.rows)
if __name__=='__main__':unittest.main()
