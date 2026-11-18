import sys,json,copy,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import ilodilo_eland as c
FIX=ROOT/'tests/fixtures/ilodilo/product.json'
def data():return json.loads(FIX.read_text('utf-8'))
LINK=c.SITE+'/i/item?itemNo=2605417451&lowerVendNo='+c.VENDOR_NO
class C:
 product_code='IDTJ2650Z'
 def __init__(self,fields=()):self.fields=fields;self.rows=[];self.errors=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return k in self.fields
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,*a,**kw):self.rows.append((a,kw))
def photos(url='https://item.elandrs.com/studio.jpg',safe=True):
 p=c.GalleryPhotos();p.append((url,url));p.detail_safe=safe;return p
class IlodiloTests(unittest.TestCase):
 def test_official_vendor_excludes_every_other_vendor(self):
  other=LINK.replace(c.VENDOR_NO,'OTHER')
  with patch.object(c,'search_site',return_value=c.SearchResults([other,LINK],2,[])):self.assertEqual(c.search(C(),'IDTJ2650Z'),[LINK])
 def test_absent_official_vendor_returns_all_candidates(self):
  links=[LINK.replace(c.VENDOR_NO,'OTHER1'),LINK.replace(c.VENDOR_NO,'OTHER2')]
  with patch.object(c,'search_site',return_value=c.SearchResults(links,2,[])):self.assertEqual(c.search(C(),'IDTJ2650Z'),links)
 def test_exact_model_and_brand_validation(self):
  doc=c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=data()))+'</script>','html.parser')
  with patch.object(c,'page',return_value=doc):
   self.assertIsNotNone(c.product(C(),LINK,'IDTJ2650Z'));self.assertIsNone(c.product(C(),LINK,'IDTJ2650'))
 def test_links_with_valid_studio_photos_remain_when_closeup_is_rejected(self):
  a=('a',data(),photos());b=('b',data(),photos('b'));mixed=('m',data(),photos(safe=False))
  self.assertEqual(c.choose_products([a,mixed,b]),[a,mixed,b]);self.assertEqual(c.choose_products([mixed]),[mixed])
 def test_body_has_original_and_size_without_common_banner(self):
  with patch.object(c,'image_info',return_value=('JPEG',1000,3000,'hash',True)):
   urls=c.detail_images(C(),data(),'IDTJ2650Z',{})
  self.assertEqual(urls,['https://img-studio.elandmall.com/Winstudio/IDTJ2650Z.jpg','https://img-studio.elandmall.com/Winstudio/ID23-SIZE.jpg'])
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
  self.assertTrue(c.has_original_body(d,'IDTJ2650Z'))
  with patch.object(c,'image_info',return_value=('JPEG',1400,3000,'hash',False)):
   urls=c.detail_images(C(),d,'IDTJ2650Z',{},original_only=True)
  self.assertEqual(urls,['https://img-studio.elandmall.com/Winstudio/IDTJ2650Z.jpg','https://img-studio.elandmall.com/Winstudio/ID23-SIZE.jpg'])
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
 def test_branch_and_location_suffixes_without_jeom_are_removed(self):
  cases=[('베이직봄버점퍼IDJA2482Z_NC대전유성','IDJA2482Z','베이직봄버점퍼'),('모던체크코트 IDJC2510Z_NC일산','IDJC2510Z','모던체크코트'),('커브드 데님 점퍼 IDJK2580Z_NC유성','IDJK2580Z','커브드 데님 점퍼'),('포근 니트 대전 유성','IDCK25W2Z','포근 니트'),('포근 니트 (일산)','IDCK25W2Z','포근 니트'),('포근 니트 [NC 대전 유성]','IDCK25W2Z','포근 니트'),('포근 니트 IDCK25W2Z_09','IDCK25W2Z','포근 니트'),('포근 니트 (주니어) IDCK25W2Z','IDCK25W2Z','포근 니트 (주니어)')]
  for raw,code,expected in cases:
   with self.subTest(raw=raw):self.assertEqual(c.clean_name(raw,code),expected)
if __name__=='__main__':unittest.main()
