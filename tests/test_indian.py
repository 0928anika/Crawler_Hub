import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import indian_official as c
from crawler_sdk import CrawlContext
class Tests(unittest.TestCase):
 def setUp(self):
  self.source=(ROOT/'tests/fixtures/indian/INTKA26WA406.html').read_text(encoding='utf8');self.doc=c.soup(self.source)
 def test_identity_brand_and_name(self):
  self.assertEqual(c.identity(self.doc,'INTKA26WA406')[0],'바람막이 착탈형 다운사파리')
  self.assertIsNone(c.identity(self.doc,'INTKA26WA40'))
  with patch.object(c,'field',side_effect=lambda d,k:'INTKA26WA406' if k=='모델명' else '다른브랜드'):self.assertIsNone(c.identity(self.doc,'INTKA26WA406'))
 def test_official_color_sizes_and_soldout(self):
  colors=c.palette(self.source);self.assertEqual(colors[0]['color_code'],'54');self.assertEqual(colors[0]['color_name'],'그레이쉬 블루');self.assertEqual(colors[0]['sizes'],['095','100','105','110'])
  rows=c.stock_rows(self.source)
  for row in rows:row['is_selling']='F';row['is_auto_soldout']='T'
  with patch.object(c,'stock_rows',return_value=rows):self.assertEqual(c.palette(self.source),colors)
 def test_detail_excludes_all_common_banners(self):
  urls=c.detail_urls(self.doc,c.SITE,'INTKA26WA406');self.assertEqual(len(urls),2);self.assertFalse(any('/banner/' in u for u in urls))
  with patch.object(c,'checked') as check:markup=c.detail(None,self.doc,c.SITE,'INTKA26WA406',{})
  self.assertEqual(check.call_count,2);self.assertIn('INTKA26WA406_INFO.jpg',markup);self.assertIn('INTKA26WA406_S.jpg',markup);self.assertIn('<table',markup);self.assertIn('소재',markup)
 def test_declared_cdn_gallery_and_prices(self):
  urls=c.gallery_urls(self.doc,c.SITE);self.assertEqual(len(urls),9)
  self.assertTrue(all('cafe24.poxo.com' in u for u in urls));self.assertEqual(c.amount(c.field(self.doc,'소비자가')),690000);self.assertEqual(c.palette(self.source)[0]['prices'][0],345000)
 def test_sdk_and_five_slots(self):
  meta=json.loads((ROOT/'brands/indian_official.json').read_text(encoding='utf8'));events=[]
  with tempfile.TemporaryDirectory() as folder:
   ctx=CrawlContext('unused',Path(folder)/'out.xlsx',0,['size','price','regular_price','main_image','additional_images','detail_page'],meta['output_headers'],lambda:None,lambda kind,**data:events.append((kind,data)),input_item={'품번':'INTKA26WA406'},mapping_mode='manual')
   with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=26940']),patch.object(c,'fetch',return_value=self.source),patch.object(c,'gallery',return_value=['https://sejungmall.com/photo.jpg']),patch.object(c,'checked'):c.crawl(ctx);ctx.finish()
   rows=[d for k,d in events if k=='row' and not d.get('header')];self.assertEqual(len(rows),1)
   row=dict(zip(meta['output_headers'],rows[0]['row']));self.assertEqual(row['색상코드'],'54');self.assertEqual(len(row['추가이미지'].split(',')),5);self.assertEqual(row['사이즈'],'095,100,105,110')
 def test_search_ignores_recommendations_and_resolves_relative_paging(self):
  source='<div class="xans-search-result"><ul class="prdList"><li><div class="name"><a href="/product/detail.html?product_no=26940">sample</a></div></li></ul></div><ul class="prdList"><li class="name"><a href="/product/detail.html?product_no=999">recommendation</a></li></ul><div class="xans-search-paging"><a href="?keyword=INTKA26WA406&amp;page=1&amp;SearchUrl=%252Fproduct%252Fsearch.html">1</a></div>'
  class Ctx:
   def checkpoint(self):pass
  with patch.object(c,'fetch',return_value=source) as fetch:
   links=c.search(Ctx(),'INTKA26WA406')
  self.assertEqual(links,[c.SITE+'/product/detail.html?product_no=26940'])
  self.assertEqual(fetch.call_count,2)
  self.assertTrue(all('/product/search.html?' in call.args[1] for call in fetch.call_args_list))
 def test_full_gallery_precedes_tail_and_extra_limit(self):
  photos=['https://sejungmall.com/photo'+str(i)+'.jpg' for i in range(14)]
  with patch.object(c,'checked'):markup=c.detail(None,self.doc,c.SITE,'INTKA26WA406',{},photos+[photos[0]])
  for url in photos:self.assertEqual(markup.count('src="'+url+'"'),1)
  self.assertLess(markup.index(photos[-1]),markup.index('INTKA26WA406_INFO.jpg'))
  self.assertLess(markup.index(photos[-1]),markup.index('INTKA26WA406_S.jpg'))
  self.assertEqual(len(c.extra_slots(photos).split(',')),9)
 def test_registration(self):
  from registry import get_brand
  self.assertTrue(get_brand('indian_official')['available'])
if __name__=='__main__':unittest.main()
