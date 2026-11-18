import copy,json,sys,unittest,io
from PIL import Image
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import clavis_eland_studio as c
class Context:
 product_code='CVTWG1108N'
 def __init__(self):self.rows=[];self.errors=[]
 def enabled(self,k):return k in ('main_image','detail_page')
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def fail_item(self,e):self.errors.append(e)
 def emit_unmapped(self,*args,**kwargs):self.rows.append(args)
class Tests(unittest.TestCase):
 def setUp(self):self.data=json.loads((ROOT/'tests/fixtures/clavis/CVTWG1108N.json').read_text('utf-8'))
 def test_registration(self):
  from registry import get_brand
  d=get_brand('clavis_eland_studio');self.assertTrue(d['available']);self.assertEqual(d['crawl_source'],'이랜드몰 스튜디오컷');self.assertEqual(c.VENDOR,'LV16003596');self.assertEqual(c.BRAND_NO,'1690000333')
 def test_official_search_priority(self):
  def page(vendors):return c.BeautifulSoup('<span data-searchresult-total>'+str(len(vendors))+'</span>'+''.join('<a class="ui-item__link" href="/i/item?itemNo='+str(i)+'&lowerVendNo='+v+'">x</a>' for i,v in enumerate(vendors)),'html.parser'),c.SITE
  with patch.object(c,'page',return_value=page(['OTHER',c.VENDOR])):self.assertEqual(len(c.search(Context(),'CVTWG1108N')),1)
  with patch.object(c,'page',return_value=page(['OTHER','OTHER2'])):self.assertEqual(len(c.search(Context(),'CVTWG1108N')),2)
 def run_case(self,vendors):
  ctx=Context();products=[];links=[]
  for i,v in enumerate(vendors):
   d=copy.deepcopy(self.data);d['itemMain']['lowerVendNo']=v;url=c.SITE+'/i/item?itemNo='+str(i)+'&lowerVendNo='+v;links.append(url);products.append((d,url))
  with patch.object(c,'search',return_value=links),patch.object(c,'product',side_effect=products) as product,patch.object(c,'studio_product',return_value=True) as studio,patch.object(c,'options',return_value=[dict(itemOptionTcode='10',itemOptionName='94 IVORY')]),patch.object(c,'gallery',side_effect=[[('https://item.elandrs.com/'+str(i)+'.jpg',str(i))] for i in range(len(vendors))]),patch.object(c,'detail',return_value=[('photo','<img src="one.jpg">')]) as detail:c.crawl(ctx)
  return ctx,product.call_count,detail.call_count,studio.call_count
 def test_official_only_one_product(self):
  ctx,products,details,studios=self.run_case([c.VENDOR,c.VENDOR]);self.assertEqual((len(ctx.rows),products,details,studios),(1,1,1,0))
 def test_all_fallback_studio_products_one_detail(self):
  ctx,products,details,studios=self.run_case(['OTHER','OTHER2']);self.assertEqual((len(ctx.rows),products,details,studios),(2,2,1,2))
 def test_embedded_shipping_banner_is_excluded(self):
  d=copy.deepcopy(self.data);d['itemDesc']={'itemDescdocumentCon':'<img src="https://item.elandrs.com/random-uuid.jpg">'}
  buf=io.BytesIO();Image.new('RGB',(600,1200),'white').save(buf,format='JPEG')
  with patch.object(c,'fetch',return_value=(buf.getvalue(),'https://item.elandrs.com/random-uuid.jpg','image/jpeg')),patch.object(c,'ocr_many',return_value=['클라비스 배송 안내 교환 반품 고객센터']):
   self.assertEqual(c.detail(Context(),d,'CVTWG1108N',{}),[])
 def test_detail_paths_and_banners(self):
  self.assertTrue(c.original_detail('https://img-studio.elandmall.com/Winstudio/CVTWG1108N.jpg','CVTWG1108N'))
  self.assertTrue(c.original_detail('https://img-studio.elandmall.com/Winstudio/CV-SIZE.jpg','CVTWG1108N'))
  self.assertFalse(c.original_detail('https://img-studio.elandmall.com/Winstudio/CK-SIZE.jpg','CVTWG1108N'))
  d=copy.deepcopy(self.data);d['itemDesc']={'itemDescdocumentCon':'<img src="https://item.elandrs.com/shipping.jpg"><img src="https://item.elandrs.com/banner.jpg">'}
  with patch.object(c,'fetch') as fetch:self.assertEqual(c.detail(Context(),d,'CVTWG1108N',{}),[]);fetch.assert_not_called()
 def test_mixed_vendor_options_keep_only_requested_vendor(self):
  main=self.data['itemMain'];rows=[dict(itemNo=main['itemNo'],lowerVendNo=c.VENDOR,itemOptionName='26'),dict(itemNo=main['itemNo'],lowerVendNo='LV16003611',itemOptionName='28')]
  raw=json.dumps(dict(resultCode=200,data=dict(itemOption=rows))).encode()
  with patch.object(c,'fetch',return_value=(raw,c.SITE+'/v1/item/option/api','application/json')):result=c.options(Context(),main,'color')
  self.assertEqual([o['itemOptionName'] for o in result],['26'])
  rows[1]['itemNo']='WRONG';raw=json.dumps(dict(resultCode=200,data=dict(itemOption=rows))).encode()
  with patch.object(c,'fetch',return_value=(raw,c.SITE+'/v1/item/option/api','application/json')):
   with self.assertRaisesRegex(c.CrawlError,'상품 ID 불일치'):c.options(Context(),main,'color')
 def test_real_official_product_identity_and_no_extra_notice_tables(self):
  main=self.data['itemMain'];url=c.SITE+'/i/item?itemNo='+main['itemNo']+'&lowerVendNo='+c.VENDOR
  doc=c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=self.data))+';</script>','html.parser')
  with patch.object(c,'page',return_value=(doc,url)):self.assertEqual(c.product_once(Context(),url,'CVTWG1108N')[0]['itemMain']['brandNo'],c.BRAND_NO)
  self.assertNotIn('CVTWG1108N',c.clean_name(main['itemName'],'CVTWG1108N'))
 def test_official_parenthesized_color_codes_and_brackets(self):
  self.assertEqual(c.color_rows([dict(itemOptionTcode='10',itemOptionName='(19)Black'),dict(itemOptionTcode='10',itemOptionName='(85)Brown')]),[dict(color_name='Black',color_code='19'),dict(color_name='Brown',color_code='85')])
  self.assertEqual(c.clean_name('[슬랙스닝] 융기모 팬츠_CVTWG1108N','CVTWG1108N'),'[슬랙스닝] 융기모 팬츠')
if __name__=='__main__':unittest.main()
