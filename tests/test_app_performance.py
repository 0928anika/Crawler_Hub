import sys, unittest, tempfile, json, hashlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import registry, detail_store

class PerformanceRegressionTests(unittest.TestCase):
 def test_repeated_detail_reuses_formatting_without_changing_output(self):
  detail_store._cached_excel_detail.cache_clear()
  markup='<p>설명</p><img src="https://example.com/one.jpg">'
  expected=detail_store._format_excel_detail(markup)
  with patch.object(detail_store,'_format_excel_detail',wraps=detail_store._format_excel_detail) as render:
   self.assertEqual(detail_store.excel_detail(markup),expected)
   self.assertEqual(detail_store.excel_detail(markup),expected)
   self.assertEqual(render.call_count,1)
   detail_store.excel_detail(markup.replace('one','two'))
   self.assertEqual(render.call_count,2)
 def test_catalog_refreshes_metadata_code_and_new_brands(self):
  with tempfile.TemporaryDirectory() as temp:
   folder=Path(temp)
   source=Path(__file__).resolve().parents[1]/'brands'
   template=json.loads((source/'laughingchild_official.json').read_text(encoding='utf-8'))
   def add(key):
    data=dict(template,id=key,script=key+'.py',code_hash=hashlib.sha256(b'# initial').hexdigest())
    (folder/(key+'.py')).write_bytes(b'# initial')
    (folder/(key+'.json')).write_text(json.dumps(data),encoding='utf-8')
    return data
   data=add('test_brand')
   with patch.object(registry,'brand_root',return_value=folder),patch.object(registry,'_load_brands',wraps=registry._load_brands) as read:
    registry._CATALOG_CACHE.clear()
    self.assertTrue(registry.get_brand('test_brand')['available'])
    registry.get_brand('test_brand')['name']='mutated'
    self.assertNotEqual(registry.get_brand('test_brand')['name'],'mutated')
    self.assertEqual(read.call_count,1)
    data['name']='updated name'
    (folder/'test_brand.json').write_text(json.dumps(data),encoding='utf-8')
    self.assertEqual(registry.get_brand('test_brand')['name'],'updated name')
    (folder/'test_brand.py').write_bytes(b'# changed code')
    self.assertFalse(registry.get_brand('test_brand')['available'])
    add('new_brand')
    self.assertEqual(registry.get_brand('new_brand')['id'],'new_brand')
    (folder/'new_brand.py').unlink()
    self.assertFalse(registry.get_brand('new_brand')['available'])
   registry._CATALOG_CACHE.clear()
class MappingViewCacheTests(unittest.TestCase):
 def job(self,folder):
  from test_hybrid_mapping import HybridMappingTests,item
  job=HybridMappingTests().job(folder,[item(0,'MODEL','60','https://img/one.jpg',True)])
  job.revision=0
  return job
 def test_repeated_snapshot_reuses_work_without_exposing_cached_mutations(self):
  import color_mapping as m
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder)
   with patch.object(m,'_snapshot',wraps=m._snapshot) as build:
    first=m.snapshot(job);first['groups'][0]['name']='mutated'
    self.assertNotEqual(m.snapshot(job)['groups'][0]['name'],'mutated')
    self.assertEqual(build.call_count,1)
    job.queue[0]['status']='FAILED';self.assertEqual(m.snapshot(job)['groups'],[])
    self.assertEqual(build.call_count,2)
 def test_mapping_save_invalidates_snapshot_and_results_immediately(self):
  import color_mapping as m
  with tempfile.TemporaryDirectory() as folder:
   job=self.job(folder);view=m.snapshot(job);group=view['groups'][0]
   with patch.object(m,'_visible_items',wraps=m._visible_items) as build:
    m.visible_items(job);m.visible_items(job);self.assertEqual(build.call_count,1)
    m.save_color(job,dict(group_id=group['id'],color_id=group['colors'][0]['id'],image_ids=[group['images'][0]['id']],expected_revision=view['revision']))
    self.assertEqual(m.snapshot(job)['saved_colors'],1)
    m.visible_items(job);self.assertEqual(build.call_count,2)
    job.items[0]['name']='updated';job.revision+=1
    self.assertEqual(m.visible_items(job)[0]['name'],'updated');self.assertEqual(build.call_count,3)
if __name__=='__main__': unittest.main()

