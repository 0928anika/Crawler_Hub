import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import dalpong_official as c
from crawler_sdk import CrawlContext
class Context:
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
class Tests(unittest.TestCase):
 def fixture(self,name):return (ROOT/('tests/fixtures/dalpong/'+name+'.html')).read_text('utf-8')
 def test_numeric_style_is_not_mistaken_for_cafe24_product_number(self):
  self.assertEqual(c.product_number('906822223083'),'');self.assertEqual(c.product_number('5311'),'5311')
  self.assertEqual(c.product_number('https://cubynmom.com/product/detail.html?product_no=5311'),'5311')
  with self.assertRaises(c.CrawlError):c.product_number('https://evil.example/product/detail.html?product_no=5311')
 def test_search_accepts_real_links_without_title_sku_and_ignores_disabled_pagination(self):
  with patch.object(c,'fetch',return_value=self.fixture('search-906822223083')) as fetch:
   links=c.search(Context(),'906822223083')
  self.assertEqual(len(links),2);self.assertEqual(fetch.call_count,1);self.assertEqual({c.product_number(url) for url in links},{'5310','5311'})
  doc=c.soup(self.fixture('5311'));self.assertIsNone(c.identity(doc,'906822223083'));self.assertEqual(c.identity(doc,'906822223083',search_confirmed=True)[0],'와이드 오버올')
 def test_zero_and_access_error_are_distinct(self):
  with patch.object(c,'fetch',return_value=self.fixture('search-100BE990')):self.assertEqual(c.search(Context(),'100BE990'),[])
  with patch.object(c,'fetch',return_value='<html>Access denied</html>'):
   with self.assertRaisesRegex(c.CrawlError,'검색 0건 아님'):c.search(Context(),'100BE990')
 def test_reverse_color_size_and_soldout_sizes_are_kept(self):
  for name,color in [('5310','beige'),('5311','brown')]:
   with self.subTest(name=name):
    p=c.palette(self.fixture(name));self.assertEqual(len(p),1);self.assertEqual(p[0]['color_name'],color);self.assertEqual(p[0]['sizes'],['90','100','110']);self.assertEqual(set(p[0]['prices']),{69000})
  p=c.palette(self.fixture('6475'))[0];self.assertEqual(p['color_name'],'베이지');self.assertIn('100 (2~3세)',p['sizes'])
 def test_color_only_options_keep_hidden_soldout_mint(self):
  colors=c.palette(self.fixture('5498'));self.assertEqual([p['color_name'] for p in colors],['민트','핑크']);self.assertTrue(all(p['color_code']=='' for p in colors))
 def test_detail_keeps_real_size_tables_and_excludes_delivery_images(self):
  doc=c.soup(self.fixture('5311'));area=doc.select_one('#prdDetail');area.append(doc.new_tag('img',src='https://cubynmom.speedgabia.com/shipping/banner.jpg'))
  with patch.object(c,'checked'):body=c.detail(Context(),doc,c.SITE,'906822223083',{})
  self.assertIn('와이드오버올1.jpg',c.unquote(body));self.assertNotIn('banner.jpg',body);self.assertEqual(len(c.soup(body).select('table')),2)
 def test_sdk_keeps_each_single_color_product_gallery_and_name_separate(self):
  meta=json.loads((ROOT/'brands/dalpong_official.json').read_text('utf-8'));links=[c.SITE+'/product/detail.html?product_no='+n for n in ['5311','5310']];events=[]
  with tempfile.TemporaryDirectory() as f:
   ctx=CrawlContext('unused',Path(f)/'out.xlsx',0,['size','price','regular_price','main_image','additional_images','detail_page'],meta['output_headers'],lambda:None,lambda k,**d:events.append((k,d)),input_item={'품번':'906822223083'},mapping_mode='manual')
   with patch.object(c,'search',return_value=links),patch.object(c,'fetch',side_effect=lambda ctx,url:self.fixture(c.product_number(url))),patch.object(c,'gallery',side_effect=lambda ctx,doc,url,cache:[c.SITE+'/'+c.product_number(url)+'.jpg']),patch.object(c,'checked'):
    c.crawl(ctx);ctx.finish()
   rows=[dict(zip(meta['output_headers'],d['row'])) for k,d in events if k=='row' and not d.get('header')]
  self.assertEqual(len(rows),2);self.assertEqual(ctx._unmapped_count,0)
  self.assertEqual([r['상품명'] for r in rows],['와이드 오버올','new밴딩 오버올']);self.assertEqual([r['색상'] for r in rows],['brown','beige']);self.assertEqual([r['대표이미지'] for r in rows],[c.SITE+'/5311.jpg',c.SITE+'/5310.jpg']);self.assertTrue(all(r['사이즈']=='90,100,110' for r in rows))
if __name__=='__main__':unittest.main()
