import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('list_lotteon_test',ROOT/'brands/list_lotteon.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class C:
 def checkpoint(self):return None
 def log(self,*a):return None
class ListLotteTests(unittest.TestCase):
 def test_exact_requested_name(self):
  self.assertEqual(c.clean_name('[패세라]울 헤어리 투버튼 자켓_TWWJKP82030','TWWJKP82030'),'울 헤어리 투버튼 자켓')
  self.assertEqual(c.clean_name('[패세라] 리스트 울 헤어리 투버튼 자켓 TWWJKP82030','TWWJKP82030'),'울 헤어리 투버튼 자켓')
 def test_background_uniformity_is_independent_of_brightness(self):
  for color in ['white',(220,210,190),(45,65,85)]:
   pic=Image.new('RGB',(800,800),color);ImageDraw.Draw(pic).rectangle((230,100,570,700),fill='red')
   self.assertTrue(c.studio(pic)[0])
  pic=Image.new('RGB',(800,800))
  draw=ImageDraw.Draw(pic)
  for i in range(8):draw.rectangle((i*100,0,i*100+99,799),fill=(i*30,70,220-i*25))
  self.assertFalse(c.studio(pic)[0])
 def test_search_retains_list_with_overlay_and_excludes_other_brand(self):
  rows=[{'key':'1','pdId':'1','pdName':'[패세라]울 헤어리 투버튼 자켓_TWWJKP82030','brandId':'P1170','emblemInfo':{'emblemName':'행사'}},{'key':'2','pdId':'2','pdName':'자켓_TWWJKP82030','brandId':'OTHER'}]
  with patch.object(c,'json_get',return_value={'itemList':rows,'total':2}):result=c.search(C(),'TWWJKP82030')
  self.assertEqual([r['pdId'] for r in result],['1'])
 def test_original_size_notice_image_kept_without_generated_table(self):
  source='<div id="llis-root"><img src="https://example.com/body.jpg"><p>상세 본문 원문</p></div>'
  with tempfile.TemporaryDirectory() as folder,patch.object(c,'fetch',return_value=source.encode()),patch.object(c,'image',return_value={'digest':'a','image':Image.new('RGB',(800,1800))}),patch.object(c,'ocr',return_value='제품 사이즈표 55 66 77 상품정보고시 소재 울 제조국'):
   markup=c.body_markup(C(),{'descInfo':{'epnJsn':[{'pdEpnTypCd':'DSCRP','dtlFileRteNm':'/LO/1/','dtlFileNm':'DSCRP_1'}]}},{},{},Path(folder))
  self.assertIn('body.jpg',markup);self.assertIn('상세 본문 원문',markup);self.assertNotIn('<table',markup)
 def test_deleted_detail_image_skips_only_404_and_keeps_body(self):
  source='<div id="llis-root"><img src="https://example.com/deleted.jpg"><img src="https://example.com/body.jpg"></div>'
  def picture(ctx,url,cache):
   if 'deleted' in url:raise c.MissingResource('404')
   return {'digest':url,'image':Image.new('RGB',(800,800))}
  with tempfile.TemporaryDirectory() as folder,patch.object(c,'fetch',return_value=source.encode()),patch.object(c,'image',side_effect=picture),patch.object(c,'ocr',return_value='상품 본문'):
   result=c.body_markup(C(),{'descInfo':{'epnJsn':[{'pdEpnTypCd':'DSCRP','dtlFileRteNm':'/LO/1/','dtlFileNm':'DSCRP_1'}]}},{},{},Path(folder))
  self.assertIn('body.jpg',result);self.assertNotIn('deleted.jpg',result)
 def test_group_product_requires_explicit_search_spd_link(self):
  row={'pdId':'PD1','spdId':'LE1','sitmId':'LE1_1'}
  data={'basicInfo':{'pdNo':'LE1','spdNo':'LE1','sitmNo':'LE1_1','brdNo':'P1170','pdNm':'자켓_TWWJKP82030'},'optionInfo':{'optionList':[{'title':'색상'}]}}
  with patch.object(c,'json_get',return_value=data):self.assertIs(c.load_product(C(),row,'TWWJKP82030'),data)
  wrong=dict(row,spdId='LE2')
  with patch.object(c,'json_get',return_value=data),self.assertRaises(c.CollectionError):c.load_product(C(),wrong,'TWWJKP82030')

 def test_automatic_query_correction_is_not_misreported_as_broken_api(self):
  source='<script>create({queryInfo: {"query":"ts","originalQuery":"TSDJPO82080","correctionKeyword":"ts"},suggestedKeyword:{isReSearch:true}})</script>'
  with patch.object(c,'fetch',side_effect=[b'',source.encode()]):self.assertEqual(c.search(C(),'TSDJPO82080'),[])
  with patch.object(c,'fetch',side_effect=[b'',source.encode()]),self.assertRaises(c.CollectionError):c.search(C(),'OTHER')
  with patch.object(c,'fetch',side_effect=[b'',b'<html>Temporary Error</html>']),self.assertRaises(c.CollectionError):c.search(C(),'TSDJPO82080')

 def test_deleted_seller_photo_uses_explicit_lotte_stored_image(self):
  item={'origImgFileNm':'https://seller.example/deleted.jpg','imgRteNm':'/20250522182429/LO/24/88/17/78/69/_2/48/81/77/87/0/','imgFileNm':'LO2488177869_2488177870_1.jpg'}
  expected=c.CDN+'/itemimage'+item['imgRteNm']+item['imgFileNm']
  good={'image':Image.new('RGB',(600,600)),'digest':'good'}
  with patch.object(c,'image',side_effect=[c.MissingResource('404'),good]) as load:
   source,info=c.gallery_image(C(),item,{})
  self.assertEqual(source,expected);self.assertIs(info,good);self.assertEqual(load.call_count,2)
  with patch.object(c,'image',side_effect=c.CollectionError('HTTP503')),self.assertRaises(c.CollectionError):c.gallery_image(C(),item,{})
 def test_halfclub_user_banner_is_recognized_by_local_ocr(self):
  with Image.open(ROOT/'tests/fixtures/list_lotteon/halfclub_banner.png') as pic,tempfile.TemporaryDirectory() as folder:
   words=c.ocr(C(),pic.convert('RGB'),Path(folder),{})
  self.assertRegex(words,c.BANNER)

if __name__=='__main__':unittest.main()
