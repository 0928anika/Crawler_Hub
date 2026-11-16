import sys, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import redface_official as c
class Context:
 def __init__(self,code,fields=()):self.product_code=code;self.fields=fields;self.rows=[];self.unmapped=[];self.errors=[]
 def enabled(self,k):return k in self.fields
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def fail_item(self,m):self.errors.append(m)
 def emit_result(self,r):self.rows.append(r)
 def emit_unmapped(self,r,colors,url,shared_detail_html=''):self.unmapped.append((r,colors,url))
class Tests(unittest.TestCase):
 def source(self,n):return (ROOT/'tests/fixtures/redface'/(n+'.html')).read_text(encoding='utf-8-sig')
 def test_official_fields_and_soldout_sizes(self):
  d=c.parse(self.source('4348'));ctx=Context('REWFJKFBF457')
  self.assertEqual(c.price(ctx,d),99000);self.assertEqual(c.price(ctx,d,True),198000)
  self.assertEqual(c.sizes(d,'베이지'),'090,095,100,105,110')
  self.assertEqual(len(c.gallery_urls(d,c.SITE)),6)
  self.assertTrue(all(not x['image'] for x in c.colors(d,True)))
  urls=c.details(d,c.SITE);self.assertEqual(len(urls),3)
  with patch.object(c,'detail_image',return_value=True):self.assertNotIn('<table',c.detail(ctx,urls,{}))
 def run_case(self,n,transform=lambda x:x,fields=('main_image',)):
  code={'3951':'REEGSTXBE101','4348':'REWFJKFBF457'}[n];ctx=Context(code,fields);src=transform(self.source(n))
  with patch.object(c,'search',return_value=[c.SITE+'/product/test/'+n+'/']),patch.object(c,'fetch',return_value=(src,c.SITE+'/product/test/'+n+'/')),patch.object(c,'prefetch'),patch.object(c,'gallery',side_effect=lambda ctx,urls,*a:(urls[0],[],'')),patch.object(c,'manual_photos',side_effect=lambda ctx,urls,linked,cache:[u for u in urls if u not in linked]):c.crawl(ctx)
  return ctx
 def test_single_color_is_automatic(self):
  ctx=self.run_case('3951');self.assertEqual(len(ctx.rows),1);self.assertFalse(ctx.unmapped);self.assertEqual(ctx.rows[0]['color_name'],'그린')
 def test_multiple_colors_without_links_are_manual(self):
  ctx=self.run_case('4348');self.assertFalse(ctx.rows);self.assertEqual(len(ctx.unmapped),6)
 def linked(self,source,all_links=False):
  d=c.parse(source);urls=c.gallery_urls(d,c.SITE)
  for i,node in enumerate(d.select('ul[option_title="색상"] li')):
   if i==0 or all_links:node['link_image']=urls[i]
  if all_links:
   for node in d.select('.xans-product-mobileimage li')[1:]:node.decompose()
  return str(d)
 def test_partial_mapping_preserves_automatic_result(self):
  ctx=self.run_case('4348',self.linked);self.assertEqual(len(ctx.rows),1);self.assertEqual(ctx.rows[0]['color_name'],'블랙');self.assertEqual(len(ctx.unmapped),5)
 def test_fully_mapped_has_no_manual_candidates(self):
  ctx=self.run_case('4348',lambda s:self.linked(s,True));self.assertEqual(len(ctx.rows),2);self.assertFalse(ctx.unmapped)
 def test_no_optional_fields(self):
  ctx=self.run_case('4348',fields=());self.assertEqual(len(ctx.rows),2);self.assertFalse(ctx.unmapped);self.assertNotIn('price',ctx.rows[0])
 def test_missing_regular_price_is_optional(self):
  with patch.object(c,'field',return_value='0원'):self.assertIsNone(c.price(Context('REWFJKFBF457'),None,True))
if __name__=='__main__':unittest.main()
