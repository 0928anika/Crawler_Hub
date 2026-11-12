import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('awk_test',ROOT/'brands/awk_official.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/awk'
RAW=(FIX/'R267B0101A.html').read_bytes()
class Context:
 product_code='R267B0101A'
 def __init__(self,fields=()):self.fields=set(fields);self.rows=[];self.errors=[];self.unmapped=[]
 def enabled(self,key):return key in self.fields
 def checkpoint(self):return None
 def stage(self,*a):return None
 def log(self,*a):return None
 def fail_item(self,msg):self.errors.append(msg)
 def emit_result(self,row):self.rows.append(row)
 def emit_unmapped(self,record,colors,url,shared_detail_html=""):self.unmapped.append((record,colors,url,shared_detail_html))
class AwkTests(unittest.TestCase):
 def test_actual_option_stock_json_and_original_labels(self):
  doc=c.parse(RAW);options=c.choices(doc,c.product_group(doc))
  self.assertEqual(len(options),1);self.assertEqual(options[0]['color'],'네이비29')
  self.assertEqual(options[0]['sizes'],['145','155','165','175'])
  self.assertEqual(c.field(doc,'자체상품코드'),'R267B0101A')
 def test_independent_price_selections_skip_unselected_image_detail_work(self):
  for fields in [[],['price'],['regular_price'],['price','regular_price']]:
   ctx=Context(fields)
   with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=5157']),patch.object(c,'fetch',return_value=RAW),patch.object(c,'gallery',side_effect=AssertionError('unselected gallery')),patch.object(c,'detail',side_effect=AssertionError('unselected detail')):
    c.crawl(ctx)
   self.assertEqual(len(ctx.rows),1);self.assertFalse(ctx.errors)
   r=ctx.rows[0];self.assertEqual('price' in r,'price' in fields);self.assertEqual('regular_price' in r,'regular_price' in fields)
   if 'price' in fields:self.assertEqual(r['price'],49900)
   if 'regular_price' in fields:self.assertEqual(r['regular_price'],109900)
 def test_missing_zero_invalid_or_source_error_normal_price_keeps_product(self):
  for value in ['', '0원','알수없음']:
   doc=c.parse(RAW)
   for tr in doc.select('.infoArea tr'):
    if tr.find('th') and tr.find('th').get_text(strip=True)=='소비자가':tr.find('td').string=value
   ctx=Context(['regular_price'])
   with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=5157']),patch.object(c,'fetch',return_value=str(doc).encode()):c.crawl(ctx)
   self.assertIsNone(ctx.rows[0]['regular_price']);self.assertFalse(ctx.errors)
  with patch.object(c,'field',side_effect=OSError('source error')):self.assertIsNone(c.regular_price(Context(),c.parse(RAW)))
 def test_shipping_notice_never_requested_and_size_images_kept(self):
  requested=[]
  def picture(ctx,url,cache):requested.append(url);return {'height':1 if url.endswith('/1.jpg') else 1000}
  with patch.object(c,'picture',side_effect=picture):result=c.detail(Context(),c.parse(RAW),c.SITE,{})
  self.assertNotIn('notice_2021',result);self.assertFalse(any('notice_2021' in u for u in requested))
  self.assertIn('/aw/R267B0101A.jpg',result);self.assertIn('size-info.jpg',result);self.assertIn('kc.jpg',result)
  self.assertNotIn('<table',result);self.assertLess(result.index('R267B0101A.jpg'),result.index('size-info.jpg'))
 def test_actual_gallery_cut_is_valid_and_repeat_slots_use_same_verified_image(self):
  ctx=Context();doc=c.parse(RAW);item=c.choices(doc,c.product_group(doc))[0]
  with patch.object(c,'fetch',return_value=(FIX/'main.jpg').read_bytes()):main,extra=c.gallery(ctx,item,{})
  self.assertTrue(main);self.assertEqual(extra.split(','),[main]*5)
 def test_multi_color_shared_photo_is_not_guessed(self):
  group={'hasVariant':[{'color':name,'size':'M','image':['https://awkmall.com/web/product/big/shared.jpg']} for name in ['블랙','레드']]}
  with patch.object(c,'stock_options',return_value=[]):options=c.choices(c.parse('<html></html>'),group)
  self.assertEqual([o['images'] for o in options],[[],[]])
  with self.assertRaises(c.CrawlError):c.gallery(Context(),options[0],{})
 def test_mapping_failure_becomes_manual_not_product_absence(self):
  ctx=Context(['main_image'])
  with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=5157']),patch.object(c,'fetch',return_value=RAW),patch.object(c,'gallery',side_effect=c.MappingFailure('색상별 사진 자동 연결 불가')),patch.object(c,'manual_candidates',return_value=['https://example.com/photo.jpg']):
   c.crawl(ctx)
  self.assertFalse(ctx.errors);self.assertFalse(ctx.rows);self.assertEqual(len(ctx.unmapped),1)

 def test_color_button_original_images_override_shared_default_jsonld(self):
  doc=c.parse((FIX/'R267B0301A.html').read_bytes());options=c.choices(doc,c.product_group(doc))
  self.assertEqual([o['color'] for o in options],['블랙19','브라운88'])
  self.assertTrue(options[0]['images'][0].endswith('/big/202610/d1a1f9d0b896ebdcb2cf2e7454c11099.jpg'))
  self.assertTrue(options[1]['images'][0].endswith('/extra/big/202609/25c460f2958fd5e98bf2bc9673c0c6f2.jpg'))
  self.assertFalse(set(options[0]['images']) & set(options[1]['images']))
  self.assertTrue(all('/option_button/' not in u for o in options for u in o['images']))

 def test_option_src_itself_can_be_full_resolution_without_link_image(self):
  doc=c.parse((FIX/'R267B1101A.html').read_bytes());options=c.choices(doc,c.product_group(doc))
  self.assertEqual([x['color'] for x in options],['멜란지그레이14','네이비29'])
  for i,item in enumerate(options):
   self.assertTrue(item['images']);self.assertIn('/option_button/',item['images'][0])
   with patch.object(c,'fetch',return_value=(FIX/('option_'+str(i)+'.jpg')).read_bytes()):main,extras=c.gallery(Context(),item,{})
   self.assertEqual(main,item['images'][0]);self.assertEqual(len(extras.split(',')),5)
 def test_numeric_color_fields_preserve_label_and_leading_zero(self):
  self.assertEqual(c.color_fields('네이비29'),('네이비','29'))
  self.assertEqual(c.color_fields('멜란지그레이14'),('멜란지그레이','14'))
  self.assertEqual(c.color_fields('화이트01'),('화이트','01'))
  self.assertEqual(c.color_fields('네이비'),('네이비',''))
 def test_small_option_photo_matches_unique_original_not_color_name_guess(self):
  import io
  from PIL import Image
  raw=(FIX/'main.jpg').read_bytes()
  with Image.open(io.BytesIO(raw)) as pic:
   thumb=pic.resize((90,90));out=io.BytesIO();thumb.save(out,format='JPEG');small=out.getvalue()
  wrong=io.BytesIO();Image.new('RGB',(700,700),'red').save(wrong,format='JPEG')
  urls=['https://awkmall.com/web/product/big/a.jpg','https://awkmall.com/web/product/big/b.jpg']
  def info(ctx,url,cache):return {'raw':raw if url==urls[0] else wrong.getvalue(),'width':700,'height':700}
  with patch.object(c,'browser_gallery',return_value=urls),patch.object(c,'picture',side_effect=info):
   self.assertEqual(c.matching_original(Context(),{'raw':small},c.SITE,{}),urls[0])

 def test_one_unknown_option_sends_whole_article_to_manual(self):
  raw=(FIX/'R267B0301A.html').read_bytes();ctx=Context(['main_image']);ctx.product_code='R267B0301A'
  with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=5160']),patch.object(c,'fetch',return_value=raw),patch.object(c,'gallery',side_effect=[('https://example.com/black.jpg','https://example.com/black.jpg'),c.MappingFailure('brown unknown')]),patch.object(c,'manual_candidates',return_value=['https://example.com/black.jpg','https://example.com/brown.jpg']):
   c.crawl(ctx)
  self.assertFalse(ctx.rows);self.assertFalse(ctx.errors);self.assertEqual(len(ctx.unmapped),2)
  self.assertEqual({c['color_code'] for c in ctx.unmapped[0][1]},{'19','88'})
  self.assertEqual({r[2] for r in ctx.unmapped},{'https://example.com/black.jpg','https://example.com/brown.jpg'})
 def test_all_confirmed_options_emit_auto_only(self):
  raw=(FIX/'R267B0301A.html').read_bytes();ctx=Context(['main_image']);ctx.product_code='R267B0301A'
  with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=5160']),patch.object(c,'fetch',return_value=raw),patch.object(c,'gallery',side_effect=[('https://example.com/black.jpg',''),('https://example.com/brown.jpg','')]),patch.object(c,'manual_candidates',side_effect=AssertionError('unneeded manual')):
   c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);self.assertFalse(ctx.unmapped);self.assertTrue(ctx.allow_automatic_results)

 def test_user_manual_example_has_all_colors_and_unconfirmed_shared_photo(self):
  doc=c.parse((FIX/'R267B3501A.html').read_bytes());options=c.choices(doc,c.product_group(doc))
  self.assertEqual([o['color'] for o in options],['멜란지그레이14','블랙19'])
  self.assertTrue(all(not o['images'] for o in options))
  with self.assertRaises(c.MappingFailure):c.gallery(Context(),options[0],{})

 def test_deleted_product_body_uses_product_cuts_then_remaining_size_images(self):
  doc=c.parse(RAW);requested=[]
  def pic(ctx,url,cache):
   requested.append(url)
   if '/aw/' in url:raise c.MissingResource('HTTP404')
   return {'height':1 if url.endswith('/1.jpg') else 1000}
  photos=['https://awkmall.com/web/product/big/front.jpg','https://awkmall.com/web/product/extra/big/back.jpg']
  with patch.object(c,'picture',side_effect=pic),patch.object(c,'manual_candidates',return_value=photos):result=c.detail(Context(),doc,c.SITE,{})
  self.assertIn('front.jpg',result);self.assertIn('back.jpg',result);self.assertIn('size-info.jpg',result)
  self.assertLess(result.index('back.jpg'),result.index('size-info.jpg'))
  self.assertNotIn('/aw/R267B0101A.jpg',result);self.assertNotIn('notice_2021',result);self.assertNotIn('awk_brand.jpg',result);self.assertNotIn('<table',result)
 def test_body_transport_errors_are_not_mislabeled_as_deleted_files(self):
  with patch.object(c,'picture',side_effect=c.CrawlError('HTTP503')),self.assertRaises(c.CrawlError):c.detail(Context(),c.parse(RAW),c.SITE,{})

if __name__=='__main__':unittest.main()
