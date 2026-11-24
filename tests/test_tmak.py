import sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/x) for x in ('app','brands')]
import tmak_official as c
class Tests(unittest.TestCase):
 def setUp(self):self.source=(Path(__file__).resolve().parent/'fixtures/tmak/PKF0112_BL.html').read_text(encoding='utf-8');self.doc=c.soup(self.source)
 def test_official_model_without_code_in_name(self):
  self.assertEqual(c.identity(self.doc,'PKF0112')[0],'여성 피트니스 원피스');self.assertIsNone(c.identity(self.doc,'PKF01120'))
 def test_combined_color_size_stock_is_separated(self):
  result=c.palette(self.source)
  self.assertEqual(result[0]['color_name'],'BL');self.assertEqual(result[0]['sizes'],['90','95','100','105'])
  self.assertEqual(set(result[0]['prices']),{69300})
 def test_lazy_product_body_and_size_kept_without_common_banners(self):
  urls=c.detail_urls(self.doc,c.SITE,'PKF0112')
  self.assertTrue(any('PKF-0112_BL_m.jpg' in u for u in urls));self.assertTrue(any('tms_sizeWOMEN.jpg' in u for u in urls))
  self.assertFalse(any('/main/00' in u or 's_guide' in u for u in urls))
 def test_single_color_page_is_automatic(self):
  ctx=Mock(product_code='PKF0112');ctx.enabled.side_effect=lambda k:k in ('size','price','regular_price','main_image','additional_images','detail_page')
  with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=1798']),patch.object(c,'fetch',return_value=self.source),patch.object(c,'gallery',return_value=['https://www.tmakstore.com/photo.jpg']),patch.object(c,'detail',return_value='<p>본문</p>'):
   c.crawl(ctx)
  row=ctx.emit_result.call_args.args[0];self.assertEqual(row['color_code'],'BL');self.assertEqual(row['size'],'90,95,100,105');self.assertEqual(row['price'],69300);self.assertEqual(row['regular_price'],99000)
  ctx.emit_unmapped.assert_not_called()
if __name__=='__main__':unittest.main()
