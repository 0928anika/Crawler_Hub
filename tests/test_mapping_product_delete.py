import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
from test_hybrid_mapping import HybridMappingTests,item
import color_mapping as m
class Tests(unittest.TestCase):
 def job(self,folder,hybrid=True):
  job=HybridMappingTests().job(folder,[item(0,'DELETE','60','https://img/delete.jpg',True),item(1,'KEEP','60','https://img/keep.jpg',True)])
  job.brand['hybrid_mapping']=hybrid;return job
 def delete(self,job,g):
  view=m.snapshot(job);return m.edit_options(job,dict(group_id=g['id'],action='delete-product',expected_revision=view['revision']))
 def test_deleted_product_is_persistent_and_excluded_from_pending_and_final_exports(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);group=m.snapshot(job)['groups'][0];self.delete(job,group)
   view=m.snapshot(job);self.assertEqual([g['code'] for g in view['groups']],['KEEP']);self.assertEqual(view['total_colors'],2)
   self.assertEqual([i['code'] for i in m.visible_items(job)],['KEEP'])
   self.assertNotIn('DELETE',str(m.export_sheet(job)));self.assertEqual(len(job.items),2)
   group=view['groups'][0]
   for color in group['colors']:
    view=m.save_color(job,dict(group_id=group['id'],color_id=color['id'],image_ids=[group['images'][0]['id']],expected_revision=view['revision']))
   view=m.finalize(job,dict(expected_revision=view['revision']))
   self.assertTrue(view['finalized']);self.assertEqual({i['code'] for i in m.final_items(job,view)},{'KEEP'})
   self.assertNotIn('DELETE',str(m.export_sheet(job)))
 def test_hybrid_preassigned_rows_for_deleted_product_do_not_return(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);job.items.extend([item(2,'DELETE','AC','https://img/auto.jpg'),item(3,'AUTO','60','https://img/fullauto.jpg')])
   group=m.snapshot(job)['groups'][0];self.delete(job,group)
   self.assertEqual([i['code'] for i in m.automatic_items(job)],['AUTO'])
 def test_last_product_can_be_deleted_and_exported_as_header_only(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder,hybrid=False)
   for group in list(m.snapshot(job)['groups']):self.delete(job,group)
   view=m.snapshot(job);self.assertEqual(view['groups'],[]);self.assertEqual(view['total_colors'],0)
   view=m.finalize(job,dict(expected_revision=view['revision']))
   self.assertTrue(view['finalized']);self.assertEqual(m.final_items(job,view),[]);self.assertEqual(len(m.export_sheet(job)),1)
 def test_manual_detail_fixed_option_does_not_prevent_product_deletion(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);job.brand['mapping_detail_mode']='manual_ordered';job.selected.append('detail_page')
   group=m.snapshot(job)['groups'][0];self.assertIn('detail',group);view=self.delete(job,group)
   self.assertNotIn(group['id'],[g['id'] for g in view['groups']]);self.assertEqual(view['total_colors'],3)
 def test_stale_revision_and_running_job_cannot_delete(self):
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);view=m.snapshot(job);group=view['groups'][0]
   with self.assertRaisesRegex(ValueError,'다른 창'):m.edit_options(job,dict(group_id=group['id'],action='delete-product',expected_revision=-1))
   job.state='running'
   with self.assertRaises(ValueError):self.delete(job,group)
if __name__=='__main__':unittest.main()
