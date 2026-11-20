import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import nba_official as c
class Ctx:
 product_code='N264TS272P'
 def __init__(self):self.rows=[];self.failures=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return k in ('size','price','main_image','additional_images','detail_page')
 def fail_item(self,m):self.failures.append(m)
 def emit_result(self,r):self.rows.append(r)
class Tests(unittest.TestCase):
 def setUp(self):
  self.doc=c.BeautifulSoup((ROOT/'tests/fixtures/nba/N264TS272P.html').read_bytes(),'html.parser');self.popup=c.BeautifulSoup((ROOT/'tests/fixtures/nba/size.html').read_bytes(),'html.parser')
 def test_registration_and_brand_boundary(self):
  from registry import get_brand
  self.assertTrue(get_brand('nba_official')['available']);self.assertEqual(c.BRAND_GROUP,'2141');self.assertEqual(c.identity(self.doc,c.SITE)[1],'N264TS272P')
  for a in self.doc.select('.brand_box a[href]'):a['href']=a['href'].replace('2141','2143')
  with self.assertRaises(c.CrawlError):c.identity(self.doc,c.SITE)
 def test_official_colour_codes_and_labels(self):
  self.assertEqual([x[0] for x in c.color_links(self.doc,c.SITE,'N264TS272P')],['LA','PI'])
  self.assertEqual(c.english_color(self.doc,c.SITE,'N264TS272P','PI'),'PINK')
 def test_womens_top_standard_size_only(self):
  with patch.object(c,'page',return_value=(self.popup,c.SITE)),patch.object(c,'image_info',return_value=True):markup=c.size_info(Ctx(),self.doc,c.SITE,'N264TS272P','PI',{})
  self.assertIn('여성 상의',markup);self.assertNotIn('여성 하의',markup);self.assertNotIn('남성 상의',markup);self.assertIn('가슴둘레',markup)
 def test_notice_is_preserved(self):
  markup=c.notice(self.doc);self.assertIn('<table',markup);self.assertIn('상품고시정보',markup)
 def test_galleries_fill_minimum_and_limit_nine(self):
  for count in [1,3,14]:
   urls=['https://image.style24.com/'+str(i)+'.jpg' for i in range(count)]
   with patch.object(c,'image_urls',return_value=urls),patch.object(c,'image_info',side_effect=lambda ctx,url,cache:('JPEG',800,1000,False,url,'')):
    main,extra=c.gallery(Ctx(),self.doc,c.SITE,{})
   self.assertTrue(5<=len(extra.split(','))<=9);self.assertEqual(main,urls[0])
 def test_crawl_all_colours_keeps_soldout_sizes(self):
  for n in self.doc.select('input[name="opt"]'):n['disabled']='disabled'
  ctx=Ctx();expected=','.join(dict.fromkeys(n.get('optcd2','') for n in self.doc.select('input[name="opt"][optcd2]')))
  with patch.object(c,'page',side_effect=lambda ctx,url:(self.doc,url)),patch.object(c,'identity',side_effect=lambda doc,url:(c.structured(doc,'ProductGroup'),'N264TS272P','LA' if 'colorCd=LA' in url else 'PI')),patch.object(c,'gallery',return_value=('https://image.style24.com/main.jpg',','.join(['https://image.style24.com/main.jpg']*5))),patch.object(c,'detail',return_value='<p>product</p>'):
   c.crawl(ctx)
  self.assertFalse(ctx.failures);self.assertEqual(len(ctx.rows),2);self.assertEqual({r['color_code'] for r in ctx.rows},{'LA','PI'});self.assertTrue(all(r['size']==expected for r in ctx.rows))
 def test_denim_straight_category_keeps_womens_bottom_size_table(self):
  doc=c.BeautifulSoup((ROOT/'tests/fixtures/nba/N254DP395P.html').read_bytes(),'html.parser');popup=c.BeautifulSoup((ROOT/'tests/fixtures/nba/N254DP395P-size.html').read_bytes(),'html.parser')
  with patch.object(c,'page',return_value=(popup,c.SITE)),patch.object(c,'image_info',return_value=True):markup=c.size_info(Ctx(),doc,c.SITE,'N254DP395P','ID',{})
  self.assertIn('여성 하의',markup);self.assertIn('허리둘레',markup);self.assertIn('<table',markup);self.assertNotIn('남성 하의',markup);self.assertNotIn('여성 상의',markup)
  self.assertEqual(c.size_family('여성 > 데님 > 일자/스트레이트')[0],'하의');self.assertEqual(c.size_family('데님 자켓')[0],'상의')
 def test_resized_image_cache_reuses_verified_url_and_bytes_check(self):
  import io
  from PIL import Image
  output=io.BytesIO();Image.new('RGB',(800,1000),'white').save(output,format='JPEG');url='https://image.style24.com/Upload/ProductImage/a/item_ID_01.jpg/gio/thumbnail/3150';other=url.rsplit('/',1)[0]+'/1080';cache={}
  with patch.object(c,'fetch',return_value=(output.getvalue(),url,'image/jpeg')) as request,patch.object(c,'content_signal',return_value=''):
   c.image_info(Ctx(),url,cache);self.assertEqual(c.image_info(Ctx(),other,cache),cache[url])
  request.assert_called_once();self.assertEqual(c.verified_image_url(other,cache),url);self.assertNotEqual(c.image_asset(other),c.image_asset(other.replace('_ID_','_IV_')))
 def test_prefetch_is_bounded_to_four_unique_assets(self):
  import threading
  barrier=threading.Barrier(4);lock=threading.Lock();state={'active':0,'max':0}
  def load(ctx,url,cache):
   with lock:state['active']+=1;state['max']=max(state['max'],state['active'])
   try:barrier.wait(timeout=5);return True
   finally:
    with lock:state['active']-=1
  urls=['https://image.style24.com/Upload/ProductImage/photo'+str(i)+'.jpg' for i in range(8)]
  with patch.object(c,'image_info',side_effect=load) as images:c.prefetch_images(Ctx(),urls+[urls[0]+'/gio/thumbnail/1080'],{})
  self.assertEqual(images.call_count,8);self.assertEqual(state['max'],4)
 def test_unselected_image_work_never_prefetches(self):
  ctx=Ctx();ctx.enabled=lambda key:key=='size'
  with patch.object(c,'page',side_effect=lambda ctx,url:(self.doc,url)),patch.object(c,'identity',side_effect=lambda doc,url:(c.structured(doc,'ProductGroup'),'N264TS272P','LA' if 'colorCd=LA' in url else 'PI')),patch.object(c,'prefetch_images',side_effect=AssertionError('images disabled')):c.crawl(ctx)
  self.assertTrue(ctx.rows);self.assertFalse(ctx.failures)
if __name__=='__main__':unittest.main()
