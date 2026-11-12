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
 def __init__(self,fields=()):self.fields=set(fields);self.rows=[];self.errors=[]
 def enabled(self,key):return key in self.fields
 def checkpoint(self):return None
 def stage(self,*a):return None
 def log(self,*a):return None
 def fail_item(self,msg):self.errors.append(msg)
 def emit_result(self,row):self.rows.append(row)
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
 def test_mapping_failure_is_not_reported_as_missing_product(self):
  ctx=Context(['main_image'])
  with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=5157']),patch.object(c,'fetch',return_value=RAW),patch.object(c,'gallery',side_effect=c.CrawlError('색상별 사진 자동 연결 불가')):
   with self.assertRaisesRegex(c.CrawlError,'상품은 조회됐으나 수집 실패'):c.crawl(ctx)
  self.assertFalse(any('공식 자체상품코드와 일치하는 AWK 상품 없음' in e for e in ctx.errors))

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

if __name__=='__main__':unittest.main()
