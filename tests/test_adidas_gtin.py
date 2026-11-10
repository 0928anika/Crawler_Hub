import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from crawler_sdk import CrawlContext
spec=importlib.util.spec_from_file_location('adidas_gtin_test',ROOT/'brands/adidas_gtin.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
PRODUCT=json.loads((ROOT/'tests/fixtures/adidas/KH4827.json').read_text(encoding='utf-8'))

class AdidasGtinTests(unittest.TestCase):
    def source(self,product):
        return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'queries':[product,copy.deepcopy(product)]})+'</script>'
    def test_actual_source_has_eight_size_barcodes_without_cache_duplicates(self):
        product=c.product_from_source(self.source(PRODUCT),'KH4827')
        rows=c.barcode_rows(product,'KH4827')
        self.assertEqual([r['옵션사이즈'] for r in rows],['XS','S','M','L','XL','2XL','3XL','4XL'])
        self.assertEqual(rows[0]['GTIN'],'4069996986732')
        self.assertEqual({r['색상'] for r in rows},{'Black'})
    def test_wrong_product_and_conflicting_duplicates_fail(self):
        with self.assertRaises(c.CollectionError):c.product_from_source(self.source(PRODUCT),'B75806')
        other=copy.deepcopy(PRODUCT);other['variation_list'][0]['gtin']='0000000000000'
        with self.assertRaises(c.CollectionError):c.product_from_source({'data':[PRODUCT,other]},'KH4827')
    def test_leading_zero_preserved_and_invalid_checksum_rejected(self):
        product=copy.deepcopy(PRODUCT);product['variation_list']=product['variation_list'][:1]
        product['variation_list'][0]['gtin']='0123456789012'
        self.assertEqual(c.barcode_rows(product,'KH4827')[0]['GTIN'],'0123456789012')
        product['variation_list'][0]['gtin']='4069996986731'
        with self.assertRaises(c.CollectionError):c.barcode_rows(product,'KH4827')
    def test_sdk_source_only_emits_gtin_without_optional_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.html';source.write_text(self.source(PRODUCT),encoding='utf-8')
            output=Path(tmp)/'result.xlsx'
            meta=json.loads((ROOT/'brands/adidas_gtin.json').read_text(encoding='utf-8'))
            ctx=CrawlContext(Path(tmp)/'unused.xlsx',output,0,[],meta['output_headers'],lambda:None,lambda *a,**k:None,input_item={'품번':'KH4827','원문파일':str(source)})
            with patch.object(c,'fetch',side_effect=AssertionError('Saved source must not request network')):c.crawl(ctx)
            ctx.finish()
            book=load_workbook(output);sheet=book.active
            self.assertEqual(sheet.max_row,9)
            column=[cell.value for cell in sheet[1]].index('GTIN')+1
            self.assertEqual(sheet.cell(2,column).data_type,'s')
            self.assertEqual(sheet.cell(2,column).value,'4069996986732');book.close()

if __name__=='__main__':unittest.main()
