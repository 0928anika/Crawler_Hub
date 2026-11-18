import sys,json,io,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import the_renoma_official as c
from crawler_sdk import CrawlContext
from PIL import Image

class Tests(unittest.TestCase):
 def setUp(self):
  self.source=(ROOT/'tests/fixtures/the_renoma/R2H41OJP820.html').read_text(encoding='utf8');self.doc=c.soup(self.source)
 def test_identity_and_official_codes(self):
  self.assertEqual(c.identity(self.doc,self.source,'R2H41OJP820',c.info_rows(self.doc))[1],'R2H41OJP820')
  self.assertIsNone(c.identity(self.doc,self.source,'R2H41OJP82',c.info_rows(self.doc)))
  self.assertEqual(c.split_color('BR(브라운)'),('브라운','BR'))
  self.assertEqual(c.split_color('CH(챠콜)'),('챠콜','CH'))
 def test_soldout_sizes_and_per_color_palette(self):
  options=c.page_colors(self.doc,self.source,{})
  self.assertEqual([v['sizes'] for _,v in options],[['055(55)','066(66)','077(77)']]*2)
  data=c.stock_data(self.source)
  for row in data.values():row['is_selling']='F';row['is_auto_soldout']='T'
  with patch.object(c,'stock_data',return_value=data):self.assertEqual(c.page_colors(self.doc,self.source,{}),options)
  palette=c.palette_of([dict(color_name='브라운',color_code='BR',sizes=['55']),dict(color_name='챠콜',color_code='CH',sizes=['66'])])
  self.assertEqual([p['size'] for p in palette],['55','66'])
 def test_detail_has_notice_and_size_but_no_common_banner(self):
  urls,notice,sizes=c.detail_parts(None,self.doc,c.CONFIG['site'],'R2H41OJP820')
  self.assertEqual(len(urls),3);self.assertFalse(any('/notice/' in u for u in urls))
  self.assertEqual(dict(notice)['품번'],'R2H41OJP820')
  self.assertIn('소재',dict(notice));self.assertIn('65.7',sizes[0][1]);self.assertTrue(sizes[0][0]);self.assertTrue(sizes[0][2])
 def test_gallery_urls_and_official_links(self):
  groups=c.gallery_groups(None,self.doc,c.CONFIG['site'],'10680')
  self.assertEqual(len(groups),9)
  self.assertTrue(all(len(g)==1 for g in groups))
  links=c.linked_images(self.doc,self.source,c.CONFIG['site'])
  self.assertEqual(set(links),{'BR(브라운)','CH(챠콜)'})
 def test_live_fixture_sdk_contract(self):
  def image(url,**kw):
   b=io.BytesIO();Image.new('RGB',(1000,1500),(hashlib.sha256(url.encode()).digest()[0],50,70)).save(b,'JPEG')
   return dict(status='accessible',data=b.getvalue(),width=1000,height=1500,format='JPEG',reasons=[],warnings=[])
  meta=json.loads((ROOT/'brands/the_renoma_official.json').read_text(encoding='utf8'));events=[]
  with tempfile.TemporaryDirectory() as folder:
   ctx=CrawlContext('unused',Path(folder)/'out.xlsx',0,['size','price','regular_price','main_image','additional_images','detail_page'],meta['output_headers'],lambda:None,lambda kind,**data:events.append((kind,data)),input_item={'품번':'R2H41OJP820'},mapping_mode='manual')
   with patch.object(c,'find_products',return_value=['10680']),patch.object(c,'get_html',return_value=(self.source,c.CONFIG['site']+'/product/detail.html?product_no=10680')),patch.object(c,'check_public_image',side_effect=image):c.crawl(ctx);ctx.finish()
   auto=[d for k,d in events if k=='row' and not d.get('header') and not d.get('unmapped')]
   self.assertEqual(len(auto),2)
   for row in auto:
    r=dict(zip(meta['output_headers'],row['row']));self.assertEqual(len(r['추가이미지'].split(',')),5);self.assertEqual(r['판매가'],316000);self.assertEqual(r['정상가'],514000);self.assertNotIn('/notice/',r['상세페이지 HTML'])
   self.assertGreater(ctx._unmapped_count,0)
 def test_lookup_before_format_validation(self):
  class Stop(Exception):pass
  class Ctx:
   product_code='NEW/FORMAT';allow_automatic_results=False
   def log(self,*a):pass
   def enabled(self,*a):return False
   def stage(self,*a):pass
   def fail_item(self,*a):pass
  with patch.object(c,'find_products',side_effect=Stop) as lookup:
   with self.assertRaises(Stop):c.crawl(Ctx())
  lookup.assert_called_once_with(unittest.mock.ANY,'NEW/FORMAT')
 def test_registration(self):
  from registry import get_brand
  self.assertTrue(get_brand('the_renoma_official')['available'])
if __name__=='__main__':unittest.main()
