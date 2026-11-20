import sys,unittest,json,io
from pathlib import Path
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import fashionplus_integrated as c
class C:
 product_code='MODEL123'
 def __init__(self):self.rows=[];self.errors=[]
 def enabled(self,k):return k in ('main_image','additional_images','detail_page','size','price','regular_price')
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def emit_unmapped(self,r,colors,url,**kw):self.rows.append((r,colors,url))
 def fail_item(self,m):self.errors.append(m)
def product(number,brand,sku):
 return c.BeautifulSoup('<meta property="og:url" content="'+c.BASE+'/goods/detail/'+number+'"><div class="m__product-info"><p class="text_product">품번 없는 상품명</p></div><div class="m_prodetail-head"><div class="mm_breadcrumb"><a href="brand-shop">'+brand+'</a></div></div><table><tr><th>상품번호</th><td>'+sku+'</td></tr></table>','html.parser')
class Tests(unittest.TestCase):
 def test_all_brands_exact_sku_match_and_every_candidate_are_manual(self):
  docs=[product('1','브랜드A','seller_MODEL123'),product('2','브랜드B','other_MODEL123'),product('3','브랜드C','seller_MODEL1234')];ctx=C()
  def photos(ctx,doc,link,cache):
   urls=[link+'/one.jpg',link+'/long.jpg']
   for u in urls:cache[(u,True)]={'data':b'photo'}
   return urls,c.BeautifulSoup('<div>소재 정보<table><tr><td>실측</td></tr></table></div>','html.parser')
  with patch.object(c,'search',return_value=[(c.BASE+'/goods/detail/'+str(i),'상품','') for i in (1,2,3)]),patch.object(c,'soup_get',side_effect=docs),patch.object(c,'read_options',return_value=([dict(color_name='블랙',color_code='')],['95'],[])),patch.object(c,'all_candidates',side_effect=photos):c.crawl(ctx)
  self.assertEqual(len(ctx.rows),4);self.assertFalse(ctx.errors);self.assertEqual({r[0]['브랜드'] for r in ctx.rows},{'브랜드A','브랜드B'});self.assertTrue(all(r[0]['product_code']=='MODEL123' for r in ctx.rows));self.assertTrue(all('실측' in r[0]['원본 상세 텍스트'] for r in ctx.rows))
 def test_long_images_and_size_charts_survive_but_embedded_shipping_does_not(self):
  doc=c.BeautifulSoup('<div class="m_prodetail-head-thumbnail"><button class="btn_thumbnail"><img src="https://img.test/one.jpg"></button></div>','html.parser');area=c.BeautifulSoup('<div><img src="https://img.test/long.jpg"><img src="https://img.test/size.jpg"><img src="https://img.test/uuid.jpg"><img src="https://img.test/shipping/banner.jpg"></div>','html.parser')
  count=[0]
  def info(ctx,url,cache,detail=False):
   count[0]+=1;buf=io.BytesIO();Image.new('RGB',(600,6000 if 'long' in url else 800),(count[0]*20,80,90)).save(buf,format='JPEG');return dict(data=buf.getvalue(),width=600,height=6000 if 'long' in url else 800,format='JPEG')
  with patch.object(c,'detail_document',return_value=area),patch.object(c,'image_info',side_effect=info),patch.object(c,'ocr_many',return_value=['','상품 설명','사이즈표','판매자 안내 배송 안내']):urls,_=c.all_candidates(C(),doc,'link',{})
  self.assertEqual(urls,['https://img.test/one.jpg','https://img.test/long.jpg','https://img.test/size.jpg'])
 def test_registration_requires_both_mapping_layers(self):
  from registry import get_brand
  d=get_brand('fashionplus_integrated');self.assertTrue(d['available']);self.assertEqual(d['mapping_mode'],'manual');self.assertEqual(d['mapping_detail_mode'],'manual_ordered');self.assertFalse(d['hybrid_mapping'])
 def test_required_image_and_detail_fields_are_retained_by_validation(self):
  from registry import get_brand
  from storage import validate_input
  result,_,_=validate_input(dict(codes='MODEL123',selected=[]),get_brand('fashionplus_integrated'))
  self.assertTrue(result['valid']);self.assertTrue(set(['main_image','additional_images','detail_page']).issubset(result['selected']))
 def test_forced_thumbnail_ratio_is_removed_before_validation_and_ocr(self):
  url='https://img.fashionplus.co.kr/mall/assets/product_img/body.jpg?RS=800x1072&AR=0'
  original=url.split('?')[0];doc=c.BeautifulSoup('<div class="m_prodetail-head-thumbnail"><button class="btn_thumbnail"><img src="'+url+'"></button></div>','html.parser')
  area=c.BeautifulSoup('<div><img src="'+original+'"></div>','html.parser')
  buf=io.BytesIO();Image.new('RGB',(800,5132),'white').save(buf,'JPEG')
  def info(ctx,requested,cache,detail=False):
   self.assertEqual(requested,original);return dict(data=buf.getvalue(),width=800,height=5132,format='JPEG')
  with patch.object(c,'detail_document',return_value=area),patch.object(c,'image_info',side_effect=info) as check,patch.object(c,'ocr_many',return_value=['상품 정보']):
   urls,_=c.all_candidates(C(),doc,'link',{})
  self.assertEqual(urls,[original]);check.assert_called_once()
if __name__=='__main__':unittest.main()
