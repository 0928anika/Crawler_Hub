import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import pierrecardin_official as c
FIX=ROOT/'tests/fixtures/pierrecardin/PRKR3901.html'
class Tests(unittest.TestCase):
 def test_real_identity_and_clean_name(self):
  raw=FIX.read_text('utf-8');doc=c.soup(raw);result=c.identity(doc,raw,'PRKR3901',c.info_rows(doc));self.assertEqual(result[1],'PRKR3901');self.assertIsNone(c.identity(doc,raw,'PRKR3902',c.info_rows(doc)));self.assertEqual(c.clean_name(result[0],c.CONFIG['aliases'],['PRKR3901']),'미니멀조직 하프터틀넥 니트')
 def test_all_real_colors_codes_and_sizes(self):
  raw=FIX.read_text('utf-8');rows=c.page_colors(c.soup(raw),raw,{});self.assertEqual([c.split_color(name) for name,_ in rows],[('터키쉬블루','34'),('와인','78'),('베이지','82'),('차콜','98')]);self.assertTrue(all(row['sizes']==['95','100','105','110'] for _,row in rows))
 def test_detail_preserves_product_size_and_excludes_common_banners(self):
  raw=FIX.read_text('utf-8');images,_,_=c.detail_parts(None,c.soup(raw),c.CONFIG['site']+'/product/detail.html?product_no=1072','PRKR3901');self.assertEqual(len(images),8);self.assertTrue(any('/size.jpg' in u for u in images));self.assertTrue(all('/common/' not in u and 'PRKR3901%20/' not in u for u in images));self.assertTrue(any('/images/d_06.jpg' in u for u in images))
 def test_actual_gallery_groups_and_host_scope(self):
  raw=FIX.read_text('utf-8');groups=c.gallery_groups(None,c.soup(raw),c.CONFIG['site']+'/product/detail.html?product_no=1072','1072');self.assertEqual(len(groups),5);self.assertTrue(all('ecimg.cafe24img.com' in u for group in groups for u in group));self.assertEqual(c.CONFIG['hosts'],('pierrecardinkorea.com','www.pierrecardinkorea.com'))
if __name__=='__main__':unittest.main()
