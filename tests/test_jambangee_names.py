import importlib.util,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('jambangee_names',ROOT/'brands/jambangee_official.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
class NameTests(unittest.TestCase):
 def test_brackets_and_trailing_codes(self):
  for source,expected in [
   ('[남성양모] 따뜻한 청바지 AP3DNE655-BK (L)','[남성양모] 따뜻한 청바지'),
   ('[남성양모] 따뜻한 청바지_BK 28','[남성양모] 따뜻한 청바지'),
   ('[남성양모] 따뜻한 청바지','[남성양모] 따뜻한 청바지'),
   ('[잠뱅이] [남성양모] 따뜻한 청바지 BK(L','[남성양모] 따뜻한 청바지'),
   ('유니) 기획 다크 인디고 밴드 와이드핏 JQ3DTG926&JQ3DTG976-DB','유니) 기획 다크 인디고 밴드 와이드핏'),
   ('유니) 텐셀 난스판 뉴 와이드 루즈핏 TG(L','유니) 텐셀 난스판 뉴 와이드 루즈핏'),
   ('남성 3D 입체 슬림핏 (XL)','남성 3D 입체 슬림핏')]:
   with self.subTest(source=source):self.assertEqual(c.product_name(source,'AP3DNE655','BK'),expected)
if __name__=='__main__':unittest.main()
