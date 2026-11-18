import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import laughingchild_official as c
FIX=ROOT/'tests/fixtures/laughingchild'
def product():return c.BeautifulSoup((FIX/'14555_product.html').read_bytes(),'html.parser')
class C:
 product_code='86c1916070'
 def __init__(self,fields=()):self.fields=fields;self.rows=[];self.logs=[];self.unmapped=[];self.errors=[]
 def enabled(self,k):return k in self.fields
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):self.logs.append(a)
 def fail_item(self,*a):self.errors.append(a)
 def emit_result(self,r):self.rows.append(r)
 def emit_unmapped(self,*a,**kw):self.unmapped.append((a,kw))
class LaughingchildTests(unittest.TestCase):
 def test_both_formats_search_same_hyphen_code(self):
  self.assertEqual(c.normalize_input('86c1916070'),'86C19-160-70');self.assertEqual(c.normalize_input('86c19-160-70'),'86C19-160-70')
  with patch.object(c,'page',return_value=c.BeautifulSoup((FIX/'14555_search.html').read_bytes(),'html.parser')) as page:
   self.assertEqual(c.search(C(),'86C19-160-70'),[c.SITE+'/product/detail.html?product_no=14555'])
  self.assertIn('keyword=86C19-160-70',page.call_args.args[1])
 def test_brand_and_title_model_verified_not_internal_product_code(self):
  doc=product();self.assertEqual(c.field(doc,'자체상품코드'),'316664');self.assertEqual(c.identity(doc,'86C19-160-70')[0],'밍크 롱 퍼리 코트');self.assertIsNone(c.identity(doc,'86C19-160-71'))
  for row in doc.select('.infoArea .xans-product-detaildesign tr'):
   if row.th and c.text(row.th)=='브랜드':row.td.string='OTHER'
  self.assertIsNone(c.identity(doc,'86C19-160-70'))
 def test_single_color_and_soldout_sizes_preserved(self):
  doc=product();self.assertEqual(c.option_labels(doc,'색상'),['PINK']);self.assertEqual(c.option_labels(doc,'사이즈'),['100','110','120','130','140'])
 def test_body_and_original_size_notice_tables_preserved(self):
  with patch.object(c,'image',return_value=dict(width=1000,height=1400)) as image:
   markup=c.detail(C(),product(),{})
  doc=c.BeautifulSoup(markup,'html.parser');self.assertEqual(len(doc.select('img')),11);self.assertEqual(len(doc.select('table')),4)
  self.assertIn('미얀마',c.text(doc));self.assertIn('20260801',c.text(doc));self.assertIn('90~95',c.text(doc));self.assertIn('23~17',c.text(doc))
  self.assertFalse(any('/notice/' in call.args[1] or 'purchase_tip_icon' in call.args[1] for call in image.call_args_list));self.assertNotIn('onerror=',markup)
 def test_marketing_removed_color_fabric_separated_into_table(self):
  with patch.object(c,'image',return_value=dict(width=1000,height=1400)):markup=c.detail(C(),product(),{})
  doc=c.BeautifulSoup(markup,'html.parser')
  for phrase in ['구매 TIP','AI를 활용','상품 소개는 대표 컬러','추천 TPO','추천 시즌','COLORPINK','FABRIC']:self.assertNotIn(phrase,doc.get_text())
  table=doc.select_one('table');self.assertEqual(c.text(table.select_one('tr th')),'색상');self.assertEqual(c.text(table.select_one('tr td')),'PINK');self.assertIn('폴리에스터',c.text(table))
 def test_access_check_is_error_not_missing_product(self):
  with patch.object(c,'fetch',return_value=b'<html><h2>For a secure experience, please check your access</h2></html>'):
   with self.assertRaisesRegex(c.CrawlError,'접속 확인'):c.page(C(),'https://www.looxloo.com/product/detail.html?product_no=1')
 def test_price_and_normal_independent(self):
  for fields in [('price',),('regular_price',),('price','regular_price'),()]:
   ctx=C(fields)
   with patch.object(c,'search',return_value=['link']),patch.object(c,'page',return_value=product()):c.crawl(ctx)
   row=ctx.rows[0];self.assertEqual(row.get('price'),111300 if 'price' in fields else None);self.assertEqual(row.get('regular_price'),159000 if 'regular_price' in fields else None)
 def test_multiple_colors_collected_for_manual_mapping(self):
  doc=product();group=doc.select_one('.infoArea [option_title="색상"]');node=doc.new_tag('li',option_value='BLUE');group.append(node);ctx=C(['main_image'])
  with patch.object(c,'search',return_value=['link']),patch.object(c,'page',return_value=doc),patch.object(c,'gallery',return_value=['photo1','photo2']),patch.object(c,'detail_photo_candidates',side_effect=lambda ctx,doc,code,cache,existing:existing):c.crawl(ctx)
  self.assertFalse(ctx.rows);self.assertEqual(len(ctx.unmapped),2);self.assertEqual(len(ctx.unmapped[0][0][1]),2)
 def test_exact_search_no_result_alert_triggers_family_search(self):
  empty=c.BeautifulSoup("<script>alert('검색결과가 없습니다.');history.back();</script>",'html.parser')
  full=c.BeautifulSoup((FIX/'14555_search.html').read_bytes(),'html.parser')
  with patch.object(c,'page',side_effect=[empty,full]) as page:links=c.search(C(),'86C19-160-70')
  self.assertTrue(links);self.assertIn('keyword=86C19-160',page.call_args.args[1])
 def test_unknown_search_response_not_reported_as_missing(self):
  with patch.object(c,'page',return_value=c.BeautifulSoup('<html></html>','html.parser')):
   with self.assertRaisesRegex(c.CrawlError,'상품 없음으로 단정하지 않음'):c.search(C(),'86C19-160-70')
 def test_family_product_requires_actual_requested_image_sku(self):
  doc=product()
  self.assertIsNone(c.identity(doc,'86C19-160-71'))
  area=doc.select_one('#prdDetail');node=doc.new_tag('img',attrs={'data-origin-src':'https://example.com/86C1916071_00.jpg'});area.append(node)
  self.assertIsNotNone(c.identity(doc,'86C19-160-71'));self.assertIsNone(c.identity(doc,'86C29-160-71'))
 def test_missing_option_widgets_use_explicit_product_notice(self):
  doc=product()
  for group in doc.select('.infoArea [option_title]'):group.decompose()
  ctx=C(['size'])
  with patch.object(c,'search',return_value=['link']),patch.object(c,'page',return_value=doc):c.crawl(ctx)
  self.assertEqual(ctx.rows[0]['color'],'PINK');self.assertEqual(ctx.rows[0]['size'],'100,110,120,130,140')
 def test_explicit_two_letter_codes_not_inferred_from_names(self):
  self.assertEqual(c.color_parts('BR BROWN'),dict(color_name='BROWN',color_code='BR'))
  self.assertEqual(c.color_parts('BROWN (BR)'),dict(color_name='BROWN',color_code='BR'))
  self.assertEqual(c.color_parts('BROWN'),dict(color_name='BROWN',color_code=''))
  doc=product();node=doc.select_one('.infoArea [option_title="색상"] li');node['option_value']='BR';node.select_one('span').string='BROWN'
  self.assertEqual(c.color_palette(doc,['BR']),[dict(color_name='BROWN',color_code='BR')])
 def test_real_sdk_manual_mode_accepts_automatic_single_color(self):
  import tempfile
  from crawler_sdk import CrawlContext
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   events=[];ctx=CrawlContext('unused.xlsx',str(Path(folder)/'result.xlsx'),0,['main_image'],['품번','상품명','색상','상품 링크','대표이미지'],lambda:None,lambda *a,**kw:events.append((a,kw)),input_item={'품번':'86C1916070'},mapping_mode='manual')
   with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=14555']),patch.object(c,'page',return_value=product()),patch.object(c,'gallery',return_value=['https://example.com/photo.jpg']):c.crawl(ctx)
   ctx.finish();self.assertEqual(ctx._automatic_count,1);self.assertFalse(ctx._failures)
 def test_detail_restores_intro_and_caption_but_not_tip_or_ai_disclaimer(self):
  with patch.object(c,'image',return_value=dict(width=1000,height=1400)):markup=c.detail(C(),product(),{})
  self.assertIn('부드러운 핑크 페이크 퍼 소재에',markup);self.assertIn('진한 핑크 배색 카라와 스냅 여밈이 돋보이는 디자인',markup)
  self.assertNotIn('구매 TIP',markup);self.assertNotIn('AI를 활용',markup)
 def test_detail_photo_candidates_include_all_style_colors_not_tables_or_other_styles(self):
  doc=product();area=doc.select_one('#prdDetail > .cont')
  extra=doc.new_tag('img',attrs={'data-origin-src':'https://example.com/86C1916071_01.jpg'});area.append(extra)
  wrong=doc.new_tag('img',attrs={'data-origin-src':'https://example.com/86C2916071_01.jpg'});area.append(wrong)
  table=doc.new_tag('table');table.append(doc.new_tag('img',attrs={'data-origin-src':'https://example.com/86C1916071_size.jpg'}));area.append(table)
  def inspect(ctx,url,cache):return dict(width=1000,height=1400,format='JPEG',frames=1,bytes=30000,digest=url)
  with patch.object(c,'image',side_effect=inspect):photos=c.detail_photo_candidates(C(),doc,'86C19-160-70',{},['gallery-photo'])
  self.assertEqual(len(photos),9);self.assertIn('https://example.com/86C1916071_01.jpg',photos);self.assertNotIn('https://example.com/86C2916071_01.jpg',photos);self.assertFalse(any('size.jpg' in u for u in photos))
 def test_detail_disabled_does_not_build_tables_or_download_body(self):
  ctx=C(['size'])
  with patch.object(c,'search',return_value=['link']),patch.object(c,'page',return_value=product()),patch.object(c,'detail') as detail:c.crawl(ctx)
  detail.assert_not_called();self.assertNotIn('detail_page',ctx.rows[0])
if __name__=='__main__':unittest.main()
