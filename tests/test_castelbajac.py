import sys,json,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import castelbajac_official as c
class C:
 def checkpoint(self):pass
 def log(self,*a):pass
class CastelbajacTests(unittest.TestCase):
 def setUp(self):
  p=ROOT/'tests/fixtures/castelbajac';self.product=json.loads((p/'13828048787.json').read_text(encoding='utf-8'));self.contents=json.loads((p/'contents.json').read_text(encoding='utf-8'))
 def test_model_boundary_and_color_sku(self):
  self.assertTrue(c.matches('BGFWDW702BK','BGFWDW702'));self.assertTrue(c.matches('BGFWDW702BK','BGFWDW702BK'));self.assertFalse(c.matches('BGFWDW7020BK','BGFWDW702'));self.assertFalse(c.matches('XBGFWDW702BK','BGFWDW702'))
 def test_official_single_color_name_code_and_soldout_sizes(self):
  self.assertEqual(c.color_info(self.product,'BGFWDW702'),dict(color='BLACK',color_name='BLACK',color_code='BK'))
  self.assertEqual(c.sizes(self.product),'085,090,095,100')
 def test_price_uses_public_discount_and_independent_named_regular(self):
  self.assertEqual(c.common_price(C(),self.product),343080);self.assertEqual(c.regular_price(C(),self.product),490000)
  self.product['name']='상품명';self.assertIsNone(c.regular_price(C(),self.product))
 def test_clean_name_removes_brand_full_model_and_price(self):
  self.assertEqual(c.product_name(self.product['name'],'BGFWDW702BK'),'여성 퀼팅 후드 다운 점퍼')
 def test_original_body_resolves_unexpiring_https_and_preserves_full_panel(self):
  urls=c.detail_urls(self.contents,'BGFWDW702BK');self.assertEqual(urls,['https://castelgolf.cafe24.com/BG/DB/BGFWDW702BK/BGFWDW702BK.jpg'])
  with patch.object(c,'checked',return_value={}):body=c.detail(C(),self.contents,'BGFWDW702BK',{})
  self.assertEqual(body.count('<img'),1);self.assertNotIn('proxy-smartstore',body);self.assertNotIn('<table',body)
 def test_other_products_and_foreign_host_not_adopted(self):
  self.assertFalse(c.original_image('https://evil.test/BG/photo.jpg'))
  with self.assertRaisesRegex(ValueError,'상세 본문 이미지 없음'):c.detail_urls(self.contents,'OTHER123')
 def test_gallery_cover_first_and_all_six_originals(self):
  self.product['productImages']=list(reversed(self.product['productImages']))
  from io import BytesIO
  from PIL import Image
  b=BytesIO();Image.new('RGB',(1000,1000)).save(b,format='JPEG')
  with patch.object(c,'checked',return_value=dict(format='JPEG',width=1000,height=1000,data=b.getvalue())):
   urls=c.gallery(C(),self.product,{})
  self.assertEqual(len(urls),6);self.assertEqual(urls[0],next(r['url'] for r in self.product['productImages'] if r['imageType']=='REPRESENTATIVE'))
 def test_search_collects_both_color_models_and_distinguishes_zero(self):
  from types import SimpleNamespace
  state=json.loads((ROOT/'tests/fixtures/castelbajac/search.json').read_text(encoding='utf-8'))
  page=SimpleNamespace(content=lambda:'<script>window.__PRELOADED_STATE__='+json.dumps(state)+'</script>')
  with patch.object(c,'navigate'):
   self.assertEqual(set(c.discover(C(),page,'BGFWDW702')),{'13828048787','13828048590'})
   state['keywordSearch'].update(simpleProducts=[],totalCount=0)
   with self.assertRaisesRegex(ValueError,'검색 결과 0건'):c.discover(C(),page,'BGFWDW702')
 def test_429_waits_retry_after_then_recovers(self):
  from types import SimpleNamespace
  loader=__import__('unittest.mock',fromlist=['Mock']).Mock(side_effect=[c.RateLimited('PRODUCT','7'),{'id':1}])
  with patch.object(c,'pause') as pause:
   self.assertEqual(c.with_backoff(C(),SimpleNamespace(),loader),{'id':1})
  pause.assert_called_once();self.assertEqual(pause.call_args.args[2],7)
 def test_persistent_429_has_bounded_retries_and_explicit_reason(self):
  from unittest.mock import Mock
  loader=Mock(side_effect=c.RateLimited('PRODUCT',''))
  with patch.object(c,'pause') as pause:
   with self.assertRaisesRegex(c.RateLimited,'상품 미조회가 아닙니다'):c.with_backoff(C(),None,loader)
  self.assertEqual(loader.call_count,3);self.assertEqual([v.args[2] for v in pause.call_args_list],[15,30])
 def test_long_server_cooldown_does_not_retry_early(self):
  from unittest.mock import Mock
  loader=Mock(side_effect=c.RateLimited('PRODUCT','3600'))
  with patch.object(c,'pause') as pause:
   with self.assertRaises(c.RateLimited):c.with_backoff(C(),None,loader)
  self.assertEqual(loader.call_count,1);pause.assert_not_called()
 def test_retry_after_http_date_and_invalid_value(self):
  with patch.object(c.time,'time',return_value=0):self.assertEqual(c.retry_seconds('Thu, 01 Jan 1970 00:00:12 GMT',15),12)
  self.assertEqual(c.retry_seconds('invalid',15),15)
 def test_api_retry_discards_failed_attempt_and_detaches_listeners(self):
  from types import SimpleNamespace
  class Page:
   def __init__(self):self.listeners=[];self.attempts=0
   def on(self,event,fn):self.listeners.append(fn)
   def remove_listener(self,event,fn):self.listeners.remove(fn)
  page=Page();expected=c.SITE+'/n/v2/channels/'+c.CHANNEL_UID+'/products/1'
  def go(ctx,p,url):
   p.attempts+=1
   response=SimpleNamespace(url=expected,status=429 if p.attempts==1 else 200,headers={'retry-after':'5'},body=lambda:json.dumps({'id':1}).encode())
   for fn in list(p.listeners):fn(response)
   if p.attempts>1:
    for fn in list(p.listeners):fn(SimpleNamespace(url=expected+'/contents/1/PC',status=200,headers={},body=lambda:b'{"renderContent":"body"}'))
  with patch.object(c,'navigate_once',side_effect=go),patch.object(c,'pause'):
   self.assertEqual(c.load_product(C(),page,'url','1',True),({'id':1},{'renderContent':'body'}))
  self.assertEqual(page.attempts,2);self.assertEqual(page.listeners,[])
 def test_browser_restores_edge_execution_and_always_closes(self):
  from unittest.mock import Mock
  engine=Mock()
  with self.assertRaisesRegex(RuntimeError,'simulated'):
   with c.browser_session(C(),engine) as context:
    self.assertIs(context,engine.chromium.launch.return_value.new_context.return_value)
    raise RuntimeError('simulated')
  engine.chromium.launch.assert_called_once_with(headless=True,channel='msedge')
  engine.chromium.launch_persistent_context.assert_not_called()
  engine.chromium.launch.return_value.close.assert_called_once()
 def test_429_without_header_does_not_claim_server_provided_wait(self):
  from unittest.mock import Mock
  with patch.object(c,'pause'):
   with self.assertRaises(c.RateLimited) as caught:c.with_backoff(C(),None,Mock(side_effect=c.RateLimited('PRODUCT','')))
  self.assertIn('서버가 제한 해제 시각을 제공하지 않았습니다',str(caught.exception))
  self.assertNotIn('서버 안내 대기시간',str(caught.exception))
 def test_proxy_only_bodies_from_all_failed_products_use_public_originals(self):
  import html
  observed=json.loads((ROOT/'tests/fixtures/castelbajac/proxy_details.json').read_text(encoding='utf-8'))
  self.assertEqual({r['code'] for r in observed},{'BGFWPT707','BGFWPT708','BGFWPT712'})
  for row in observed:
   body={'renderContent':'<img src="data:image/png;base64,placeholder" data-src="'+html.escape(row['proxy'],quote=True)+'">'}
   self.assertEqual(c.detail_urls(body,row['model']),[row['original']])
   with patch.object(c,'checked',return_value={}):markup=c.detail(C(),body,row['model'],{})
   self.assertIn(row['original'],markup);self.assertNotIn('token=',markup);self.assertNotIn('proxy-smartstore',markup)
 def test_proxy_fallback_when_link_metadata_has_no_valid_source(self):
  from bs4 import BeautifulSoup
  row=json.loads((ROOT/'tests/fixtures/castelbajac/proxy_details.json').read_text(encoding='utf-8'))[0]
  markup='<a data-linkdata="{}"><img data-src="'+row['proxy']+'"></a>'
  self.assertEqual(c.detail_urls({'renderContent':markup},row['model']),[row['original']])
 def test_proxy_decoder_rejects_invalid_and_foreign_urls(self):
  import base64
  def proxy(text):return 'https://proxy-smartstore.naver.net/img/'+base64.urlsafe_b64encode(text.encode()).decode()
  for url in ('https://proxy-smartstore.naver.net/img/INVALID!','https://evil.test/img/123',proxy('evil.test/BG/photo.jpg'),proxy('castelgolf.cafe24.com/BG/../private.jpg')):
   self.assertFalse(c.detail_original(url),url)
 def test_store_data_from_another_brand_is_rejected(self):
  with self.assertRaisesRegex(ValueError,'공식 스토어'):c.state_from('<script>window.__PRELOADED_STATE__={"channel":{"brandUrl":"other"}}</script>')
if __name__=='__main__':unittest.main()
