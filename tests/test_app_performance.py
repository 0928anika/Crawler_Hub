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
if __name__=='__main__': unittest.main()

