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
 def test_color_and_size_without_separator_and_with_spaces(self):
  source=self.fixture('4936')
  for variant in [source,source.replace('화이트90','화이트 90').replace('핑크100','핑크 100')]:
   colors=c.palette(variant)
   self.assertEqual({r['color_name']:r['sizes'] for r in colors},{'화이트':['90','100','110'],'핑크':['90','100','110']})
  rows=[dict(option_name_original=['옵션'],option_value_orginal=[v],option_price=1000) for v in ['화이트 90','핑크100','90화이트','100 핑크','화이트(WH)110']]
  with patch.object(c,'stock_rows',return_value=rows):colors=c.palette('<html></html>')
  self.assertEqual([(r['color_name'],r['color_code'],r['sizes']) for r in colors],[('화이트','',['90']),('핑크','',['100']),('화이트','WH',['110'])])
 def test_empty_deleted_slots_do_not_discard_real_options(self):
  colors=c.palette(self.fixture('5938'));self.assertEqual(len(colors),1);self.assertEqual(colors[0]['color_name'],'green');self.assertEqual(colors[0]['sizes'],['90'])
  rows=c.stock_rows(self.fixture('5938'));self.assertEqual(len(rows),8)
  with patch.object(c,'stock_rows',return_value=rows[1:]):
   with self.assertRaisesRegex(c.CrawlError,'전부 비어'):c.palette('<html></html>')
 def test_slash_in_color_name_is_not_split_as_another_dimension(self):
  colors=c.palette(self.fixture('5063'));self.assertEqual([r['color_name'] for r in colors],['m/gray','green']);self.assertEqual(set(colors[0]['sizes']),{'90','100','110','120'})
 def test_general_size_and_color_formats(self):
  cases=[('FREE SIZE/white','white','FREE SIZE'),('m/gray','m/gray',''),('S/M','','S/M'),('newborn/white','white','newborn'),('화이트/newborn','화이트','newborn'),('옐로우/newborn (0~3개월)','옐로우','newborn (0~3개월)'),('블루/S','블루','S'),('M/핑크','핑크','M'),('블루/85X85','블루','85X85'),('65*85/cream (미니)','cream (미니)','65*85'),('크림/110*200','크림','110*200'),('옐로우/1-2세','옐로우','1-2세'),('3~4세/블루','블루','3~4세'),('화이트90','화이트','90'),('100핑크','핑크','100'),('90/m/gray','m/gray','90'),('화이트 2XL','화이트','2XL'),('달퐁허니엠보손수건 플라워_10매입','달퐁허니엠보손수건 플라워_10매입',''),('여우나염방수요','여우나염방수요','')]
  for raw,color,size in cases:
   with self.subTest(value=raw):self.assertEqual(c.split_option(raw),(color,size))
  for size in ['newborn','FREE','NB','S','M','2XL','85X85','65*85','110×200cm','1~2세','5~6m']:
   with self.subTest(size=size):self.assertTrue(c.size_token(size))
  for color in ['white','cream','m/gray','mint','pink']:self.assertFalse(c.size_token(color))
 def test_supplied_newborn_and_dimensions_products(self):
  p=c.palette(self.fixture('6458'))[0];self.assertEqual((p['color_name'],p['sizes']),('white',['newborn']))
  p=c.palette(self.fixture('5361'))[0];self.assertEqual((p['color_name'],p['sizes']),('cream',['65*85 (미니)','110*200 (슈퍼싱글)']));self.assertEqual(p['prices'],[29000,99000])
 def test_size_only_does_not_invent_a_color(self):
  p=c.palette(self.fixture('6484'))[0];self.assertEqual(p['color_name'],'');self.assertTrue(p['color_missing']);self.assertEqual(p['sizes'],['5~6m','1~2세','3~4세'])
 def test_named_dimensions_are_used_before_text_shape(self):
  rows=[dict(option_name_original=['컬러 선택','크기'],option_value_orginal=['블루','S'],option_price=100),dict(option_name_original=['Colour','규격'],option_value_orginal=['핑크','newborn'],option_price=100),dict(option_name_original=['색상'],option_value_orginal=['M'],option_price=100)]
  with patch.object(c,'stock_rows',return_value=rows):p=c.palette('<html/>')
  self.assertEqual([(v['color_name'],v['sizes']) for v in p],[('블루',['S']),('핑크',['newborn']),('M',[])])
 def test_no_option_is_different_from_missing_option_response(self):
  source="<script>var has_option = 'F';</script>"
  self.assertTrue(c.palette(source)[0]['color_missing'])
  with self.assertRaises(c.CrawlError):c.palette('<html>incomplete</html>')
 def test_permanently_broken_detail_uses_verified_gallery_without_hiding_access_errors(self):
  doc=c.soup(self.fixture('5311'));cover='https://cubynmom.com/cover.jpg'
  with patch.object(c,'checked',side_effect=c.CrawlError('공개 GET 거부: HTTP 404')):
   body=c.detail(Context(),doc,c.SITE,'STYLE',{},[cover])
  self.assertIn(cover,body);self.assertEqual(len(c.soup(body).select('table')),2)
  with patch.object(c,'checked',side_effect=c.CrawlError('공개 GET 거부: HTTP 403')):
   with self.assertRaises(c.CrawlError):c.detail(Context(),doc,c.SITE,'STYLE',{},[cover])
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
