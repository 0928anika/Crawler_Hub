import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import fahrenheit_naver_official as c
from crawler_sdk import CrawlContext
class Ctx:
 def checkpoint(self):pass
 def log(self,*a):pass
class Tests(unittest.TestCase):
 def setUp(self):
  self.product=json.loads((ROOT/'tests/fixtures/fahrenheit/product.json').read_text(encoding='utf8'));self.contents=json.loads((ROOT/'tests/fixtures/fahrenheit/contents.json').read_text(encoding='utf8'))
 def test_registration(self):
  from registry import get_brand
  self.assertTrue(get_brand('fahrenheit_naver_official')['available']);self.assertEqual(c.STORE,'fahrenheit')
 def test_search_keeps_original_english_query_and_validates_model(self):
  state=dict(channel=dict(brandUrl='fahrenheit',channelUid='official'),keywordSearch=dict(keyword='FPFAH4203',totalCount=1,simpleProducts=[dict(id=13751254548,naverShoppingSearchInfo=dict(modelName='FPFAH4203'))]))
  page=MagicMock();page.content.return_value='<script>window.__PRELOADED_STATE__ = '+json.dumps(state)+';</script>'
  with patch.object(c,'navigate') as nav:links,uid=c.discover(Ctx(),page,'FPFAH4203')
  self.assertIn('ignoreExp=1',nav.call_args.args[2]);self.assertEqual(uid,'official');self.assertEqual(len(links),1)
  state['channel']['brandUrl']='other';page.content.return_value='<script>window.__PRELOADED_STATE__ = '+json.dumps(state)+';</script>'
  with patch.object(c,'navigate'),self.assertRaises(ValueError):c.discover(Ctx(),page,'FPFAH4203')
 def test_official_options_price_name(self):
  colors,pos,labels=c.color_rows(self.product);self.assertEqual(colors,[dict(color_code='GYD',color_name='다크그레이')]);self.assertEqual(c.common_sizes(Ctx(),self.product,pos,labels),'78,82');self.assertEqual(c.common_price(Ctx(),self.product),119400)
  self.assertEqual(c.product_name(self.product['name'],'FPFAH4203'),'스탠다드핏 카치온 다잉 스트레치 팬츠')
 def test_detail_excludes_common_assets_before_requests(self):
  with patch.object(c,'public_image',return_value={'data':b'valid'}) as image:markup=c.detail(Ctx(),self.product,self.contents,'FPFAH4203',{})
  self.assertEqual(image.call_count,1);self.assertIn('/frht/2026P/FPFAH4203/',markup);self.assertNotIn('/event/',markup);self.assertNotIn('/styleid_public/',markup)
 def test_wrong_product_detail_fails(self):
  with patch.object(c,'public_image',side_effect=AssertionError('unrelated image')):
   with self.assertRaises(ValueError):c.detail(Ctx(),self.product,self.contents,'OTHER',{})
 def test_access_limit_remains_explicit(self):
  page=MagicMock();page.goto.return_value.status=429
  with self.assertRaisesRegex(ValueError,'429'):c.navigate(Ctx(),page,c.SITE+'/fahrenheit/search?q=CODE')
 def test_sdk_end_to_end_with_real_public_response_fixtures(self):
  meta=json.loads((ROOT/'brands/fahrenheit_naver_official.json').read_text(encoding='utf8'));events=[]
  with tempfile.TemporaryDirectory() as folder:
   ctx=CrawlContext('unused',Path(folder)/'out.xlsx',0,['size','price','main_image','additional_images','detail_page'],meta['output_headers'],lambda:None,lambda kind,**data:events.append((kind,data)),input_item={'품번':'FPFAH4203'},mapping_mode='manual')
   engine=MagicMock();browser=engine.__enter__.return_value.chromium.launch.return_value;page=browser.new_page.return_value;page.url=c.SITE+'/fahrenheit/products/13751254548'
   with patch('playwright.sync_api.sync_playwright',return_value=engine),patch.object(c,'discover',return_value=({'13751254548':page.url},self.product['channel']['channelUid'])),patch.object(c,'navigate'),patch.object(c,'wait_data',side_effect=lambda *a:self.product if a[-1]=='product' else self.contents),patch.object(c,'gallery',return_value=['https://shop-phinf.pstatic.net/one.jpg','https://shop-phinf.pstatic.net/two.jpg']),patch.object(c,'public_image',return_value={'data':b'valid'}):c.crawl(ctx);ctx.finish()
   self.assertEqual(ctx._unmapped_count,2)
   raw=next(d for k,d in events if k=='row' and not d.get('header'));self.assertEqual(raw['unmapped']['colors'][0]['size'],'78,82');self.assertEqual(raw['unmapped']['colors'][0]['color_code'],'GYD')
if __name__=='__main__':unittest.main()
