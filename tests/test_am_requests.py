import base64,copy,io,sys,tempfile,unittest
from pathlib import Path
from openpyxl import Workbook
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import am_requests as am
import color_mapping as mapping
import test_hybrid_mapping as helper
from color_palette import normalize_palette

class AmRequestTests(unittest.TestCase):
 def test_both_text_forms_and_size_rows_aggregate(self):
  one=am.parse_requests({'text':'AB12345 화이트, 블랙'})
  two=am.parse_requests({'text':'AB12345 화이트\nAB12345 블랙\nAB12345 블랙'})
  self.assertEqual(one['products'],two['products']);self.assertEqual(two['option_count'],2)
  table=am.parse_requests({'text':'상품명\t사이즈\t색상\t품번\n셔츠\t90\t화이트\tab12345\n셔츠\t95\t화이트\tAB12345'})
  self.assertEqual(table['option_count'],1);self.assertEqual(list(table['products']),['AB12345'])
 def test_excel_reordered_columns_sheet_and_leading_zero_code(self):
  book=Workbook();sheet=book.active;sheet.title='요청';sheet.append(['상품명','색상코드','사이즈','품번','색상명']);sheet.append(['티',1,100,'AB12345','화이트']);sheet['B2'].number_format='000';sheet.append(['티',1,110,'AB12345','화이트']);sheet['B3'].number_format='000'
  stream=io.BytesIO();book.save(stream)
  result=am.parse_requests({'file':base64.b64encode(stream.getvalue()).decode(),'file_name':'AM.xlsx'},['AB12345'])
  self.assertEqual(result['sheets'],['요청']);self.assertEqual(result['option_count'],1)
  self.assertEqual(result['products']['AB12345'][0]['label'],'화이트 (001)')
 def test_codes_manual_columns_and_mismatched_rows(self):
  result=am.parse_requests({'text':'AB12345 WH, BK\nAB12345 D/GREY'})
  self.assertEqual([c['color_code'] for c in result['products']['AB12345']],['WH','BK',''])
  ambiguous={'text':'상품\tAB12345\tWH\t90'}
  self.assertTrue(am.parse_requests(ambiguous,['AB12345'])['needs_columns'])
  result=am.parse_requests(dict(ambiguous,columns={'product':1,'color':2}),['AB12345'])
  self.assertEqual(result['products']['AB12345'][0]['color_code'],'WH')
  result=am.parse_requests({'text':'품번\t색상명\t색상코드\nAB12345\t화이트,블랙\tWH'})
  self.assertEqual(result['option_count'],0);self.assertEqual(result['warning_count'],1)
 def test_bad_input_is_rejected(self):
  for data in [{'text':''},{'file':'not base64','file_name':'a.xlsx'},{'text':'AB12345 화이트','columns':{'product':50}}]:
   with self.assertRaises(ValueError):am.parse_requests(data)

