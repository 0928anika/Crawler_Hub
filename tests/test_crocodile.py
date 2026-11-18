import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import crocodile_official as c
class Tests(unittest.TestCase):
 def test_trailing_two_digit_color_codes_and_size_separation(self):
  for source,result in [('화이트 90',('화이트','90')),('차콜 98',('차콜','98')),('다크 네이비 78',('다크 네이비','78')),('WHITE 90',('WHITE','90')),('블랙(BK)',('블랙','BK')),('(39)Ivory',('Ivory','39')),('90 화이트',('화이트','90')),('95',('95','')),('오프화이트',('오프화이트',''))]:
   self.assertEqual(c.split_color(source),result)
 def test_known_broken_host_uses_verified_official_path_only(self):
  base='https://dawnfieldmall.com/product/detail.html?product_no=8400';path='/product_images/2025/c/CDAABJK4101/size.jpg'
  self.assertEqual(c.absolute(base,'https://dfamall1..afe24.com'+path),c.CONFIG['site']+path)
  self.assertEqual(c.absolute(base,'https://example.com'+path),'https://example.com'+path)
  self.assertEqual(c.absolute(base,'https://dfamall1.cafe24.com'+path),'https://dfamall1.cafe24.com'+path)
 def test_real_product_color_and_detail_size_image_are_preserved(self):
  raw=(ROOT/'tests/fixtures/crocodile/CDAABJK4101.html').read_text('utf-8');doc=c.soup(raw);found=c.identity(doc,raw,'CDAABJK4101',c.info_rows(doc));self.assertIsNotNone(found)
  colors=c.page_colors(doc,raw,{});self.assertEqual(c.split_color(colors[0][0]),('차콜','98'));self.assertEqual(colors[0][1]['sizes'],['95','100','105','110'])
  images,_,_=c.detail_parts(None,doc,c.CONFIG['site']+'/product/detail.html?product_no=8400',found[1]);self.assertEqual(len(images),5);self.assertIn(c.CONFIG['site']+'/product_images/2025/c/CDAABJK4101/size.jpg',images);self.assertFalse(any('..afe24' in u for u in images))
if __name__=='__main__':unittest.main()
