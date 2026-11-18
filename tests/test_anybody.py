import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import anybody_eland_official as c
FIX=ROOT/'tests/fixtures/anybody'
def load(name):return json.loads((FIX/(name+'.json')).read_text('utf-8'))
LINK=c.SITE+'/i/item?itemNo=2512220870&lowerVendNo=CN003546'
class C:
 product_code='ANJS26165'
 def __init__(self,fields=()):self.fields=fields;self.rows=[];self.errors=[];self.logs=[]
 def enabled(self,k):return k in self.fields
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):self.logs.append(a)
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,*a,**kw):self.rows.append((a,kw))
def options(ctx,main,parent=None):return load('colors' if parent is None else 'sizes')
class AnybodyTests(unittest.TestCase):
 def test_search_selects_only_requested_vendor(self):
  doc=c.BeautifulSoup('<em data-searchresult-total>2</em><a class="ui-item__link" href="'+LINK+'"></a><a class="ui-item__link" href="/i/item?itemNo=2&lowerVendNo=OTHER"></a>','html.parser')
  with patch.object(c,'page',return_value=doc):self.assertEqual(c.search(C(),'ANJS26165'),[LINK])
 def test_zero_results_and_other_seller_have_distinct_failure_reasons(self):
  cases=[('<em data-searchresult-total>0</em>','이랜드몰 검색 결과 0건'),
]
  for markup,reason in cases:
   ctx=C(['main_image'])
   with patch.object(c,'page',return_value=c.BeautifulSoup(markup,'html.parser')):
    with self.assertRaisesRegex(c.CollectionError,reason):c.crawl(ctx)
   self.assertIn(reason,ctx.errors[0])
 def test_transport_and_invalid_search_responses_not_misreported_as_zero(self):
  for response in [c.CollectionError('통신 실패'),c.BeautifulSoup('<html></html>','html.parser')]:
   ctx=C(['main_image'])
   with patch.object(c,'page',**({'side_effect':response} if isinstance(response,Exception) else {'return_value':response})):
    with self.assertRaises(c.CollectionError):c.crawl(ctx)
   self.assertNotIn('0건',ctx.errors[0]);self.assertNotIn('다른 판매자 상품만',ctx.errors[0])
 def test_product_rechecks_vendor_brand_and_model(self):
  for k,v in [('brandNo','OTHER'),('modelNo','WRONG')]:
   d=load('product');d['itemMain'][k]=v
   doc=c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=d))+'</script>','html.parser')
   with patch.object(c,'page',return_value=doc):self.assertIsNone(c.product(C(),LINK,'ANJS26165'))
 def test_other_vendor_options_excluded(self):
  rows=load('colors');other=copy.deepcopy(rows[0]);other['lowerVendNo']='OTHER';rows.append(other)
  with patch.object(c,'fetch',return_value=json.dumps(dict(resultCode='200',data=dict(itemOption=rows))).encode()):
   result=c.options(C(),load('product')['itemMain'])
  self.assertEqual(len(result),1);self.assertEqual(result[0]['lowerVendNo'],'CN003546')
 def test_official_color_code_and_soldout_sizes_preserved(self):
  palette,_=c.colors(C(),load('colors'));self.assertEqual(palette,[dict(color_name='NAVY',color_code='78')])
  ctx=C(['size','price','regular_price']);record={}
  with patch.object(c,'options',side_effect=options):c.optional_values(ctx,load('product')['itemMain'],load('colors'),record)
  self.assertEqual(record['size'],'LLL,MMM,SSS,XLL');self.assertEqual(record['price'],35900);self.assertEqual(record['regular_price'],39900)
 def test_entire_body_preserved_no_generated_tables(self):
  ctx=C(['main_image','detail_page','price','regular_price','size'])
  with patch.object(c,'studio_product',return_value=True),patch.object(c,'search',return_value=[LINK]),patch.object(c,'product',return_value=load('product')),patch.object(c,'options',side_effect=options),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/photo.jpg','hash')]),patch.object(c,'image_info',return_value=('JPEG',1080,4500,'hash',False)):
   c.crawl(ctx)
  self.assertFalse(ctx.errors);self.assertEqual(len(ctx.rows),1)
  args,kw=ctx.rows[0];self.assertEqual(args[1],[dict(color_name='NAVY',color_code='78')]);self.assertEqual(kw['shared_detail_html'].count('<img'),6);self.assertNotIn('<table',kw['shared_detail_html']);self.assertEqual(args[0]['판매자번호'],'CN003546')
 def test_invalid_normal_price_nonfatal(self):
  main=load('product')['itemMain'];main['orgSellprice']=0;record={};c.optional_values(C(['regular_price']),main,[],record);self.assertIsNone(record['regular_price'])
if __name__=='__main__':unittest.main()
