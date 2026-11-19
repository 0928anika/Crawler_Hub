import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import organicmom_official as c
from crawler_sdk import CrawlContext
class Tests(unittest.TestCase):
 def setUp(self):
  self.source=(ROOT/'tests/fixtures/organicmom/MLZLVE07.html').read_text(encoding='utf8');self.doc=c.soup(self.source)
 def test_identity_and_registration(self):
  from registry import get_brand
  self.assertTrue(get_brand('organicmom_official')['available']);self.assertEqual(c.identity(self.doc,'MLZLVE07')[0],'티티조끼');self.assertIsNone(c.identity(self.doc,'MLZLVE070'))
 def test_no_guessed_color_code_and_all_soldout_sizes(self):
  with self.assertRaises(c.CrawlError):c.palette(self.source)
  expected=c.palette(self.source,'크림');self.assertEqual(expected[0]['color_name'],'크림');self.assertEqual(expected[0]['color_code'],'');self.assertEqual(expected[0]['sizes'],['90','100','110'])
  rows=c.stock_rows(self.source)
  for row in rows:row['is_auto_soldout']='T';row['is_selling']='F'
  with patch.object(c,'stock_rows',return_value=rows):self.assertEqual(c.palette(self.source,'크림'),expected)
 def test_ocr_extracts_only_explicit_color(self):
  self.assertEqual(c.colour_from_text('PRODUCT INFO 모델명 MLZLVE07 상품명 티티조끼 사이즈 90,100,110 색상 크림 소재 주원단 폴리에스터'),'크림')
  with self.assertRaises(c.CrawlError):c.colour_from_text('흰 옷 사진만 있고 색상 정보 없음 소재 면')
 def test_detail_excludes_banners_gif_recommendations(self):
  urls=c.detail_urls(self.doc,c.SITE,'MLZLVE07');self.assertEqual(len(urls),2)
  self.assertTrue(any('MLZLVE07.jpg' in u for u in urls));self.assertTrue(any('babychart.png' in u for u in urls));self.assertFalse(any('bottum' in u or '.gif' in u or 'top2' in u for u in urls))
 def test_gallery_uses_declared_big_images_once_and_nine_limit(self):
  urls=c.gallery_urls(self.doc,c.SITE);self.assertEqual(len(urls),5);self.assertTrue(all('/big/' in u for u in urls))
  self.assertEqual(len(c.extra_slots(['https://site/'+str(i) for i in range(14)]).split(',')),9)
 def test_sdk_success_and_optional_images(self):
  meta=json.loads((ROOT/'brands/organicmom_official.json').read_text(encoding='utf8'));events=[]
  with tempfile.TemporaryDirectory() as folder:
   ctx=CrawlContext('unused',Path(folder)/'out.xlsx',0,['size','price','regular_price','main_image','additional_images','detail_page'],meta['output_headers'],lambda:None,lambda k,**d:events.append((k,d)),input_item={'품번':'MLZLVE07'},mapping_mode='manual')
   with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=21883']),patch.object(c,'fetch',return_value=self.source),patch.object(c,'official_image_color',return_value='크림'),patch.object(c,'checked'),patch.object(c,'gallery',return_value=['https://organicmommall.co.kr/photo.jpg']):c.crawl(ctx);ctx.finish()
   row=next(d['row'] for k,d in events if k=='row' and not d.get('header'));r=dict(zip(meta['output_headers'],row));self.assertEqual(r['사이즈'],'90,100,110');self.assertEqual(r['색상명'],'크림');self.assertEqual(r['판매가'],31200);self.assertEqual(r['정상가'],39000);self.assertEqual(len(r['추가이미지'].split(',')),5)
if __name__=='__main__':unittest.main()
