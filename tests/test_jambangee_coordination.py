import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import jambangee_official as c
from bs4 import BeautifulSoup
class Context:
 def __init__(self):self.rows=[];self.failures=[];self.product_code='AO4DIH710'
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
 def enabled(self,k):return False
 def fail_item(self,m):self.failures.append(m)
 def emit_result(self,r):self.rows.append(r)
class CoordinationTests(unittest.TestCase):
 def test_search_excludes_coordination_set_but_keeps_single_product(self):
  doc=BeautifulSoup('<div class="xans-search-result"><div class="name"><a href="/product/detail.html?product_no=8814">[코디추천] 남성 맨투맨+양모데님</a></div><div class="name"><a href="/product/detail.html?product_no=8129">남성) 오비안밴드 캣워싱 양모데님 AO4DIH710-DB</a></div></div>','html.parser')
  with patch.object(c,'fetch',return_value=(doc,c.SITE+'/product/search.html')):result=c.search(Context(),'AO4DIH710')
  self.assertEqual(set(result),{'8129'})
 def test_set_detail_skipped_even_when_search_title_does_not_mark_bundle(self):
  doc=BeautifulSoup('<div class="infoArea"><h1 class="prdName">남성 맨투맨 데님</h1></div><select option_title="상의" option_product_no="8814"></select><select option_title="하의" option_product_no="8814"></select>','html.parser')
  self.assertIsNone(c.identity(doc,'8814','AO4DIH710',True))
 def test_skipped_set_does_not_fail_successful_single_item(self):
  setdoc=BeautifulSoup('<div class="infoArea"><h1 class="prdName">[코디추천] 남성 맨투맨+양모데님</h1></div>','html.parser')
  single=BeautifulSoup('<div class="infoArea"><h1 class="prdName">남성) 오비안밴드 캣워싱 양모데님 AO4DIH710-DB</h1></div><meta property="product:productId" content="8129"><select option_title="색상" option_product_no="8129"><option value="DB">다크블루</option></select>','html.parser')
  ctx=Context()
  with patch.object(c,'search',return_value={'8814':c.SITE+'/set','8129':c.SITE+'/single'}),patch.object(c,'fetch',side_effect=[(setdoc,c.SITE+'/set'),(single,c.SITE+'/single')]):c.crawl(ctx)
  self.assertEqual(ctx.failures,[]);self.assertEqual(len(ctx.rows),1);self.assertEqual(ctx.rows[0]['color_code'],'DB')
if __name__=='__main__':unittest.main()
