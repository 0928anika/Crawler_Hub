import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('list_official_test',ROOT/'brands/list_official.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class C:
 product_code='tcamfq82050'
 def __init__(self):self.failures=[]
 def stage(self,*args):pass
 def checkpoint(self):pass
 def fail_item(self,m):self.failures.append(m)
class ListFailureTests(unittest.TestCase):
 def test_missing_index_is_reported_before_worker_exits(self):
  ctx=C()
  with patch.object(c,'fetch',side_effect=HTTPError(c.SITE,404,'Not Found',None,None)):
   with self.assertRaisesRegex(ValueError,'공식 상품 인덱스 없음'):c.crawl(ctx)
  self.assertEqual(len(ctx.failures),1);self.assertIn('TCAMFQ82050',ctx.failures[0]);self.assertIn('404',ctx.failures[0])
 def test_access_error_is_distinct_from_missing_index(self):
  ctx=C()
  with patch.object(c,'fetch',side_effect=HTTPError(c.SITE,403,'Forbidden',None,None)):
   with self.assertRaises(HTTPError):c.crawl(ctx)
  self.assertIn('접근 실패',ctx.failures[0]);self.assertNotIn('인덱스 없음',ctx.failures[0])
 def test_missing_color_does_not_discard_valid_colors(self):
  doc=c.BeautifulSoup('<ul option_title="색상"><li option_value="BLACK"></li><li option_value="LIGHT KHAKI"></li></ul>','html.parser')
  result=c.colors(doc,{'images':{'colors':{'BK':{'name':'BLACK','files':['black.jpg']}}}},{'BLACK':['BK'],'LIGHT KHAKI':['LK']})
  self.assertEqual([r['code'] for r in result],['BK','LK'])
  self.assertNotIn('error',result[0]);self.assertIn('공식 옵션은 있으나',result[1]['error'])
if __name__=='__main__':unittest.main()
