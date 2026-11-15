import sys,json,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import fashionplus_item as c
class C:
 def __init__(self,fields=()):self.fields=fields;self.product_code='432217746';self.rows=[];self.unmapped=[];self.errors=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return k in self.fields
 def emit_result(self,r):self.rows.append(r)
 def emit_unmapped(self,*a,**kw):self.unmapped.append((a,kw))
 def fail_item(self,m):self.errors.append(m)
class FashionplusItemTests(unittest.TestCase):
 def test_number_or_link_and_external_rejection(self):
  self.assertEqual(c.item_number('432217746'),'432217746');self.assertEqual(c.item_number('https://www.fashionplus.co.kr/goods/detail/432217746?x=1'),'432217746')
  for v in ('CODE123','https://evil.test/goods/detail/432217746'):
   with self.assertRaises(c.CollectionError):c.item_number(v)
 def test_original_product_name_and_standard_prices(self):
  doc=c.BeautifulSoup((ROOT/'tests/fixtures/fashionplus_item/432217746_product.html').read_text(encoding='utf-8'),'html.parser')
  self.assertIn('RMDVM4382',c.product_info(doc,'432217746')['product_name']);self.assertEqual(c.read_price(doc),99000);self.assertEqual(c.read_price(doc,True),319000)
 def test_wrong_canonical_is_not_adopted(self):
  doc=c.BeautifulSoup('<meta property="og:url" content="https://www.fashionplus.co.kr/goods/detail/2"><div class="m__product-info"><p class="text_product">상품</p></div>','html.parser')
  with self.assertRaises(c.CollectionError):c.product_info(doc,'1')
 def test_flat_official_options_and_stock_are_preserved(self):
  raw=(ROOT/'tests/fixtures/fashionplus_item/432217746_options.json').read_bytes()
  with patch.object(c,'fetch',return_value=raw):colors,sizes,rows=c.read_options(C(),'link')
  self.assertEqual(colors,[{'color_name':'BLACK','color_code':''}]);self.assertEqual(sizes,['100','105','110']);self.assertEqual(rows[0]['option_id'],432217748);self.assertEqual(rows[0]['stock'],1)
 def test_nested_unknown_and_no_options_are_not_discarded(self):
  raw=[{'options':[{'_name':'RED','options':[{'_name':'S','_stock':0,'_id':1}]},{'_name':'특수옵션: A+B'}]}]
  with patch.object(c,'fetch',return_value=json.dumps(raw).encode()):colors,sizes,rows=c.read_options(C(),'link')
  self.assertEqual(rows[0]['label'],'RED / S');self.assertEqual(rows[0]['stock'],0);self.assertEqual(sizes,['S']);self.assertEqual(len(colors),2)
  with patch.object(c,'fetch',return_value=b'[]'):colors,_,_=c.read_options(C(),'link')
  self.assertEqual(colors[0]['color_name'],'옵션 미표시')
 def test_gallery_keeps_all_source_photos_without_studio_filter(self):
  doc=c.BeautifulSoup('<div class="m_prodetail-head-thumbnail">'+''.join('<button class="btn_thumbnail"><img src="https://example.test/'+str(i)+'.jpg"></button>' for i in range(8))+'</div>','html.parser')
  with patch.object(c,'image_info',return_value={'width':900,'height':2100}):photos=c.gallery(C(),doc,{})
  self.assertEqual(len(photos),8);self.assertTrue(photos[0].endswith('/0.jpg'))
 def test_original_body_text_tables_and_all_product_images_preserved(self):
  doc=c.BeautifulSoup('<div class="m_prodetail-desc"><div class="mm_pinchzoom-wrapper"><p>원본 상품 설명</p><img src="https://example.test/product.jpg"><table><tr><th>사이즈</th><td>100</td></tr></table><img src="https://example.test/shipping/banner.jpg"><script>alert(1)</script></div></div>','html.parser')
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'image_info',return_value={'width':900,'height':3000}):body=c.detail(C(),'link',{})
  self.assertIn('원본 상품 설명',body);self.assertIn('<table',body);self.assertEqual(body.count('<img'),1);self.assertNotIn('<script',body);self.assertNotIn('shipping',body)
 def test_optional_fields_do_not_download_images_or_body(self):
  ctx=C();doc=c.BeautifulSoup('<div class="m__product-info"><p class="text_product">상품</p></div>','html.parser')
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'read_options',return_value=([dict(color_name='BLACK',color_code='')],['100'],[])),patch.object(c,'gallery') as images,patch.object(c,'detail') as detail:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),1);images.assert_not_called();detail.assert_not_called()
 def test_multicolor_keeps_every_photo_for_manual_mapping(self):
  ctx=C(['main_image']);doc=c.BeautifulSoup('<div class="m__product-info"><p class="text_product">다른 브랜드 상품</p></div>','html.parser')
  palette=[dict(color_name='BLACK',color_code=''),dict(color_name='WHITE',color_code='')]
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'read_options',return_value=(palette,[],[])),patch.object(c,'gallery',return_value=['https://img.test/a.jpg','https://img.test/b.jpg']):c.crawl(ctx)
  self.assertEqual(len(ctx.unmapped),2);self.assertEqual(ctx.unmapped[0][0][1],palette);self.assertIn('a.jpg',ctx.unmapped[0][0][0]['전체 상품사진'])
 def test_multicolor_metadata_only_does_not_require_photo_mapping(self):
  ctx=C();doc=c.BeautifulSoup('<div class="m__product-info"><p class="text_product">상품</p></div>','html.parser')
  palette=[dict(color_name='BLACK',color_code=''),dict(color_name='WHITE',color_code='')]
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'read_options',return_value=(palette,[],[])),patch.object(c,'gallery') as images:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);images.assert_not_called()
 def test_long_options_are_preserved_without_excel_cell_truncation(self):
  text='옵션😀'*10000;record={};c.preserve_text(record,'원본 옵션',text)
  self.assertEqual(''.join(record.values()),text)
  self.assertTrue(all(len(v.encode('utf-16-le'))//2<=28000 for v in record.values()))
if __name__=='__main__':unittest.main()
