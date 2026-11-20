import copy,io,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import pat_eland_studio as c
class Context:
 product_code='1L31203'
 def __init__(self):self.rows=[];self.errors=[];self.logs=[]
 def enabled(self,k):return k in ('main_image','detail_page')
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,message,*a):self.logs.append(message)
 def fail_item(self,e):self.errors.append(e)
 def emit_unmapped(self,*args,**kwargs):self.rows.append(args)
class Tests(unittest.TestCase):
 def setUp(self):self.data=json.loads((ROOT/'tests/fixtures/pat_eland_studio/product.json').read_text(encoding='utf-8'))
 def test_search_never_prioritizes_any_vendor(self):
  doc=c.BeautifulSoup('<em data-searchresult-total>2</em><a class="ui-item__link" href="/i/item?itemNo=1&amp;lowerVendNo=LV20023674"></a><a class="ui-item__link" href="/i/item?itemNo=2&amp;lowerVendNo=OTHER"></a>','html.parser')
  with patch.object(c,'page',return_value=(doc,c.SITE)):
   self.assertEqual(len(c.search(Context(),'1L31203')),2)
 def test_empty_model_number_uses_exact_title_with_brand_guard(self):
  d=copy.deepcopy(self.data);main=d['itemMain'];url=c.SITE+'/i/item?itemNo='+main['itemNo']+'&lowerVendNo='+main['lowerVendNo']
  def run():
   doc=c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=d))+';</script>','html.parser')
   with patch.object(c,'page',return_value=(doc,url)):return c.product_once(Context(),url,'1L31203')
  self.assertIsNotNone(run());main['itemName']=main['itemName'].replace('1L31203','1L312030');self.assertIsNone(run())
  main['itemName']='PAT 자켓 1L31203';main['brandNo']='OTHER';self.assertIsNone(run())
 def test_all_vendors_inspected_and_one_safe_detail_used(self):
  ctx=Context();products=[];links=[]
  for i,vendor in enumerate(['LV20023674','OTHER']):
   d=copy.deepcopy(self.data);d['itemMain']['lowerVendNo']=vendor;url=c.SITE+'/i/item?itemNo='+str(i)+'&lowerVendNo='+vendor;links.append(url);products.append((d,url))
  with patch.object(c,'search',return_value=links),patch.object(c,'product',side_effect=products),patch.object(c,'studio_product',return_value=True) as studio,patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='BE 베이지')]),patch.object(c,'gallery',side_effect=[[('https://item.elandrs.com/one.jpg','one')],[('https://item.elandrs.com/two.jpg','two')]]),patch.object(c,'detail',side_effect=[[],[('photo','<img src="https://item.elandrs.com/safe.jpg">')]]) as detail:
   c.crawl(ctx)
  self.assertEqual(studio.call_count,2);self.assertEqual(detail.call_count,2);self.assertEqual(len(ctx.rows),2);self.assertFalse(ctx.errors)
  self.assertTrue(all('safe.jpg' in r[0]['detail_page'] for r in ctx.rows))
 def test_all_banner_details_are_omitted_without_losing_photos(self):
  ctx=Context()
  with patch.object(c,'search',return_value=['one']),patch.object(c,'product',return_value=(self.data,'one')),patch.object(c,'studio_product',return_value=True),patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='BE 베이지')]),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/one.jpg','one')]),patch.object(c,'detail',return_value=[]):c.crawl(ctx)
  self.assertTrue(ctx.rows);self.assertFalse(ctx.errors);self.assertEqual(ctx.rows[0][0]['detail_page'],'');self.assertTrue(any('상세페이지 생략' in x for x in ctx.logs))
 def test_embedded_seller_and_shipping_banners_are_excluded(self):
  d=copy.deepcopy(self.data);d['itemDesc']={'itemDescdocumentCon':'<img src="https://item.elandrs.com/body.jpg">'}
  buf=io.BytesIO();Image.new('RGB',(600,1600),'white').save(buf,'JPEG')
  for text in ['상품 설명 배송 안내 교환 반품','이랜드리테일이 보증 전국 49개 NC백화점 뉴코아 지점']:
   with patch.object(c,'fetch',return_value=(buf.getvalue(),'https://item.elandrs.com/body.jpg','image/jpeg')),patch.object(c,'ocr_many',return_value=[text]):self.assertEqual(c.detail(Context(),d,'1L31203',{}),[])
 def test_studio_background_rejects_patterned_background(self):
  uniform=Image.new('RGB',(800,1000),'#eeeeee');ImageDraw.Draw(uniform).rectangle((200,100,600,900),fill='#444444')
  self.assertTrue(c.studio_background(uniform)[0])
  patterned=Image.new('RGB',(800,1000),'#ffffff');draw=ImageDraw.Draw(patterned)
  for y in range(0,1000,20):
   for x in range(0,800,20):draw.rectangle((x,y,x+19,y+19),fill='#888888' if (x//20+y//20)%2 else '#eeeeee')
  self.assertFalse(c.studio_background(patterned)[0])
 def test_brand_and_model_removed_from_product_name(self):
  self.assertEqual(c.clean_name('피에이티 PAT 남성 자켓 1L31203','1L31203'),'남성 자켓')
if __name__=='__main__':unittest.main()
