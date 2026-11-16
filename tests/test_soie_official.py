import copy,json,sys,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import soie_official as c
TEXT=(ROOT/'tests/fixtures/soie/26FW402.html').read_text(encoding='utf-8-sig')
URL=c.SITE+'/goods/goods_view.php?goodsNo=1000011558'
class Context:
 product_code='26FW402'
 def __init__(self,fields=('main_image','additional_images','detail_page','size','price','regular_price')):self.fields=fields;self.rows=[];self.unmapped=[];self.errors=[]
 def enabled(self,k):return k in self.fields
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def fail_item(self,m):self.errors.append(m)
 def emit_result(self,r):self.rows.append(r)
 def emit_unmapped(self,r,colors,url,shared_detail_html=''):self.unmapped.append((r,colors,url))
class Tests(unittest.TestCase):
 def setUp(self):
  with patch.object(c,'get_html',return_value=(TEXT,URL)):self.page=c.product(Context(),'1000011558')
 def crawl(self,ctx,page=None,hits=None):
  page=self.page if page is None else page
  with patch.object(c,'search',return_value=hits if hits is not None else [('1000011558',page['full'],'소이')]),patch.object(c,'product',return_value=page),patch.object(c,'screen_shots',side_effect=lambda ctx,urls,cache:list(urls)),patch.object(c,'detail_images',side_effect=lambda ctx,urls,cache:list(urls)):c.crawl(ctx)
 def test_actual_model_brand_colors_sizes_and_gallery(self):
  self.assertEqual(self.page['model'],'26FW402');self.assertEqual(self.page['brand'],'소이')
  self.assertEqual([x['color_name'] for x in c.colors_of(self.page)],['블랙','아이보리'])
  self.assertEqual(len(c.gallery(self.page)),3);self.assertEqual(dict(self.page['info'])['치수'],'11호,13호,15호,17호')
 def test_original_detail_only_no_generated_notice_or_size_table(self):
  ctx=Context();self.crawl(ctx);self.assertEqual(len(ctx.unmapped),3)
  markup=ctx.unmapped[0][0]['detail_page'];self.assertEqual(markup.count('<img'),1);self.assertNotIn('<table',markup);self.assertIn('/soie/26W/26FW402.jpg',markup);self.assertNotIn('/banner/',markup);self.assertFalse(ctx.rows)
 def test_price_and_regular_price_selected_independently(self):
  for fields in [(),('price',),('regular_price',),('price','regular_price')]:
   ctx=Context(fields);self.crawl(ctx);self.assertEqual(len(ctx.rows),2)
   for key in ['price','regular_price']:self.assertEqual(key in ctx.rows[0],key in fields)
   if 'price' in fields:self.assertEqual(ctx.rows[0]['price'],19800)
   if 'regular_price' in fields:self.assertEqual(ctx.rows[0]['regular_price'],40000)
 def test_missing_regular_price_does_not_fail_product(self):
  page=copy.deepcopy(self.page);page['doc'].select_one('input[name="set_goods_fixedPrice"]')['value']='0'
  ctx=Context(('regular_price',));self.crawl(ctx,page);self.assertIsNone(ctx.rows[0]['regular_price']);self.assertFalse(ctx.errors)
 def test_unselected_photos_and_detail_do_not_download(self):
  ctx=Context(())
  with patch.object(c,'search',return_value=[('1000011558',self.page['full'],'소이')]),patch.object(c,'product',return_value=self.page),patch.object(c,'gallery') as gallery,patch.object(c,'detail_images') as detail:c.crawl(ctx)
  gallery.assert_not_called();detail.assert_not_called()
 def test_other_brand_from_listing_is_excluded(self):
  with self.assertRaises(c.CrawlError):self.crawl(Context(),hits=[('1000011558','[에꼴리에] 상품(26FW402)','에꼴리에')])
 def test_wrong_detail_brand_even_if_listing_says_soie_is_excluded(self):
  page=copy.deepcopy(self.page);page['brand']='에꼴리에'
  with self.assertRaises(c.CrawlError):self.crawl(Context(),page)
 def test_similar_longer_code_not_accepted(self):
  page=copy.deepcopy(self.page);page['model']='26FW4020';page['full']='[소이] 상품(26FW4020)'
  with self.assertRaises(c.CrawlError):self.crawl(Context(),page)
 def test_single_color_uses_automatic_mapping(self):
  page=copy.deepcopy(self.page);page['doc'].select('select[name="optionNo_0"] option')[-1].decompose();ctx=Context(('main_image','additional_images'))
  with patch.object(c,'auto_slots',return_value=('https://example.com/main.jpg',['https://example.com/extra.jpg'])):self.crawl(ctx,page)
  self.assertEqual(len(ctx.rows),1);self.assertEqual(ctx.rows[0]['color_name'],'블랙');self.assertFalse(ctx.unmapped)
 def test_explicit_option_color_codes_only(self):
  page=copy.deepcopy(self.page);page['doc']=c.soup('<select name="optionNo_0"><option value="">컬러 선택</option><option value="BK">블랙</option><option value="아이보리">아이보리</option></select>')
  self.assertEqual([x['color_code'] for x in c.colors_of(page)],['BK',''])
 def test_selected_detail_missing_is_failure(self):
  with self.assertRaises(c.CrawlError):c.build_detail(Context(),[])
 def test_registration_hash_and_display(self):
  from brand_catalog import definitions
  meta=next(b for b in definitions(ROOT/'brands') if b['id']=='soie_official')
  self.assertEqual(meta['name'],'소이');self.assertEqual(meta['script'],'soie_official.py');self.assertEqual(meta['code_hash'],hashlib.sha256((ROOT/'brands/soie_official.py').read_bytes()).hexdigest())
if __name__=='__main__':unittest.main()
