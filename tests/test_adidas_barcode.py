import sys,json,copy,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
from test_oberon_brands import load,run
from crawler_sdk import CrawlContext
from schema import field_for,output_headers,review_result,preview
from registry import brands
from storage import validate_input

class Tests(unittest.TestCase):
 def product(self):return json.loads((ROOT/'tests/fixtures/adidas_browser/KS4159.json').read_text('utf-8'))
 def routes(self,p):return {'https://www.adidas.co.kr/api/products/KS4159':p}
 def test_default_stays_one_color_row_with_comma_joined_sizes(self):
  c=load('adidas_official');p=self.product();rows,fail,error,_=run(c,'adidas_official','KS4159',['size'],self.routes(p))
  self.assertIsNone(error,fail);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['사이즈'],'XS,S,M,L,XL,2XL,3XL,4XL');self.assertIsNone(rows[0]['바코드'])
 def test_selected_barcode_expands_exact_size_gtin_pairs(self):
  c=load('adidas_official');p=self.product();rows,fail,error,_=run(c,'adidas_official','KS4159',['barcode'],self.routes(p))
  self.assertIsNone(error,fail);self.assertEqual(len(rows),8)
  self.assertEqual([(r['사이즈'],r['바코드']) for r in rows],[(v['size'],v['gtin']) for v in p['variation_list']])
  self.assertTrue(all(r['품번']=='KS4159' and r['색상']=='Aurora Coffee' for r in rows))
 def test_missing_gtin_keeps_size_and_warns_without_inventing(self):
  c=load('adidas_official');p=self.product();p['variation_list'][1].pop('gtin')
  rows,fail,error,_=run(c,'adidas_official','KS4159',['barcode','size'],self.routes(p))
  self.assertIsNone(error,fail);self.assertEqual(len(rows),8);self.assertEqual(rows[1]['사이즈'],'S');self.assertEqual(rows[1]['바코드'],'')
  item=preview(['품번','상품명','색상','상품 링크','바코드'],['KS4159','name','Brown','https://www.adidas.co.kr/KS4159.html',''],['barcode'])
  self.assertIn('바코드 미수집',review_result(item,['barcode'])[1])
 def test_metadata_validation_adds_size_only_for_supported_barcode(self):
  b=next(b for b in brands(ROOT/'app') if b['id']=='adidas_official')
  result,_,_=validate_input({'codes':'KS4159','selected':['barcode']},b)
  self.assertEqual(set(result['selected']),{'barcode','size'});self.assertIn('바코드',result['output_headers']);self.assertIn('사이즈',result['output_headers'])
  self.assertEqual(field_for('GTIN'),'barcode');self.assertNotIn('바코드',output_headers(b['output_headers'],['size']))
 def test_leading_zero_is_excel_text(self):
  c=load('adidas_official');p=self.product();p['variation_list']=[dict(size='XS',gtin='04068817729862')]
  metadata=json.loads((ROOT/'brands/adidas_official.json').read_text('utf-8'))
  with tempfile.TemporaryDirectory() as folder:
   target=Path(folder)/'out.xlsx';events=[];ctx=CrawlContext('unused',target,0,['barcode','size'],metadata['output_headers'],lambda:None,lambda kind,**d:events.append((kind,d)),input_item={'품번':'KS4159'})
   with patch.object(c,'fetch_product',return_value=p):c.crawl(ctx);ctx.finish()
   book=load_workbook(target);headers=[v.value for v in book.active[1]];cell=book.active.cell(2,headers.index('바코드')+1)
   self.assertEqual(cell.value,'04068817729862');self.assertEqual(cell.data_type,'s');book.close()

if __name__=='__main__':unittest.main()
