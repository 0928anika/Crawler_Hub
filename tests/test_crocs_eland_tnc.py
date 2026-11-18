import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import crocs_eland_tnc as c
class C:
 def checkpoint(self):pass
 def log(self,*a):pass
class TncTests(unittest.TestCase):
 def setUp(self):self.data=json.loads((ROOT/'tests/fixtures/crocs_eland_tnc/2507980563.json').read_text(encoding='utf-8'))
 def test_vendor_and_model_are_required(self):
  row,model=c.record_identity(self.data,'202651-3C7');self.assertEqual(row['color_code'],'3C7');self.assertEqual(row['product_name'],'오프로드 스포츠 클로그 공용샌들')
  self.assertIsNone(c.record_identity(self.data,'202651-02S'))
  self.assertIsNotNone(c.record_identity(self.data,'202651'))
  self.data['itemMain']['lowerVendNo']='OTHER';self.assertIsNone(c.record_identity(self.data,'202651-3C7'))
 def test_all_seller_banners_and_charts_excluded_before_download(self):
  urls=c.body_urls(self.data,'202651-3C7');self.assertEqual(urls,['https://tnc7070.godohosting.com/CROCS/202651-3C7.jpg'])
  with patch.object(c,'checked',return_value={}) as check:
   body=c.detail(C(),self.data,'202651-3C7',[{'itemOptionName':'M6W8(250mm)'}],{})
  self.assertEqual(check.call_count,1);self.assertEqual(body.count('<img'),1);self.assertEqual(body.count('<table'),1)
  for banned in ('CROCS_01','about_CROCS','crocs_size','crocs_as','tnc_notice','tnc_call','상품정보고시'):self.assertNotIn(banned,body)
 def test_official_adult_table_exact_measurements(self):
  body=c.size_guide([{'itemOptionName':'M8W10(265mm)'}]);self.assertIn('성인',body);self.assertNotIn('키즈·토들러',body);self.assertIn('265mm',body)
  self.assertIn('M8 | W10',body);self.assertNotIn('판매자',body)
 def test_official_kids_table_is_separate(self):
  body=c.size_guide([{'itemOptionName':'C10(165mm)'},{'itemOptionName':'J3(220mm)'}]);self.assertIn('키즈·토들러',body);self.assertNotIn('공식 성인',body);self.assertIn('90mm',body);self.assertIn('J6',body)
 def test_ambiguous_options_never_guess_age_group(self):
  body=c.size_guide([{'itemOptionName':'230mm'}]);self.assertIn('공식 성인',body);self.assertIn('키즈·토들러',body)
 def test_missing_product_body_is_failure(self):
  self.data['itemDesc']['itemDescdocumentCon']='<img src="https://tnc7070.godohosting.com/CROCS/crocs_size.jpg">'
  with self.assertRaisesRegex(c.CrawlError,'본문 이미지 없음'):c.body_urls(self.data,'202651-3C7')
 def test_other_color_body_not_merged(self):
  self.data['itemDesc']['itemDescdocumentCon']+='<img src="https://tnc7070.godohosting.com/CROCS/202651-02S.jpg">'
  self.assertEqual(len(c.body_urls(self.data,'202651-3C7')),1)
if __name__=='__main__':unittest.main()
