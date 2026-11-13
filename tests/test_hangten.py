import sys, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import hangten_official as h
class Context:
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
class DetailTests(unittest.TestCase):
 def test_actual_empty_center_layout_preserves_all_four_body_images_and_size(self):
  soup=h.BeautifulSoup((ROOT/'tests/fixtures/hangten/14523_detail.html').read_text('utf-8'),'html.parser')
  with patch.object(h,'public_image',side_effect=lambda ctx,url,cache:dict(status='accessible',data=url.encode())) as loader:
   result=h.detail_page(Context(),soup,h.ORIGIN,'14590-110-803-41','003')
  self.assertEqual(result.count('<img'),5)
  self.assertIn('_02_01.jpg',result);self.assertIn('_02_02.jpg',result);self.assertIn('_02_03.jpg',result);self.assertIn('_03.jpg',result);self.assertIn('TEENS_SIZE.jpg',result)
  self.assertNotIn('intro_teens',result);self.assertNotIn('/info.jpg',result)
  self.assertEqual(loader.call_count,5)
 def test_nested_legacy_layout_excludes_other_models_and_outside_detail(self):
  valid='https://blskorea.hgodo.com/Product/HTN/26FW/detail/14590-110-803-41-003_03.jpg'
  wrong=valid.replace('-003_','-044_')
  soup=h.BeautifulSoup('<img src="'+valid+'"><div id="prdDetail"><div class="cont"><center><div><img src="'+valid+'"><img src="'+wrong+'"></div></center></div></div>','html.parser')
  with patch.object(h,'public_image',return_value=dict(status='accessible',data=b'photo')) as loader:
   result=h.detail_page(Context(),soup,h.ORIGIN,'14590-110-803-41','003')
  self.assertEqual(result.count('<img'),1);self.assertEqual(loader.call_count,1);self.assertNotIn('-044_',result)
if __name__=='__main__':unittest.main()
