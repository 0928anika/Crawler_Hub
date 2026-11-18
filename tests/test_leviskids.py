import importlib.util,sys,unittest,io
from pathlib import Path
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('leviskids_test',ROOT/'brands/leviskids_official.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/leviskids'
def source():return c.BeautifulSoup((FIX/'product.html').read_bytes(),'html.parser')
class C:
 product_code='V263JK135P'
 def __init__(self,fields=()):self.fields=fields;self.rows=[];self.failures=[];self.logs=[]
 def enabled(self,k):return k in self.fields
 def stage(self,*a):pass
 def checkpoint(self):pass
 def log(self,*args):self.logs.append(args)
 def emit_result(self,r):self.rows.append(r)
 def fail_item(self,m):self.failures.append(m)
class LevisKidsTests(unittest.TestCase):
 def test_identity_and_disabled_sizes(self):
  row=c.identity(source(),'V263JK135P');self.assertEqual((row['color_code'],row['product_name']),('GN','리버서블 우븐 데님자켓 (토들러)'))
  self.assertEqual(c.sizes(source(),'GN'),'110,120,130')
  self.assertEqual(list(c.color_links(source(),'V263JK135P')),['GN'])
  with self.assertRaises(ValueError):c.identity(source(),'OTHER123')
 def test_size_tab_keeps_only_chart_image(self):
  with patch.object(c,'fetch',return_value=(c.BeautifulSoup((FIX/'size.html').read_bytes(),'html.parser'),c.SITE)) as request,patch.object(c,'checked',return_value=True) as loader:
   urls=c.size_images(C(),source(),'V263JK135P','GN',{})
  self.assertEqual(len(urls),1);self.assertIn('/Upload/editor/',urls[0]);self.assertNotIn('info_size_',urls[0]);self.assertEqual(loader.call_count,1);self.assertEqual(request.call_args.kwargs['method'],'POST')
 def test_notice_only_uses_product_notice_section(self):
  result=c.notice_table(C(),source());self.assertIn('제품소재',result);self.assertNotIn('상품구분',result);self.assertEqual(len(c.BeautifulSoup(result,'html.parser').select('tr')),14)
  self.assertEqual(c.notice_table(C(),c.BeautifulSoup('','html.parser')),'')
 def test_absent_size_data_does_not_fail(self):
  with patch.object(c,'fetch',side_effect=AssertionError('no button')):self.assertEqual(c.size_images(C(),c.BeautifulSoup('','html.parser'),'V263JK135P','GN',{}),[])
 def test_prices_are_independent(self):
  ctx=C();d=source();self.assertEqual(c.prices(ctx,d,'GN'),(151200,189000))
  d.select_one('.org_price').decompose();self.assertEqual(c.prices(ctx,d,'GN'),(151200,None))
 def test_unselected_images_and_detail_make_no_requests(self):
  ctx=C(['size'])
  with patch.object(c,'fetch',return_value=(source(),c.SITE+'/goods/detail/form?goodsCd=V263JK135P')),patch.object(c,'checked',side_effect=AssertionError('unselected image')),patch.object(c,'size_images',side_effect=AssertionError('unselected detail')):c.crawl(ctx)
  self.assertFalse(ctx.failures);self.assertEqual(ctx.rows[0]['size'],'110,120,130');self.assertNotIn('price',ctx.rows[0])
 def test_actual_composite_cover_is_excluded(self):
  output=io.BytesIO();Image.new('RGB',(600,780),'white').save(output,'JPEG');good=output.getvalue()
  def check(ctx,url,cache,detail=False):return {'data':(FIX/'composite.jpg').read_bytes() if '_01.' in url else good}
  with patch.object(c,'checked',side_effect=check):main,extras,_=c.gallery(C(),c.product_images(source(),'V263JK135P','GN'),'V263JK135P','GN',{})
  self.assertIn('_02.',main);self.assertEqual(len(extras),5)
 def test_official_pb_color_can_link_db_image_filenames(self):
  d=c.BeautifulSoup((FIX/'V263JK337P.html').read_bytes(),'html.parser')
  self.assertEqual(c.identity(d,'V263JK337P')['color_code'],'PB')
  urls=c.product_images(d,'V263JK337P','PB')
  self.assertEqual(len(urls),6);self.assertTrue(all('V263JK337P_DB_' in u for u in urls))
  self.assertEqual(c.product_images(d,'V263JK337P','BK'),[])
 def test_missing_gallery_links_are_not_reported_as_bad_dimensions(self):
  with self.assertRaisesRegex(ValueError,'갤러리 주소'):c.gallery(C(),[],'V263JK337P','PB',{})
if __name__=='__main__':unittest.main()
