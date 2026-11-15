import importlib.util,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('crocs_naver_test',ROOT/'brands/crocs_naver_official.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/crocs_naver'
def product():return json.loads((FIX/'product.json').read_bytes())
class C:
 def __init__(self):self.logs=[]
 def enabled(self,key):return True
 def log(self,*args):self.logs.append(args)
 def checkpoint(self):pass
class CrocsNaverTests(unittest.TestCase):
 def test_identity_color_and_soldout_sizes(self):
  p=product();r,sizes,deltas=c.identity(p,'5892438157','202651-02S','2sWDzOLlMZPwI9qrQFj3y')
  self.assertEqual(r['product_name'],'남녀공용 오프로드 스포츠 클로그');self.assertEqual(r['color_code'],'02S');self.assertEqual(len(sizes),11);self.assertEqual(sizes[0],'220_M3W5')
  with self.assertRaises(ValueError):c.identity(p,'5892438157','202651-001','2sWDzOLlMZPwI9qrQFj3y')
 def test_plain_single_color_uses_verified_model_code(self):
  p=product();p['id']=9827136209;p['naverShoppingSearchInfo']['modelName']='202651-3C7'
  color_pos=next(i+1 for i,g in enumerate(p['options']) if g['groupName'] not in ('사이즈','SIZE'))
  for row in p['optionCombinations']:row['optionName'+str(color_pos)]='아미 그린'
  r,sizes,_=c.identity(p,'9827136209','202651-3C7','2sWDzOLlMZPwI9qrQFj3y')
  self.assertEqual((r['color_name'],r['color_code']),('아미 그린','3C7'));self.assertTrue(sizes)
  for row in p['optionCombinations']:row['optionName'+str(color_pos)]='아미 그린(001)'
  with self.assertRaisesRegex(ValueError,'공식 옵션 불일치'):c.identity(p,'9827136209','202651-3C7','2sWDzOLlMZPwI9qrQFj3y')
 def test_multiple_plain_colors_still_rejected(self):
  p=product();pos=next(i+1 for i,g in enumerate(p['options']) if g['groupName'] not in ('사이즈','SIZE'))
  p['optionCombinations'][0]['optionName'+str(pos)]='아미 그린'
  with self.assertRaisesRegex(ValueError,'단일 색상'):c.identity(p,'5892438157','202651-02S','2sWDzOLlMZPwI9qrQFj3y')
 def test_notice_nested_values_are_preserved(self):
  d=c.BeautifulSoup(c.notice_table(C(),product()),'html.parser')
  self.assertEqual(len(d.select('tr')),7);self.assertIn('발길이:',d.get_text());self.assertIn('제조국',d.get_text());self.assertIn('중국',d.get_text());self.assertNotIn('A/S 책임자와 전화번호',d.get_text())
 def test_detail_excludes_shipping_composite_and_has_notice_only(self):
  contents={'renderContent':(FIX/'contents.html').read_text(encoding='utf-8')}
  with patch.object(c,'public_image',return_value={'data':b'valid'}):text=c.detail(C(),product(),contents,{})
  d=c.BeautifulSoup(text,'html.parser');self.assertEqual(len(d.select('table')),1);self.assertFalse(d.select('img'));self.assertNotIn('trace.smartwiz',text);self.assertNotIn('crocs_img_notice',text);self.assertNotIn('10126_NEW',text);self.assertNotIn('PDP_FULL',text);self.assertNotIn('사이즈 안내',text);self.assertNotIn('A/S 책임자와 전화번호',text)
 def test_external_body_images_from_both_missing_detail_cases(self):
  for number,code,count in [('12355848141','206935-1FS',3),('12282842984','212333-41S',5)]:
   p=product();p['naverShoppingSearchInfo']['modelName']=code;p.pop('productSize',None)
   contents={'renderContent':(FIX/(number+'_contents.html')).read_text(encoding='utf-8')}
   with patch.object(c,'public_image',return_value={'data':b'valid'}):text=c.detail(C(),p,contents,{})
   doc=c.BeautifulSoup(text,'html.parser');self.assertEqual(len(doc.select('img')),count)
   self.assertIn(code.split('-')[0],text);self.assertNotIn('10126_NEW',text);self.assertNotIn('proxy-smartstore',text);self.assertNotIn('crocs_img_top',text)
 def test_empty_body_is_not_success_with_only_notice(self):
  with self.assertRaisesRegex(ValueError,'본문 이미지 수집 0장'):c.detail(C(),product(),{'renderContent':'<div></div>'},{})
 def test_proxy_resolves_known_source_only(self):
  import base64
  def proxy(u):return 'https://proxy-smartstore.naver.net/img/'+base64.b64encode(u.encode()).decode()+'?token=expired'
  self.assertEqual(c.body_original(proxy('toossa.com/goodsimg/ilban/Crocs/new_page/206935-1FS_01.jpg')),'https://toossa.com/goodsimg/ilban/Crocs/new_page/206935-1FS_01.jpg')
  self.assertFalse(c.body_original(proxy('evil.test/image.jpg')))
 def test_prices_are_independent(self):
  p=product();self.assertEqual(c.prices(C(),p,[0],True),(43900,69900));self.assertEqual(c.prices(C(),p,[0],False),(43900,None))
  self.assertIsNone(c.prices(C(),p,[0,1000],True)[0])
 def test_missing_tables_do_not_create_data(self):
  self.assertEqual(c.notice_table(C(),{}),'')
 def test_store_state_rejects_wrong_store(self):
  with self.assertRaises(ValueError):c.state_from('<script>window.__PRELOADED_STATE__={"channel":{"brandUrl":"other"}};</script>')
if __name__=='__main__':unittest.main()
