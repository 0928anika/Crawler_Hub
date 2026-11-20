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
  doc.append(c.BeautifulSoup('<script type="application/ld+json">{"@type":"Product","sku":"seller_MODEL","mpn":432217746}</script>','html.parser'))
  self.assertIn('RMDVM4382',c.product_info(doc,'432217746')['product_name']);self.assertEqual(c.read_price(doc),99000);self.assertEqual(c.read_price(doc,True),319000)
 def test_wrong_canonical_is_not_adopted(self):
  doc=c.BeautifulSoup('<meta property="og:url" content="https://www.fashionplus.co.kr/goods/detail/2"><div class="m__product-info"><p class="text_product">상품</p></div><script type="application/ld+json">{"@type":"Product","sku":"seller_MODEL","mpn":432217746}</script>','html.parser')
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
 def test_image_lists_do_not_export_bullet_markers_or_empty_rows(self):
  doc=c.BeautifulSoup('<div class="m_prodetail-desc"><div class="mm_pinchzoom-wrapper"><ul><li><img src="https://img.test/product.jpg"></li><li></li><li><img src="https://img.test/shipping.jpg"></li></ul><ul><li>원본 설명</li></ul><ol><li>첫 번째 순서</li></ol></div></div>','html.parser')
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'image_info',return_value={'width':900,'height':3000}):body=c.detail(C(),'link',{})
  result=c.BeautifulSoup(body,'html.parser');self.assertFalse(result.select('ul'));self.assertEqual(len(result.select('li')),1);self.assertEqual(len(result.select('img')),1);self.assertIn('원본 설명',body);self.assertTrue(result.select('ol li'))
 def test_optional_fields_do_not_download_images_or_body(self):
  ctx=C();doc=c.BeautifulSoup('<div class="m__product-info"><p class="text_product">상품</p></div><script type="application/ld+json">{"@type":"Product","sku":"seller_MODEL","mpn":432217746}</script>','html.parser')
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'read_options',return_value=([dict(color_name='BLACK',color_code='')],['100'],[])),patch.object(c,'gallery') as images,patch.object(c,'detail') as detail:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),1);images.assert_not_called();detail.assert_not_called()
 def test_multicolor_keeps_every_photo_for_manual_mapping(self):
  ctx=C(['main_image']);doc=c.BeautifulSoup('<div class="m__product-info"><p class="text_product">다른 브랜드 상품</p></div><script type="application/ld+json">{"@type":"Product","sku":"seller_MODEL","mpn":432217746}</script>','html.parser')
  palette=[dict(color_name='BLACK',color_code=''),dict(color_name='WHITE',color_code='')]
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'read_options',return_value=(palette,[],[])),patch.object(c,'gallery',return_value=['https://img.test/a.jpg','https://img.test/b.jpg']),patch.object(c,'detail_candidates',return_value=[]):c.crawl(ctx)
  self.assertEqual(len(ctx.unmapped),2);self.assertEqual(ctx.unmapped[0][0][1],palette);self.assertIn('a.jpg',ctx.unmapped[0][0][0]['전체 상품사진'])
 def test_multicolor_metadata_only_does_not_require_photo_mapping(self):
  ctx=C();doc=c.BeautifulSoup('<div class="m__product-info"><p class="text_product">상품</p></div><script type="application/ld+json">{"@type":"Product","sku":"seller_MODEL","mpn":432217746}</script>','html.parser')
  palette=[dict(color_name='BLACK',color_code=''),dict(color_name='WHITE',color_code='')]
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'read_options',return_value=(palette,[],[])),patch.object(c,'gallery') as images:c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);images.assert_not_called()
 def test_body_candidates_keep_individual_cuts_but_not_long_panels_or_charts(self):
  from io import BytesIO
  from PIL import Image
  doc=c.BeautifulSoup('<div class="m_prodetail-desc"><div class="mm_pinchzoom-wrapper">'+''.join('<img src="https://img.test/'+name+'">' for name in ('cut.jpg','portrait.jpg','long.jpg','size.jpg','shipping.jpg'))+'</div></div>','html.parser')
  def info(ctx,url,cache,detail=False):
   w,h=(900,4000) if 'long' in url else (900,1600) if 'portrait' in url else (900,900)
   b=BytesIO();Image.new('RGB',(w,h)).save(b,format='JPEG');return dict(width=w,height=h,data=b.getvalue(),format='JPEG')
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'image_info',side_effect=info):photos=c.detail_candidates(C(),'link',{})
  self.assertEqual([u.rsplit('/',1)[-1] for u in photos],['cut.jpg','portrait.jpg'])
 def test_cover_stays_first_and_resized_duplicate_is_not_added_again(self):
  cover='https://img.fashionplus.co.kr/product.jpg?RS=800x1000&AR=0';extra='https://img.test/extra.jpg';body='https://img.fashionplus.co.kr/product.jpg';new='https://img.test/detail-cut.jpg'
  self.assertEqual(c.merge_photos([cover,extra],[body,new],{}),[cover,extra,new])
 def test_body_document_requested_once_for_detail_and_mapping(self):
  doc=c.BeautifulSoup('<div class="m_prodetail-desc"><div class="mm_pinchzoom-wrapper"><p>상품 설명</p></div></div>','html.parser');cache={}
  with patch.object(c,'soup_get',return_value=doc) as request:
   c.detail(C(),'link',cache);self.assertEqual(c.detail_candidates(C(),'link',cache),[])
  self.assertEqual(request.call_count,1)
 def test_long_options_are_preserved_without_excel_cell_truncation(self):
  text='옵션😀'*10000;record={};c.preserve_text(record,'원본 옵션',text)
  self.assertEqual(''.join(record.values()),text)
  self.assertTrue(all(len(v.encode('utf-16-le'))//2<=28000 for v in record.values()))
 def test_parent_id_and_sku_are_not_derived_from_name(self):
  doc=c.BeautifulSoup('<meta property="og:url" content="https://www.fashionplus.co.kr/goods/detail/446631636"><div class="m__product-info"><p class="text_product">품번 없는 상품명</p></div><script type="application/ld+json">{"@type":"Product","sku":"775951_IF95NSEJ6230","mpn":446631636}</script>','html.parser')
  record=c.product_info(doc,'446631636');self.assertEqual(record['product_code'],'775951_IF95NSEJ6230');self.assertEqual(record['상위품목ID'],'446631636')
  doc.select_one('script').decompose();doc.append(c.BeautifulSoup('<table><tr><th>상품번호</th><td>775951_IF95NSEJ6230</td></tr></table>','html.parser'))
  self.assertEqual(c.product_info(doc,'446631636')['product_code'],'775951_IF95NSEJ6230')
  doc.select_one('table').decompose()
  with self.assertRaisesRegex(c.CollectionError,'SKU'):c.product_info(doc,'446631636')
 def test_export_keeps_parent_before_sku_and_mapping_preserves_values(self):
  import color_mapping
  from types import SimpleNamespace
  from unittest.mock import patch
  brand=json.loads((ROOT/'brands/fashionplus_item.json').read_text('utf-8'))
  job=SimpleNamespace(brand=brand,items=[dict(raw={'상위품목ID':'446631636','품번':'775951_IF95NSEJ6230'})],selected=['main_image'])
  self.assertEqual(color_mapping.result_headers(job)[:2],['상위품목ID','품번'])
  with patch.object(color_mapping,'snapshot',return_value={'finalized':True}),patch.object(color_mapping,'final_items',return_value=job.items):sheet=color_mapping.export_sheet(job)
  self.assertEqual(sheet[1][:2],['446631636','775951_IF95NSEJ6230'])
 def test_detail_only_gallery_and_all_body_cuts_reach_mapping(self):
  ctx=C(['main_image']);doc=c.BeautifulSoup('<div class="m__product-info"><p class="text_product">상품</p></div><table><tr><th>상품번호</th><td>seller_MODEL</td></tr></table>','html.parser')
  colors=[dict(color_name='BLACK',color_code=''),dict(color_name='WHITE',color_code='')]
  photos=['https://img.test/body'+str(i)+'.jpg' for i in range(8)]
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'read_options',return_value=(colors,[],[])),patch.object(c,'gallery',return_value=[]),patch.object(c,'detail_candidates',return_value=photos):c.crawl(ctx)
  self.assertEqual([a[0][2] for a in ctx.unmapped],photos);self.assertTrue(all(a[0][0]['product_code']=='seller_MODEL' for a in ctx.unmapped))
 def test_single_color_exports_all_detail_photos_not_only_five(self):
  ctx=C(['main_image','additional_images']);doc=c.BeautifulSoup('<div class="m__product-info"><p class="text_product">상품</p></div><table><tr><th>상품번호</th><td>seller_MODEL</td></tr></table>','html.parser')
  photos=['https://img.test/body'+str(i)+'.jpg' for i in range(10)]
  with patch.object(c,'soup_get',return_value=doc),patch.object(c,'read_options',return_value=([dict(color_name='BLACK',color_code='')],[],[])),patch.object(c,'gallery',return_value=photos[:2]),patch.object(c,'detail_candidates',return_value=photos[2:]):c.crawl(ctx)
  self.assertEqual(ctx.rows[0]['main_image'],photos[0]);self.assertEqual(ctx.rows[0]['additional_images'].split(','),photos[1:])
 def test_mapping_final_excel_keeps_parent_sku_and_all_selected_cuts(self):
  import tempfile,color_mapping as m
  from test_hybrid_mapping import HybridMappingTests,item
  from openpyxl import Workbook,load_workbook
  with tempfile.TemporaryDirectory() as folder:
   code='775951_IF95NSEJ6230';items=[item(i,code,'60','https://img/photo'+str(i)+'.jpg',True) for i in range(10)]
   for row in items:row['raw']['상위품목ID']='446631636'
   job=HybridMappingTests().job(folder,items);job.brand.update(json.loads((ROOT/'brands/fashionplus_item.json').read_text('utf-8')))
   view=m.snapshot(job);group=view['groups'][0]
   for color in group['colors']:
    view=m.save_color(job,dict(expected_revision=view['revision'],group_id=group['id'],color_id=color['id'],image_ids=[im['id'] for im in group['images']]))
   view=m.finalize(job,dict(expected_revision=view['revision']));sheet=m.export_sheet(job)
   self.assertEqual(sheet[0][:2],['상위품목ID','품번']);self.assertEqual(sheet[1][:2],['446631636',code]);self.assertEqual(len(sheet[1][sheet[0].index('추가이미지')].split(',')),9)
   path=Path(folder)/'result.xlsx';book=Workbook()
   for row in sheet:book.active.append(row)
   book.save(path);check=load_workbook(path);self.assertEqual(check.active['A2'].value,'446631636');self.assertEqual(check.active['B2'].value,code);check.close()
 def test_child_page_resolves_parent_sku_and_all_parent_options(self):
  folder=ROOT/'tests/fixtures/fashionplus_item';child=c.BeautifulSoup((folder/'446631639-child.html').read_text(encoding='utf-8'),'html.parser');parent=c.BeautifulSoup((folder/'446631636-parent.html').read_text(encoding='utf-8'),'html.parser')
  payload=(folder/'446631636-options.json').read_bytes();ctx=C(['size','price']);ctx.product_code='446631639'
  with patch.object(c,'soup_get',side_effect=[child,parent]),patch.object(c,'fetch',return_value=payload):c.crawl(ctx)
  self.assertEqual(len(ctx.rows),1);r=ctx.rows[0]
  self.assertEqual((r['product_code'],r['상위품목ID'],r['하위품목ID'],r['입력품목ID']),('775951_IF95NSEJ6230','446631636','446631639','446631639'))
  self.assertEqual((r['color_name'],r['size'],r['price']),('베이지','55,66',289000));self.assertIn('66',r['size'])
 def test_child_not_in_parent_options_is_not_collected(self):
  folder=ROOT/'tests/fixtures/fashionplus_item';child=c.BeautifulSoup((folder/'446631639-child.html').read_text(encoding='utf-8'),'html.parser');parent=c.BeautifulSoup((folder/'446631636-parent.html').read_text(encoding='utf-8'),'html.parser');ctx=C(['size']);ctx.product_code='446631639'
  with patch.object(c,'soup_get',side_effect=[child,parent]),patch.object(c,'read_options',return_value=([dict(color_name='베이지',color_code='')],['66'],[dict(label='베이지 66',price=242760,option_id=446631640)])):
   with self.assertRaisesRegex(c.CollectionError,'공식 옵션에 없습니다'):c.crawl(ctx)
  self.assertFalse(ctx.rows)
 def test_child_parent_sku_mismatch_is_rejected(self):
  folder=ROOT/'tests/fixtures/fashionplus_item';child=c.BeautifulSoup((folder/'446631639-child.html').read_text(encoding='utf-8'),'html.parser');parent=c.BeautifulSoup((folder/'446631636-parent.html').read_text(encoding='utf-8').replace('775951_IF95NSEJ6230','DIFFERENT_SKU'),'html.parser')
  with patch.object(c,'soup_get',side_effect=[child,parent]):
   with self.assertRaisesRegex(c.CollectionError,'SKU 불일치'):c.resolve_product(C([]),'446631639')
 def test_child_of_multicolor_parent_keeps_photo_mapping_manual(self):
  folder=ROOT/'tests/fixtures/fashionplus_item';child=c.BeautifulSoup((folder/'446631639-child.html').read_text(encoding='utf-8'),'html.parser');parent=c.BeautifulSoup((folder/'446631636-parent.html').read_text(encoding='utf-8'),'html.parser');ctx=C(['main_image']);ctx.product_code='446631639'
  colors=[dict(color_name='베이지',color_code=''),dict(color_name='블랙',color_code='')]
  raw=[dict(label='베이지 55',price=242760,option_id=446631639),dict(label='블랙 66',price=242760,option_id=123)]
  with patch.object(c,'soup_get',side_effect=[child,parent]),patch.object(c,'read_options',return_value=(colors,['55','66'],raw)),patch.object(c,'gallery',return_value=['https://img.test/a.jpg']),patch.object(c,'detail_candidates',return_value=[]):c.crawl(ctx)
  self.assertFalse(ctx.rows);self.assertEqual(len(ctx.unmapped),1);self.assertEqual(ctx.unmapped[0][0][1],colors)
if __name__=='__main__':unittest.main()
