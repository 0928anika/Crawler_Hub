import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/x) for x in ('app','brands')]
import ziozia_official as c
class Context:
 def checkpoint(self):pass
 def stage(self,*a):pass
 def log(self,*a):pass
class Tests(unittest.TestCase):
 def setUp(self):self.data=json.loads((Path(__file__).resolve().parent/'fixtures/ziozia/AAH1JP1102DKH.json').read_text(encoding='utf-8'))
 def test_separate_brand_exact_code_and_actual_sizes(self):
  with patch.object(c,'fetch',return_value=json.dumps(self.data).encode()):g=c.load_product(Context(),'AAH1JP1102DKH','AAH1JP1102')
  self.assertEqual(c.BRAND,'ABR');self.assertEqual([x['itmNm'] for x in g['godItmExList']],['95','100','105','110'])
  wrong=copy.deepcopy(self.data);wrong['goods']['sysBrnd']['brndId']='EBR'
  with patch.object(c,'fetch',return_value=json.dumps(wrong).encode()):self.assertIsNone(c.load_product(Context(),'AAH1JP1102DKH','AAH1JP1102'))
 def test_actual_size_chart_and_notice_preserved(self):
  g=self.data['goods']
  with patch.object(c,'picture',side_effect=lambda ctx,url,cache,**kw:{'digest':url}),patch.object(c,'gallery_assets',return_value=[{'url':'https://img.goodwearmall.com/product.jpg','digest':'product','detail_role':True}]):
   body=c.detail_markup(Context(),[g],[],{})
  self.assertIn('실측사이즈',body);self.assertIn('상품정보제공고시',body);self.assertIn('제품 주소재',body);self.assertIn('가슴둘레',body)
 def test_notice_is_one_table_with_all_colors_and_common_values_once(self):
  first=self.data['goods'];second=copy.deepcopy(first);second['god']['colorCd']='BE'
  for row in second['godDetailList']:
   if row['godDetailTpCd']=='AFF_NTFC_DETAIL_HTML':row['godDetailCont']=row['godDetailCont'].replace('DKH(다크 카키)','BE(베이지)')
  markup=c.combined_notice([first,second]);doc=c.BeautifulSoup(markup,'html.parser')
  self.assertEqual(len(doc.select('table')),1)
  self.assertEqual(markup.count('제품 주소재'),1)
  self.assertEqual(next(row.find('td').get_text() for row in doc.select('tr') if row.find('th').get_text()=='색상'),'DKH(다크 카키), BE(베이지)')
 def test_large_original_is_read_without_bypassing_gallery_validation(self):
  import io
  from PIL import Image
  stream=io.BytesIO();Image.new('RGB',(600,900),'white').save(stream,'JPEG')
  raw=stream.getvalue()+b'\0'*11000000
  with patch.object(c,'fetch',return_value=raw) as fetch,patch.object(c,'validate_gallery_image',return_value={'passed':False,'reasons':['파일 크기 초과']}) as validate:
   info=c.picture(Context(),'https://img.goodwearmall.com/large.jpg',{},gallery=True)
  self.assertEqual(fetch.call_args.args[2],64000000)
  self.assertFalse(info['validation']['passed']);validate.assert_called_once()
 def test_product_name_excludes_brand_and_code(self):
  self.assertEqual(c.clean_name('지오지아 ZIOZIA 남성 점퍼 AAH1JP1102DKH','AAH1JP1102','AAH1JP1102DKH'),'남성 점퍼')
if __name__=='__main__':unittest.main()
