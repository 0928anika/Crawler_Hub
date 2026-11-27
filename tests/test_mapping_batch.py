import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
import color_mapping as m
import test_hybrid_mapping as h
item=h.item
from color_palette import normalize_palette
class Tests(unittest.TestCase):
 def job(self,folder):
  j=h.HybridMappingTests().job(folder,[item(i,'STYLE','60','https://img/'+str(i)+'.jpg',True) for i in range(3)])
  j.revision=1
  return j
 def entries(self,view):
  g=view['groups'][0];return [dict(group_id=g['id'],color_id=c['id'],image_ids=[g['images'][1]['id'],g['images'][0]['id']]) for c in g['colors']]
 def test_batch_writes_once_and_survives_reload_with_shared_photos_and_order(self):
  with tempfile.TemporaryDirectory() as f:
   j=self.job(f);v=m.snapshot(j);entries=self.entries(v)
   with patch.object(m,'atomic_json',wraps=m.atomic_json) as write:
    result=m.save_colors(j,dict(expected_revision=v['revision'],entries=entries));self.assertEqual(write.call_count,1)
   self.assertEqual(result['revision'],1);self.assertEqual(len(result['groups']),1)
   self.assertNotIn('am_import',result)
   fresh=m._snapshot(j);self.assertEqual(m.snapshot(j),fresh);self.assertTrue(fresh['groups'][0]['complete'])
   for e in entries:self.assertEqual(fresh['groups'][0]['orders'][e['color_id']],e['image_ids'])
 def test_warm_batch_reuses_catalog_without_parsing_raw_rows(self):
  with tempfile.TemporaryDirectory() as f:
   j=self.job(f);v=m.snapshot(j)
   with patch.object(m,'source_groups',side_effect=AssertionError('raw rows rebuilt')):
    result=m.save_colors(j,dict(expected_revision=v['revision'],entries=self.entries(v)))
   self.assertTrue(result['groups'][0]['complete'])
 def test_invalid_batch_does_not_write_partial_state(self):
  with tempfile.TemporaryDirectory() as f:
   j=self.job(f);v=m.snapshot(j);entries=self.entries(v);entries[1]['image_ids']=['foreign']
   with self.assertRaises(ValueError):m.save_colors(j,dict(expected_revision=v['revision'],entries=entries))
   self.assertFalse((j.dir/'color_mapping.json').exists())
 def test_stale_revision_preserves_saved_bytes(self):
  with tempfile.TemporaryDirectory() as f:
   j=self.job(f);v=m.snapshot(j);data=dict(expected_revision=v['revision'],entries=self.entries(v));m.save_colors(j,data);saved=(j.dir/'color_mapping.json').read_bytes()
   with self.assertRaisesRegex(ValueError,'다른 창'):m.save_colors(j,data)
   self.assertEqual(saved,(j.dir/'color_mapping.json').read_bytes())
 def test_am_batch_can_include_reference_without_losing_am_options(self):
  with tempfile.TemporaryDirectory() as f:
   j=self.job(f);v=m.snapshot(j);original=v['groups'][0]['colors'][0]
   parsed=dict(needs_columns=False,products={'STYLE':normalize_palette([dict(color_name='AM WHITE'),dict(color_name='AM BLACK')])},option_count=2)
   with patch.object(m.am_requests,'parse_requests',return_value=parsed):v=m.import_am(j,dict(expected_revision=v['revision']))
   entries=self.entries(v);entries.append(dict(entries[0],color_id=original['id']))
   m.save_colors(j,dict(expected_revision=v['revision'],entries=entries));v=m._snapshot(j);self.assertEqual(len(v['groups'][0]['colors']),3);self.assertTrue(v['groups'][0]['complete'])
# (Oberon: 상세페이지 수동 순서 옵션이 없어 develop의 상세 순서 테스트는 제외)
if __name__=='__main__':unittest.main()
