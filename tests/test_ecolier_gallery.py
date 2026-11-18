import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import ecolier_official as c
class EcolierGalleryTests(unittest.TestCase):
 def page(self,markup,number='1000010293'):return dict(doc=c.soup(markup),number=number,url=c.SITE+'/goods/goods_view.php?goodsNo='+number,info=[])
 def test_actual_gallery_keeps_additional_color_photos_beside_single_zoom(self):
  page=self.page((ROOT/'tests/fixtures/ecolier/1000010293_gallery.html').read_text('utf-8'));urls=c.gallery(page)
  self.assertEqual(len(urls),3);self.assertIn('magnify_049.jpg',urls[0]);self.assertIn('detail_175.jpg',urls[1]);self.assertIn('detail_266.jpg',urls[2]);self.assertFalse(any('/thumb/' in u for u in urls));self.assertFalse(any('detail_020.jpg' in u for u in urls))
 def test_dom_only_fallback_combines_zoom_and_all_additional_kinds(self):
  prefix='https://cdn.soiemall.co.kr/goods/1001/image/'
  page=self.page(''.join('<img src="'+prefix+u+'">' for u in ['magnify/main.jpg','add1/one.jpg','add2/two.jpg','list/thumb.jpg']),'1001')
  self.assertEqual(c.gallery(page),[prefix+'magnify/main.jpg',prefix+'add1/one.jpg',prefix+'add2/two.jpg'])
 def test_color_codes_are_only_taken_from_explicit_option_evidence(self):
  p=self.page('<select name="optionNo_0"><option value="">컬러 선택</option><option value="BR">브라운</option><option value="아이보리" data-color-code="IV">아이보리</option><option value="블랙(BK)">블랙(BK)</option><option value="퍼플">퍼플</option></select>')
  colors=c.colors_of(p);self.assertEqual([(x['color_name'],x['color_code']) for x in colors],[('브라운','BR'),('아이보리','IV'),('블랙','BK'),('퍼플','')])
if __name__=='__main__':unittest.main()
