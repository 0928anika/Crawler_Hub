import sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/x) for x in ('app','brands')]
import tmak_janecourt as c
class Tests(unittest.TestCase):
 def setUp(self):self.source=(Path(__file__).resolve().parent/'fixtures/tmak_janecourt/GNA2402.html').read_text(encoding='utf-8');self.doc=c.soup(self.source)
 def test_identity_from_product_name_not_required_model_field(self):
  self.assertEqual(c.identity(self.doc,'GNA2402')[0],'아이스 슬러시 쇼츠');self.assertIsNone(c.identity(self.doc,'GNA24020'))
 def test_actual_five_colors_and_soldout_sizes_are_preserved(self):
  colors=c.palette(self.source)
  self.assertEqual([x['color_name'] for x in colors],['베이지','블랙','카키','블루','핑크'])
  self.assertTrue(all(x['sizes']==['M','L','XL'] for x in colors))
 def test_product_fabric_size_images_retained_without_common_banners(self):
  urls=c.detail_urls(self.doc,c.SITE,'GNA2402')
  self.assertTrue(any('GNA2402_size.jpg' in u for u in urls));self.assertTrue(any('ICE_SLUSH.jpg' in u for u in urls))
  self.assertFalse(any('/main/00.jpg' in u or 'delivery.jpg' in u for u in urls))
 def test_one_plus_one_is_not_merged_with_single_product(self):
  self.assertTrue(c.bundle_page('',c.soup('<div class="infoArea"><h2>누디소프트 4부 레깅스 1&1 GNA2401</h2></div>')))
  self.assertFalse(c.bundle_page('',c.soup('<div class="infoArea"><h2>누디소프트 4부 레깅스 GNA2401</h2><div>추천 양말 1&1</div></div>')))
 def test_english_zero_count_is_not_access_error(self):
  source='<div class="searchResult">TOTAL 0 ITEMS</div><div class="noData">검색결과가 없습니다.</div>'
  with patch.object(c,'fetch',return_value=source):self.assertEqual(c.search(Mock(),'GNA2102'),[])
  with patch.object(c,'fetch',return_value='<html>접근 제한</html>'):
   with self.assertRaisesRegex(c.CrawlError,'검색 0건 아님'):c.search(Mock(),'GNA2102')
 def test_multicolor_without_official_image_links_uses_manual_mapping(self):
  ctx=Mock(product_code='GNA2402');ctx.enabled.side_effect=lambda k:k in ('size','price','regular_price','main_image','detail_page')
  with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=1875']),patch.object(c,'fetch',return_value=self.source),patch.object(c,'gallery',return_value=['https://www.janecourt.com/photo.jpg']),patch.object(c,'detail',return_value='<p>본문</p>'):
   c.crawl(ctx)
  args=ctx.emit_unmapped.call_args.args
  self.assertEqual(args[0]['product_name'],'아이스 슬러시 쇼츠');self.assertEqual(args[0]['price'],24500);self.assertEqual(args[0]['regular_price'],49000);self.assertEqual(len(args[1]),5)
  ctx.emit_result.assert_not_called()
if __name__=='__main__':unittest.main()
