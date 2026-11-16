import sys,json,copy,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import tdg_eland_studio as c
FIX=ROOT/'tests/fixtures/tdg_eland_studio/TGMA25T09_0.json'
def data():return json.loads(FIX.read_text('utf-8'))
LINK=c.SITE+'/i/item?itemNo=2511167056&lowerVendNo='+c.VENDOR_NO
class C:
 product_code='TGMA25T09'
 def __init__(self,fields=()):self.fields=fields;self.rows=[];self.errors=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return k in self.fields
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,*a,**kw):self.rows.append((a,kw))
def photos(url='https://item.elandrs.com/studio.jpg',safe=True):
 p=c.GalleryPhotos();p.append((url,url));p.detail_safe=safe;return p
class TdgStudioTests(unittest.TestCase):
 def test_verified_uploaded_bodies_keep_size_and_exclude_unknown_seller_images(self):
  for code,count in [('TGAK25A05',3),('TGBR25A01',2),('TGTJ25F02',3),('TGWR25A01',2)]:
   d=json.loads((FIX.parent/(code+'_0.json')).read_text('utf-8'))
   d['itemDesc']['itemDescdocumentCon']+='<img src="https://item.elandrs.com/unverified.jpg">'
   with patch.object(c,'image_info',return_value=('JPEG',1080,4000,'hash',False)):
    urls=c.detail_images(C(),d,code,{},original_only=True)
   self.assertEqual(len(urls),count)
   self.assertEqual(set(urls),set(c.VERIFIED_UPLOADED_BODIES[code]))
   self.assertFalse(c.original_detail_url(urls[0],'WRONGCODE'))
 def test_official_vendor_excludes_every_other_vendor(self):
  other=LINK.replace(c.VENDOR_NO,'OTHER')
  with patch.object(c,'search_site',return_value=c.SearchResults([other,LINK,LINK+'&duplicate=1'],3,[])):self.assertEqual(c.search(C(),'TGMA25T09'),[LINK])
 def test_absent_official_vendor_returns_all_candidates(self):
  links=[LINK.replace(c.VENDOR_NO,'OTHER1'),LINK.replace(c.VENDOR_NO,'OTHER2')]
  with patch.object(c,'search_site',return_value=c.SearchResults(links,2,[])):self.assertEqual(c.search(C(),'TGMA25T09'),links)
 def test_exact_model_and_brand_validation(self):
  doc=c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=data()))+'</script>','html.parser')
  with patch.object(c,'page',return_value=doc):
   self.assertIsNotNone(c.product(C(),LINK,'TGMA25T09'));self.assertIsNone(c.product(C(),LINK,'TGMA25T0'))
 def test_links_with_valid_studio_photos_remain_when_closeup_is_rejected(self):
  a=('a',data(),photos());b=('b',data(),photos('b'));mixed=('m',data(),photos(safe=False))
  self.assertEqual(c.choose_products([a,mixed,b]),[a,mixed,b]);self.assertEqual(c.choose_products([mixed]),[mixed])
 def test_body_has_original_and_size_without_common_banner(self):
  with patch.object(c,'image_info',return_value=('JPEG',1000,3000,'hash',True)):
   urls=c.detail_images(C(),data(),'TGMA25T09',{})
  self.assertEqual(urls,['https://img-studio.elandmall.com/Winstudio/TGMA25T09.jpg','https://img-studio.elandmall.com/Winstudio/TG25-SIZE-1.jpg'])
 def test_official_closeups_preserved_after_primary_studio_check(self):
  main=dict(lowerVendNo=c.VENDOR_NO,itemImage=[dict(imageDcode='010',imagePath='primary.jpg'),dict(imageDcode='030',imagePath='close.jpg')])
  def info(ctx,url,cache):return ('JPEG',1000,1000,url,'primary' in url)
  with patch.object(c,'image_info',side_effect=info):
   gallery=c.gallery(C(),main,{})
  self.assertEqual(len(gallery),2);self.assertTrue(gallery.detail_safe)
  main['lowerVendNo']='OTHER'
  with patch.object(c,'image_info',side_effect=info):gallery=c.gallery(C(),main,{})
  self.assertFalse(gallery.detail_safe)
 def test_photos_options_merged_but_only_one_body_link_used(self):
  ctx=C(['main_image','detail_page']);d=data();d2=copy.deepcopy(d);d2['itemMain']['itemNo']='2'
  def options(ctx,main,parent=None):return [dict(itemOptionTcode='10',itemOptionName='09 BLACK' if str(main['itemNo'])!='2' else '62 LIGHT BLUE',itemOptionNo='1')]
  with patch.object(c,'search',return_value=[LINK,'other']),patch.object(c,'product',side_effect=[d,d2]),patch.object(c,'gallery',side_effect=[photos(),photos('https://item.elandrs.com/second.jpg')]),patch.object(c,'options',side_effect=options),patch.object(c,'detail_images',return_value=['https://item.elandrs.com/body.jpg']) as detail:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);self.assertEqual(len(ctx.rows[0][0][1]),2);self.assertEqual(detail.call_count,1)
  self.assertEqual(ctx.rows[0][1]['shared_detail_html'],ctx.rows[1][1]['shared_detail_html'])
 def test_official_model_cover_does_not_hide_later_studio_packshots(self):
  main=dict(lowerVendNo=c.VENDOR_NO,itemImage=[dict(imageDcode='010',imagePath='model.jpg'),dict(imageDcode='030',imagePath='studio.jpg'),dict(imageDcode='030',imagePath='close.jpg')])
  def info(ctx,url,cache):return ('JPEG',1400,1400,url,'studio' in url)
  with patch.object(c,'image_info',side_effect=info):gallery=c.gallery(C(),main,{})
  self.assertEqual(len(gallery),3);self.assertTrue(gallery.detail_safe)
  main['lowerVendNo']='OTHER'
  with patch.object(c,'image_info',side_effect=info):gallery=c.gallery(C(),main,{})
  self.assertFalse(gallery.detail_safe)
 def test_failure_is_reported_only_once(self):
  ctx=C(['main_image'])
  with patch.object(c,'search',side_effect=c.CollectionError('검색 결과 없음')),self.assertRaises(c.CollectionError):c.crawl(ctx)
  self.assertEqual(ctx.errors,['검색 결과 없음'])
 def test_mixed_gallery_uses_known_original_body_only(self):
  d=data();d['itemDesc']['itemDescdocumentCon']+='<img src="https://item.elandrs.com/seller.jpg">'
  self.assertTrue(c.has_original_body(d,'TGMA25T09'))
  with patch.object(c,'image_info',return_value=('JPEG',1400,3000,'hash',False)):
   urls=c.detail_images(C(),d,'TGMA25T09',{},original_only=True)
  self.assertEqual(urls,['https://img-studio.elandmall.com/Winstudio/TGMA25T09.jpg','https://img-studio.elandmall.com/Winstudio/TG25-SIZE-1.jpg'])
 def test_empty_gallery_is_still_excluded(self):
  self.assertEqual(c.choose_products([('x',data(),c.GalleryPhotos())]),[])
 def test_rate_limit_waits_retry_after_before_retrying(self):
  from unittest.mock import MagicMock
  error=c.urllib.error.HTTPError('https://item.elandrs.com/x',429,'limited',{'Retry-After':'2'},None)
  response=MagicMock();response.__enter__.return_value=response;response.headers={'Content-Length':'2'};response.read1.side_effect=[b'ok',b'']
  opener=MagicMock();opener.open.side_effect=[error,response]
  with patch.object(c,'public_opener',return_value=opener),patch.object(c.time,'sleep') as pause:
   self.assertEqual(c.fetch(C(),'https://item.elandrs.com/x'),b'ok')
  self.assertEqual(opener.open.call_count,2);self.assertGreaterEqual(sum(x.args[0] for x in pause.call_args_list),1.99)
 def test_banner_named_official_size_image_is_preserved(self):
  d=data();d['itemDesc']['itemDescdocumentCon']=d['itemDesc']['itemDescdocumentCon'].replace('TG25-SIZE-1.jpg','TG25-SIZE-1BANNER.jpg')
  with patch.object(c,'image_info',return_value=('JPEG',1000,3000,'hash',False)):
   urls=c.detail_images(C(),d,'TGMA25T09',{})
  self.assertEqual(len(urls),2);self.assertTrue(urls[-1].endswith('TG25-SIZE-1BANNER.jpg'))
 def test_deleted_official_image_does_not_destroy_remaining_body(self):
  d=data()
  def info(ctx,url,cache,detail=False):
   if 'SIZE' in url:raise c.AccessDenied('HTTP 403')
   return ('JPEG',1000,3000,'hash',True)
  with patch.object(c,'image_info',side_effect=info):urls=c.detail_images(C(),d,'TGMA25T09',{})
  self.assertEqual(len(urls),1)
 def test_nonofficial_body_uses_only_one_original_link(self):
  d=data();d['itemMain']['lowerVendNo']='OTHER';d['itemDesc']['itemDescdocumentCon']+='<img src="https://item.elandrs.com/seller.jpg">'
  with patch.object(c,'image_info',return_value=('JPEG',1400,3000,'hash',False)):
   urls=c.detail_images(C(),d,'TGMA25T09',{},original_only=True)
  self.assertFalse(any('seller.jpg' in u for u in urls))
 def test_branch_name_removed(self):
  self.assertEqual(c.clean_name('힙해 트렌디 맨투맨 TGMA25T09_강서점','TGMA25T09'),'힙해 트렌디 맨투맨')
