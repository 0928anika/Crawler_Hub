import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import usall_eland as c
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
if __name__=='__main__':unittest.main()
