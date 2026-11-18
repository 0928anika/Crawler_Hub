import copy,io,json,sys,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import izmelbon_eland_studio as studio
MODULES=[studio]

class Context:
    product_code='SUJF26130'
    def __init__(self):self.rows=[];self.errors=[]
    def enabled(self,k):return k in ('main_image','detail_page','size')
    def checkpoint(self):pass
    def stage(self,*args):pass
    def log(self,*args):pass
    def fail_item(self,error):self.errors.append(error)
    def emit_unmapped(self,record,colors,url,**kw):self.rows.append((record,colors,url,kw))

def data(vendor,index=1):
    return dict(itemMain=dict(itemNo=str(index),lowerVendNo=vendor,brandNo='1700005506',modelNo='SUJF26130',itemName='이즈멜본 자켓 SUJF26130',itemTcode='10'),itemDesc={})

class Tests(unittest.TestCase):
    def test_single_unified_registration_and_official_priority(self):
        from registry import get_brand
        from brand_catalog import definitions
        registered=[b for b in definitions(ROOT/'brands') if b['id'].startswith('izmelbon_eland')]
        self.assertEqual(sorted(b['id'] for b in registered),['izmelbon_eland_ncys','izmelbon_eland_studio'])
        brand=get_brand('izmelbon_eland_studio');self.assertTrue(brand['available']);self.assertEqual(brand['crawl_source'],'이랜드몰 스튜디오컷');self.assertEqual(studio.VENDOR,'CN003590')
        for vendors,expected in [(['CN003590','LV25019267','LV25019303'],1),(['LV25019267','LV25019303'],2)]:
            doc=studio.BeautifulSoup('<span data-searchresult-total>'+str(len(vendors))+'</span>'+''.join('<a class="ui-item__link" href="/i/item?itemNo='+str(i)+'&lowerVendNo='+v+'">x</a>' for i,v in enumerate(vendors)),'html.parser')
            with patch.object(studio,'page',return_value=(doc,studio.SITE)):links=studio.search(Context(),'SUJF26130')
            self.assertEqual(len(links),expected)
            if 'CN003590' in vendors:self.assertIn('CN003590',links[0])

    def test_wrong_seller_brand_and_model_are_rejected(self):
        for module in MODULES:
            product=data(module.VENDOR);url=module.SITE+'/i/item?itemNo=1&lowerVendNo='+module.VENDOR
            def doc():return module.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=product))+';</script>','html.parser')
            with patch.object(module,'page',side_effect=lambda *args:(doc(),url)):
                self.assertIsNotNone(module.product_once(Context(),url,'SUJF26130'))
                self.assertIsNone(module.product_once(Context(),url,'OTHER'))
                product['itemMain']['brandNo']='OTHER'
                self.assertIsNone(module.product_once(Context(),url,'SUJF26130'))

    def test_studio_collects_every_product_but_uses_one_detail(self):
        ctx=Context();products=[data('LV25019303',1),data('LV25019267',2)]
        urls=[studio.SITE+'/i/item?itemNo='+str(i+1)+'&lowerVendNo='+p['itemMain']['lowerVendNo'] for i,p in enumerate(products)]
        def options(ctx,main,selected=None):
            if selected is None:return [dict(itemOptionNo='1',itemOptionName='19 BLACK',itemOptionTcode='10')]
            return [dict(itemOptionName='090',itemOptionTcode='20')]
        with patch.object(studio,'search',return_value=urls),patch.object(studio,'product',side_effect=list(zip(products,urls))) as get,patch.object(studio,'studio_product',return_value=True) as check,patch.object(studio,'options',side_effect=options),patch.object(studio,'gallery',side_effect=[[('https://item.elandrs.com/one.jpg','a')],[('https://item.elandrs.com/two.jpg','b')]]),patch.object(studio,'detail',return_value=[('photo','<img src="https://item.elandrs.com/detail-one.jpg">')]) as detail:
            studio.crawl(ctx)
        self.assertEqual(get.call_count,2);self.assertEqual(check.call_count,2);self.assertEqual(detail.call_count,1)
        self.assertEqual(len(ctx.rows),2);self.assertFalse(ctx.errors)
        self.assertTrue(all(row[1][0]['size']=='090' for row in ctx.rows))
        self.assertTrue(all(row[3]['shared_detail_html'].count('<img')==1 for row in ctx.rows))

    def test_official_allows_nonstudio_photo_fallback_requires_studio(self):
        for vendor,accepted in [('CN003590',True),('LV25019267',False)]:
            main=data(vendor)['itemMain'];main['itemImage']=[dict(imageDcode='150',imagePath='photo.jpg')]
            with patch.object(studio,'image_info',return_value=('JPEG',800,1000,False,'hash','',False)):
                self.assertEqual(bool(studio.gallery(Context(),main,{})),accepted)

    def test_official_stops_after_one_product_and_skips_studio_test(self):
        ctx=Context();products=[data('CN003590',i) for i in (1,2)];urls=[studio.SITE+'/i/item?itemNo='+str(i)+'&lowerVendNo=CN003590' for i in (1,2)]
        def options(ctx,main,selected=None):
            return [dict(itemOptionNo='1',itemOptionName='19 BLACK',itemOptionTcode='10')] if selected is None else [dict(itemOptionName='090',itemOptionTcode='20')]
        with patch.object(studio,'search',return_value=urls),patch.object(studio,'product',side_effect=list(zip(products,urls))) as get,patch.object(studio,'studio_product') as check,patch.object(studio,'options',side_effect=options),patch.object(studio,'gallery',return_value=[('https://item.elandrs.com/one.jpg','a')]),patch.object(studio,'detail',return_value=[('photo','<img src="https://item.elandrs.com/detail-one.jpg">')]) as detail:
            studio.crawl(ctx)
        self.assertEqual(get.call_count,1);check.assert_not_called();self.assertEqual(detail.call_count,1);self.assertEqual(len(ctx.rows),1);self.assertFalse(ctx.errors)

    def test_seller_shipping_and_review_banners_are_not_requested(self):
        for module in MODULES:
            d=data(module.VENDOR);d['itemDesc']={'itemDescdocumentCon':'<img src="https://item.elandrs.com/shipping.jpg"><img src="https://item.elandrs.com/판매자안내.jpg"><img src="https://item.elandrs.com/review.jpg">'}
            with patch.object(module,'fetch') as fetch:
                self.assertEqual(module.detail(Context(),d,'SUJF26130',{}),[]);fetch.assert_not_called()

    def test_opaque_uuid_shipping_banner_is_rejected_by_ocr(self):
        buf=io.BytesIO();Image.new('RGB',(600,1000),'white').save(buf,format='JPEG')
        for module in MODULES:
            d=data(module.VENDOR);d['itemDesc']['itemDescdocumentCon']='<img src="https://item.elandrs.com/uuid.jpg">'
            with patch.object(module,'fetch',return_value=(buf.getvalue(),'','image/jpeg')),patch.object(module,'ocr_many',return_value=['판매자 안내 배송 교환 반품 문의']):
                self.assertEqual(module.detail(Context(),d,'SUJF26130',{}),[])

    def test_original_size_and_body_images_are_kept_without_generated_tables(self):
        for module in MODULES:
            d=data(module.VENDOR);d['itemDesc']['itemDescdocumentCon']='<img src="https://img-studio.elandmall.com/Winstudio/SU-BANNER.jpg"><img src="https://img-studio.elandmall.com/Winstudio/SUJF26130.jpg"><img src="https://img-studio.elandmall.com/Winstudio/WO-SIZE.jpg">'
            with patch.object(module,'image_info',return_value=True),patch.object(module,'fetch',side_effect=AssertionError('banner requested')):
                parts=module.detail(Context(),d,'SUJF26130',{})
            html=''.join(markup for _,markup in parts);self.assertEqual(html.count('<img'),2)
            self.assertNotIn('<table',html);self.assertNotIn('BANNER',html)

    def test_restored_ncys_is_separate_and_only_uses_ncys_vendor(self):
        import izmelbon_eland_ncys as ncys
        from registry import get_brand
        self.assertTrue(get_brand('izmelbon_eland_ncys')['available']);self.assertEqual(ncys.VENDOR,'LV25019267')
        vendors=['CN003590','LV25019267','LV25019303']
        doc=ncys.BeautifulSoup('<span data-searchresult-total>3</span>'+''.join('<a class="ui-item__link" href="/i/item?itemNo='+str(i)+'&lowerVendNo='+v+'">x</a>' for i,v in enumerate(vendors)),'html.parser')
        with patch.object(ncys,'page',return_value=(doc,ncys.SITE)):links=ncys.search(Context(),'SUJF26130')
        self.assertEqual(len(links),1);self.assertIn('LV25019267',links[0])
        main=data(ncys.VENDOR)['itemMain'];main['itemImage']=[dict(imageDcode='150',imagePath='photo.jpg')]
        with patch.object(ncys,'image_info',return_value=('JPEG',800,1000,False,'hash','',False)):self.assertTrue(ncys.gallery(Context(),main,{}))

if __name__=='__main__':unittest.main()