class DirectLinkTests(unittest.TestCase):
 def test_tracking_query_removed_and_ids_preserved(self):
  self.assertEqual(c.input_link('https://www.elandmall.co.kr/i/item?itemNo=2512217779&lowerVendNo=LV19015810&srsltid=tracking'),'https://www.elandmall.co.kr/i/item?itemNo=2512217779&lowerVendNo=LV19015810')
 def test_external_missing_and_ambiguous_links_rejected(self):
  for u in ('https://evil.test/i/item?itemNo=1&lowerVendNo=A','https://www.elandmall.co.kr/i/item?itemNo=1','https://www.elandmall.co.kr/i/item?itemNo=1&itemNo=2&lowerVendNo=A'):
   with self.assertRaises(c.CollectionError):c.input_link(u)
 def test_direct_link_skips_search_and_outputs_real_model(self):
  d=data();ctx=C(['main_image','detail_page']);ctx.product_code=LINK+'&srsltid=tracking'
  with patch.object(c,'search',side_effect=AssertionError('direct URL must not search')),patch.object(c,'product',return_value=d) as product,patch.object(c,'gallery',return_value=photos()),patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='09 BLACK',itemOptionNo='1')]),patch.object(c,'detail_images',return_value=['https://img-studio.elandmall.com/Winstudio/TGMA25T09.jpg']):c.crawl(ctx)
  self.assertEqual(product.call_count,1);self.assertIsNone(product.call_args.args[2])
  self.assertEqual(ctx.rows[0][0][0]['product_code'],'TGMA25T09');self.assertEqual(ctx.rows[0][0][0]['product_url'],LINK)
 def test_direct_wrong_brand_is_not_adopted(self):
  ctx=C(['main_image']);ctx.product_code=LINK
  with patch.object(c,'product',return_value=None),self.assertRaisesRegex(c.CollectionError,'더데이걸 상품이 아닙니다'):c.crawl(ctx)
if __name__=='__main__':unittest.main()
