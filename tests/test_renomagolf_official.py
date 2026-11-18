import sys,unittest
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import renomagolf_official as c
class C:
 def checkpoint(self):pass
 def log(self,*a):pass
class GalleryTests(unittest.TestCase):
 def test_actual_cdn_photos_for_both_colors(self):
  for color in ('100','199'):
   d=BeautifulSoup((ROOT/f'tests/fixtures/renomagolf_official/RMAKO3834-{color}.html').read_text(encoding='utf-8'),'html.parser')
   with patch.object(c,'image_info',side_effect=lambda ctx,url,cache:('JPEG',1000,1000,False,url,'')) as fetch:
    main,extras=c.gallery(C(),d,c.SITE,{})
   self.assertTrue(main.startswith('https://cafe24img.poxo.com/hansungimall/web/product/big/'))
   self.assertEqual(len(extras.split(',')),5)
   self.assertTrue(all('/extra/big/' in u for u in extras.split(',')))
   self.assertEqual(fetch.call_count,6)
 def test_legacy_host_still_accepted(self):
  self.assertEqual(c.gallery_url('/web/product/big/test.jpg',c.SITE),c.SITE+'/web/product/big/test.jpg')
 def test_only_official_shop_and_original_big_images(self):
  for u in ('https://cafe24img.poxo.com/other/web/product/big/a.jpg','https://evil.test/hansungimall/web/product/big/a.jpg','https://cafe24img.poxo.com/hansungimall/web/product/small/a.jpg','https://hansungmall.net/web/upload/banner.jpg','https://cafe24img.poxo.com/hansungimall/web/product/big/../a.jpg'):
   self.assertFalse(c.gallery_url(u,c.SITE),u)
 def test_missing_gallery_is_failure_instead_of_blank_success(self):
  with self.assertRaisesRegex(c.CrawlError,'수집 미완료'):c.gallery(C(),BeautifulSoup('<div class="imgArea"></div>','html.parser'),c.SITE,{})
 def test_floral_product_photos_are_not_text_labels(self):
  from PIL import Image
  for i in (0,1):
   with Image.open(ROOT/f'tests/fixtures/renomagolf_official/RWTH67109_pattern_{i}.jpg') as image:
    self.assertEqual(c.content_signal(image),'')
 def test_wide_repeated_text_rows_are_still_rejected(self):
  from PIL import Image,ImageDraw
  image=Image.new('RGB',(480,480),'white');draw=ImageDraw.Draw(image)
  for y in range(30,430,20):
   for x in range(30,450,10):draw.rectangle((x,y,x+2,y+4),fill='black')
  self.assertIn('작은 글자',c.content_signal(image))
if __name__=='__main__':unittest.main()
