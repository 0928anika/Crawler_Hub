"""Contract and actual browser-source fixtures; direct unattended access remains conditional."""
import copy,json,sys,unittest,tempfile
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch,MagicMock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import jessi_naver_ncbg as c
class Context:
 product_code='13869773896'
 def __init__(self,fields=()):self.fields=fields;self.rows=[];self.raw=[];self.errors=[]
 def enabled(self,key):return key in self.fields
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def emit_result(self,row):self.rows.append(row)
 def emit_unmapped(self,*a,**kw):self.raw.append(a)
 def fail_item(self,e):self.errors.append(e)
class Tests(unittest.TestCase):
 def product(self):
  return dict(id='13869773896',name='제시뉴욕 상품',salePrice=100000,discountedSalePrice=80000,naverShoppingSearchInfo=dict(modelName='ACTUAL-MODEL'),options=[dict(groupName='색상'),dict(groupName='사이즈')],optionCombinations=[dict(optionName1='블랙',optionName2='55',price=0),dict(optionName1='블랙',optionName2='66',price=0)])
 def test_numbers_and_identity(self):
  self.assertEqual(c.product_number(' 13869773896 '),'13869773896')
  for n in ['AEM5NSDP5490','https://smartstore.naver.com/x','13869773896.0']:
   with self.assertRaises(ValueError):c.product_number(n)
  with self.assertRaisesRegex(ValueError,'상품번호'):c.verify_product(self.product(),'13869739542')
 def test_palette_preserves_color_sizes(self):
  p=self.product();p['optionCombinations'].append(dict(optionName1='아이보리',optionName2='77',price=0))
  palette,d=c.option_palette(p);self.assertEqual([c['size'] for c in palette],['55,66','77']);self.assertEqual([c['color_code'] for c in palette],['',''])
 def test_product_number_is_output_code_not_model(self):
  for fields in [(),('price',),('regular_price',),('price','regular_price')]:
   ctx=Context(fields)
   with patch.object(c,'load_product',return_value=(self.product(),'','https://smartstore.naver.com/ncdept297/products/13869773896')):c.crawl(ctx)
   self.assertEqual(ctx.rows[0]['product_code'],'13869773896');self.assertEqual(ctx.rows[0]['원본 품번'],'ACTUAL-MODEL')
   self.assertEqual('price' in ctx.rows[0],'price' in fields);self.assertEqual('regular_price' in ctx.rows[0],'regular_price' in fields)
 def test_multiple_colors_are_manual_only(self):
  p=self.product();p['optionCombinations'].append(dict(optionName1='아이보리',optionName2='77',price=0));ctx=Context(('main_image','size'))
  with patch.object(c,'load_product',return_value=(p,'','https://smartstore.naver.com/ncdept297/products/13869773896')),patch.object(c,'gallery',return_value=['https://shop-phinf.pstatic.net/photo.jpg']):c.crawl(ctx)
  self.assertFalse(ctx.rows);self.assertEqual(ctx.raw[0][1][1]['size'],'77')
 def test_restriction_is_failure_not_empty_search(self):
  ctx=Context()
  with patch.object(c,'load_product',side_effect=ValueError('[접근 제한] 네이버 HTTP 429')):
   with self.assertRaisesRegex(ValueError,'접근 제한'):c.crawl(ctx)
  self.assertTrue(ctx.errors);self.assertFalse(ctx.rows)
 def test_price_ambiguity_is_blank(self):
  self.assertIsNone(c.selected_price(Context(),self.product(),[0,1000]));self.assertEqual(c.selected_price(Context(),self.product(),[0,0]),80000)
 def test_access_wait_resumes_same_product(self):
  from unittest.mock import Mock
  page=Mock();page.is_closed.return_value=False;page.url='https://smartstore.naver.com/ncdept297/products/13869773896';page.content.return_value='<script>window.__PRELOADED_STATE__={"channel":{"url":"ncdept297"},"simpleProductForDetailPage":{"id":"13869773896","name":"상품"}};</script>'
  state=c.wait_access(Context(),page,'13869773896',429,{},timeout=1);self.assertEqual(state['simpleProductForDetailPage']['id'],'13869773896')
 def test_access_wait_never_accepts_other_product(self):
  from unittest.mock import Mock
  page=Mock();page.is_closed.return_value=False;page.url='https://smartstore.naver.com/ncdept297/products/99999999'
  with self.assertRaisesRegex(ValueError,'접속 확인 시간 초과'):c.wait_access(Context(),page,'13869773896',429,{},timeout=0)
  with self.assertRaisesRegex(ValueError,'상품 페이지 없음'):c.wait_access(Context(),page,'13869773896',404,{},timeout=0)
 def test_real_browser_snapshot_and_expiry(self):
  data=json.loads((ROOT/'tests/fixtures/jessi_ncbg/13869773896.json').read_text('utf-8'))
  data['collected_at']=datetime.now(timezone.utc).isoformat()
  with tempfile.TemporaryDirectory() as folder,patch.object(c,'snapshot_dir',return_value=Path(folder)):
   path=Path(folder)/'13869773896.json';path.write_text(json.dumps(data),encoding='utf-8');ctx=Context(('detail_page',))
   product,body,url=c.saved_browser_product(ctx,'13869773896')
   self.assertEqual(product['name'],'머플러 니트 티셔츠');self.assertIn('se-main-container',body);self.assertEqual(c.option_palette(product)[0][0]['size'],'55,66');self.assertEqual(c.selected_price(ctx,product,[]),83100)
   data['store']='OTHER';path.write_text(json.dumps(data),encoding='utf-8')
   with self.assertRaisesRegex(ValueError,'스토어'):c.saved_browser_product(ctx,'13869773896')
   data['store']='ncdept297';data['collected_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat();path.write_text(json.dumps(data),encoding='utf-8')
   with self.assertRaisesRegex(ValueError,'24시간'):c.saved_browser_product(ctx,'13869773896')
 def test_second_real_product_is_not_hardcoded(self):
  data=json.loads((ROOT/'tests/fixtures/jessi_ncbg/13869739542.json').read_text('utf-8'))
  palette,_=c.option_palette(data['product']);self.assertEqual(palette[0]['color_name'],'블랙멀티');self.assertEqual(palette[0]['size'],'EU 42')
 def test_no_color_options_uses_actual_notice_without_inventing_sizes(self):
  product=dict(id='13867119949',name='카디건',productInfoProvidedNoticeView=dict(basic={'색상':'검점멀티','치수':'80-86-155'}))
  palette,deltas=c.option_palette(product)
  self.assertEqual(len(palette),1);self.assertEqual(palette[0]['color_name'],'검점멀티');self.assertEqual(palette[0]['size'],'80-86-155');self.assertEqual(palette[0]['color_code'],'');self.assertFalse(deltas)
  product['productInfoProvidedNoticeView']['basic']['치수']='상세페이지 참조'
  self.assertEqual(c.option_palette(product)[0][0]['size'],'')
  product['productInfoProvidedNoticeView']['basic']['색상']='상세정보 확인'
  with self.assertRaisesRegex(ValueError,'색상'):c.option_palette(product)
 def test_unjoined_options_preserve_product_list_with_scope_warning(self):
  product=dict(id='13867096498',name='투웨이 지퍼 카디건',optionStandards=[dict(optionGroupName='색상',optionName=x) for x in ['네이비','브라운']]+[dict(optionGroupName='사이즈',optionName=x) for x in ['55','66']])
  palette,_=c.option_palette(product)
  self.assertEqual([v['color_name'] for v in palette],['네이비','브라운']);self.assertTrue(all(v['size']=='55,66' for v in palette));self.assertTrue(all('조합 미확인' in v['size_scope'] for v in palette))
  ctx=Context(('size',));ctx.product_code='13867096498'
  with patch.object(c,'load_product',return_value=(product,'','https://smartstore.naver.com/ncdept297/products/13867096498')):c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);self.assertFalse(ctx.errors);self.assertTrue(all('조합 미확인' in r['사이즈 확인 범위'] for r in ctx.rows))
 def test_full_combinations_keep_actual_color_size_pairs(self):
  product=self.product();product['optionCombinations']=[dict(optionName1='블랙',optionName2='55',price=0),dict(optionName1='화이트',optionName2='66',price=0)]
  palette,_=c.option_palette(product);self.assertEqual({v['color_name']:v['size'] for v in palette},{'블랙':'55','화이트':'66'})
 def test_actual_collected_option_failure_fixtures(self):
  no_options=json.loads((ROOT/'tests/fixtures/jessi_ncbg/13867119949.json').read_text('utf-8'))['product']
  palette,_=c.option_palette(no_options);self.assertEqual(palette[0]['color_name'],'검점멀티');self.assertEqual(palette[0]['size'],'80-86-155')
  unjoined=json.loads((ROOT/'tests/fixtures/jessi_ncbg/13867096498.json').read_text('utf-8'))['product']
  palette,_=c.option_palette(unjoined);self.assertEqual([p['color_name'] for p in palette],['네이비','브라운']);self.assertTrue(all(p['size']=='55,66' and '조합 미확인' in p['size_scope'] for p in palette))
 def test_login_then_storefront_returns_to_requested_product_once(self):
  class Page:
   url='https://nid.naver.com/nidlogin.login'
   calls=[]
   def is_closed(self):return False
   def wait_for_timeout(self,*a):self.url='https://smartstore.naver.com/ncdept297'
   def goto(self,url,**kw):self.calls.append(url);self.url=url
   def content(self):return '<script>window.__PRELOADED_STATE__={"channel":{"url":"ncdept297"},"simpleProductForDetailPage":{"id":"13869773896","name":"상품"}};</script>'
  page=Page();state=c.wait_access(Context(),page,'13869773896',200,{},timeout=1)
  self.assertEqual(page.calls,['https://smartstore.naver.com/ncdept297/products/13869773896']);self.assertEqual(state['simpleProductForDetailPage']['id'],'13869773896')
 def test_closed_login_window_is_explicit(self):
  from unittest.mock import Mock
  page=Mock();page.is_closed.return_value=True
  with self.assertRaisesRegex(ValueError,'수집창 닫힘'):c.wait_access(Context(),page,'13869773896',200,{},timeout=1)
 def test_slow_existing_endpoint_is_retried(self):
  with patch.object(c,'chrome_endpoint_once',side_effect=[None,None,'ws://127.0.0.1:1234/devtools/browser/existing']),patch.object(c.time,'sleep'):
   self.assertEqual(c.chrome_endpoint(Path('.')),'ws://127.0.0.1:1234/devtools/browser/existing')
 def test_disconnected_existing_profile_never_spawns_new_window(self):
  from unittest.mock import Mock
  with tempfile.TemporaryDirectory() as folder:
   work=Path(folder)/'browser-profiles/jessi_naver_ncbg/profile';work.mkdir(parents=True);(work/'DevToolsActivePort').write_text('1234\n/devtools/browser/existing')
   with patch.dict(c.os.environ,{'CRAWLER_HUB_HOME':folder}),patch.object(c,'chrome_endpoint',return_value=None),patch('playwright.sync_api.sync_playwright',return_value=MagicMock()),patch.object(c.subprocess,'Popen') as start:
    with self.assertRaisesRegex(RuntimeError,'기존 Chrome 연결 설정 필요'):
     with c.naver_browser_session(Context()):pass
    start.assert_not_called()
 def test_existing_chrome_discovery_is_loopback_only(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'DevToolsActivePort';path.write_text('9222\n/devtools/browser/abc-123')
   self.assertEqual(c.chrome_endpoint_once(Path(folder)),'ws://127.0.0.1:9222/devtools/browser/abc-123')
   for text in ['0\n/devtools/browser/abc','9222\nhttps://external.example/','9222\n/devtools/page/abc']:
    path.write_text(text);self.assertIsNone(c.chrome_endpoint_once(Path(folder)))
 def test_existing_connection_does_not_launch_or_close_browser(self):
  manager=MagicMock();runtime=manager.__enter__.return_value;browser=runtime.chromium.connect_over_cdp.return_value;context=MagicMock();browser.contexts=[context]
  with patch.object(c,'chrome_endpoint',return_value='ws://127.0.0.1:9222/devtools/browser/existing'),patch('playwright.sync_api.sync_playwright',return_value=manager),patch.object(c.subprocess,'Popen') as start:
   with c.naver_browser_session(Context()) as result:self.assertIs(result,context)
   start.assert_not_called();browser.close.assert_not_called();context.close.assert_not_called();context.new_page.assert_not_called()
 def test_original_body_image_urls_are_declared_not_guessed(self):
  source='<div class="se-main-container"><div class="se-image"><a data-linkdata=\'{"src":"https://shop-phinf.pstatic.net/original.jpg"}\'><img data-src="https://shop-phinf.pstatic.net/original.jpg?type=w848"></a></div><img src="https://other.example/review.jpg"></div>'
  self.assertEqual(c.body_image_urls(source),['https://shop-phinf.pstatic.net/original.jpg'])
 def test_body_photo_and_chart_are_separated(self):
  import io
  from PIL import Image,ImageDraw
  im=Image.new('RGB',(800,1000),'white');ImageDraw.Draw(im).rectangle((150,50,650,950),fill='navy');buf=io.BytesIO();im.save(buf,format='JPEG');raw=buf.getvalue()
  urls=['https://shop-phinf.pstatic.net/photo.jpg','https://shop-phinf.pstatic.net/chart-uuid.jpg'];source='<div class="se-main-container">'+''.join('<img src="'+u+'">' for u in urls)+'</div>'
  with patch.object(c,'public_image',return_value=dict(data=raw)),patch.object(c,'internal_gutter',return_value=False),patch.object(c,'ocr_many',return_value=['카디건','실측 사이즈표 어깨 가슴 총장']):
   result=c.body_gallery(Context(),source,{})
  self.assertEqual([u for u,_ in result],[urls[0]])
 def test_all_body_candidates_go_to_manual_even_one_color(self):
  ctx=Context(('main_image',));product=self.product()
  with patch.object(c,'load_product',return_value=(product,'body','https://smartstore.naver.com/ncdept297/products/13869773896')),patch.object(c,'photo_candidates',return_value=['https://shop-phinf.pstatic.net/a.jpg','https://shop-phinf.pstatic.net/b.jpg']):c.crawl(ctx)
  self.assertEqual(len(ctx.raw),2);self.assertFalse(ctx.rows)
 def test_existing_tab_selection_never_creates_or_uses_other_site(self):
  from types import SimpleNamespace as N
  app=N(url='http://127.0.0.1:8765/');setup=N(url='chrome://inspect/#remote-debugging');store=N(url='https://smartstore.naver.com/ncdept297')
  self.assertIs(c.existing_product_tab(N(pages=[app,setup])),setup)
  self.assertIs(c.existing_product_tab(N(pages=[setup,store])),store)
  with self.assertRaisesRegex(ValueError,'상품 탭 필요'):c.existing_product_tab(N(pages=[app,N(url='https://smartstore.naver.com/other/products/123456')]))
 def test_mobile_and_desktop_notice_structures(self):
  for markup in ['<ul><li><strong>색상</strong><div>검점멀티</div></li><li><strong>치수</strong><div>80-86-155</div></li></ul>','<table><tr><th>색상</th><td>검점멀티</td></tr><tr><th>치수</th><td>80-86-155</td></tr></table>','<dl><dt>색상</dt><dd>검점멀티</dd><dt>치수</dt><dd>80-86-155</dd></dl>']:
   self.assertEqual(c.parse_notice_markup(markup),{'색상':'검점멀티','치수':'80-86-155'})
 def test_ready_broker_is_reused_without_starting_process(self):
  info={'port':1234,'token':'a'*32,'version':'v','ready':True}
  with patch.object(c,'broker_info',return_value=info),patch.object(c,'broker_version',return_value='v'),patch.object(c,'broker_request',return_value={'ok':True,'version':'v'}),patch.object(c.subprocess,'Popen') as start,patch.object(c,'broker_folder',return_value=Path(tempfile.gettempdir())):
   self.assertEqual(c.ensure_broker(Context()),info);start.assert_not_called()
 def test_broker_keeps_one_connection_and_filters_response(self):
  import threading,time
  from contextlib import contextmanager
  calls=[];context=MagicMock();context.browser.is_connected.return_value=True
  @contextmanager
  def session(ctx):calls.append('connect');yield context
  def reader(ctx,browser,number):return {'id':number,'name':'상품','userNickname':'not-exported'},'<div>body</div>','https://smartstore.naver.com/ncdept297/products/'+number
  with tempfile.TemporaryDirectory() as folder,patch.object(c,'broker_folder',return_value=Path(folder)),patch.object(c,'broker_version',return_value='test-v1'),patch.object(c,'naver_browser_session',side_effect=session),patch.object(c,'read_product_page',side_effect=reader):
   thread=threading.Thread(target=c.run_existing_chrome_broker,daemon=True);thread.start();info=None
   for _ in range(100):
    info=c.broker_info()
    if info and info.get('ready'):break
    time.sleep(.01)
   self.assertTrue(info and info.get('ready'))
   try:
    self.assertTrue(c.broker_request(info,'/health')['ok'])
    for number in ['13867119949','13867096498']:
     result=c.broker_request(info,'/collect',{'number':number,'fields':[]});self.assertEqual(result['product']['id'],number);self.assertNotIn('userNickname',result['product'])
    self.assertFalse(c.broker_request(info,'/collect',{'number':'wrong','fields':[]})['ok']);self.assertEqual(calls,['connect'])
   finally:c.broker_request(info,'/stop',{});thread.join(timeout=5)
   self.assertFalse(thread.is_alive());context.close.assert_not_called()
 def test_pending_approval_does_not_start_duplicate_broker(self):
  pending={'port':1234,'token':'a'*32,'version':'v','ready':False,'pid':123,'started':0};ready=dict(pending,ready=True)
  with patch.object(c,'broker_info',side_effect=[pending,ready]),patch.object(c,'broker_version',return_value='v'),patch.object(c,'broker_alive',return_value=True),patch.object(c,'broker_request',return_value={'ok':True,'version':'v'}),patch.object(c.subprocess,'Popen') as start,patch.object(c,'broker_folder',return_value=Path(tempfile.gettempdir())):
   self.assertEqual(c.ensure_broker(Context()),ready);start.assert_not_called()
 def test_store_state_and_registration(self):
  from registry import get_brand
  b=get_brand('jessi_naver_ncbg');self.assertTrue(b['available']);self.assertEqual(b['crawl_source'],'네이버 NC불광');self.assertFalse(b['capabilities']['regular_price']['supported'])
  with self.assertRaisesRegex(ValueError,'스토어'):c.state_from('<script>window.__PRELOADED_STATE__={"channel":{"url":"OTHER"}};</script>')
if __name__=='__main__':unittest.main()
