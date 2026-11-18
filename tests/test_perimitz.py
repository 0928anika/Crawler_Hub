import sys,json,copy,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import perimitz_eland as c
FIX=ROOT/'tests/fixtures/perimitz'
def data():return json.loads((FIX/'product.json').read_text('utf-8'))
LINK=c.SITE+'/i/item?itemNo=2609595250&lowerVendNo=LV23003771'
class C:
 product_code='P2632B310'
 def __init__(self,fields=()):self.fields=fields;self.rows=[];self.errors=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return k in self.fields
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,*a,**kw):self.rows.append((a,kw))
def photos(clean=True):
 p=c.GalleryPhotos();p.append(('https://item.elandrs.com/studio.jpg','hash'));p.detail_safe=clean;return p
class PerimitzTests(unittest.TestCase):
 def test_official_seller_priority_and_all_seller_fallback(self):
  other=LINK.replace(c.VENDOR_NO,'OTHER')
  with patch.object(c,'search_site',return_value=c.SearchResults([other,LINK],2,[])):self.assertEqual(c.search(C(),'P2632B310'),[LINK])
  with patch.object(c,'search_site',return_value=c.SearchResults([other],1,[])):self.assertEqual(c.search(C(),'P2632B310'),[other])
 def test_exact_base_model_allows_observed_color_suffix(self):
  doc=c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=data()))+'</script>','html.parser')
  with patch.object(c,'page',return_value=doc):self.assertIsNotNone(c.product(C(),LINK,'P2632B310'));self.assertIsNone(c.product(C(),LINK,'P2632B31'))
 def test_clean_products_win_and_single_mixed_is_only_fallback(self):
  a=('mixed',data(),photos(False));b=('clean',data(),photos(True))
  self.assertEqual(c.choose_products([a,b]),[b]);self.assertEqual(c.choose_products([a]),[a]);self.assertEqual(c.choose_products([a,('mixed2',data(),photos(False))]),[])
 def test_common_banners_never_requested_only_model_images_kept(self):
  with patch.object(c,'image_info',return_value=('JPEG',1000,3000,'h',False)) as inspect:urls=c.detail_images(C(),data(),'P2632B310',{})
  self.assertEqual(len(urls),2);self.assertTrue(urls[0].endswith('P2632B310_M.jpg'));self.assertTrue(urls[1].endswith('P2632B310_page.jpg'));self.assertTrue(all('the8ight' not in x.args[1] and '_title.' not in x.args[1] for x in inspect.call_args_list))
 def test_single_mixed_detail_removes_known_seller_photo_and_keeps_named_size(self):
  d=data();d['itemDesc']['itemDescdocumentCon']='<img src="https://item.elandrs.com/seller.jpg"><img src="https://item.elandrs.com/studio.jpg"><img src="https://item.elandrs.com/size.jpg">'
  p=photos(False);p.rejected_urls={'https://item.elandrs.com/seller.jpg'}
  def inspect(ctx,u,cache,detail=False):return ('JPEG',1000,3000 if 'size' in u else 1400,u,'studio' in u)
  with patch.object(c,'image_info',side_effect=inspect):urls=c.detail_images(C(),d,'P2632B310',{},mixed_gallery=p)
  self.assertEqual(urls,['https://item.elandrs.com/studio.jpg','https://item.elandrs.com/size.jpg'])
 def test_multiple_clean_links_merge_options_photos_but_one_body(self):
  ctx=C(['main_image','detail_page']);links=[LINK,LINK.replace('2609595250','2')];d=data();d2=copy.deepcopy(d);d2['itemMain']['itemNo']='2'
  p1=photos();p2=photos();p2[0]=('https://item.elandrs.com/studio2.jpg','hash2')
  def options(ctx,main,parent=None):return [dict(itemOptionTcode='10',itemOptionName='09 BROWN' if str(main['itemNo'])!='2' else '10 WHITE',itemOptionNo='1')]
  with patch.object(c,'search',return_value=links),patch.object(c,'product',side_effect=[d,d2]),patch.object(c,'gallery',side_effect=[p1,p2]),patch.object(c,'options',side_effect=options),patch.object(c,'detail_images',return_value=['body']) as detail:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);self.assertEqual(len(ctx.rows[0][0][1]),2);self.assertEqual(detail.call_count,1);self.assertEqual(ctx.rows[0][1]['shared_detail_html'].count('<img'),1)
 def test_single_mixed_uses_filtered_body_mode(self):
  ctx=C(['main_image','detail_page']);p=photos(False)
  with patch.object(c,'search',return_value=[LINK]),patch.object(c,'product',return_value=data()),patch.object(c,'gallery',return_value=p),patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='브라운',itemOptionNo='1')]),patch.object(c,'detail_images',return_value=[]) as detail:c.crawl(ctx)
  self.assertIs(detail.call_args.kwargs['mixed_gallery'],p);self.assertTrue(ctx.rows);self.assertEqual(ctx.rows[0][1]['shared_detail_html'],'')
 def test_product_title_cleanup(self):self.assertEqual(c.clean_name(data()['itemMain']['itemName'],'P2632B310'),'올리비안 체크 셔츠')
if __name__=='__main__':unittest.main()
