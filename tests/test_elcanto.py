import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import elcanto_official as c
from crawler_sdk import CrawlContext
class Tests(unittest.TestCase):
 def setUp(self):
  self.source=(ROOT/'tests/fixtures/elcanto/LCWD89I513.html').read_text(encoding='utf8');self.doc=c.soup(self.source)
 def test_identity_and_registration(self):
  from registry import get_brand
  self.assertTrue(get_brand('elcanto_official')['available']);self.assertEqual(c.identity(self.doc,'LCWD89I513')[0],'여성 베이직 홑겹 펌프스 3cm');self.assertIsNone(c.identity(self.doc,'LCWD89I51'))
 def test_soldout_colours_and_all_sizes(self):
  colors=c.palette(self.source);self.assertEqual([v['color_name'] for v in colors],['블랙','베이지']);self.assertTrue(all(v['sizes']==['225','230','235','240','245','250'] for v in colors));self.assertTrue(all(v['color_code']=='' for v in colors))
  self.assertIn("sto_state:'HIDE'",self.source)
 def test_prices_come_from_offer_and_real_options(self):
  self.assertEqual(set(c.palette(self.source)[0]['prices']),{29900});self.assertEqual(c.amount(self.doc.select_one('.price_wrap strike').get_text()),139000)
 def test_product_detail_and_common_banners(self):
  urls=c.detail_urls(self.doc,c.SITE,'LCWD89I513');self.assertEqual(len(urls),10);self.assertTrue(all('/LCWD89I513/' in u for u in urls));self.assertTrue(any(u.endswith('sol.jpg') for u in urls));self.assertFalse(any('in_top' in u or '/cs_' in u for u in urls))
  with patch.object(c,'checked'):markup=c.detail(None,self.doc,c.SITE,'LCWD89I513',{})
  self.assertIn('제품의 주소재',markup);self.assertIn('베트남',markup);self.assertIn('<table',markup)
 def test_gif_and_foreign_product_cannot_enter_detail(self):
  area=self.doc.select_one('.prd-detail')
  for url in ['https://elcanto8.speedgabia.com/2025/women/LCWD89I513/video.gif','https://elcanto8.speedgabia.com/2025/women/OTHER/OTHER.jpg','https://evil.example/LCWD89I513/a.jpg']:area.append(self.doc.new_tag('img',src=url))
  self.assertEqual(len(c.detail_urls(self.doc,c.SITE,'LCWD89I513')),10)
 def test_gallery_and_extra_slots(self):
  self.assertEqual(len(c.gallery_urls(self.doc,c.SITE)),3)
  for n in [1,3,15]:
   urls=['https://img/'+str(i)+'.jpg' for i in range(n)];self.assertTrue(5<=len(c.extra_slots(urls).split(','))<=9)
 def test_sdk_keeps_colour_specific_metadata(self):
  meta=json.loads((ROOT/'brands/elcanto_official.json').read_text(encoding='utf8'));events=[]
  with tempfile.TemporaryDirectory() as folder:
   ctx=CrawlContext('unused',Path(folder)/'out.xlsx',0,['size','price','regular_price','main_image','additional_images','detail_page'],meta['output_headers'],lambda:None,lambda k,**d:events.append((k,d)),input_item={'품번':'LCWD89I513'},mapping_mode='manual')
   with patch.object(c,'search',return_value=[c.SITE+'/shop/shopdetail.html?branduid=3365812']),patch.object(c,'fetch',return_value=self.source),patch.object(c,'gallery',return_value=['https://elcanto8.speedgabia.com/test.jpg']),patch.object(c,'checked'):c.crawl(ctx);ctx.finish()
   self.assertEqual(ctx._unmapped_count,1);data=next(d for k,d in events if k=='row' and not d.get('header'));self.assertEqual(data['unmapped']['colors'][0]['size'],'225,230,235,240,245,250')
if __name__=='__main__':unittest.main()
