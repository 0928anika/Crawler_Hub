import sys,unittest,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import soup_official as c
class Context:
 def __init__(self,fields=()):self.product_code='SC1JK85';self.fields=fields;self.rows=[];self.unmapped=[];self.errors=[]
 def checkpoint(self):pass
 def enabled(self,k):return k in self.fields
 def log(self,*a):pass
 def stage(self,*a):pass
 def fail_item(self,m):self.errors.append(m)
 def emit_result(self,row):self.rows.append(row)
 def emit_unmapped(self,r,colors,url,shared_detail_html=''):self.unmapped.append((r,colors,url,shared_detail_html))
class SoupTests(unittest.TestCase):
 def setUp(self):
  self.source=(ROOT/'tests/fixtures/soup/61128.html').read_text(encoding='utf-8');self.doc=c.parse(self.source);self.colors=c.palette(self.doc,c.SITE)
 def test_product_name_colors_and_soldout_sizes(self):
  self.assertEqual(c.identity(self.doc,'SC1JK85')['product_name'],'울 블렌드 체크 반더블 재킷')
  self.assertIsNone(c.identity(self.doc,'SC1JK8'))
  self.assertEqual([x['color_code'] for x in self.colors],['55','36'])
  self.assertEqual(c.sizes(self.doc,'BROWN(55)'),'055,066')
 def test_prices_exclude_coupons(self):
  self.assertEqual(c.price(Context(),self.doc),199000);self.assertEqual(c.price(Context(),self.doc,True),289000)
 def test_original_body_excludes_only_banners_and_preserves_size(self):
  urls=c.detail_urls(self.doc,c.SITE);self.assertEqual(len(urls),11)
  self.assertFalse(any('banner' in u or 'detail_bottom' in u for u in urls))
  area=self.doc.select_one('#prdDetail .cont')
  area.append(c.parse('<img src="https://images.dcollect.co.kr/GdsImages/S/C/1/JK/SC1JK85_size.jpg"><img src="https://images.dcollect.co.kr/GdsImages/S/C/1/JK/SC1JK85_bottom.jpg">'))
  urls=c.detail_urls(self.doc,c.SITE);self.assertEqual(len(urls),13)
  with patch.object(c,'detail_image',return_value=True):body=c.build_detail(Context(),urls,{})
  self.assertIn('SC1JK85_size.jpg',body);self.assertIn('SC1JK85_bottom.jpg',body);self.assertNotIn('<table',body)
 def test_shared_variant_cover_never_assigned_to_each_color(self):
  self.assertEqual(c.explicit_connections(self.doc,self.colors,c.SITE),{})
 def test_official_color_sections_not_numeric_photo_suffix(self):
  urls=c.detail_urls(self.doc,c.SITE);mapping=c.body_photo_sections(urls,'SC1JK85',self.colors)
  self.assertEqual(mapping[next(u for u in urls if u.endswith('_70.jpg'))][0],'DK-GREY(36)')
  self.assertEqual(mapping[next(u for u in urls if u.endswith('_74.jpg'))][0],'BROWN(55)')
  self.assertFalse(c.body_photo_sections([u for u in urls if '_img.' not in u],'SC1JK85',self.colors))
 def test_optional_images_skip_network(self):
  ctx=Context()
  with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=61128']),patch.object(c,'fetch',return_value=(self.source,c.SITE+'/product/detail.html?product_no=61128')),patch.object(c,'photo') as photo,patch.object(c,'build_detail') as detail:
   c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);photo.assert_not_called();detail.assert_not_called()
 def test_partial_mapping_emits_both_types_and_shared_body(self):
  ctx=Context(('main_image','additional_images','detail_page','size'))
  with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=61128']),patch.object(c,'fetch',return_value=(self.source,c.SITE+'/product/detail.html?product_no=61128')),patch.object(c,'build_detail',return_value='<img src="https://images.dcollect.co.kr/body.jpg">'),patch.object(c,'photo',side_effect=lambda ctx,u,cache,panel=False:dict(data=u.encode())):
   c.crawl(ctx)
  self.assertEqual(len(ctx.rows),8);self.assertEqual(len(ctx.unmapped),4)
  self.assertEqual({r['color_code'] for r in ctx.rows},{'36','55'})
  self.assertTrue(all(r[3]==ctx.rows[0]['detail_page'] for r in ctx.unmapped))
 def test_tall_panel_excluded_from_gallery_but_kept_in_detail(self):
  from io import BytesIO
  from PIL import Image
  data=BytesIO();Image.new('RGB',(950,2110),'white').save(data,format='JPEG')
  with patch.object(c,'public_image',return_value=dict(format='JPEG',width=950,height=2110,data=data.getvalue())):
   self.assertIsNone(c.photo(Context(),'https://images.dcollect.co.kr/panel.jpg',{}))
  self.assertTrue(any(u.endswith('_36_img.jpg') for u in c.detail_urls(self.doc,c.SITE)))
 def test_external_images_rejected(self):
  self.assertFalse(c.image_url('https://evil.test/photo.jpg'));self.assertTrue(c.image_url('//images.dcollect.co.kr/size.jpg'))
if __name__=='__main__':unittest.main()
