import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import jeepkids_debecmall as c
FIX=ROOT/'tests/fixtures/jeepkids_debecmall'
def fixture(name): return json.loads((FIX/(name+'.json')).read_text('utf-8'))['result']
class Context:
    product_code='kr3tpu247'
    def __init__(self,fields=()): self.fields=fields;self.rows=[];self.raw=[];self.errors=[]
    def enabled(self,key): return key in self.fields
    def checkpoint(self): pass
    def stage(self,*a): pass
    def log(self,*a): pass
    def fail_item(self,e): self.errors.append(e)
    def emit_result(self,r): self.rows.append(r)
    def emit_unmapped(self,r,p,u,**kw): self.raw.append((r,p,u))
class Tests(unittest.TestCase):
    def test_identity_and_missing_are_distinct(self):
        ctx=Context()
        with patch.object(c,'fetch',return_value=[]):
            with self.assertRaisesRegex(ValueError,'검색 결과 없음'): c.search(ctx,'KR3TPU247')
        wrong=dict(fixture('search')[0],VENDOR_PRODUCT_CODE='KR3TPU248')
        with patch.object(c,'fetch',return_value=[wrong]):
            with self.assertRaisesRegex(ValueError,'품번 불일치'): c.search(ctx,'KR3TPU247')
    def test_numeric_registration_suffix_is_same_model(self):
        rows=fixture('KQ4THU055_search')
        with patch.object(c,'fetch',return_value=rows):
            self.assertEqual(len(c.search(Context(),'KQ4THU055')),3)
        for value in ['KQ4THU055','kq4thu055-1','KQ4THU055-12']:
            self.assertTrue(c.matches_product(dict(BRAND_NAME='지프키즈',VENDOR_PRODUCT_CODE=value),'KQ4THU055'))
        for value in ['KQ4THU0550','KQ4THU055A','KQ4THU055-1SET','OTHER-KQ4THU055','KQ4THU056-1']:
            self.assertFalse(c.matches_product(dict(BRAND_NAME='지프키즈',VENDOR_PRODUCT_CODE=value),'KQ4THU055'))
        self.assertFalse(c.matches_product(dict(BRAND_NAME='다른브랜드',VENDOR_PRODUCT_CODE='KQ4THU055-1'),'KQ4THU055'))
        self.assertEqual(c.name_of(dict(PRODUCT_NAME='[지프키즈] 후드티(KQ4THU055-2)'),'KQ4THU055'),'후드티')

    def test_all_three_color_pages_collected(self):
        ctx=Context(('size','price'));ctx.product_code='KQ4THU055'
        def fetch(ctx,path,**params):
            if path.endswith('prdDetail'): return fixture('KQ4THU055_'+params['productCode']+'_product')
            if path.endswith('listSkuInfo'):
                for code in ['822262079638','822262080442','822262145173']:
                    if fixture('KQ4THU055_'+code+'_product')[0]['PRODUCT_ID']==params['productId']:
                        return fixture('KQ4THU055_'+code+'_options')
            raise AssertionError((path,params))
        with patch.object(c,'fetch',side_effect=fetch),patch.object(c,'search',return_value=fixture('KQ4THU055_search')):
            c.crawl(ctx)
        self.assertEqual({r['color']:r['size'] for r in ctx.rows},{'블랙':'145,165','그린':'130,145,155','멜란지베이지':'130,145,155'})
        self.assertEqual({r['color']:r['price'] for r in ctx.rows},{'블랙':68000,'그린':68000,'멜란지베이지':59500})
        self.assertFalse(ctx.errors)

    def test_product_body_only_and_name(self):
        attrs=fixture('attributes')+[dict(ATTR_NAME='공통배너',ATTR_VALUE='<img src="/files/DETAIL/pickup.jpg">')]
        urls=c.body_urls(attrs)
        self.assertEqual(len(urls),2);self.assertIn('190809914',urls[1])
        self.assertFalse(c.image_url('/layout/card.jpg','DETAIL'))
        self.assertEqual(c.name_of(dict(PRODUCT_NAME='[대구백화점][지프키즈] 반바지(KR3TPU247)'),'KR3TPU247'),'반바지')

    def test_official_double_slash_editor_path_is_preserved(self):
        attrs=[dict(ATTR_NAME='상세설명',ATTR_VALUE='<img src="http://www.debecmall.co.kr/files//DETAIL/925/photo.jpg">')]
        self.assertEqual(c.body_urls(attrs),['https://www.debecmall.co.kr/files//DETAIL/925/photo.jpg'])
        self.assertFalse(c.image_url('https://evil.example/files//DETAIL/photo.jpg','DETAIL'))
    def test_options_do_not_invent_color_code(self):
        self.assertEqual(c.palette(fixture('options')),[dict(color_name='블랙',color_code='',size='130,145,155')])
        mixed=fixture('options')+[dict(SKU_NAME='블랙 165'),dict(SKU_NAME='베이지 145')]
        self.assertEqual(c.palette(mixed)[1]['size'],'145')
    def run_crawl(self,fields,options=None):
        ctx=Context(fields);calls=[]
        def fetch(ctx,path,**params):
            calls.append((path,params))
            if path.endswith('prdDetail'): return fixture('product')
            if path.endswith('listSkuInfo'): return options or fixture('options')
            if path.endswith('prdProductImage'): return fixture('images')
            if params.get('sqlId')=='product.prd_attr': return fixture('attributes')
            raise AssertionError((path,params))
        with patch.object(c,'search',return_value=fixture('search')),patch.object(c,'fetch',side_effect=fetch),patch.object(c,'checked',side_effect=lambda ctx,u,*a:u): c.crawl(ctx)
        return ctx,calls
    def test_optional_fields_independent(self):
        for fields in [(),('price',),('regular_price',),('price','regular_price')]:
            ctx,calls=self.run_crawl(fields);row=ctx.rows[0]
            self.assertEqual('price' in row,'price' in fields)
            self.assertEqual('regular_price' in row,'regular_price' in fields)
            self.assertEqual(len(calls),2)
            if 'price' in fields:self.assertEqual(row['price'],69000)
            if 'regular_price' in fields:self.assertIsNone(row['regular_price'])
    def test_single_color_and_multi_color_sizes(self):
        ctx,calls=self.run_crawl(('size','main_image','additional_images','detail_page'))
        row=ctx.rows[0];self.assertEqual(row['size'],'130,145,155');self.assertEqual([row['main_image']]*5,row['additional_images'].split(','));self.assertEqual(row['detail_page'].count('<img'),2);self.assertFalse(ctx.raw)
        options=fixture('options')+[dict(SKU_NAME='베이지 165')]
        ctx,calls=self.run_crawl(('size','main_image'),options)
        self.assertFalse(ctx.rows);self.assertEqual(ctx.raw[0][1][1]['size'],'165')

    def test_vendor_color_suffix_and_set_components_match_exactly(self):
        for code,vendor in [('KL4PTU792','KL4PTU792(D.B)'),('KP3TSU802','KP3TSU802 DO'),
                            ('KP3TSU802','KP3TSU802VI'),('KO1THU040','KO1THU040/KO1TPU940'),
                            ('KO3TPF165','KO3TSF165/KO3TPF165')]:
            with self.subTest(vendor=vendor):
                self.assertTrue(c.matches_product(dict(BRAND_NAME='지프키즈',VENDOR_PRODUCT_CODE=vendor),code))
        for vendor in ['XKL4PTU792(D.B)','KL4PTU7920','KL4PTU792SET','KL4PTU793(D.B)','KL4PTU792/WRONG']:
            self.assertFalse(c.matches_product(dict(BRAND_NAME='지프키즈',VENDOR_PRODUCT_CODE=vendor),'KL4PTU792'))

    def test_detail_skips_deleted_file_but_keeps_valid_original(self):
        with patch.object(c,'checked',side_effect=[c.MissingImage('HTTP 404'),'https://www.debecmall.co.kr/files/DETAIL/good.jpg']):
            html=c.detail_html(Context(),['deleted','good'],{})
        self.assertEqual(html.count('<img'),1)
        self.assertIn('/good.jpg',html)
        with patch.object(c,'checked',side_effect=c.MissingImage('HTTP 410')):
            with self.assertRaisesRegex(ValueError,'모두 404/410'):c.detail_html(Context(),['deleted'],{})
        with patch.object(c,'checked',side_effect=ValueError('HTTP 403')):
            with self.assertRaisesRegex(ValueError,'403'):c.detail_html(Context(),['denied'],{})

    def test_checked_distinguishes_deleted_file_and_transient_failure(self):
        with patch.object(c,'check_public_image',return_value=dict(status='inaccessible',reasons=['공개 GET 거부: HTTP 404'])):
            with self.assertRaises(c.MissingImage):c.checked(Context(),'https://www.debecmall.co.kr/files/DETAIL/missing.jpg',{},True)
        with patch.object(c,'check_public_image',return_value=dict(status='unknown',reasons=['timed out'])):
            with self.assertRaises(ValueError) as exc:c.checked(Context(),'https://www.debecmall.co.kr/files/DETAIL/timeout.jpg',{},True)
            self.assertNotIsInstance(exc.exception,c.MissingImage)

    def test_api_read_timeout_retries_then_returns_actual_result(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"result": []}'
        opener=MagicMock();opener.open.side_effect=[TimeoutError('read timed out'),response]
        with patch.object(c,'build_opener',return_value=opener),patch.object(c.time,'sleep'):
            self.assertEqual(c.fetch(Context(),'/api/product/searchProductPage',q='KL4PTU792'),[])
        self.assertEqual(opener.open.call_count,2)

    def test_additional_images_always_have_five_slots(self):
        for count in [1,2,3,5,6,9]:
            photos=['https://www.debecmall.co.kr/files/PRODUCT/'+str(i)+'.jpg' for i in range(count)]
            result=c.extra_slots(photos)
            self.assertEqual(len(result),5)
            self.assertTrue(all(url in photos for url in result))
            self.assertEqual(result[:min(5,count-1)],photos[1:6])
if __name__=='__main__': unittest.main()
