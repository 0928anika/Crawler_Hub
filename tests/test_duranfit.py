import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('duranfit_test',ROOT/'brands/duranfit_official.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/duranfit'
def source():return c.soup((FIX/'product.html').read_text(encoding='utf-8'))
class C:
 def checkpoint(self):pass
 def log(self,*args):pass
class DuranTests(unittest.TestCase):
 def test_both_input_forms_search_official_code(self):
  for value in ['DBF6W-9242','dbf6w9242']:
   base=c.canonical_code(value);self.assertEqual(base,'DBF6W-9242')
   with patch.object(c,'get_html',return_value=((FIX/'search.html').read_text(encoding='utf-8'),c.CONFIG['site'])) as request:
    self.assertEqual(c.find_products(C(),base),['1983'])
   self.assertIn('keyword=DBF6W-9242',request.call_args[0][1])
 def test_name_and_options(self):
  d=source();text=str(d);raw,base,color=c.identity(d,text,'DBF6W-9242',c.info_rows(d))
  self.assertEqual(c.clean_name(raw,c.CONFIG['aliases'],[base]),'코듀로이 융기모 와이드 팬츠')
  rows=dict(c.page_colors(d,text,{}));self.assertEqual(set(rows),{'크림','카키','버건디','블랙'})
  for row in rows.values():self.assertEqual(row['sizes'],['S','M','L'])
  self.assertFalse(c.linked_images(d,text,c.CONFIG['site']))
 def test_only_product_body_keeps_size_image(self):
  imgs,notice,blocks=c.detail_parts(C(),source(),c.CONFIG['site'],'DBF6W-9242')
  self.assertEqual(len(imgs),8);self.assertTrue(imgs[-1].endswith('_size.jpg'));self.assertEqual((notice,blocks),([],[]))
  self.assertTrue(all('DBF6W-9242_' in url for url in imgs))
 def test_text_size_and_notice_are_tables_when_present(self):
  d=c.soup('<div id="-description"><table><tr><th>사이즈</th><th>총장</th></tr><tr><td>M</td><td>100</td></tr></table><table><tr><th>제품소재</th><td>면100%</td></tr><tr><th>제조국</th><td>한국</td></tr></table></div>')
  images,notice,blocks=c.detail_parts(C(),d,c.CONFIG['site'],'DBF6W-9242')
  self.assertFalse(images);self.assertEqual(notice,[('제품소재','면100%'),('제조국','한국')]);self.assertIn('<table',c.size_section(blocks))
 def test_missing_detail_is_allowed_and_external_images_rejected(self):
  self.assertEqual(c.detail_parts(C(),c.soup(''),c.CONFIG['site'],'DBF6W-9242'),([],[],[]))
  self.assertFalse(c.body_image_url('https://other.example/DBF6W-9242_size.jpg',c.CONFIG['site']))
 def test_gallery_urls_are_declared_by_page(self):
  groups=c.gallery_groups(C(),source(),c.CONFIG['site'],'1983');self.assertEqual(len(groups),6)
  self.assertTrue(all(len(group)==1 for group in groups));self.assertTrue(all('/web/product/' in group[0] for group in groups))
 def test_different_sizes_survive_mapping_by_color(self):
  import tempfile,threading
  from types import SimpleNamespace
  import color_mapping as mapping
  from color_palette import normalize_palette
  from schema import preview
  text=(FIX/'DTF6W-3106_options.html').read_text(encoding='utf-8')
  options=c.page_colors(c.soup(text),text,{})
  colors=[dict(c.color_entry(name),**data) for name,data in options]
  palette=normalize_palette(c.palette_of(colors))
  expected={'블루':'FREE','카키':'FREE','베이지':'FREE','브라운':'FREE,L','블랙':'FREE,L'}
  self.assertEqual({p['color_name']:p['size'] for p in palette},expected)
  headers=['품번','상품명','색상','상품 링크','사이즈','대표이미지','추가이미지']
  values=['DTF6W-3106','상품','전체색상','https://duran-fit.com/product/detail.html?product_no=1965','FREE,L','https://example.com/p.jpg','']
  row=preview(headers,values,['size','main_image','additional_images'])
  row.update(id='0',task_id='0',raw=dict(zip(headers,values)),unmapped=dict(colors=palette,image_url=values[5],shared_detail_html=''))
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=SimpleNamespace(brand=dict(mapping_mode='manual',image_mode='url',output_headers=headers),items=[row],selected=['size','main_image','additional_images'],queue=[dict(id='0',status='SUCCESS')],dir=Path(folder),state='completed',data_lock=threading.RLock(),save_lock=threading.RLock(),checkpoint=lambda:None,save=lambda:None)
   view=mapping.snapshot(job);group=view['groups'][0]
   for color in group['colors']:
    view=mapping.save_color(job,dict(expected_revision=view['revision'],group_id=group['id'],color_id=color['id'],image_ids=[group['images'][0]['id']]))
   mapping.finalize(job,dict(expected_revision=view['revision']))
   self.assertEqual({row['raw']['색상']:row['raw']['사이즈'] for row in mapping.visible_items(job)},expected)
if __name__=='__main__':unittest.main()
