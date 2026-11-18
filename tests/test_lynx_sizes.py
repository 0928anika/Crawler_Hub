import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('lynx_test',ROOT/'brands/lynx_official.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class SizeTests(unittest.TestCase):
 def test_real_underscore_combined_options(self):
  for i,color in [(0,'BK'),(1,'KH')]:
   d=c.BeautifulSoup((ROOT/'tests/fixtures/lynx'/('combined_'+str(i)+'.html')).read_bytes(),'html.parser')
   self.assertEqual(c.product_sizes(d,color),['64','67','70','73'])
   self.assertEqual(c.product_sizes(d,'OTHER'),[])
 def test_mixed_colors_keep_only_requested_sizes_and_soldout(self):
  d=c.BeautifulSoup('<select class="basic_option" label="옵션"><option value="">선택</option><option value="0" title="BK_064">BK_064</option><option value="1" disabled title="BK_067">BK_067</option><option value="2">KH_70</option></select>','html.parser')
  self.assertEqual(c.product_sizes(d,'BK'),['064','067']);self.assertEqual(c.product_sizes(d,'KH'),['70'])
 def test_separate_size_and_declared_colon_regression(self):
  d=c.BeautifulSoup('<select label="사이즈"><option value="0">90</option><option value="1" disabled>95</option></select>','html.parser')
  self.assertEqual(c.product_sizes(d,'BK'),['90','95'])
  d=c.BeautifulSoup('<select label="색상:사이즈"><option value="0">BK:78</option><option value="1">KH:82</option></select>','html.parser')
  self.assertEqual(c.product_sizes(d,'BK'),['78'])
 def test_spaced_title_color_requires_matching_official_option(self):
  d=c.BeautifulSoup((ROOT/'tests/fixtures/lynx/spaced_color.html').read_bytes(),'html.parser')
  base,color,p=c.identity(d)
  self.assertEqual((base,color),('L21D3PD156','BL'))
  self.assertEqual(c.product_sizes(d,color),['64','67','70','73','76','79'])
  name=c.clean_product_name(p['name'],[],[p['title_code'],p['title_code_raw'],base])
  self.assertEqual(name,'여성 세미 테이퍼드핏 데님 팬츠')
  for o in d.select('option[title]'):o['title']=o['title'].replace('BL_','BK_')
  with self.assertRaises(RuntimeError):c.identity(d)
if __name__=='__main__':unittest.main()
