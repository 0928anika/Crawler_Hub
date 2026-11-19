import sys,json,copy,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
from test_oberon_brands import load,run,ALL
FIX=ROOT/'tests/fixtures/phantom'
def fixture(name):return json.loads((FIX/(name+'.json')).read_text(encoding='utf-8'))
class Tests(unittest.TestCase):
 def routes(self,goods=None,detail=None,search=None):
  goods=goods or fixture('product')
  return {'https://bucketstore.com/bucketapi/goods/search/v4*':search or fixture('search'),
   'https://bucketstore.com/bucketapi/goods/lambda*':[{'itemkind':{'category1':'골프','category2':'가방'}}],
   'https://bucketstore.com/goods/detail/211F3CB201_TQ':'1:'+json.dumps({'goodsDetail':goods,'actualMeasurementSizeList':fixture('measure')}),
   'https://bucketstore.com/bucketapi/goods/211F3CB201_TQ/detail*':detail if detail is not None else fixture('detail')}
 def test_actual_product_options_prices_notice_sizes_and_banner_exclusion(self):
  rows,fail,error,_=run(load('phantom_official'),'phantom_official','211f3cb201',ALL,self.routes())
  self.assertIsNone(error,fail);self.assertEqual(len(rows),1);r=rows[0]
  self.assertEqual((r['상품명'],r['색상명'],r['색상코드'],r['사이즈']),('남성 스탠드 캐디백','터쿠아즈','TQ','F'))
  self.assertEqual((r['판매가'],r['정상가']),(530600,758000))
  self.assertIn('제품소재',r['상세페이지 HTML']);self.assertIn('가로',r['상세페이지 HTML']);self.assertIn('127',r['상세페이지 HTML'])
  self.assertNotIn('brand_intro',r['상세페이지 HTML']);self.assertNotIn('/contents/brand/',r['상세페이지 HTML'])
  self.assertTrue(r['대표이미지']);self.assertIn('211F3CB201_TQ',r['대표이미지'])
 def test_requested_color_filters_exact_code_and_other_brands(self):
  search=fixture('search');search['data']['body'] += [dict(code='211F3CB201_BK',brandName='FANTOM'),dict(code='211F3CB201_TQ',brandName='PING'),dict(code='211F3CB2010_TQ',brandName='FANTOM')]
  rows,fail,error,_=run(load('phantom_official'),'phantom_official','211F3CB201_TQ',[],self.routes(search=search))
  self.assertIsNone(error,fail);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['색상코드'],'TQ')
 def test_wrong_detail_identity_fails(self):
  for field,value in [('brandName','PING'),('goodsCode','OTHER_TQ')]:
   goods=fixture('product');goods[field]=value
   rows,fail,error,_=run(load('phantom_official'),'phantom_official','211F3CB201',[],self.routes(goods=goods))
   self.assertIsNotNone(error);self.assertFalse(rows);self.assertTrue(fail)
 def test_missing_notice_and_sizes_do_not_block_product(self):
  detail=fixture('detail');detail['content']['notificationInfoList']=[]
  routes=self.routes(detail=detail);key='https://bucketstore.com/goods/detail/211F3CB201_TQ'
  routes[key]='1:'+json.dumps({'goodsDetail':fixture('product')})
  rows,fail,error,_=run(load('phantom_official'),'phantom_official','211F3CB201',['detail_page'],routes)
  self.assertIsNone(error,fail);self.assertEqual(len(rows),1);self.assertIn('<img',rows[0]['상세페이지 HTML'])
if __name__=='__main__':unittest.main()
