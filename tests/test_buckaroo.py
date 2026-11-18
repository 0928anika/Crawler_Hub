import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import buckaroo_official as c
from test_leviskids import C
FIX=ROOT/'tests/fixtures/buckaroo/B254Z1020P_DH.html'
def source():return c.BeautifulSoup(FIX.read_bytes(),'html.parser')
class Tests(unittest.TestCase):
 def test_identity_colors_and_brand_scope(self):
  doc=source();r=c.identity(doc,'B254Z1020P');self.assertEqual((r['product_name'],r['color_code']),('기모 쭈리 후드 티셔츠','DH'));self.assertEqual(set(c.color_links(doc,'B254Z1020P')),{'DH','1M','BK','WI'})
  self.assertTrue(c.sizes(doc,'DH'));self.assertEqual(c.prices(C(),doc,'DH'),(22900,69000))
  doc=c.BeautifulSoup(str(doc).replace('BUCKAROO','OTHER'),'html.parser')
  with self.assertRaisesRegex(ValueError,'브랜드'):c.identity(doc,'B254Z1020P')
 def test_declared_z_images_are_exact_color_candidates(self):
  urls=c.product_images(source(),'B254Z1020P','DH');self.assertEqual(len(urls),5);self.assertTrue(all('_DH_Z' in u for u in urls))
  self.assertEqual(c.product_images(source(),'B254Z1020P','BK'),[])
 def test_original_body_and_size_image_without_size_layer(self):
  doc=source();wrap=doc.select_one('.pd-core__wrap');wrap.append(c.BeautifulSoup('<img src="https://image.style24.com/Upload/editor/size-table.jpg">','html.parser'))
  with patch.object(c,'fetch',side_effect=AssertionError('size layer must not be requested')),patch.object(c,'checked',return_value=True):markup=c.detail(C(),doc,'B254Z1020P','DH',[],{})
  self.assertIn('size-table.jpg',markup);self.assertIn('_DH_A2.jpg',markup);self.assertIn('_DH_B5.jpg',markup);self.assertIn('상품고시정보',markup);self.assertIn('제품소재',markup);self.assertNotIn('N254JP102P',markup);self.assertNotIn('BRAND_1145',markup)
 def test_missing_notice_is_allowed(self):
  doc=source()
  for n in doc.select('.area_infotbl'):n.decompose()
  with patch.object(c,'checked',return_value=True):markup=c.detail(C(),doc,'B254Z1020P','DH',[],{})
  self.assertNotIn('<table',markup);self.assertIn('<img',markup)
 def test_metadata_only_never_fetches_images(self):
  ctx=C(['size']);ctx.product_code='B254Z1020P_DH'
  with patch.object(c,'fetch',return_value=(source(),c.SITE+'/goods/detail/form?goodsCd=B254Z1020P&colorCd=DH')),patch.object(c,'checked',side_effect=AssertionError('image disabled')):c.crawl(ctx)
  self.assertEqual(len(ctx.rows),1);self.assertEqual(ctx.rows[0]['color_code'],'DH');self.assertFalse(ctx.failures)
if __name__=='__main__':unittest.main()
