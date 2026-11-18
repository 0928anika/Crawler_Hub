import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import disneygolf_official as c
FIX=ROOT/'tests/fixtures/disneygolf'
class Context:
 def checkpoint(self):pass
 def log(self,*a):pass
class Tests(unittest.TestCase):
 def setUp(self):self.source=(FIX/'DQ1LTJ007.html').read_text('utf-8');self.doc=c.soup(self.source)
 def test_exact_search_and_identity(self):
  with patch.object(c,'get_html',return_value=((FIX/'search.html').read_text('utf-8'),c.CONFIG['site'])):
   self.assertEqual(c.find_products(Context(),'DQ1LTJ007'),['4438'])
  info=c.info_rows(self.doc);self.assertEqual(c.identity(self.doc,self.source,'DQ1LTJ007',info)[1],'DQ1LTJ007')
  self.assertIsNone(c.identity(self.doc,self.source,'DQ1LTJ008',info))
 def test_name_removes_brand_and_model(self):
  self.assertEqual(c.clean_name('[디즈니골프]여성 캐릭터 패턴 하프넥 집업 점퍼 DQ1LTJ007',c.CONFIG['aliases'],['DQ1LTJ007']),'여성 캐릭터 패턴 하프넥 집업 점퍼')
 def test_real_color_codes_sizes_and_unlinked_gallery(self):
  colors=c.page_colors(self.doc,self.source,{})
  self.assertEqual([c.split_color(label) for label,_ in colors],[('블랙','BK'),('화이트','WH')])
  self.assertTrue(all(data['sizes']==['090','095','100','105'] for _,data in colors))
  self.assertTrue(all(set(data['prices'])=={159000} for _,data in colors))
  self.assertFalse(c.linked_images(self.doc,self.source,c.CONFIG['site']))
 def test_body_preserves_embedded_notice_size_without_tables(self):
  images,notice,sizes=c.detail_parts(Context(),self.doc,c.CONFIG['site'],'DQ1LTJ007')
  self.assertEqual(images,['https://tmdcf5716.cafe24.com/image/disneygolf/DQ1LTJ007.jpg'])
  self.assertEqual((notice,sizes),([],[]))
  self.assertFalse(any('/top/' in u or 'caicon' in u for u in images))
 def test_registration(self):
  from registry import get_brand
  b=get_brand('disneygolf_official');self.assertTrue(b['available']);self.assertEqual(b['crawl_source'],'공식 홈페이지');self.assertEqual(b['mapping_mode'],'manual');self.assertTrue(b['hybrid_mapping'])

 def test_dq1lts080_keeps_soldout_color_and_all_original_sizes(self):
  source=(FIX/'DQ1LTS080_options.html').read_text('utf-8')
  colors=c.page_colors(c.soup(source),source,{})
  self.assertEqual([label for label,_ in colors],['블랙(BK)','그린(GN)','핑크(PI)','화이트(WH)'])
  self.assertTrue(all(data['sizes']==['090','095','100','105'] for _,data in colors))
  self.assertTrue(all(set(data['prices'])=={59000} for _,data in colors))

 def test_mapping_palette_preserves_different_color_sizes_only_when_selected(self):
  colors=[dict(color_name='블랙',color_code='BK',sizes=['090','095']),dict(color_name='화이트',color_code='WH',sizes=['100','105'])]
  palette=c.palette_of(colors,include_size=True)
  self.assertEqual([entry['size'] for entry in palette],['090,095','100,105'])
  self.assertTrue(all('size' not in entry for entry in c.palette_of(colors)))
if __name__=='__main__':unittest.main()
