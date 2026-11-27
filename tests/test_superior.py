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
 def test_base_and_full_sku_match_exactly_and_clean_name(self):
  for n,color,code in [('9202','T/블루','47'),('9203','D/네이비','48')]:
   d=c.soup(self.fixture(n));p=c.identity(d,'IMGGBPS202');self.assertEqual((p['name'],p['color_name'],p['color_code']),('남성 모던 컴포트 팬츠',color,code));self.assertIsNone(c.identity(d,'IMGGBPS20'));self.assertIsNone(c.identity(d,'IMGGBPS20299'))
 def test_other_brand_cannot_enter_same_search(self):
  d=c.soup(self.fixture('9203'))
  for row in d.select('.infoArea tr'):
   if row.find('th') and row.find('th').get_text(strip=True)=='브랜드':row.find('td').string='다른브랜드'
  self.assertIsNone(c.identity(d,'IMGGBPS202'))
 def test_notice_and_title_sku_must_agree(self):
  d=c.soup(self.fixture('9203'))
  for row in c.product_notice(d)[0].select('tr'):
   cells=row.find_all(['th','td'],recursive=False)
   if len(cells)==2 and cells[0].get_text(strip=True)=='색상':cells[1].string='D/네이비.OTHER999'
  with self.assertRaisesRegex(c.CrawlError,'불일치'):c.identity(d,'IMGGBPS202')
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
 def run_sdk(self,code,selected):
  meta=json.loads((ROOT/'brands/superior_official.json').read_text('utf-8'));events=[]
  with tempfile.TemporaryDirectory() as f:
   ctx=CrawlContext('unused',Path(f)/'out.xlsx',0,selected,meta['output_headers'],lambda:None,lambda k,**d:events.append((k,d)),input_item={'품번':code})
   links=[c.SITE+'/product/detail.html?product_no='+n for n in ['9202','9203']]
   with patch.object(c,'search',return_value=links),patch.object(c,'fetch',side_effect=lambda ctx,url:self.fixture(c.product_number(url))),patch.object(c,'gallery',side_effect=AssertionError('Unselected image fetch')),patch.object(c,'detail',side_effect=AssertionError('Unselected detail fetch')):c.crawl(ctx);ctx.finish()
  return [dict(zip(meta['output_headers'],d['row'])) for k,d in events if k=='row' and not d.get('header')]
 def test_prices_are_independent_and_sizes_keep_leading_zero(self):
  rows=self.run_sdk('IMGGBPS202',['price','regular_price','size']);self.assertEqual(len(rows),2)
  for r in rows:self.assertEqual(r['판매가'],49000);self.assertEqual(r['정상가'],218000);self.assertEqual(r['사이즈'],'082,085,088,092,096')
 def test_full_sku_restricts_to_its_color_even_if_search_returns_siblings(self):
  rows=self.run_sdk('IMGGBPS20248',['size']);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['색상코드'],'48')
if __name__=='__main__':unittest.main()
