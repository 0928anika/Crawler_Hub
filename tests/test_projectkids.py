import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/x) for x in ('app','brands')]
import projectkids_official as c
class Context:
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
class Tests(unittest.TestCase):
 def setUp(self):self.data=json.loads((Path(__file__).resolve().parent/'fixtures/projectkids/EKG4DP3100LIN.json').read_text(encoding='utf-8'))
 def test_separate_brand_exact_code_and_actual_sizes(self):
  with patch.object(c,'fetch',return_value=json.dumps(self.data).encode()):g=c.load_product(Context(),'EKG4DP3100LIN','EKG4DP3100')
  self.assertEqual(c.BRAND,'EKBR');self.assertEqual([x['itmNm'] for x in g['godItmExList']],['120','130','140','150','160'])
  wrong=copy.deepcopy(self.data);wrong['goods']['sysBrnd']['brndId']='EBR'
  with patch.object(c,'fetch',return_value=json.dumps(wrong).encode()):self.assertIsNone(c.load_product(Context(),'EKG4DP3100LIN','EKG4DP3100'))
 def test_actual_size_chart_and_notice_preserved(self):
  g=self.data['goods']
  with patch.object(c,'picture',side_effect=lambda ctx,url,cache,**kw:{'digest':url}),patch.object(c,'gallery_assets',return_value=[{'url':'https://img.goodwearmall.com/product.jpg','digest':'product','detail_role':True}]):
   body=c.detail_markup(Context(),[g],[],{})
  self.assertIn('실측사이즈',body);self.assertIn('상품정보제공고시',body);self.assertIn('제품 주소재',body);self.assertIn('허리둘레',body)
 def test_product_name_excludes_brand_and_code(self):
  self.assertEqual(c.clean_name('프로젝트키즈 PROJECT KIDS 공용 데님 EKG4DP3100LIN','EKG4DP3100','EKG4DP3100LIN'),'공용 데님')
if __name__=='__main__':unittest.main()
