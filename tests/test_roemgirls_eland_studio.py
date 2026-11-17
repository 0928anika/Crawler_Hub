import copy,json,sys,unittest,io
from PIL import Image
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import roemgirls_eland_studio as c
class Context:
 product_code='RGJP26W2P'
 def __init__(self):self.rows=[];self.errors=[]
 def enabled(self,k):return k in ('main_image','detail_page')
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def fail_item(self,e):self.errors.append(e)
 def emit_unmapped(self,*args,**kwargs):self.rows.append(args)
class Tests(unittest.TestCase):
 def setUp(self):self.data=json.loads((ROOT/'tests/fixtures/roemgirls/RGJP26W2P.json').read_text('utf-8'))
 def test_registration(self):
  from registry import get_brand
  d=get_brand('roemgirls_eland_studio');self.assertTrue(d['available']);self.assertEqual(d['crawl_source'],'이랜드몰 스튜디오컷');self.assertEqual(c.VENDOR,'LV17008490');self.assertEqual(c.BRAND_NO,'1690000157')
 def test_official_search_priority(self):
  def page(vendors):return c.BeautifulSoup('<span data-searchresult-total>'+str(len(vendors))+'</span>'+''.join('<a class="ui-item__link" href="/i/item?itemNo='+str(i)+'&lowerVendNo='+v+'">x</a>' for i,v in enumerate(vendors)),'html.parser'),c.SITE
  with patch.object(c,'page',return_value=page(['OTHER',c.VENDOR])):self.assertEqual(len(c.search(Context(),'RGJP26W2P')),1)
  with patch.object(c,'page',return_value=page(['OTHER','OTHER2'])):self.assertEqual(len(c.search(Context(),'RGJP26W2P')),2)
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
  with patch.object(c,'fetch',return_value=(buf.getvalue(),'https://item.elandrs.com/random-uuid.jpg','image/jpeg')),patch.object(c,'ocr_many',return_value=['로엠걸즈 배송 안내 교환 반품 고객센터']):
   self.assertEqual(c.detail(Context(),d,'RGJP26W2P',{}),[])
 def test_detail_paths_and_banners(self):
  self.assertTrue(c.original_detail('https://img-studio.elandmall.com/Winstudio/RGJP26W2P.jpg','RGJP26W2P'))
  self.assertTrue(c.original_detail('https://img-studio.elandmall.com/Winstudio/RG-SIZE.jpg','RGJP26W2P'))
  self.assertFalse(c.original_detail('https://img-studio.elandmall.com/Winstudio/CK-SIZE.jpg','RGJP26W2P'))
  d=copy.deepcopy(self.data);d['itemDesc']={'itemDescdocumentCon':'<img src="https://item.elandrs.com/shipping.jpg"><img src="https://item.elandrs.com/banner.jpg">'}
  with patch.object(c,'fetch') as fetch:self.assertEqual(c.detail(Context(),d,'RGJP26W2P',{}),[]);fetch.assert_not_called()
if __name__=='__main__':unittest.main()
