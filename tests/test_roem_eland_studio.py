import copy,io,json,sys,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import roem_eland_studio as c

class Context:
    product_code='RMKAG4TRTN'
    def __init__(self,fields=()):self.fields=fields;self.rows=[];self.failures=[]
    def enabled(self,key):return key in self.fields
    def checkpoint(self):pass
    def stage(self,*args):pass
    def log(self,*args):pass
    def fail_item(self,message):self.failures.append(message)
    def emit_unmapped(self,record,colors,url,**kwargs):self.rows.append((record,colors,url))

def product():
    return dict(itemMain=dict(itemNo='2609561242',lowerVendNo=c.VENDOR,brandNo=c.BRAND_NO,
                             modelNo='RMKAG4TRTN',itemName='V넥 풀오버 RMKAG4TRTN',orgSellprice=59900),itemDesc={})

class Tests(unittest.TestCase):
    def test_unicode_image_url_is_encoded_without_double_encoding(self):
        url='https://item-imghub.eland.co.kr/brands/'+c.IMGHUB_BRAND+'/product/RMCKG49A11_상세_800.jpg?x=%EC%83%81'
        response=MagicMock();response.read.return_value=b'public image';response.geturl.return_value=c.quote(url,safe=":/?&=%+#,;@!$*()~[]'");response.headers.get.return_value='image/jpeg'
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch.object(c,'build_opener',return_value=opener):
            self.assertEqual(c.fetch(Context(),url)[0],b'public image')
        requested=opener.open.call_args.args[0].full_url
        requested.encode('ascii');self.assertIn('%EC%83%81%EC%84%B8',requested);self.assertNotIn('%25EC',requested)

    def test_korean_product_detail_filename_remains_official_original(self):
        url='https://item-imghub.eland.co.kr/brands/'+c.IMGHUB_BRAND+'/product/RMCKG49A11_RMTJG49A11_상세_800.jpg'
        self.assertTrue(c.original_detail(url,'RMCKG49A11'))
        self.assertTrue(c.original_detail(c.quote(url,safe=':/'),'RMCKG49A11'))
        self.assertFalse(c.original_detail(url,'DIFFERENT'))

    def test_unexpected_failure_has_actionable_error_event(self):
        ctx=Context()
        with patch.object(c,'_crawl',side_effect=ValueError('specific failure')):
            with self.assertRaises(ValueError):c.crawl(ctx)
        self.assertIn('ValueError: specific failure',ctx.failures[0])

    def test_registration_and_brand(self):
        from registry import get_brand
        brand=get_brand('roem_eland_studio')
        self.assertTrue(brand['available']);self.assertEqual(c.VENDOR,'LV16003577')
        self.assertEqual(c.BRAND_NO,'1690000158');self.assertEqual(brand['mapping_mode'],'manual')

    def test_official_seller_is_preferred(self):
        doc=c.BeautifulSoup('<span data-searchresult-total>2</span><a class="ui-item__link" href="/i/item?itemNo=1&lowerVendNo=OTHER">x</a><a class="ui-item__link" href="/i/item?itemNo=2&lowerVendNo='+c.VENDOR+'">y</a>','html.parser')
        with patch.object(c,'page',return_value=(doc,c.SITE)):
            self.assertEqual(c.search(Context(),'RMKAG4TRTN'),[c.SITE+'/i/item?itemNo=2&lowerVendNo='+c.VENDOR])

    def test_parenthesized_official_color_code(self):
        self.assertEqual(c.color_rows([dict(itemOptionTcode='10',itemOptionName='(16)Light Gray')]),[dict(color_name='Light Gray',color_code='16')])

    def test_official_page_option_pool_keeps_item_identity(self):
        main=product()['itemMain'];main['itemTcode']='10'
        row=dict(itemNo=main['itemNo'],lowerVendNo='LV16003632',itemOptionName='(16)Light Gray',itemOptionTcode='10')
        def result():return json.dumps(dict(resultCode='200',data=dict(itemOption=[row]))).encode(),c.SITE+'/v1/item/option/api','application/json'
        with patch.object(c,'fetch',side_effect=lambda *args:result()):
            self.assertEqual(c.options(Context(),main)[0]['lowerVendNo'],'LV16003632')
            row['itemNo']='DIFFERENT'
            with self.assertRaises(c.CrawlError):c.options(Context(),main)

    def test_exact_model_brand_and_seller_are_checked(self):
        data=product();url=c.SITE+'/i/item?itemNo=2609561242&lowerVendNo='+c.VENDOR
        def doc():return c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=data))+';</script>','html.parser')
        with patch.object(c,'page',side_effect=lambda *args:(doc(),url)):
            self.assertIsNotNone(c.product_once(Context(),url,'RMKAG4TRTN'))
            self.assertIsNone(c.product_once(Context(),url,'RMKAG4TRTM'))
            data['itemMain']['brandNo']='1690000157'
            self.assertIsNone(c.product_once(Context(),url,'RMKAG4TRTN'))

    def test_original_gosi_and_size_images_remain_banners_not_requested(self):
        base='https://item-imghub.eland.co.kr/detail-pages/'+c.IMGHUB_BRAND+'/product/'
        sources=[base+'detail-BASE-a.jpg',base+'detail-GOSI-b.jpg','https://item.elandrs.com/shipping.jpg','https://item.elandrs.com/review.jpg']
        data=product();data['itemDesc']['itemDescdocumentCon']=''.join('<img src="'+url+'">' for url in sources)
        with patch.object(c,'image_info',return_value=True),patch.object(c,'fetch',side_effect=AssertionError('banner requested')):
            parts=c.detail(Context(),data,'RMKAG4TRTN',{})
        self.assertEqual(len(parts),2)
        self.assertIn('GOSI',parts[1][1]);self.assertNotIn('<table',''.join(part[1] for part in parts))
        self.assertTrue(c.original_detail('https://img-studio.elandmall.com/Winstudio/RM-SIZE.jpg','RMKAG4TRTN'))
        self.assertFalse(c.original_detail(base.replace(c.IMGHUB_BRAND,'OTHER')+'detail-GOSI-b.jpg','RMKAG4TRTN'))

    def test_uuid_seller_banner_is_rejected_by_content(self):
        data=product();data['itemDesc']['itemDescdocumentCon']='<img src="https://item.elandrs.com/random-uuid.jpg">'
        buf=io.BytesIO();Image.new('RGB',(600,1200),'white').save(buf,format='JPEG')
        with patch.object(c,'fetch',return_value=(buf.getvalue(),'','image/jpeg')),patch.object(c,'ocr_many',return_value=['로엠 매장 안내 배송 교환 반품 고객센터']):
            self.assertEqual(c.detail(Context(),data,'RMKAG4TRTN',{}),[])

    def test_different_color_sizes_are_preserved_in_mapping_candidates(self):
        ctx=Context(('size','price','main_image'));data=product();url=c.SITE+'/i/item?itemNo=2609561242&lowerVendNo='+c.VENDOR
        def options(ctx,main,selected=None):
            if selected is None:return [dict(itemOptionNo='1',itemOptionTcode='10',itemOptionName='16 LIGHT GRAY'),dict(itemOptionNo='2',itemOptionTcode='10',itemOptionName='59 NAVY')]
            return [dict(itemOptionTcode='20',itemOptionName=size,sellPrice=39900) for size in (['090','095'] if selected=='1' else ['100','105'])]
        with patch.object(c,'search',return_value=[url]),patch.object(c,'product',return_value=(data,url)),patch.object(c,'options',side_effect=options),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/product.jpg','hash')]):
            c.crawl(ctx)
        self.assertFalse(ctx.failures);self.assertEqual(len(ctx.rows),1)
        self.assertEqual([color['size'] for color in ctx.rows[0][1]],['090,095','100,105'])

if __name__=='__main__':unittest.main()
