import json,sys,tempfile,unittest
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
from test_hybrid_mapping import HybridMappingTests,item,PALETTE,HEADERS
import photo_exclusion as photos
import color_mapping as mapping
from storage import atomic_json
from openpyxl import Workbook

class Tests(unittest.TestCase):
 def job(self,folder,models=2):
  items=[]
  for n in range(models):
   for color in PALETTE:
    for role in ('front','model','back'):
     row=item(len(items),'STYLE'+str(n),color['color_code'],'https://img/'+str(n)+'-'+color['color_code']+'-'+role+'.jpg',True)
     row['raw']['상세페이지 HTML']='<table><tr><td>'+color['color_code']+' size notice</td></tr></table>'
     row['unmapped'].update(colors=[color],preassigned_color_id=color['id'],review_required=True,detail_mode='site_shared')
     items.append(row)
  job=HybridMappingTests().job(folder,items)
  job.brand.update(id='phantom_official_no_model',output_headers=HEADERS+['상세페이지 HTML'],mapping_detail_mode='selected_images_with_tail',mapping_tail_scope='color')
  job.selected.append('detail_page')
  def save():
   sheet=mapping.export_sheet(job);wb=Workbook()
   for row in sheet:wb.active.append(row)
   wb.save(job.dir/'result.xlsx')
  job.save=save
  return job
 def test_old_job_bulk_save_restore_and_raw_preservation(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);before=deepcopy(job.items);view=photos.gallery(job)
   self.assertFalse('photo_exclusion' in job.brand);self.assertEqual(len(view['photos']),12)
   self.assertFalse((job.dir/'color_mapping.json').exists())
   excluded=[p['id'] for p in view['photos'] if 'model.jpg' in p['url']]
   result=photos.save(job,dict(expected_revision=0,excluded_ids=excluded))
   self.assertEqual(result['excluded'],4);self.assertEqual(result['rows'],4)
   rows=mapping.visible_items(job);self.assertEqual(len(rows),4)
   for row in rows:
    markup=row['raw']['상세페이지 HTML'];self.assertNotIn('model.jpg',markup)
    self.assertIn(row['raw']['색상코드']+' size notice',markup)
   self.assertEqual(job.items,before)
   self.assertEqual(sum(p['excluded'] for p in photos.gallery(job)['photos']),4)
   photos.save(job,dict(expected_revision=result['revision'],excluded_ids=[]))
   self.assertFalse(any(p['excluded'] for p in photos.gallery(job)['photos']))
   self.assertTrue(any('model.jpg' in row['raw']['상세페이지 HTML'] for row in mapping.visible_items(job)))
   self.assertEqual(job.items,before)
 def test_all_photos_of_color_can_be_excluded_and_restored(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder,1);view=photos.gallery(job)
   excluded=[p['id'] for p in view['photos'] if '60-' in p['url']]
   saved=photos.save(job,dict(expected_revision=0,excluded_ids=excluded))
   self.assertEqual(saved['rows'],1);self.assertEqual(mapping.visible_items(job)[0]['raw']['색상코드'],'AC')
   photos.save(job,dict(expected_revision=saved['revision'],excluded_ids=[]))
   self.assertEqual(len(mapping.visible_items(job)),2)
 def test_existing_manual_exclusions_survive_first_open_and_save(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder,1);v=mapping.snapshot(job);g=v['groups'][0];color=g['colors'][0]
   kept=[i['id'] for i in g['images'] if color['id'] in i['assigned_colors'] and 'model.jpg' not in i['url']]
   v=mapping.save_color(job,dict(group_id=g['id'],color_id=color['id'],image_ids=kept,expected_revision=0))
   gallery=photos.gallery(job);excluded=[p['id'] for p in gallery['photos'] if p['excluded']]
   self.assertEqual(len(excluded),1)
   saved=photos.save(job,dict(expected_revision=v['revision'],excluded_ids=excluded))
   self.assertEqual(sum(p['excluded'] for p in photos.gallery(job)['photos']),1)
   photos.save(job,dict(expected_revision=saved['revision'],excluded_ids=[]))
   self.assertFalse(any(p['excluded'] for p in photos.gallery(job)['photos']))
 def test_draft_survives_reopen_without_replacing_final_export(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder,1);photos.save(job,dict(expected_revision=0,excluded_ids=[]))
   original=(job.dir/'result.xlsx').read_bytes()
   target=photos.gallery(job)['photos'][0]['id']
   result=photos.save(job,dict(expected_revision=1,excluded_ids=[target],draft=True))
   self.assertTrue(result['draft']);self.assertEqual((job.dir/'result.xlsx').read_bytes(),original)
   reopened=photos.gallery(job);self.assertTrue(reopened['draft']);self.assertFalse(reopened['finalized']);self.assertEqual(sum(p['excluded'] for p in reopened['photos']),1)
   self.assertFalse(any('front.jpg' not in row['raw']['상세페이지 HTML'] for row in mapping.visible_items(job)))
   photos.save(job,dict(expected_revision=result['revision'],excluded_ids=[target]))
   self.assertFalse(photos.gallery(job)['draft']);self.assertNotEqual((job.dir/'result.xlsx').read_bytes(),original)
 def test_stale_or_invalid_save_does_not_change_saved_state(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder,1);photos.save(job,dict(expected_revision=0,excluded_ids=[]))
   before=(job.dir/'color_mapping.json').read_bytes()
   for data in (dict(expected_revision=0,excluded_ids=[]),dict(expected_revision=1,excluded_ids=['not-a-photo'])):
    with self.assertRaises(ValueError):photos.save(job,data)
    self.assertEqual((job.dir/'color_mapping.json').read_bytes(),before)
 def test_failed_export_rolls_back_and_keeps_backup(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder,1);photos.save(job,dict(expected_revision=0,excluded_ids=[]))
   before=(job.dir/'color_mapping.json').read_bytes();book=(job.dir/'result.xlsx').read_bytes()
   def broken():
    (job.dir/'result.xlsx').write_bytes(b'partial');raise OSError('export failed')
   job.save=broken
   with self.assertRaises(OSError):photos.save(job,dict(expected_revision=1,excluded_ids=[photos.gallery(job)['photos'][0]['id']]))
   self.assertEqual((job.dir/'color_mapping.json').read_bytes(),before);self.assertEqual((job.dir/'result.xlsx').read_bytes(),book)
   self.assertTrue(list((job.dir/'photo-review-backups').glob('*/result.xlsx')))

if __name__=='__main__':unittest.main()
