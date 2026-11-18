import copy,json,sys,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import littlebren_eland_studio as c
from PIL import Image
URL=c.SITE+'/i/item?itemNo=2411573718&lowerVendNo=CN003568'
class Context:
 product_code='LBCK24TZ2'
 def __init__(self,fields=('main_image','detail_page')):self.fields=fields;self.rows=[];self.errors=[]
 def enabled(self,k):return k in self.fields
 def checkpoint(self):pass
 def log(self,*a):pass
 def stage(self,*a):pass
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,r,colors,url,shared_detail_html=''):self.rows.append((r,colors,url))
class Tests(unittest.TestCase):
 def setUp(self):self.data=json.loads((ROOT/'tests/fixtures/littlebren/LBCK24TZ2.json').read_text(encoding='utf-8'))
 def page(self,data=None,url=URL):return c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps({'itemDetail':self.data if data is None else data})+';</script>','html.parser'),url
 def test_actual_brand_vendor_model_and_clean_name(self):
  with patch.object(c,'page',return_value=self.page()):d,url=c.product(Context(),URL,'LBCK24TZ2')
  self.assertEqual(c.VENDOR,'CN003568');self.assertEqual(d['itemMain']['brandNo'],c.BRAND_NO);self.assertEqual(d['itemMain']['modelNo'],'LBCK24TZ2');self.assertNotIn('LBCK24TZ2',c.clean_name(d['itemMain']['itemName'],'LBCK24TZ2'))
 def test_wrong_brand_model_or_vendor_is_rejected(self):
  for key,value in [('brandNo','1690000401'),('modelNo','LBCK24TZ3'),('lowerVendNo','OTHER')]:
   d=copy.deepcopy(self.data);d['itemMain'][key]=value
   with patch.object(c,'page',return_value=self.page(d)):self.assertIsNone(c.product(Context(),URL,'LBCK24TZ2'))
 def test_transient_json_missing_retried(self):
  blank=c.BeautifulSoup('<html></html>','html.parser'),URL
  with patch.object(c,'page',side_effect=[blank,blank,self.page()]) as get:self.assertTrue(c.product(Context(),URL,'LBCK24TZ2'))
  self.assertEqual(get.call_count,3)
 def test_official_priority_and_all_seller_fallback(self):
  def page(vendors):
   html='<span data-searchresult-total>'+str(len(vendors))+'</span>'+''.join('<a class="ui-item__link" href="/i/item?itemNo='+str(i)+'&lowerVendNo='+v+'">item</a>' for i,v in enumerate(vendors))
   return c.BeautifulSoup(html,'html.parser'),c.SITE+'/s/srch'
  with patch.object(c,'page',return_value=page(['OTHER',c.VENDOR,'OTHER2'])):
   links=c.search(Context(),'LBCK24TZ2');self.assertEqual(len(links),1);self.assertIn(c.VENDOR,links[0])
  with patch.object(c,'page',return_value=page(['OTHER','OTHER2'])):self.assertEqual(len(c.search(Context(),'LBCK24TZ2')),2)
 def test_options_request_uses_actual_seller(self):
  main=copy.deepcopy(self.data['itemMain']);main['lowerVendNo']='OTHER'
  payload=dict(resultCode='200',data=dict(itemOption=[dict(itemNo=main['itemNo'],lowerVendNo='OTHER',itemOptionTcode='99',itemOptionName='블랙')]))
  with patch.object(c,'fetch',return_value=(json.dumps(payload).encode(),c.SITE+'/v1/item/option/api','application/json')) as get:c.options(Context(),main)
  self.assertEqual(c.parse_qs(c.urlsplit(get.call_args.args[1]).query)['lowerVendNo'],['OTHER'])
 def test_brand_banner_excluded_size_preserved(self):
  with patch.object(c,'image_info',return_value=True),patch.object(c,'ocr_many') as ocr:parts=c.detail(Context(),self.data,'LBCK24TZ2',{})
  self.assertEqual(len(parts),2);self.assertIn('LBCK24TZ2.jpg',parts[0][1]);self.assertIn('LBK-SIZE-1.jpg',parts[1][1]);self.assertTrue(all('BANNER' not in m for _,m in parts));ocr.assert_not_called()
 def test_real_store_background_rejected(self):
  for name in ['ULJM26T02_cover','ULJM26T02_brick','ULJM26T02_hanger']:
   with Image.open(ROOT/'tests/fixtures/usall'/(name+'.jpg')) as im:self.assertFalse(c.studio_background(im)[0])
 def test_nonstudio_primary_rejects_before_options(self):
  data=copy.deepcopy(self.data);data['itemMain']['lowerVendNo']='OTHER';url=URL.replace(c.VENDOR,'OTHER');ctx=Context()
  with patch.object(c,'search',return_value=[url]),patch.object(c,'product',return_value=(data,url)),patch.object(c,'studio_product',return_value=False),patch.object(c,'options') as options:
   with self.assertRaisesRegex(c.CrawlError,'스튜디오컷 없음'):c.crawl(ctx)
  options.assert_not_called();self.assertFalse(ctx.rows)
 def test_all_studio_sellers_but_only_one_detail(self):
  ctx=Context();links=[URL.replace(c.VENDOR,'SELLER'+str(i)) for i in [1,2]];products=[]
  for i in [1,2]:
   d=copy.deepcopy(self.data);d['itemMain']['lowerVendNo']='SELLER'+str(i);products.append((d,links[i-1]))
  with patch.object(c,'search',return_value=links),patch.object(c,'product',side_effect=products),patch.object(c,'studio_product',return_value=True),patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='05 GREY')]),patch.object(c,'gallery',side_effect=[[('https://item.elandrs.com/one.jpg','one')],[('https://item.elandrs.com/two.jpg','two')]]),patch.object(c,'detail',side_effect=[[('photo','<img src="first.jpg">')],[('photo','<img src="second.jpg">')]]) as detail:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);self.assertEqual(detail.call_count,1);self.assertTrue(all(r[0]['detail_page']=='<img src="first.jpg">' for r in ctx.rows))
 def test_search_zero_has_distinct_failure(self):
  ctx=Context()
  with patch.object(c,'search',return_value=[]):
   with self.assertRaisesRegex(c.CrawlError,'품번 검색 결과 없음'):c.crawl(ctx)
  self.assertNotIn('스튜디오컷 없음',ctx.errors[0])
 def test_selected_detail_empty_fails(self):
  ctx=Context()
  with patch.object(c,'search',return_value=[URL]),patch.object(c,'product',return_value=(self.data,URL)),patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='05 GREY')]),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/a.jpg','a')]),patch.object(c,'detail',return_value=[]):
   with self.assertRaisesRegex(c.CrawlError,'상세페이지 수집 실패'):c.crawl(ctx)
 def test_registration_and_actual_sdk(self):
  from registry import brands
  from crawler_sdk import CrawlContext
  items=[b for b in brands(ROOT/'app') if b['id']=='littlebren_eland_studio'];self.assertEqual(len(items),1);self.assertTrue(items[0]['available']);self.assertEqual(items[0]['display_name'],'리틀브렌 (이랜드몰 스튜디오컷)')
  with tempfile.TemporaryDirectory() as folder:
   ctx=CrawlContext('unused.xlsx',str(Path(folder)/'out.xlsx'),0,['main_image','detail_page'],items[0]['output_headers'],lambda:None,lambda *a,**k:None,input_item={'품번':'LBCK24TZ2'},mapping_mode='manual')
   with patch.object(c,'search',return_value=[URL]),patch.object(c,'product',return_value=(self.data,URL)),patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='05 GREY')]),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/a.jpg','a')]),patch.object(c,'image_info',return_value=True):c.crawl(ctx)
   ctx.finish();self.assertTrue((Path(folder)/'out.xlsx').exists())
if __name__=='__main__':unittest.main()
