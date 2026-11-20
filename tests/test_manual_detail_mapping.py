import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
import color_mapping as m
from test_hybrid_mapping import HybridMappingTests,item
from color_palette import normalize_palette
class Tests(unittest.TestCase):
 def job(self,folder):
  job=HybridMappingTests().job(folder,[item(i,'STYLE','60','https://img/'+str(i)+'.jpg',True) for i in range(3)])
  job.brand.update(mapping_detail_mode='manual_ordered',hybrid_mapping=False);job.selected.append('detail_page');job.brand['output_headers'].append('상세페이지 HTML');return job
 def detail(self,job,ids):
  view=m.snapshot(job);g=view['groups'][0];return m.save_color(job,dict(group_id=g['id'],color_id=m.DETAIL_OPTION_ID,image_ids=ids,expected_revision=view['revision']))
 def test_order_persists_independent_from_color_and_final_html(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);view=m.snapshot(job);g=view['groups'][0];ids=[i['id'] for i in g['images']];view=self.detail(job,[ids[2],ids[0]])
   self.assertEqual(view['groups'][0]['detail']['selected'],[ids[2],ids[0]]);self.assertTrue(all(not i['assigned_colors'] for i in view['groups'][0]['images']))
   for color in g['colors']:
    view=m.save_color(job,dict(group_id=g['id'],color_id=color['id'],image_ids=[ids[0],ids[1]],expected_revision=view['revision']))
   self.assertTrue(view['groups'][0]['complete']);self.assertEqual(view['groups'][0]['detail']['selected'],[ids[2],ids[0]])
   view=m.finalize(job,dict(expected_revision=view['revision']));rows=m.final_items(job,view)
   self.assertEqual(len(rows),2)
   for row in rows:
    markup=row['raw']['상세페이지 HTML'];self.assertLess(markup.index('2.jpg'),markup.index('0.jpg'));self.assertNotIn('1.jpg',markup);self.assertNotEqual(row['code'],m.DETAIL_OPTION_ID)
 def test_manual_preview_uses_registration_image_dimensions(self):
  from bs4 import BeautifulSoup
  from detail_store import DETAIL_WIDTH, excel_detail
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);g=m.snapshot(job)['groups'][0];ids=[i['id'] for i in g['images']]
   self.detail(job,[ids[2],ids[0]])
   markup=m.detail_preview(job,g['id'])['html']
   exported,overflow=excel_detail(markup);self.assertFalse(overflow)
   for value in [markup,exported]:
    soup=BeautifulSoup(value,'html.parser');images=soup.find_all('img')
    self.assertEqual([i['src'] for i in images],['https://img/2.jpg','https://img/0.jpg'])
    for image in images:
     self.assertEqual(image['width'],str(DETAIL_WIDTH))
     self.assertIn('max-width:%spx'%DETAIL_WIDTH,image['style'])
     self.assertIn('height:auto',image['style']);self.assertNotIn('height',image.attrs)

  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);view=m.snapshot(job);g=view['groups'][0]
   for action in [dict(action='delete',color_id=m.DETAIL_OPTION_ID),dict(action='add',color_name='상세페이지')]:
    with self.assertRaisesRegex(ValueError,'상세페이지'):m.edit_options(job,dict(group_id=g['id'],expected_revision=view['revision'],**action))
   view=m.edit_options(job,dict(group_id=g['id'],action='delete-unsaved',expected_revision=view['revision']));self.assertIn('detail',view['groups'][0]);self.assertEqual(view['groups'][0]['colors'],[])
 def test_am_import_never_duplicates_or_resets_detail(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);g=m.snapshot(job)['groups'][0];ids=[i['id'] for i in g['images']];view=self.detail(job,[ids[1],ids[0]])
   parsed=dict(needs_columns=False,products={'STYLE':normalize_palette([dict(color_name='상세페이지'),dict(color_name='화이트')])},option_count=2)
   with patch.object(m.am_requests,'parse_requests',return_value=parsed):view=m.import_am(job,dict(expected_revision=view['revision']))
   g=view['groups'][0];self.assertEqual(g['detail']['selected'],[ids[1],ids[0]]);self.assertTrue(g['detail']['confirmed']);self.assertFalse(any(m.reserved_detail(c) for c in g['colors']+g['am_options']+g['original_colors']))
   view=m.edit_options(job,dict(group_id=g['id'],action='reset',expected_revision=view['revision']));self.assertEqual(view['groups'][0]['detail']['selected'],[ids[1],ids[0]])
 def test_color_confirmation_alone_cannot_finish(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);view=m.snapshot(job);g=view['groups'][0]
   for color in g['colors']:view=m.save_color(job,dict(group_id=g['id'],color_id=color['id'],image_ids=[g['images'][0]['id']],expected_revision=view['revision']))
   self.assertFalse(view['groups'][0]['complete'])
   with self.assertRaisesRegex(ValueError,'상세페이지'):m.finalize(job,dict(expected_revision=view['revision']))
 def test_invalid_detail_selection_cannot_modify_existing_color(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);view=m.snapshot(job);g=view['groups'][0]
   for ids in [[],['other-product-image']]:
    with self.assertRaises(ValueError):self.detail(job,ids)
   self.assertFalse((job.dir/'color_mapping.json').exists())
 def test_existing_fashionplus_detail_choice_uses_verified_original_url(self):
  from detail_store import verified_original_image_url
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);job.brand['id']='fashionplus_integrated'
   url='https://img.fashionplus.co.kr/mall/assets/product_img/body.jpg?RS=800x1072&AR=0'
   group=dict(images=[dict(id='existing-choice',url=url)],detail=dict(selected=['existing-choice']))
   verified_original_image_url.cache_clear()
   with patch('crawler_rules.check_public_image',return_value=dict(status='accessible',format='JPEG')):
    markup=m.selected_detail(job,group)
   self.assertIn('body.jpg"',markup);self.assertNotIn('RS=',markup);self.assertEqual(group['detail']['selected'],['existing-choice'])
if __name__=='__main__':unittest.main()
