import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import usall_eland_studio as c
URL=c.SITE+'/i/item?itemNo=2610598080&lowerVendNo='+c.VENDOR
class Context:
 product_code='ULTM26N02'
 def __init__(self):self.rows=[];self.errors=[];self.logs=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):self.logs.append(a)
 def enabled(self,k):return k=='main_image'
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,r,colors,url,shared_detail_html=''):self.rows.append((r,colors,url))
class Tests(unittest.TestCase):
 def setUp(self):self.data=json.loads((ROOT/'tests/fixtures/usall/ULTM26N02.json').read_text(encoding='utf-8'))
 def page(self,data=None):
  source='<script>const itemDetailJson = '+json.dumps({'itemDetail':self.data if data is None else data})+';</script>'
  return c.BeautifulSoup(source,'html.parser'),URL
 def empty(self):return c.BeautifulSoup('<html><title>융기모 카고 조거팬츠 ULTM26N02</title></html>','html.parser'),URL
 def test_live_fixture_exact_identity(self):
  with patch.object(c,'page',return_value=self.page()):d,url=c.product(Context(),URL,'ULTM26N02')
  self.assertEqual(d['itemMain']['modelNo'],'ULTM26N02');self.assertEqual(d['itemMain']['lowerVendNo'],c.VENDOR)
 def test_json_missing_twice_then_recovers_without_item_failure(self):
  ctx=Context()
  with patch.object(c,'page',side_effect=[self.empty(),self.empty(),self.page()]) as get:
   result=c.product(ctx,URL,'ULTM26N02')
  self.assertTrue(result);self.assertEqual(get.call_count,3);self.assertFalse(ctx.errors);self.assertEqual(len(ctx.logs),3)
 def test_missing_is_bounded_and_does_not_add_cascade_errors(self):
  ctx=Context()
  with patch.object(c,'search',return_value=[URL]),patch.object(c,'page',return_value=self.empty()) as get:
   with self.assertRaises(c.CrawlError):c.crawl(ctx)
  self.assertEqual(get.call_count,3);self.assertEqual(len(ctx.errors),1)
  self.assertEqual(ctx.errors[0],'공식 상품 JSON 없음: '+URL)
  self.assertFalse(ctx.rows)
 def test_malformed_json_is_not_retried(self):
  bad=c.BeautifulSoup('<script>const itemDetailJson = {broken;</script>','html.parser')
  with patch.object(c,'page',return_value=(bad,URL)) as get:
   with self.assertRaisesRegex(c.CrawlError,'파싱 실패'):c.product(Context(),URL,'ULTM26N02')
  self.assertEqual(get.call_count,1)
 def test_wrong_vendor_and_code_are_not_accepted_or_retried(self):
  for field,value in [('lowerVendNo','OTHER'),('modelNo','ULTM26N03')]:
   data=copy.deepcopy(self.data);data['itemMain'][field]=value
   with patch.object(c,'page',return_value=self.page(data)) as get:self.assertIsNone(c.product(Context(),URL,'ULTM26N02'))
   self.assertEqual(get.call_count,1)
 def test_wrong_item_identity_fails_without_retry(self):
  data=copy.deepcopy(self.data);data['itemMain']['itemNo']='999'
  with patch.object(c,'page',return_value=self.page(data)) as get:
   with self.assertRaisesRegex(c.CrawlError,'상품 ID 불일치'):c.product(Context(),URL,'ULTM26N02')
  self.assertEqual(get.call_count,1)
 def test_recovery_emits_mapping_candidates(self):
  ctx=Context();main=self.data['itemMain'];image=next(x for x in main['itemImage'] if x.get('imagePath'))
  options=[dict(itemOptionTcode='10',itemOptionName='05 MELANGE GREY'),dict(itemOptionTcode='10',itemOptionName='09 BLACK')]
  with patch.object(c,'search',return_value=[URL]),patch.object(c,'page',side_effect=[self.empty(),self.page()]),patch.object(c,'options',return_value=options),patch.object(c,'gallery',return_value=[(c.urljoin('https://item.elandrs.com/',image['imagePath']),'digest')]):c.crawl(ctx)
  self.assertEqual(len(ctx.rows),1);self.assertFalse(ctx.errors);self.assertEqual([x['color_code'] for x in ctx.rows[0][1]],['05','09'])
 def test_stop_interrupts_retry(self):
  class Stop(RuntimeError):pass
  ctx=Context();ctx.checkpoint=unittest.mock.Mock(side_effect=[None,Stop('stop')])
  with patch.object(c,'page',return_value=self.empty()) as get:
   with self.assertRaises(Stop):c.product(ctx,URL,'ULTM26N02')
  self.assertEqual(get.call_count,1)
 def test_search_official_priority_and_all_sellers_fallback(self):
  def source(vendors):
   links=''.join('<a class="ui-item__link" href="/i/item?itemNo='+str(i)+'&lowerVendNo='+vendor+'">product</a>' for i,vendor in enumerate(vendors))
   return c.BeautifulSoup('<span data-searchresult-total>'+str(len(vendors))+'</span>'+links,'html.parser'),c.SITE+'/s/srch'
  ctx=Context()
  with patch.object(c,'page',return_value=source(['SELLER1',c.VENDOR,'SELLER2'])):
   urls=c.search(ctx,'ULTM26N02');self.assertEqual(len(urls),1);self.assertIn(c.VENDOR,urls[0])
  with patch.object(c,'page',return_value=source(['SELLER1','SELLER2'])):
   urls=c.search(ctx,'ULTM26N02');self.assertEqual(len(urls),2)
 def test_other_seller_uses_matching_product_and_option_vendor(self):
  data=copy.deepcopy(self.data);data['itemMain']['lowerVendNo']='SELLER1';url=URL.replace(c.VENDOR,'SELLER1')
  doc,_=self.page(data)
  with patch.object(c,'page',return_value=(doc,url)):self.assertTrue(c.product(Context(),url,'ULTM26N02'))
  payload={'resultCode':'200','data':{'itemOption':[dict(itemNo='2610598080',lowerVendNo='SELLER1',itemOptionTcode='99',itemOptionName='블랙')]}}
  with patch.object(c,'fetch',return_value=(json.dumps(payload).encode(),c.SITE+'/v1/item/option/api','application/json')) as get:
   rows=c.options(Context(),data['itemMain']);self.assertEqual(c.parse_qs(c.urlsplit(get.call_args.args[1]).query)['lowerVendNo'],['SELLER1'])
   self.assertEqual(c.color_rows(rows),[dict(color_name='블랙',color_code='')])
 def test_fallback_collects_every_link_but_only_one_detail(self):
  ctx=Context();ctx.enabled=lambda k:k in ('main_image','detail_page')
  links=[URL.replace(c.VENDOR,'SELLER1'),URL.replace(c.VENDOR,'SELLER2')]
  data=[]
  for i in [1,2]:
   d=copy.deepcopy(self.data);d['itemMain']['lowerVendNo']='SELLER'+str(i);data.append((d,links[i-1]))
  options=[[dict(itemOptionTcode='10',itemOptionName='05 GREY')],[dict(itemOptionTcode='10',itemOptionName='09 BLACK')]]
  with patch.object(c,'search',return_value=links),patch.object(c,'product',side_effect=data),patch.object(c,'options',side_effect=options),patch.object(c,'gallery',side_effect=[[('https://item.elandrs.com/one.jpg','one')],[('https://item.elandrs.com/two.jpg','two')]]),patch.object(c,'detail',side_effect=[[('photo','<img src="first.jpg">')],[('photo','<img src="second.jpg">')]]) as detail:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);self.assertEqual(detail.call_count,1)
  self.assertTrue(all(row[0]['detail_page']=='<img src="first.jpg">' for row in ctx.rows));self.assertEqual(len(ctx.rows[0][1]),2)
 def test_nonofficial_gallery_keeps_only_studio_images(self):
  main=copy.deepcopy(self.data['itemMain']);main['lowerVendNo']='SELLER1';main['itemImage']=[dict(imageDcode='010',imagePath='a.jpg'),dict(imageDcode='030',imagePath='b.jpg')]
  with patch.object(c,'image_info',side_effect=[('JPEG',1000,1000,False,'a','',False),('JPEG',1000,1000,False,'b','',True)]):result=c.gallery(Context(),main,{})
  self.assertEqual(result,[('https://item.elandrs.com/b.jpg','b')])
 def test_trusted_detail_omits_brand_and_seller_banners_without_ocr(self):
  with patch.object(c,'image_info',return_value=True),patch.object(c,'ocr_many') as ocr:
   parts=c.detail(Context(),self.data,'ULTM26N02',{})
  self.assertEqual(len(parts),2);self.assertTrue(all('BANNER' not in html for _,html in parts));ocr.assert_not_called()
 def test_uuid_seller_banner_is_checked_and_excluded(self):
  import io
  from PIL import Image
  stream=io.BytesIO();Image.new('RGB',(600,800),'white').save(stream,format='JPEG')
  data={'itemDesc':{'itemDescdocumentCon':'<img src="https://item.elandrs.com/r/image/item/banner.jpg"><img src="https://item.elandrs.com/r/image/item/uuid1.jpg"><img src="https://item.elandrs.com/r/image/item/uuid2.jpg">'}}
  with patch.object(c,'fetch',return_value=(stream.getvalue(),'https://item.elandrs.com/x.jpg','image/jpeg')) as get,patch.object(c,'ocr_many',return_value=['구매 전 안내 배송비 교환 반품 고객센터','제품 소재 색상 치수 제조국 세탁방법']):
   parts=c.detail(Context(),data,'ULTM26N02',{})
  self.assertEqual(get.call_count,2);self.assertEqual(len(parts),1);self.assertIn('uuid2',parts[0][1])
 def test_renamed_registration_has_no_duplicate_old_entry(self):
  import hashlib
  from brand_catalog import definitions
  brands=definitions(ROOT/'brands');items=[b for b in brands if b['id'] in ('usall_eland','usall_eland_studio')]
  self.assertEqual(len(items),1);self.assertEqual(items[0]['id'],'usall_eland_studio')
  self.assertEqual(items[0]['crawl_source'],'이랜드몰 스튜디오컷');self.assertEqual(items[0]['code_hash'],hashlib.sha256((ROOT/'brands/usall_eland_studio.py').read_bytes()).hexdigest())
 def test_actual_nonofficial_fixture_keeps_requested_vendor(self):
  data=json.loads((ROOT/'tests/fixtures/usall/ULMT24855.json').read_text(encoding='utf-8'));main=data['itemMain']
  url=c.SITE+'/i/item?'+c.urlencode(dict(itemNo=main['itemNo'],lowerVendNo=main['lowerVendNo']))
  doc,_=self.page(data)
  with patch.object(c,'page',return_value=(doc,url)):
   found=c.product(Context(),url,'ULMT24855');self.assertTrue(found);self.assertNotEqual(found[0]['itemMain']['lowerVendNo'],c.VENDOR)
 def test_fallback_detail_never_merges_across_links(self):
  ctx=Context();ctx.enabled=lambda k:k in ('main_image','detail_page');links=[URL.replace(c.VENDOR,'SELLER'+str(i)) for i in range(3)]
  data=[]
  for i in range(3):
   d=copy.deepcopy(self.data);d['itemMain']['lowerVendNo']='SELLER'+str(i);data.append((d,links[i]))
  with patch.object(c,'search',return_value=links),patch.object(c,'product',side_effect=data),patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='05 GREY')]),patch.object(c,'gallery',side_effect=[[('https://item.elandrs.com/'+str(i)+'.jpg',str(i))] for i in range(3)]),patch.object(c,'detail',side_effect=[[],[('photo','<img src="only-second.jpg">')],[('photo','<img src="third.jpg">')]]) as detail:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),3);self.assertEqual(detail.call_count,2);self.assertTrue(all(r[0]['product_url']==links[1] and r[0]['detail_page']=='<img src="only-second.jpg">' for r in ctx.rows))
if __name__=='__main__':unittest.main()
