import importlib.util,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('anybody_fp',ROOT/'brands/anybody_fashionplus.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/anybody_fashionplus';LINK=c.BASE+'/goods/detail/442640957'
def doc(kind='product'):return c.BeautifulSoup((FIX/('442640957_'+kind+'.html')).read_bytes(),'html.parser')
class C:
 product_code='ANJS26N68'
 def __init__(self,fields=()):self.fields=fields;self.rows=[];self.errors=[]
 def enabled(self,k):return k in self.fields
 def stage(self,*a):pass
 def log(self,*a):pass
 def checkpoint(self):pass
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,*a,**kw):self.rows.append((a,kw))
def shot():
 im=Image.new('RGB',(800,1072),'white');ImageDraw.Draw(im).rectangle((220,100,580,970),fill='black');return im
class AnybodyFashionplusTests(unittest.TestCase):
 def test_color_numeric_prefix_and_all_actual_sizes(self):
  with patch.object(c,'fetch',return_value=(FIX/'442640957_options.json').read_bytes()):
   colors,sizes=c.options(C(),LINK,True,doc=doc(),code='ANJS26N68')
  self.assertTrue(all(x==dict(color_name='네이비',color_code='78') for x in colors));self.assertEqual(set(sizes),{'S','M','L','XL'})
 def test_clean_title(self):self.assertEqual(c.clean_name('공용 밍크극세사 파자마 세트_모던 ANJS26N68 EIC','ANJS26N68'),'공용 밍크극세사 파자마 세트_모던')
 def test_seller_epc_suffix_removed(self):self.assertEqual(c.clean_name('공용 밍크극세사 파자마세트-모던 ANJS26N68 _epc','ANJS26N68'),'공용 밍크극세사 파자마세트-모던')
 def test_large_white_packshot_accepted_busy_background_rejected(self):
  with Image.open(ROOT/'tests/fixtures/anybody/ANPM26A17_representative.jpg') as im:self.assertTrue(c.studio_background(im)[0])
  im=shot();draw=ImageDraw.Draw(im)
  for x in range(0,800,8):draw.line((x,0,x,1072),fill='black',width=3)
  self.assertFalse(c.studio_background(im)[0])
 def test_all_original_body_images_preserved_no_generated_tables(self):
  with patch.object(c,'soup_get',return_value=doc('detail')),patch.object(c,'image_get',return_value=shot()):urls=c.detail_images(C(),LINK,'ANJS26N68')
  self.assertEqual(len(urls),11);self.assertEqual(urls[0],'https://img-studio.elandmall.com/Winstudio/ANJS26N68.jpg')
 def test_nonstudio_thumbnail_skipped_without_product_request(self):
  ctx=C(['main_image']);im=Image.new('RGB',(800,1072),'white')
  with patch.object(c,'search',return_value=[(LINK,'ANJS26N68','thumb')]),patch.object(c,'image_get',return_value=im),patch.object(c,'soup_get') as loader:
   with self.assertRaises(c.CollectionError):c.crawl(ctx)
  loader.assert_not_called()
 def test_mixed_gallery_uses_next_clean_link_detail(self):
  ctx=C(['main_image','detail_page']);second=c.BASE+'/goods/detail/2';documents={}
  for link,prefix in [(LINK,'first'),(second,'second')]:
   p=doc()
   for i,node in enumerate(p.select('.m_prodetail-head-thumbnail .btn_thumbnail [data-preload]')):node['data-preload']=json.dumps(dict(src=prefix+str(i)))
   documents[link]=p
  busy=Image.new('RGB',(800,1072),'white')
  def image(ctx,url):return busy if url.endswith('/first1') else shot()
  with patch.object(c,'search',return_value=[(LINK,'ANJS26N68','thumb1'),(second,'ANJS26N68','thumb2')]),patch.object(c,'soup_get',side_effect=lambda ctx,url:documents[url]),patch.object(c,'image_get',side_effect=image),patch.object(c,'options',return_value=([dict(color_name='네이비',color_code='78')],[])),patch.object(c,'detail_images',return_value=['clean-body']) as body:
   c.crawl(ctx)
  body.assert_called_once_with(ctx,second,'ANJS26N68');self.assertFalse(ctx.errors);self.assertTrue(ctx.rows)
  self.assertTrue(all(args[0]['product_url']==second for args,kw in ctx.rows));self.assertFalse(any(args[2].endswith('first1') for args,kw in ctx.rows))
 def test_no_clean_gallery_leaves_detail_empty(self):
  ctx=C(['main_image','detail_page']);counter=[0]
  def image(ctx,url):
   counter[0]+=1
   return Image.new('RGB',(800,1072),'white') if counter[0]==3 else shot()
  with patch.object(c,'search',return_value=[(LINK,'ANJS26N68','thumb')]),patch.object(c,'soup_get',return_value=doc()),patch.object(c,'image_get',side_effect=image),patch.object(c,'options',return_value=([dict(color_name='네이비',color_code='78')],[])),patch.object(c,'detail_images') as body:
   c.crawl(ctx)
  body.assert_not_called();self.assertTrue(ctx.rows);self.assertTrue(all(kw['shared_detail_html']=='' for args,kw in ctx.rows))
if __name__=='__main__':unittest.main()
