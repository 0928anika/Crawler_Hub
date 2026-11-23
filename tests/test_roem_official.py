import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import roem_official as c
from crawler_sdk import CrawlContext
class Tests(unittest.TestCase):
 def setUp(self):self.source=(ROOT/'tests/fixtures/roem/RMJHG4VA11.html').read_text(encoding='utf-8');self.doc=c.soup(self.source)
 def test_current_empty_search_is_not_access_error(self):
  source='<div class="prdCount">검색결과 <span class="total_count">0</span></div><div class="xans-search-form"><div class="noData">검색결과가 없습니다.</div></div>'
  with patch.object(c,'fetch',return_value=source):self.assertEqual(c.search(type('C',(),{})(),'RMKAG4VRTA'),[])
 def test_missing_search_structure_still_reports_access_error(self):
  with patch.object(c,'fetch',return_value='<html>접근 제한</html>'):
   with self.assertRaisesRegex(c.CrawlError,'검색 0건 아님'):c.search(type('C',(),{})(),'RMKAG4VRTA')
 def test_registration_separate_from_eland_and_name_keeps_collection(self):
  from registry import get_brand
  self.assertTrue(get_brand('roem_official')['available']);self.assertTrue(get_brand('roem_eland_studio')['available'])
  self.assertEqual(c.identity(self.doc,'RMJHG4VA11')[0],'[올데이코트] 카라 미디코트');self.assertIsNone(c.identity(self.doc,'RMJHG4VA110'))
 def test_english_color_size_labels_codes_and_general_price(self):
  colors=c.palette(self.source);self.assertEqual([r['color_code'] for r in colors],['19','31','60','64'])
  self.assertEqual(colors[0]['color_name'],'Black');self.assertEqual(colors[0]['sizes'],['S(085)','M(088)','L(091)'])
  self.assertTrue(all(set(r['prices'])=={159200} for r in colors));self.assertEqual(c.amount(c.field(self.doc,'소비자가')),199000)
  self.assertNotEqual(colors[0]['prices'][0],c.amount(c.field(self.doc,'최적할인가')))
 def test_original_body_images_preserved_without_common_notice_svg(self):
  urls=c.detail_urls(self.doc,c.SITE,'RMJHG4VA11');self.assertEqual(len(urls),4)
  self.assertTrue(any('RMJHG4VA11_800.jpg' in u for u in urls));self.assertFalse(any('notice.svg' in u for u in urls))
  self.assertTrue(all(u.startswith('https://') for u in urls));self.assertTrue(c.gallery_urls(self.doc,c.SITE))
 def test_crawl_merges_photos_and_uses_one_detail_source(self):
  meta=json.loads((ROOT/'brands/roem_official.json').read_text(encoding='utf-8'));events=[]
  with tempfile.TemporaryDirectory() as folder:
   ctx=CrawlContext('unused',Path(folder)/'out.xlsx',0,['size','price','main_image','detail_page'],meta['output_headers'],lambda:None,lambda k,**d:events.append((k,d)),input_item={'품번':'RMJHG4VA11'},mapping_mode='manual',image_mode='url')
   with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=1',c.SITE+'/product/detail.html?product_no=2']),patch.object(c,'fetch',side_effect=[self.source,self.source.replace('Black','Back')]),patch.object(c,'gallery',side_effect=[['https://roem.com/a.jpg'],['https://roem.com/b.jpg']]),patch.object(c,'checked',side_effect=lambda ctx,url,cache:{'data':url.encode()}),patch.object(c,'detail',return_value='<p>공식 본문</p>') as detail:
    c.crawl(ctx);ctx.finish()
   detail.assert_called_once()
  rows=[d for k,d in events if k=='row' and not d.get('header')];self.assertEqual(len(rows),2)
  for row in rows:
   self.assertEqual(len(row['unmapped']['colors']),4);self.assertEqual(row['unmapped']['shared_detail_html'],'<p>공식 본문</p>')
   self.assertEqual(next(v for v in row['unmapped']['colors'] if v['color_code']=='19')['color_name'],'Black')
if __name__=='__main__':unittest.main()
