import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import superior_official as c
from crawler_sdk import CrawlContext
class Context:
 def checkpoint(self):pass
 def log(self,*a):pass
 def stage(self,*a):pass
class Tests(unittest.TestCase):
 def fixture(self,n):return (ROOT/('tests/fixtures/superior/'+n+'.html')).read_text('utf-8')
 def test_model_metadata_cleans_name_without_filtering_search_result(self):
  for n,color,code in [('9202','T/블루','47'),('9203','D/네이비','48')]:
   d=c.soup(self.fixture(n));p=c.identity(d,'IMGGBPS202');self.assertEqual((p['name'],p['color_name'],p['color_code']),('남성 모던 컴포트 팬츠',color,code));self.assertIsNotNone(c.identity(d,'IMGGBPS20'));self.assertIsNotNone(c.identity(d,'IMGGBPS20299'))
 def test_official_store_brand_label_does_not_override_exact_sku(self):
  d=c.soup(self.fixture('9203'))
  for row in d.select('.infoArea tr'):
   if row.find('th') and row.find('th').get_text(strip=True)=='브랜드':row.find('td').string='다른브랜드'
  self.assertIsNotNone(c.identity(d,'IMGGBPS202'));self.assertIsNotNone(c.identity(d,'OTHER999'))
 def test_notice_disagreement_is_metadata_warning_not_product_rejection(self):
  d=c.soup(self.fixture('9203'))
  for row in c.product_notice(d)[0].select('tr'):
   cells=row.find_all(['th','td'],recursive=False)
   if len(cells)==2 and cells[0].get_text(strip=True)=='색상':cells[1].string='D/네이비.OTHER999'
  info=c.identity(d,'IMGGBPS202');self.assertEqual(info['sku'],'IMGGBPS20248');self.assertTrue(info['warnings'])
 def test_body_has_only_matching_product_images_and_exact_size_guide(self):
  d=c.soup(self.fixture('9203'));p=c.identity(d,'IMGGBPS202');area=d.select_one('#prdDetail');area.append(d.new_tag('img',src='https://spholdings.diskn.com/SGF/Detail/IMGGBPS20247_page1.jpg'))
  urls=c.body_urls(d,c.SITE,p);self.assertEqual(len(urls),6);self.assertTrue(any('SZIF_IMGGBPS202.jpg' in u for u in urls));self.assertFalse(any('IP_INFO' in u or 'delivery' in u or 'brand_top' in u or '20247_' in u for u in urls))
  with patch.object(c,'checked'):html=c.detail(Context(),d,c.SITE,p,{},[])
  self.assertIn('나일론',html);self.assertIn('제품 소재',html);self.assertNotIn('IP_delivery_info',html)
 def test_gallery_adds_individual_product_cuts_but_not_long_body_or_size_chart(self):
  d=c.soup(self.fixture('9203'));urls=c.gallery_urls(d,c.SITE,c.identity(d,'IMGGBPS202'))
  self.assertEqual(len(urls),4);self.assertTrue('/web/product/big/' in urls[0]);self.assertEqual(sum('/P/' in u for u in urls),3);self.assertFalse(any('page1' in u or 'SZIF' in u for u in urls))
 def test_pagination_does_not_follow_disabled_fragment_links(self):
  with patch.object(c,'fetch',return_value=self.fixture('search')) as fetch:links=c.search(Context(),'IMGGBPS202')
  self.assertEqual(len(links),2);self.assertEqual(fetch.call_count,1)
 def test_actual_zero_search_without_counter_unit_is_not_an_access_error(self):
  with patch.object(c,'fetch',return_value=self.fixture('search-zero')):self.assertEqual(c.search(Context(),'IM0GBPD401'),[])
 def test_empty_hint_alone_does_not_hide_access_or_structure_errors(self):
  for source in ['<html>Access denied</html>','<p class="noData">검색결과가 없습니다</p>','<div class="searchResult">상품 검색 결과 10</div><p class="noData">검색결과가 없습니다</p>']:
   with self.subTest(source=source),patch.object(c,'fetch',return_value=source):
    with self.assertRaisesRegex(c.CrawlError,'검색 0건 아님'):c.search(Context(),'IM0GBPD401')
 def test_real_zero_has_clear_crawl_failure(self):
  class C(Context):
   product_code='IM0GBPD401'
   def fail_item(self,message):self.message=message
  ctx=C()
  with patch.object(c,'fetch',return_value=self.fixture('search-zero')):
   with self.assertRaisesRegex(c.CrawlError,'슈페리어 공식 검색 0건'):c.crawl(ctx)
  self.assertIn('검색 0건',ctx.message)
 def run_sdk(self,code,selected,numbers=('9202','9203')):
  meta=json.loads((ROOT/'brands/superior_official.json').read_text('utf-8'));events=[]
  with tempfile.TemporaryDirectory() as f:
   ctx=CrawlContext('unused',Path(f)/'out.xlsx',0,selected,meta['output_headers'],lambda:None,lambda k,**d:events.append((k,d)),input_item={'품번':code})
   links=[c.SITE+'/product/detail.html?product_no='+n for n in numbers]
   with patch.object(c,'search',return_value=links),patch.object(c,'fetch',side_effect=lambda ctx,url:self.fixture(c.product_number(url))),patch.object(c,'gallery',side_effect=AssertionError('Unselected image fetch')),patch.object(c,'detail',side_effect=AssertionError('Unselected detail fetch')):c.crawl(ctx);ctx.finish()
  return [dict(zip(meta['output_headers'],d['row'])) for k,d in events if k=='row' and not d.get('header')]
 def test_numeric_prefix_and_other_official_brand_preserve_metadata(self):
  rows=self.run_sdk('QW0FTTE301',['size','price','regular_price'],numbers=('7504','7503'))
  self.assertEqual(len(rows),2);self.assertEqual([r['색상코드'] for r in rows],['36','42']);self.assertEqual([r['색상'] for r in rows],['M/GRAY','S/BLUE']);self.assertTrue(all(r['상품명']=='Symbol Hooded Sweatshirts' and r['사이즈']=='S,M,L' for r in rows))
  rows=self.run_sdk('QW0FTTE30136',['size'],numbers=('7504','7503'));self.assertEqual(len(rows),2);self.assertEqual(rows[0]['색상코드'],'36')
  self.assertIsNotNone(c.identity(c.soup(self.fixture('7504')),'QW0FTTE30'))
 def test_martine_body_keeps_own_size_chart_without_common_brand_or_influencer_images(self):
  d=c.soup(self.fixture('7504'));info=c.identity(d,'QW0FTTE301');urls=c.body_urls(d,c.SITE,info)
  self.assertEqual(len(urls),5);self.assertTrue(any(u.endswith('_SIZE.jpg') for u in urls));self.assertTrue(all('QW0FTTE30136_' in u for u in urls));self.assertFalse(any('influencer' in u or 'martine_top' in u or 'delivery' in u for u in urls))
 def test_shorter_family_is_collected_without_length_rule(self):
  rows=self.run_sdk('QW0EVT301',['size'],numbers=('5294','5293'))
  self.assertEqual(len(rows),2);self.assertEqual([r['색상코드'] for r in rows],['49','60']);self.assertEqual([r['색상'] for r in rows],['NAVY','CAMEL']);self.assertTrue(all(r['상품명']=='Y Neck Knit Vest' for r in rows))
 def test_missing_code_or_color_in_metadata_is_not_a_product_gate(self):
  info=c.identity(c.soup('<div class="infoArea"><h1>공식 상품</h1></div>'),'SEARCH123')
  self.assertEqual(info['name'],'공식 상품');self.assertEqual(info['base'],'SEARCH123');self.assertEqual(info['color_name'],'')
 def test_prices_are_independent_and_sizes_keep_leading_zero(self):
  rows=self.run_sdk('IMGGBPS202',['price','regular_price','size']);self.assertEqual(len(rows),2)
  for r in rows:self.assertEqual(r['판매가'],49000);self.assertEqual(r['정상가'],218000);self.assertEqual(r['사이즈'],'082,085,088,092,096')
 def test_official_search_results_are_not_filtered_again_by_input_sku(self):
  rows=self.run_sdk('IMGGBPS20248',['size']);self.assertEqual(len(rows),2);self.assertEqual(rows[1]['색상코드'],'48')
if __name__=='__main__':unittest.main()
