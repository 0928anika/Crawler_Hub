import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import anybody_eland_official,clovis_eland_official
from PIL import Image,ImageDraw
class Context:
 def __init__(self,code):self.product_code=code;self.rows=[];self.errors=[]
 def enabled(self,k):return k in ['main_image','detail_page','size']
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def fail_item(self,m):self.errors.append(m)
 def emit_unmapped(self,*args,**kw):self.rows.append((args,kw))
class StudioTests(unittest.TestCase):
 def test_large_packshot_actual_white_background_is_accepted(self):
  with Image.open(ROOT/'tests/fixtures/anybody/ANPM26A17_representative.jpg') as im:
   for c in [anybody_eland_official,clovis_eland_official]:
    self.assertTrue(c.studio_background(im)[0])
 def test_large_product_on_uniform_colored_border_is_accepted(self):
  im=Image.new('RGB',(300,300),(160,180,140));ImageDraw.Draw(im).rectangle((16,20,284,280),fill=(210,180,160));ImageDraw.Draw(im).line((150,35,150,265),fill='black',width=8)
  for c in [anybody_eland_official,clovis_eland_official]:
   self.assertTrue(c.studio_background(im)[0])
 def test_official_priority_and_all_sellers_fallback(self):
  for c in [anybody_eland_official,clovis_eland_official]:
   official=c.SITE+'/i/item?itemNo=1&lowerVendNo='+c.VENDOR_NO
   other=c.SITE+'/i/item?itemNo=2&lowerVendNo=OTHER'
   with patch.object(c,'search_site',return_value=c.SearchResults([other,official],2,['OTHER',c.VENDOR_NO])):
    self.assertEqual(c.search(Context('CODE'),'CODE'),[official])
   with patch.object(c,'search_site',return_value=c.SearchResults([other],1,['OTHER'])):
    self.assertEqual(c.search(Context('CODE'),'CODE'),[other])
 def test_freeform_seller_options_remain_manual_candidates(self):
  for c in [anybody_eland_official,clovis_eland_official]:
   rows=[dict(itemOptionTcode='99',itemOptionName='아이보리',itemOptionNo='1'),dict(itemOptionTcode='99',itemOptionName='네이비',itemOptionNo='2')]
   palette,actual=c.colors(Context('CODE'),rows)
   self.assertEqual(palette,[dict(color_name='아이보리',color_code=''),dict(color_name='네이비',color_code='')]);self.assertEqual(len(actual),2)
 def test_official_seller_does_not_require_uniform_background(self):
  for c,fixture in [(anybody_eland_official,'anybody'),(clovis_eland_official,'clovis')]:
   data=json.loads((ROOT/'tests/fixtures'/fixture/'product.json').read_text('utf-8'));ctx=Context(data['itemMain']['modelNo'])
   with patch.object(c,'search',return_value=['link']),patch.object(c,'product',return_value=data),patch.object(c,'studio_product') as studio,patch.object(c,'colors',return_value=([dict(color_name='NAVY',color_code='78')],[])),patch.object(c,'options',return_value=[]),patch.object(c,'gallery',return_value=[('photo','hash')]),patch.object(c,'detail_images',return_value=['body']):
    c.crawl(ctx)
   studio.assert_not_called();self.assertEqual(len(ctx.rows),1)
 def test_uniform_colored_background_and_irregular_background(self):
  plain=Image.new('RGB',(300,400),(150,170,130));ImageDraw.Draw(plain).rectangle((80,45,220,355),fill='black')
  busy=Image.new('RGB',(300,400),'white');draw=ImageDraw.Draw(busy)
  for x in range(0,300,4):draw.rectangle((x,0,x+1,400),fill='black')
  for c in [anybody_eland_official,clovis_eland_official]:
   self.assertTrue(c.studio_background(plain)[0]);self.assertFalse(c.studio_background(busy)[0])
 def test_fallback_merges_all_options_photos_but_one_link_detail(self):
  for c,fixture in [(anybody_eland_official,'anybody'),(clovis_eland_official,'clovis')]:
   base=json.loads((ROOT/'tests/fixtures'/fixture/'product.json').read_text('utf-8'));code=base['itemMain']['modelNo'];ctx=Context(code)
   links=[c.SITE+'/i/item?itemNo='+str(i)+'&lowerVendNo=SELLER'+str(i) for i in [1,2]]
   data=[]
   for i in [1,2]:
    d=copy.deepcopy(base);d['itemMain'].update(itemNo=str(i),lowerVendNo='SELLER'+str(i));data.append(d)
   def opts(ctx,main,parent=None):
    return [dict(itemOptionTcode='10',itemOptionName=('01 RED' if main['itemNo']=='1' else '02 BLUE'),itemOptionNo='x')] if parent is None else [dict(itemOptionTcode='20',itemOptionName=('S' if main['itemNo']=='1' else 'L'))]
   with patch.object(c,'search',return_value=links),patch.object(c,'product',side_effect=data),patch.object(c,'studio_product',return_value=True),patch.object(c,'options',side_effect=opts),patch.object(c,'gallery',side_effect=[[('photo1','hash1')],[('photo2','hash2')]]),patch.object(c,'detail_images',return_value=['https://item.elandrs.com/body.jpg']) as detail:
    c.crawl(ctx)
   self.assertFalse(ctx.errors);self.assertEqual(len(ctx.rows),2);self.assertEqual(detail.call_count,1)
   for args,kw in ctx.rows:
    self.assertEqual(len(args[1]),2);self.assertEqual(args[0]['size'],'S,L');self.assertEqual(args[0]['product_url'],links[0]);self.assertEqual(kw['shared_detail_html'].count('<img'),1)
 def test_nonstudio_product_is_skipped_before_options(self):
  for c,fixture in [(anybody_eland_official,'anybody'),(clovis_eland_official,'clovis')]:
   data=json.loads((ROOT/'tests/fixtures'/fixture/'product.json').read_text('utf-8'));ctx=Context(data['itemMain']['modelNo']);data['itemMain']['lowerVendNo']='OTHER'
   with patch.object(c,'search',return_value=['link']),patch.object(c,'product',return_value=data),patch.object(c,'studio_product',return_value=False),patch.object(c,'options') as opts:
    with self.assertRaisesRegex(c.CollectionError,'스튜디오컷 조건 통과 상품 없음'):c.crawl(ctx)
   opts.assert_not_called()
if __name__=='__main__':unittest.main()
