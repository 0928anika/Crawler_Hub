import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
from bs4 import BeautifulSoup
from detail_store import excel_detail,EXCEL_TEXT_LIMIT
from schema import sanitized_html
class DetailLayoutTests(unittest.TestCase):
 def test_export_has_uniform_width_centering_and_responsive_height(self):
  markup='<div><img src="https://example.com/a.jpg" width="200" height="300"><img src="https://example.com/b.jpg" style="width:1200px;float:left"></div>'
  output,overflow=excel_detail(markup);self.assertFalse(overflow);soup=BeautifulSoup(output,'html.parser')
  self.assertEqual(soup.div['align'],'center');self.assertIn('max-width:860px',soup.div['style']);self.assertIn('margin:0 auto',soup.div['style'])
  for img in soup.select('img'):
   self.assertEqual(img['width'],'860');self.assertNotIn('height',img.attrs);self.assertIn('width:100%',img['style']);self.assertIn('height:auto',img['style']);self.assertIn('margin:0 auto',img['style']);self.assertNotIn('float',img['style'])
 def test_reexport_does_not_accumulate_wrappers(self):
  once,_=excel_detail('<p>상품 설명</p><img src="https://example.com/a.jpg"><img src="https://example.com/b.jpg">');twice,_=excel_detail(once);self.assertEqual(once,twice)
 def test_preview_keeps_same_layout_and_removes_unsafe_source_styles(self):
  output,_=excel_detail('<img src="https://example.com/a.jpg" onload="bad()"><script>bad()</script>')
  preview=sanitized_html(output);soup=BeautifulSoup(preview,'html.parser');self.assertIn('width:100%',soup.img['style']);self.assertIn('margin:0 auto',soup.div['style']);self.assertNotIn('onload',preview);self.assertNotIn('<script',preview)
 def test_tables_text_and_original_image_order_preserved(self):
  source='<h2>사이즈표</h2><table><tr><td colspan="2">95</td></tr></table><img src="https://example.com/1.jpg"><img src="https://example.com/2.jpg">'
  output,_=excel_detail(source);soup=BeautifulSoup(output,'html.parser');self.assertEqual(soup.td['colspan'],'2');self.assertIn('사이즈표',soup.get_text());self.assertEqual([x['src'] for x in soup.select('img')],['https://example.com/1.jpg','https://example.com/2.jpg'])
 def test_readable_tables_and_headings_survive_export_and_preview(self):
  output,_=excel_detail('<h2>소재</h2><table><tr><th>색상</th><td>IVORY</td></tr></table><img src="https://example.com/a.jpg">')
  soup=BeautifulSoup(output,'html.parser');self.assertIn('border-collapse:collapse',soup.table['style']);self.assertIn('background:#f6f6f6',soup.th['style']);self.assertIn('padding:10px',soup.td['style']);self.assertIn('font-size:20px',soup.h2['style'])
  again,_=excel_detail(output);self.assertEqual(again,output)
  self.assertIn('border-collapse:collapse',sanitized_html(output))
 def test_overflow_checked_after_layout_added_and_empty_stays_empty(self):
  self.assertEqual(excel_detail(''),('',False));output,overflow=excel_detail('<p>'+('x'*EXCEL_TEXT_LIMIT)+'</p><img src="https://example.com/a.jpg">');self.assertEqual(output,'');self.assertTrue(overflow)
if __name__=='__main__':unittest.main()
