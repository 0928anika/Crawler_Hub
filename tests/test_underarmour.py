import sys,json,copy,tempfile,unittest,types
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from contextlib import nullcontext
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import underarmour_official as m
from crawler_sdk import CrawlContext
FIX=ROOT/'tests/fixtures/underarmour'
def fixture(name):return json.loads((FIX/(name+'.json')).read_text(encoding='utf-8'))
class Tests(unittest.TestCase):
 def test_input_and_variation_url_preserve_leading_zero_and_selected_color(self):
  self.assertEqual(m.input_code('1373882-001'),('1373882','001'))
  self.assertEqual(m.input_code('1373882_013'),('1373882','013'))
  for value in ['137388','13738820','1373882-1','https://example.com']:
   with self.assertRaises(ValueError):m.input_code(value)
  url=m.variation_url(m.API+'Product-Variation?dwvar_1373882_color=&dwvar_1373882_size=SM&dwvar_1373882_length=R','1373882','001')
  query=parse_qs(urlsplit(url).query);self.assertEqual(query['dwvar_1373882_color'],['001']);self.assertNotIn('dwvar_1373882_size',query)
 def test_exact_identity_and_color_required(self):
  product=fixture('variation')['product'];m.verify_product(product,'1373882','013')
  for style,color in [('1373883','013'),('1373882','001')]:
   with self.assertRaises(ValueError):m.verify_product(product,style,color)
 def test_size_table_and_product_detail_keep_content_and_uniform_images(self):
  from bs4 import BeautifulSoup
  data=fixture('variation');photos=m.product_images(data['product']);chart=m.size_markup(fixture('size'))
  value=m.detail_markup(data['product'],photos,chart,data['productCustomDescriptionHtml'])
  doc=BeautifulSoup(value,'html.parser');self.assertTrue(doc.select('table tr'));self.assertIn('허리둘레',value)
  self.assertIn('80% 면/20% 폴리에스테르',value);self.assertIn('핏 및 관리 방법',value)
  self.assertEqual([i['src'] for i in doc.select('img')],photos)
  for image in doc.select('img'):
   self.assertEqual(image['width'],'860');self.assertIn('height:auto',image['style'])
 def test_absent_guide_and_information_are_not_invented(self):
  self.assertEqual(m.size_markup({}), '');value=m.detail_markup({'custom':{}},[],'')
  self.assertNotIn('사이즈 가이드',value);self.assertNotIn('제품 정보',value)
 def test_only_official_image_hosts(self):
  for url in ['https://evil.example/image.jpg','https://underarmour.scene7.com.evil.example/image.jpg']:
   with self.assertRaises(ValueError):m.public_url(url,images=True)
 def run_crawl(self,code,selected,multiple_lengths=False):
  events=[];calls=[];data=fixture('variation')
  def response(ctx,page,url):
   calls.append(url)
   if 'Product-SizeChart' in url:return fixture('size')
   q=parse_qs(urlsplit(url).query);color=q['dwvar_1373882_color'][0];value=copy.deepcopy(data);product=value['product'];product['custom']['color']=color
   product['images']={k:[dict(r,url=(r.get('url') or '').replace('-013_','-'+color+'_')) for r in rows] for k,rows in product['images'].items()}
   if multiple_lengths:
    product['variationAttributes']=[a for a in product['variationAttributes'] if a.get('id')!='length']+[dict(id='length',values=[dict(value='R',displayValue='REGULAR'),dict(value='T',displayValue='TALL')])]
   return value
  page=types.SimpleNamespace(url=m.SITE+'/ko-kr/p/1373882.html',goto=lambda *a,**kw:types.SimpleNamespace(status=200),content=lambda:(FIX/'page.html').read_text(encoding='utf-8'))
  browser=types.SimpleNamespace(new_page=lambda **kw:page,close=lambda:None)
  runtime=types.SimpleNamespace(chromium=types.SimpleNamespace(launch=lambda **kw:browser))
  fake=types.ModuleType('playwright.sync_api');fake.sync_playwright=lambda:nullcontext(runtime)
  meta=json.loads((ROOT/'brands/underarmour_official.json').read_text(encoding='utf-8'))
  with tempfile.TemporaryDirectory() as folder,patch.dict(sys.modules,{'playwright.sync_api':fake}),patch.object(m,'fetch_json',side_effect=response),patch.object(m,'checked_images',side_effect=lambda ctx,urls,cache:urls):
   ctx=CrawlContext('unused',Path(folder)/'out.xlsx',0,selected,meta['output_headers'],lambda:None,lambda kind,**d:events.append((kind,d)),input_item={'품번':code},mapping_mode='automatic',image_mode='url')
   m.crawl(ctx);ctx.finish()
  headers=next(d['row'] for k,d in events if k=='row' and d.get('header'))
  rows=[dict(zip(headers,d['row'])) for k,d in events if k=='row' and not d.get('header')]
  return rows,calls
 def test_all_colors_prices_sizes_and_chart_fetched_once(self):
  rows,calls=self.run_crawl('1373882',['size','price','regular_price','main_image','additional_images','detail_page'])
  self.assertEqual([r['색상코드'] for r in rows],['001','013'])
  for r in rows:
   self.assertEqual((r['판매가'],r['정상가']),(53400,89000));self.assertIn('XS',r['사이즈'])
   self.assertIn('-'+r['색상코드']+'_',r['대표이미지']);self.assertIn('사이즈 가이드',r['상세페이지 HTML'])
  self.assertEqual(sum('Product-SizeChart' in u for u in calls),1)
 def test_requested_color_and_no_unselected_guide_request(self):
  rows,calls=self.run_crawl('1373882-013',[])
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['색상코드'],'013')
  self.assertFalse(any('SizeChart' in u for u in calls));self.assertFalse(rows[0].get('상세페이지 HTML'))
 def test_multiple_lengths_are_distinguished_within_color(self):
  rows,calls=self.run_crawl('1373882-013',['size'],multiple_lengths=True)
  self.assertIn('REGULAR/XS',rows[0]['사이즈']);self.assertIn('TALL/XS',rows[0]['사이즈'])
if __name__=='__main__':unittest.main()
