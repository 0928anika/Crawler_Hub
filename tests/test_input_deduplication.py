import base64
import io
import sys
import unittest
from pathlib import Path
from openpyxl import Workbook
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from storage import deduplicate_input, parse_input

class InputDeduplicationTests(unittest.TestCase):
    def test_excel_keeps_first_row_and_other_columns(self):
        wb=Workbook();ws=wb.active
        for row in [['색상','품번','수량'],['BLACK','A01',2],['RED','B02',3],['BLUE','A01',9]]:ws.append(row)
        output=io.BytesIO();wb.save(output);wb.close()
        result=deduplicate_input({'file':base64.b64encode(output.getvalue()).decode()}, {})
        headers,rows=parse_input(result,{})
        self.assertEqual(result['removed'],1)
        self.assertEqual(headers,['품번','색상','수량'])
        self.assertEqual(rows,[['A01','BLACK',2],['B02','RED',3]])
        self.assertEqual(deduplicate_input(result,{})['removed'],0)

    def test_blank_codes_remain_for_validation(self):
        wb=Workbook();ws=wb.active
        for row in [['품번','색상'],[None,'BLACK'],[None,'RED'],['A01','BLUE'],['A01','GREEN']]:ws.append(row)
        output=io.BytesIO();wb.save(output);wb.close()
        result=deduplicate_input({'file':base64.b64encode(output.getvalue()).decode()}, {})
        _,rows=parse_input(result,{})
        self.assertEqual(result['removed'],1)
        self.assertEqual(rows,[['','BLACK'],['','RED'],['A01','BLUE']])

if __name__=='__main__':unittest.main()
