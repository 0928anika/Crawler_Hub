import sys, unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/x) for x in ('app','brands')]
import naver_smartstore_integrated as c
from storage import parse_input

class Tests(unittest.TestCase):
 def test_store_and_identifiers(self):
  self.assertEqual(c.store_slug('https://smartstore.naver.com/ncsongpa269/products/13885830324'),'ncsongpa269')
  for value in ['https://evil.com/shop','https://smartstore.naver.com.evil.com/shop','https://user@smartstore.naver.com/shop']:
   with self.assertRaises(ValueError):c.store_slug(value)
  self.assertEqual(c.input_identifier('AB-123'),'AB-123')
 def test_store_once_applies_to_every_input(self):
  heads,rows=parse_input({'codes':'13885830324\nMODEL123','store_url':'https://smartstore.naver.com/ncsongpa269'},{'store_input':True})
  self.assertEqual(heads,['품번','스토어 주소'])
  self.assertTrue(all(r[1]=='https://smartstore.naver.com/ncsongpa269' for r in rows))
  with self.assertRaisesRegex(ValueError,'스토어 주소'):parse_input({'codes':'13885830324'},{'store_input':True})
 def test_notice_omits_masked_contact_shipping_but_keeps_material(self):
  markup=c.notice_table(Mock(),{'productInfoProvidedNoticeView':{'basic':{'제품소재':'면 100%','제조국':'***','A/S 책임자와 전화번호':'02-123-4567','배송안내':'택배 발송','색상':'블랙'}}})
  self.assertIn('면 100%',markup);self.assertIn('블랙',markup)
  for word in ['제조국','책임자','배송','02-123']:self.assertNotIn(word,markup)
 def test_detail_uses_declared_lazy_original_and_filters_shipping(self):
  import io
  from PIL import Image
  buf=io.BytesIO();Image.new('RGB',(600,800),'white').save(buf,'JPEG')
  source='<div class="se-main-container"><div class="se-image"><a data-linkdata=\'{"src":"https://shop-phinf.pstatic.net/product.jpg"}\'></a><img src="data:image/png;base64,AAA"></div><img src="https://shop-phinf.pstatic.net/notice.jpg"></div>'
  cache={}
  def public(ctx,url,values,detail=False):
   result={'data':buf.getvalue(),'status':'accessible'};values[(url,detail)]=result;return result
  with patch.object(c,'public_image',side_effect=public),patch.object(c,'ocr_many',return_value=['상품 사진','배송 안내 교환 반품']):
   markup=c.detail_markup(Mock(),source,{},cache)
  self.assertIn('product.jpg',markup);self.assertNotIn('notice.jpg',markup);self.assertNotIn('data:image',markup)
 def test_display_price_is_preserved_without_combination_prices(self):
  self.assertEqual(c.selected_price(Mock(),{'salePrice':139000,'benefitsView':{'discountedSalePrice':54210}},[]),54210)
 def test_information_merges_concrete_values_without_duplicate_labels(self):
  product={'productBasicInfo':{'모델명':'JP4SHU131','색상':'레드, 브라운','제품 소재':'면 100%','상품번호':'13885830324'},'productInfoProvidedNoticeView':{'basic':{'색상':'상품상세참조','제품소재':'상품상세참조','제조국':'국산','A/S':'***'}}}
  result=c.merged_information(product)
  self.assertEqual(result['색상'],'레드, 브라운');self.assertEqual(result['제품 소재'],'면 100%')
  markup=c.notice_table(Mock(),product)
  self.assertEqual(markup.count('<table'),1);self.assertEqual(markup.count('<th>색상</th>'),1)
  self.assertIn('JP4SHU131',markup);self.assertIn('국산',markup);self.assertNotIn('***',markup)
 def test_basic_information_reads_all_header_value_pairs(self):
  page=Mock();page.get_by_role.return_value.count.return_value=0;page.content.return_value='<table><tr><th>상품번호</th><td>13885830324</td><th>상품상태</th><td>새상품</td></tr><tr><th>배송비</th><td>2500원</td></tr></table>'
  result=c.read_basic_information(page,{'naverShoppingSearchInfo':{'modelName':'JP4SHU131'}})
  self.assertEqual(result,{'상품번호':'13885830324','상품상태':'새상품','모델명':'JP4SHU131','배송비':'2500원'})
 def test_image_download_is_reused_across_detail_and_gallery(self):
  cache={};result={'status':'accessible','data':b'image'}
  with patch.object(c,'check_public_image',return_value=result) as check:
   c.public_image(Mock(),'https://shop-phinf.pstatic.net/product.jpg',cache,True)
   c.public_image(Mock(),'https://shop-phinf.pstatic.net/product.jpg',cache,False)
  self.assertEqual(check.call_count,1)
 def test_real_product_information_list_is_collected(self):
  source=(Path(__file__).resolve().parent/'fixtures/naver_smartstore_integrated/basic_information.html').read_text(encoding='utf-8')
  result=c.parse_basic_information(source)
  self.assertEqual(result['제조일자'],'2026.10.31.')
  self.assertEqual(result['주요소재'],'면, 폴리에스테르')
  self.assertEqual(result['핏'],'루즈핏/오버핏')
  self.assertEqual(result['상품번호'],'13884123546')
 def test_semantic_duplicate_uses_concrete_value_from_either_source(self):
  for basic,notice in [({'제조일자':'2026.10.31.','주요소재':'면, 폴리에스테르'},{'제조연월':'상품 상세페이지 참조','제품소재':'상세 참조'}),({'제조일자':'상세페이지 참조','주요소재':'상세 정보 확인'},{'제조연월':'2026.10.31.','제품소재':'면, 폴리에스테르'})]:
   result=c.merged_information({'productBasicInfo':basic,'productInfoProvidedNoticeView':{'basic':notice}})
   self.assertEqual(result,{'제조일자':'2026.10.31.','주요소재':'면, 폴리에스테르'})
 def test_naver_product_number_is_omitted_from_information_table(self):
  markup=c.notice_table(Mock(),{'productBasicInfo':{'상품번호':'13885830324','모델명':'JP4SHU131'}})
  self.assertNotIn('상품번호',markup);self.assertNotIn('13885830324',markup)
  self.assertIn('JP4SHU131',markup)
 def test_model_matching_is_exact(self):
  self.assertTrue(c.exact_model({'naverShoppingSearchInfo':{'modelName':'MODEL123'}},'model123'))
  self.assertTrue(c.exact_model({'name':'블라우스 MODEL123 블랙'},'MODEL123'))
  self.assertFalse(c.exact_model({'name':'블라우스 MODEL1234'},'MODEL123'))
 def test_numeric_input_does_not_search(self):
  browser=Mock()
  self.assertEqual(c.resolve_numbers(Mock(),browser,'13885830324'),['13885830324'])
  browser.assert_not_called()
 def test_live_result_uses_model_and_keeps_input_number(self):
  ctx=Mock(product_code='13885830324',item={'스토어 주소':'https://smartstore.naver.com/ncsongpa269'})
  ctx.enabled.side_effect=lambda k:False
  product={'id':'13885830324','name':'상품 MODEL123','channel':{'url':'ncsongpa269'},'naverShoppingSearchInfo':{'modelName':'MODEL123'},'productInfoProvidedNoticeView':{'basic':{'색상':'블랙'}}}
  with patch.object(c,'broker_collect',return_value=[{'product':product,'source':''}]):c.crawl(ctx)
  row=ctx.emit_result.call_args.args[0]
  self.assertEqual(row['product_code'],'MODEL123');self.assertEqual(row['상품번호'],'13885830324')
 def test_other_store_response_is_rejected(self):
  ctx=Mock(product_code='13885830324',item={'스토어 주소':'https://smartstore.naver.com/ncsongpa269'})
  with patch.object(c,'broker_collect',return_value=[{'product':{'id':'13885830324','channel':{'url':'other'}}}]):
   with self.assertRaisesRegex(ValueError,'스토어'):c.crawl(ctx)

if __name__=='__main__':unittest.main()
