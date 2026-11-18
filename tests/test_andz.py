import sys,json,copy,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import andz_official as c
class Context:
 def __init__(self,fields=(),code='bzg3sb1101'):self.product_code=code;self.fields=fields;self.rows=[];self.errors=[]
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return k in self.fields
 def emit_result(self,r):self.rows.append(r)
 def fail_item(self,m):self.errors.append(m)
class AndzTests(unittest.TestCase):
 def setUp(self):self.data=json.loads((ROOT/'tests/fixtures/andz/BZG3SB1101BE.json').read_text(encoding='utf-8'));self.g=self.data['goods']
 def test_actual_official_sizes_prices_and_gallery_order(self):
  self.assertEqual(c.sizes(self.g),'M(95),L(100),XL(103),XL(105),XXL(110),3XL(115)')
  self.assertTrue(c.gallery_urls(self.g)[0].endswith('_MDL_1.jpg'))
  self.assertEqual(len(c.gallery_urls(self.g)),11)
  self.assertEqual(c.money(self.g['dspGodPrc']['salePrc']),209400)
  self.assertEqual(c.money(self.g['god']['cvrPrc']),349000)
 def test_current_ai_body_is_preserved_not_replaced_by_gallery(self):
  urls=c.body_images(self.g)
  self.assertEqual(len(urls),3)
  self.assertIn('https://img.goodwearmall.com/ai_goods/detail/BZG3SB1101/BZG3SB1101-1.jpg',urls)
  self.assertFalse(any('/goods/BZG3SB/' in u for u in urls))
 def test_legacy_original_detail_body_and_product_photos(self):
  self.g['godAiImgList']=[];self.g['godAiModelImgList']=[];self.g['godCaUseYn']='N';self.g['god']['godDetailCont']='<img src="https://img.goodwearmall.com/body.jpg"><img src="https://img.goodwearmall.com/banner/coupon.jpg">'
  urls=c.body_images(self.g);self.assertEqual(len(urls),10);self.assertTrue(urls[0].endswith('/body.jpg'));self.assertNotIn('coupon',''.join(urls))
 def test_size_diagram_precedes_table_and_notice_present(self):
  with patch.object(c,'checked',return_value={'status':'accessible'}):body=c.detail(Context(),self.g,{})
  self.assertLess(body.index('/goods/realsize/jaket.jpg'),body.index('실측사이즈'))
  self.assertIn('가슴둘레',body);self.assertIn('상품정보제공고시',body);self.assertIn('인도네시아',body)
  self.assertNotIn('<script',body)
 def test_absent_optional_size_notice_does_not_fail(self):
  self.g.update(gdSizeLktb='',godDetailList=[],godNtfcList=[],acmsrImgUrl='')
  with patch.object(c,'checked',return_value={'status':'accessible'}):body=c.detail(Context(),self.g,{})
  self.assertIn('ai_goods/detail',body);self.assertNotIn('실측사이즈',body);self.assertNotIn('상품정보제공고시',body)
 def test_size_and_notice_fallback(self):
  self.g['gdSizeLktb']='';self.g['godNtfcList']=[]
  self.assertIn('가슴둘레',c.measurement(self.g)[1]);self.assertIn('인도네시아',c.notice(self.g))
 def test_search_only_andz_and_true_zero(self):
  with patch.object(c,'api',return_value={'godList':[{'GOD_NO':'A','BRND_ID':'BBR'},{'GOD_NO':'B','BRND_ID':'PBR'}]}):self.assertEqual(c.search(Context(),'BZG3SB1101'),['A'])
  with patch.object(c,'api',return_value={'searchDTO':{'searchWord':'NO-SUCH-CODE'}}):self.assertEqual(c.search(Context(),'NO-SUCH-CODE'),[])
  with patch.object(c,'api',return_value={}):
   with self.assertRaisesRegex(ValueError,'응답 형식'):c.search(Context(),'NO-SUCH-CODE')
 def test_optional_fields_and_color_specific_input(self):
  for code in ('bzg3sb1101','BZG3SB1101_BE','BZG3SB1101BE'):
   ctx=Context(code=code)
   source='<meta property="og:url" content="https://www.goodwearmall.com/product/BZG3SB1101BE/detail">'
   with patch.object(c,'search',return_value=['BZG3SB1101BE']),patch.object(c,'api',return_value=self.data),patch.object(c,'fetch',return_value=(source,c.SITE+'/product/BZG3SB1101BE/detail')),patch.object(c,'gallery') as gallery,patch.object(c,'detail') as detail:
    c.crawl(ctx)
   self.assertEqual(len(ctx.rows),1);self.assertEqual(ctx.rows[0]['color_code'],'BE');gallery.assert_not_called();detail.assert_not_called()
if __name__=='__main__':unittest.main()
