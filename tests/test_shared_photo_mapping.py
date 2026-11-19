import json,tempfile,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
import test_hybrid_mapping as helper
import color_mapping as m
import local_images
from unittest.mock import patch
class SharedPhotoMappingTests(unittest.TestCase):
 def make_job(self,folder):return helper.HybridMappingTests().job(folder,[helper.item(0,'STYLE','60','https://img/shared.jpg',True)])
 def test_many_selected_photos_export_at_most_nine_extras(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder)
   job.items=[helper.item(i,'STYLE','60','https://img/photo'+str(i)+'.jpg',True) for i in range(15)]
   job.brand.update(export_all_additional_images=True,additional_image_count=9)
   view=m.snapshot(job);g=view['groups'][0]
   for color in g['colors']:
    view=m.save_color(job,dict(group_id=g['id'],color_id=color['id'],image_ids=[i['id'] for i in g['images']],expected_revision=view['revision']))
   rows=m.final_items(job,view)
   self.assertEqual(len(rows),2)
   for row in rows:self.assertEqual(len(row['raw']['추가이미지'].split(',')),9)
 def test_detail_preview_is_product_scoped_read_only_and_sanitized(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);job.selected.append('detail_page')
   job.items[0]['raw']['상세페이지 HTML']='<h3>상품고시</h3><img src="https://img/body.jpg" onerror="alert(1)"><script>alert(1)</script>'
   other=helper.item(1,'OTHER','60','https://img/other.jpg',True);other['raw']['상세페이지 HTML']='<p>다른품번상세</p>';job.items.append(other)
   group=m.snapshot(job)['groups'][0];view=m.detail_preview(job,group['id'])
   self.assertEqual(view['code'],'STYLE');self.assertIn('상품고시',view['html']);self.assertNotIn('다른품번',view['html']);self.assertNotIn('script',view['html']);self.assertNotIn('onerror',view['html'])
   self.assertTrue(view['product_url'].startswith('https://'));self.assertFalse((job.dir/'color_mapping.json').exists())
   with self.assertRaisesRegex(ValueError,'품번'):m.detail_preview(job,'unknown')
 def test_detail_not_selected_does_not_substitute_gallery_images(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);group=m.snapshot(job)['groups'][0];view=m.detail_preview(job,group['id'])
   self.assertEqual(view['html'],'');self.assertIn('선택하지',view['notice'])
 def test_shared_photo_survives_save_reopen_finalize_and_export(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);v=m.snapshot(job);g=v['groups'][0];iid=g['images'][0]['id']
   for color in g['colors']:
    v=m.save_color(job,dict(group_id=g['id'],color_id=color['id'],image_ids=[iid],expected_revision=v['revision']))
   v=m.snapshot(job);self.assertEqual(v['saved_colors'],2);self.assertTrue(v['groups'][0]['complete']);self.assertEqual(len(v['groups'][0]['images'][0]['assigned_colors']),2)
   v=m.finalize(job,dict(expected_revision=v['revision']));rows=m.final_items(job,v)
   self.assertEqual(len(rows),2);self.assertTrue(all(r['raw']['대표이미지']=='https://img/shared.jpg' for r in rows))
   color=g['colors'][0]
   with self.assertRaisesRegex(ValueError,'최소 1장'):m.save_color(job,dict(group_id=g['id'],color_id=color['id'],image_ids=[],expected_revision=v['revision']))
   self.assertEqual(m.snapshot(job)['saved_colors'],2)
 def test_legacy_scalar_state_is_preserved_when_second_color_uses_photo(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);v=m.snapshot(job);g=v['groups'][0];iid=g['images'][0]['id'];red,blue=[c['id'] for c in g['colors']]
   state=dict(revision=1,groups={g['id']:dict(assignments={iid:red},confirmed=[red],image_signature=g['signature'])})
   (job.dir/'color_mapping.json').write_text(json.dumps(state),encoding='utf-8')
   v=m.save_color(job,dict(group_id=g['id'],color_id=blue,image_ids=[iid],expected_revision=1))
   self.assertEqual(set(v['groups'][0]['images'][0]['assigned_colors']),{red,blue});self.assertEqual(v['saved_colors'],2)
 def test_local_file_plan_includes_shared_asset_for_each_color(self):
  view=dict(groups=[dict(id='g',code='STYLE',colors=[dict(id='a',color_code='01',color_name='red'),dict(id='b',color_code='02',color_name='blue')],images=[dict(id='x',assigned_to='a',assigned_colors=['a','b'],asset_path='images/hash.jpg')])])
  plan=local_images.plan(view,['main_image'],5);self.assertEqual(len(plan[('g','a')]),1);self.assertEqual(len(plan[('g','b')]),1);self.assertNotEqual(plan[('g','a')][0]['filename'],plan[('g','b')][0]['filename'])
 def test_color_save_groups_once_and_persists_without_rewriting_progress(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);v=m.snapshot(job);g=v['groups'][0]
   job.checkpoint=lambda: self.fail('Color save must not rewrite collection progress')
   with patch.object(m,'source_groups',wraps=m.source_groups) as build:
    saved=m.save_color(job,dict(group_id=g['id'],color_id=g['colors'][0]['id'],image_ids=[g['images'][0]['id']],expected_revision=v['revision']))
   self.assertEqual(build.call_count,1)
   self.assertEqual(saved,m.snapshot(job))
   self.assertEqual(saved['saved_colors'],1)
   with self.assertRaisesRegex(ValueError,'다른 창'):
    m.save_color(job,dict(group_id=g['id'],color_id=g['colors'][1]['id'],image_ids=[g['images'][0]['id']],expected_revision=v['revision']))
   self.assertEqual(m.snapshot(job),saved)
 def test_bulk_delete_unsaved_preserves_saved_colors_photos_and_other_product(self):
  import copy
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);job.items.append(helper.item(1,'OTHER','60','https://img/other.jpg',True));original=copy.deepcopy(job.items)
   view=m.snapshot(job);g=next(g for g in view['groups'] if g['code']=='STYLE');other=next(g for g in view['groups'] if g['code']=='OTHER');saved=g['colors'][0]['id'];iid=g['images'][0]['id']
   view=m.save_color(job,dict(group_id=g['id'],color_id=saved,image_ids=[iid],expected_revision=view['revision']))
   view=m.edit_options(job,dict(group_id=g['id'],action='add',color_name='추가색상',expected_revision=view['revision']))
   revision=view['revision'];view=m.edit_options(job,dict(group_id=g['id'],action='delete-unsaved',color_ids=[saved],expected_revision=revision))
   current=next(v for v in view['groups'] if v['id']==g['id']);self.assertEqual([c['id'] for c in current['colors']],[saved]);self.assertEqual(current['confirmed'],[saved]);self.assertEqual(current['images'][0]['assigned_colors'],[saved]);self.assertEqual(view['revision'],revision+1)
   self.assertEqual(next(v for v in view['groups'] if v['id']==other['id']),other);self.assertEqual(job.items,original);self.assertEqual(m.snapshot(job),{k:v for k,v in view.items() if k!='active_color'})
 def test_bulk_delete_all_unsaved_keeps_all_source_images(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);view=m.snapshot(job);g=view['groups'][0]
   view=m.edit_options(job,dict(group_id=g['id'],action='delete-unsaved',expected_revision=view['revision']));current=view['groups'][0]
   self.assertEqual(current['colors'],[]);self.assertEqual([i['url'] for i in current['images']],[i['url'] for i in g['images']]);self.assertTrue(current['complete'])
   with self.assertRaisesRegex(ValueError,'미저장'):m.edit_options(job,dict(group_id=g['id'],action='delete-unsaved',expected_revision=view['revision']))
 def test_bulk_delete_rejects_stale_revision_without_changing_state(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);view=m.snapshot(job);g=view['groups'][0]
   with self.assertRaisesRegex(ValueError,'다른 창'):m.edit_options(job,dict(group_id=g['id'],action='delete-unsaved',expected_revision=view['revision']-1))
   self.assertEqual(m.snapshot(job),view)
if __name__=='__main__':unittest.main()