class AmMappingTests(unittest.TestCase):
 def make_job(self,folder):return helper.HybridMappingTests().job(folder,[helper.item(0,'STYLE','60','https://img/shared.jpg',True)])
 def test_am_only_missing_option_persists_and_keeps_source(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);original=copy.deepcopy(job.items);view=mapping.snapshot(job);gid=view['groups'][0]['id']
   view=mapping.import_am(job,dict(text='STYLE WH, BK\nUNKNOWN RD',expected_revision=view['revision']))
   group=view['groups'][0];self.assertEqual([c['label'] for c in group['colors']],['WH','BK']);self.assertEqual(len(group['original_colors']),2)
   self.assertEqual(view['am_applied'],['STYLE']);self.assertEqual(view['am_import']['unmatched_codes'],['UNKNOWN']);self.assertEqual(job.items,original)
   self.assertEqual(mapping.snapshot(job)['groups'][0],group)
   cid=group['colors'][0]['id'];iid=group['images'][0]['id']
   view=mapping.save_color(job,dict(group_id=gid,color_id=cid,image_ids=[iid],expected_revision=view['revision']))
   group=view['groups'][0]
   view=mapping.edit_options(job,dict(action='configure',group_id=gid,entries=[dict(source='am',id=cid)],expected_revision=view['revision']))
   view=mapping.finalize(job,dict(expected_revision=view['revision']));rows=mapping.final_items(job,view)
   self.assertEqual(len(rows),1);self.assertEqual(rows[0]['raw']['색상코드'],'WH')
 def test_reference_activates_only_on_nonempty_save_and_exports(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);v=mapping.snapshot(job);g=v['groups'][0];reference=g['colors'][0];iid=g['images'][0]['id']
   v=mapping.save_color(job,dict(group_id=g['id'],color_id=reference['id'],image_ids=[iid],expected_revision=v['revision']))
   v=mapping.import_am(job,dict(text='STYLE WH',expected_revision=v['revision']));g=v['groups'][0]
   self.assertTrue(g['am_mode']);self.assertEqual(len(g['colors']),1);self.assertEqual(v['saved_colors'],0)
   with self.assertRaises(ValueError):mapping.save_color(job,dict(group_id=g['id'],color_id=reference['id'],image_ids=[],expected_revision=v['revision']))
   self.assertEqual(len(mapping.snapshot(job)['groups'][0]['colors']),1)
   am_id=g['colors'][0]['id'];v=mapping.save_color(job,dict(group_id=g['id'],color_id=am_id,image_ids=[iid],expected_revision=v['revision']))
   self.assertTrue(v['groups'][0]['complete']);self.assertEqual(v['total_colors'],1)
   v=mapping.save_color(job,dict(group_id=g['id'],color_id=reference['id'],image_ids=[iid],expected_revision=v['revision']))
   self.assertEqual(v['total_colors'],2);self.assertEqual(v['saved_colors'],2);self.assertTrue(v['groups'][0]['complete'])
   v=mapping.finalize(job,dict(expected_revision=v['revision']));self.assertEqual(len(mapping.final_items(job,v)),2)
   with self.assertRaisesRegex(ValueError,'다른 창'):mapping.import_am(job,dict(text='STYLE RD',expected_revision=0))
 def test_every_am_option_must_be_saved_or_deleted_and_reset_keeps_am_default(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=self.make_job(folder);v=mapping.snapshot(job);g=v['groups'][0]
   v=mapping.import_am(job,dict(text='STYLE WH, BK',expected_revision=v['revision']));g=v['groups'][0]
   v=mapping.save_color(job,dict(group_id=g['id'],color_id=g['colors'][0]['id'],image_ids=[g['images'][0]['id']],expected_revision=v['revision']))
   self.assertFalse(v['groups'][0]['complete'])
   v=mapping.edit_options(job,dict(action='delete-unsaved',group_id=g['id'],expected_revision=v['revision']))
   self.assertTrue(v['groups'][0]['complete'])
   v=mapping.edit_options(job,dict(action='reset',group_id=g['id'],expected_revision=v['revision']))
   self.assertEqual(set(c['label'] for c in v['groups'][0]['colors']),{'WH','BK'});self.assertFalse(v['groups'][0]['complete'])
 def test_first_row_rule_uses_known_codes_without_override(self):
  rows='티셔츠\tAB12345\tWH\n재킷\tAB12345\tBK'
  result=am.parse_requests(dict(text=rows,columns={'product':1,'color':2,'header_row':0}),['AB12345'])
  self.assertEqual(result['columns']['header_row'],-1);self.assertEqual(result['option_count'],2)
  result=am.parse_requests(dict(text=rows,columns={'product':1,'color':2,'header_row':-1}),['OTHER'])
  self.assertEqual(result['columns']['header_row'],0);self.assertEqual(result['option_count'],1)

if __name__=='__main__':unittest.main()
