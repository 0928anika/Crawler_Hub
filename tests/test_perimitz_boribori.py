import sys,json,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import perimitz_boribori as c
FIX=ROOT/'tests/fixtures/perimitz_boribori'
class Context:
 product_code='P2545P293'
 def __init__(self,fields=()):self.fields=fields;self.rows=[]
 def enabled(self,k):return k in self.fields
 def checkpoint(self):pass
 def log(self,*a):pass
 def stage(self,*a):pass
 def emit_unmapped(self,*a,**kw):self.rows.append((a,kw))
class BoriboriTests(unittest.TestCase):
 def test_real_product_options_prices_and_body(self):
  with patch.object(c,'fetch',return_value=(FIX/'product.html').read_bytes()):data,prices,link=c.product(Context(),408114549)
  colors,sizes,raw=c.palette(data)
  self.assertEqual([x['color_code'] for x in colors],['10','09','06','04']);self.assertEqual(sizes,['100','110','120','130','140'])
  self.assertEqual(prices['selPrc'],12900);self.assertEqual(prices['normPrc'],43000)
  urls=c.body_images(data);self.assertEqual(len(urls),2);self.assertTrue(all('P2545P293_' in u for u in urls));self.assertFalse(any('popup' in u or '_title' in u for u in urls))
 def test_collect_manual_candidates_and_detail_without_generated_tables(self):
  def fetch(ctx,url,*args):return (FIX/('search.json' if 'searches' in url else 'product.html')).read_bytes()
  ctx=Context(['price','regular_price','size','detail_page'])
  with patch.object(c,'fetch',side_effect=fetch),patch.object(c,'checked_photo',return_value=True):c.crawl(ctx)
  self.assertEqual(len(ctx.rows),2);row,colors,url=ctx.rows[0][0]
  self.assertEqual(row['price'],12900);self.assertEqual(row['regular_price'],43000);self.assertEqual(row['product_name'],'벨로아골지레깅스');self.assertNotIn('<table',row['detail_page']);self.assertEqual(len(colors),4)
 def test_optional_prices_are_not_emitted_when_unselected(self):
  def fetch(ctx,url,*args):return (FIX/('search.json' if 'searches' in url else 'product.html')).read_bytes()
  ctx=Context()
  with patch.object(c,'fetch',side_effect=fetch),patch.object(c,'checked_photo',return_value=True):c.crawl(ctx)
  self.assertNotIn('regular_price',ctx.rows[0][0][0]);self.assertNotIn('price',ctx.rows[0][0][0])
 def test_search_empty_and_invalid_response_are_distinct(self):
  empty=b'{"data":{"result":{"hits":{"total":{"value":0},"hits":[]}}}}'
  with patch.object(c,'fetch',return_value=empty):self.assertEqual(c.search(Context(),'X'),([],0))
  with patch.object(c,'fetch',return_value=b'{}'),self.assertRaises(c.CollectionError):c.search(Context(),'X')
 def test_studio_check_accepts_packshot_rejects_irregular_background(self):
  import random
  image=Image.new('RGB',(200,268),'white')
  from PIL import ImageDraw
  ImageDraw.Draw(image).rectangle((50,40,150,230),fill='pink');self.assertTrue(c.studio_background(image)[0])
  rng=random.Random(42);image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(200*268)]);self.assertFalse(c.studio_background(image)[0])
 def test_pale_ivory_knit_is_not_rejected_as_blank(self):
  with Image.open(FIX/'pale_knit.jpg') as image:self.assertTrue(c.studio_background(image)[0])
  self.assertFalse(c.studio_background(Image.new('RGB',(200,268),'white'))[0])
 def test_combined_color_size_options_are_split(self):
  data=dict(optionItem=[dict(optItemNm='색상/사이즈',optValueList=[dict(optValueNm='크림／110'),dict(optValueNm='크림／120')])])
  colors,sizes,raw=c.palette(data);self.assertEqual(colors,[dict(color_name='크림',color_code='')]);self.assertEqual(sizes,['110','120'])
 def test_expected_failure_is_reported_to_app(self):
  ctx=Context();ctx.fail_item=lambda message:setattr(ctx,'error',message)
  with patch.object(c,'search',return_value=([],0)),self.assertRaises(c.CollectionError):c.crawl(ctx)
  self.assertIn('검색 결과 없음',ctx.error)
 def test_mixed_seller_link_and_its_banner_never_contribute(self):
  clean=json.loads((FIX/'442750200.json').read_text('utf-8'));mixed=json.loads((FIX/'443619885.json').read_text('utf-8'))
  ctx=Context(['detail_page']);ctx.product_code='P2642K450'
  def photo(ctx,url):return 'add_8' in url or '/P442750200/' in url
  # The mixed seller has one label close-up passing; its other photos fail.
  mixed['productImage']['add8ExtNm']='cdn/product/P443619885/add_8.jpg'
  with patch.object(c,'search',return_value=(['mixed','clean'],2)),patch.object(c,'product',side_effect=[(mixed,{},'mixed'),(clean,{},'clean')]),patch.object(c,'checked_photo',side_effect=photo):c.crawl(ctx)
  self.assertTrue(ctx.rows);self.assertTrue(all('/P442750200/' in row[0][2] for row in ctx.rows))
  body=ctx.rows[0][0][0]['detail_page'];self.assertIn('P2642K450_M.jpg',body);self.assertNotIn('KakaoTalk',body);self.assertNotIn('popup_img',body)
 def test_sellerhub_shared_header_footer_are_excluded(self):
  for url in ['http://wi.sellerhub.co.kr/Image/Header/v23/123.jpg','http://wi2.sellerhub.co.kr/Image/Footer/v20/123.jpg','http://image.pravs.co.kr/island/avantop.jpg','http://image.pravs.co.kr/island/avanbottom.jpg']:
   self.assertTrue(c.excluded_image(url))
if __name__=='__main__':unittest.main()
