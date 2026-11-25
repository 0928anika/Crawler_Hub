import sys,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
from test_oberon_brands import load,ADIDAS

class Tests(unittest.TestCase):
 def test_403_falls_back_to_existing_browser_not_missing_product(self):
  c=load('adidas_official');ctx=Mock()
  with patch.object(c,'get_json',side_effect=c.CrawlError('HTTP 403')),patch.object(c,'get_html',side_effect=c.CrawlError('HTTP 403')),patch.object(c,'browser_request',return_value=ADIDAS) as browser:
   self.assertEqual(c.fetch_product(ctx,'JZ3160'),ADIDAS)
  browser.assert_called_once_with(ctx,'/json',{'path':'/api/products/JZ3160'})
 def test_nested_product_and_recommendation_identity(self):
  c=load('adidas_official');source='<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'nested':[dict(ADIDAS,id='OTHER'),ADIDAS]}}})+'</script>'
  self.assertEqual(c.next_product(source,'JZ3160'),ADIDAS);self.assertIsNone(c.next_product(source,'KS4159'))
 def test_browser_keeps_full_canonical_link_and_does_not_open_short_url(self):
  c=load('adidas_official');p=Mock();p.url='https://www.adidas.co.kr/samba-og/JZ3160.html';p.content.return_value='<script id="__NEXT_DATA__">'+json.dumps(ADIDAS)+'</script>';context=Mock();context.pages=[p]
  product=c.browser_product(context,'JZ3160');self.assertEqual(product['id'],'JZ3160');p.goto.assert_not_called();context.new_page.assert_not_called()
 def test_search_resolves_actual_matching_article_url(self):
  c=load('adidas_official');page=Mock();page.url='https://www.adidas.co.kr/';page.content.return_value='<script id="__NEXT_DATA__">'+json.dumps(ADIDAS)+'</script>'
  links=['https://www.adidas.co.kr/other/OTHER.html','https://www.adidas.co.kr/samba-og/JZ3160.html','https://evil.com/samba-og/JZ3160.html'];page.locator.return_value.evaluate_all.return_value=links
  def go(url,**kw):page.url=url;return Mock(status=200)
  page.goto.side_effect=go;context=Mock();context.pages=[page]
  result=c.browser_product(context,'JZ3160');self.assertEqual(result['id'],'JZ3160');self.assertEqual(page.goto.call_args_list[-1].args[0],links[1])
 def test_api_path_cannot_access_other_site_or_paths(self):
  c=load('adidas_official')
  for path in ['https://evil.com/api/products/KS4159','/account/profile','/api/products/../../secret']:
   with self.assertRaises(c.CrawlError):c.browser_json(Mock(),path)

 def test_actual_korean_product_fixture_exports_canonical_prices_and_sizes(self):
  from test_oberon_brands import run,ALL
  c=load('adidas_official');product=json.loads((ROOT/'tests/fixtures/adidas_browser/KS4159.json').read_text('utf-8'))
  routes={'https://www.adidas.co.kr/api/products/KS4159':product,'https://www.adidas.co.kr/size-chart/api/size-chart/*':c.CrawlError('HTTP 404')}
  rows,fail,error,_=run(c,'adidas_official','KS4159',ALL,routes)
  self.assertIsNone(error,fail);self.assertEqual(len(rows),1);r=rows[0]
  self.assertEqual(r['상품명'],'코듀로이 푸퍼 재킷');self.assertEqual(r['색상'],'Aurora Coffee');self.assertEqual((r['판매가'],r['정상가']),(259000,259000))
  self.assertEqual(r['사이즈'],'XS,S,M,L,XL,2XL,3XL,4XL');self.assertIn('/%EC%BD',r['상품 링크']);self.assertTrue(r['대표이미지']);self.assertIn('100%폴리에스터',r['상세페이지 HTML'])
 def test_existing_connection_is_reused_without_launching_another(self):
  c=load('adidas_official');info={'ready':True,'port':12345,'token':'test'}
  with patch.object(c,'connection_info',return_value=info),patch.object(c,'connection_call',return_value={'ok':True}),patch.object(c.subprocess,'Popen') as launch,patch.object(c,'connection_folder',return_value=Mock()):
   self.assertIs(c.ensure_connection(Mock()),info)
  launch.assert_not_called()

if __name__=='__main__':unittest.main()
