import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import jambangee_official as c
class MissingOptionsTests(unittest.TestCase):
 def doc(self,number):return c.BeautifulSoup((ROOT/'tests/fixtures/jambangee'/('AO4WIZ844_'+number+'.html')).read_text('utf-8'),'html.parser')
 def test_actual_hidden_stock_options_supply_color_and_sizes(self):
  for number,code,name in [('7967','MG','멜란지그레이'),('7966','BK','블랙')]:
   d=self.doc(number);r=c.identity(d,number,'AO4WIZ844',True)
   self.assertEqual((r['color_code'],r['color_name']),(code,name));self.assertTrue(c.options(d,'사이즈',number))
 def test_title_code_used_if_no_select_or_embedded_options(self):
  d=self.doc('7967')
  for t in d.select('script'):t.decompose()
  r=c.identity(d,'7967','AO4WIZ844',True);self.assertEqual(r['color_code'],'MG');self.assertEqual(r['color'],'MG')
 def test_multiple_real_colors_are_not_overridden_by_title(self):
  d=self.doc('7967');select=d.select_one('[option_title="색상"]')
  select.append(c.BeautifulSoup('<option value="x">블랙</option><option value="y">그레이</option>','html.parser'))
  with self.assertRaises(c.ColorMappingError):c.identity(d,'7967','AO4WIZ844',True)
 def test_missing_options_without_explicit_title_code_still_fails(self):
  d=self.doc('7967')
  for t in d.select('script'):t.decompose()
  d.select_one('.prdName').string='공용 후드 집업'
  with self.assertRaises(c.ColorMappingError):c.identity(d,'7967','AO4WIZ844',True)
if __name__=='__main__':unittest.main()
