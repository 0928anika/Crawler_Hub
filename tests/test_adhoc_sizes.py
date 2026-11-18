import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('adhoc_sizes',ROOT/'brands/adhoc_official.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class SizeTests(unittest.TestCase):
 def test_real_soldout_sizes_are_kept_for_manual_common_sizes(self):
  text=(ROOT/'tests/fixtures/adhoc/HBAHC90_options.html').read_text(encoding='utf-8');rows=c.page_colors(c.soup(text),text,{})
  self.assertEqual(len(rows),6)
  for color,data in rows:self.assertEqual(data['sizes'],['095','100','105','FFF'])
 def test_combined_dom_without_stock_keeps_hyphenated_color_and_leading_zero(self):
  d=c.soup('<select option_title="색상-사이즈"><option value="*">선택</option><option value="1">CH-GREY(37)-095</option><option value="2">CH-GREY(37)-100</option><option value="3" disabled>BLACK(39)-FFF</option></select>')
  rows=dict(c.page_colors(d,'',{}));self.assertEqual(rows['CH-GREY(37)']['sizes'],['095','100']);self.assertEqual(rows['BLACK(39)']['sizes'],['FFF'])
 def test_single_stock_field_combined_option(self):
  import json
  item={'option_name_original':['색상-사이즈'],'option_value_orginal':['DK-BROWN(57)-105'],'option_price':1000}
  from unittest.mock import patch
  with patch.object(c,'stock_data',return_value={'a':item}):rows=dict(c.page_colors(c.soup(''),'',{}))
  self.assertEqual(rows['DK-BROWN(57)']['sizes'],['105'])
if __name__=='__main__':unittest.main()
