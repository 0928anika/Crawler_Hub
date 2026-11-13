import copy
import json
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import clovis_eland_official as c
FIX=ROOT/'tests/fixtures/clovis'
LINK=c.SITE+'/i/item?itemNo=2609584409&lowerVendNo=LV16003596'
def source(name='product'):return json.loads((FIX/(name+'.json')).read_text(encoding='utf-8'))
class Context:
    product_code='CVJHG4V06M'
    def __init__(self,fields=()):self.fields=fields;self.rows=[];self.logs=[];self.errors=[]
    def checkpoint(self):pass
    def enabled(self,key):return key in self.fields
    def stage(self,*a):pass
    def log(self,*a):self.logs.append(a)
    def fail_item(self,message):self.errors.append(message)
    def emit_unmapped(self,record,colors,url,shared_detail_html=''):self.rows.append((record,colors,url,shared_detail_html))
def document(data):return c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=data))+';</script>','html.parser')
def options(ctx,main,parent=None):
    if parent is None:return source('colors')
    return [row for row in source('gray' if str(parent)=='26094770709' else 'navy') if row['lowerVendNo']==c.VENDOR_NO]

class ClovisTests(unittest.TestCase):
    def test_search_filters_other_vendor_and_accepts_both_official_hosts(self):
        doc=c.BeautifulSoup('<em data-searchresult-total>2</em><a class="ui-item__link" href="https://clovis.elandmall.co.kr/i/item?itemNo=2609584409&amp;lowerVendNo=LV16003596"></a><a class="ui-item__link" href="/i/item?itemNo=2&amp;lowerVendNo=OTHER"></a>','html.parser')
        with patch.object(c,'page',return_value=doc):self.assertEqual(c.search(Context(),'CVJHG4V06M'),[LINK])

    def test_detail_ids_rechecked_and_brand_model_mismatch_excluded(self):
        for field,value in [('brandNo','OTHER'),('modelNo','WRONG')]:
            d=source();d['itemMain'][field]=value
            with patch.object(c,'page',return_value=document(d)):
                self.assertIsNone(c.product(Context(),LINK,'CVJHG4V06M'))
        d=source();d['itemMain']['lowerVendNo']='OTHER'
        with patch.object(c,'page',return_value=document(d)):
            with self.assertRaises(c.CollectionError):c.product(Context(),LINK,'CVJHG4V06M')

    def test_original_gif_detail_is_preserved_but_not_gallery_candidate(self):
        raw=io.BytesIO();c.Image.new('RGB',(600,800),'white').save(raw,format='GIF')
        url='https://item.elandrs.com/body.gif'
        with patch.object(c,'fetch',return_value=raw.getvalue()):
            self.assertEqual(c.image_info(Context(),url,{},detail=True)[0],'GIF')
            with self.assertRaises(c.ImageRejected):c.image_info(Context(),url,{})

    def test_unicode_image_urls_preserve_existing_escapes(self):
        url='https://item.elandrs.com/r/클라비스/상품%20사진.jpg?size=1000'
        encoded=c.image_url(url)
        self.assertTrue(encoded.isascii())
        self.assertIn('%20',encoded);self.assertNotIn('%2520',encoded)
        d=source();d['itemDesc']['itemDescdocumentCon']='<img src="'+url+'">'
        with patch.object(c,'image_info',return_value=('JPEG',1080,2200,'hash',False)) as loader:
            self.assertEqual(c.detail_images(Context(),d,'CODE',{}),[encoded])
        self.assertEqual(loader.call_args.args[1],encoded)

    def test_transient_missing_product_json_is_retried(self):
        with patch.object(c,'page',side_effect=[c.BeautifulSoup('<html></html>','html.parser'),document(source())]) as page:
            self.assertEqual(c.product(Context(),LINK,'CVJHG4V06M')['itemMain']['brandNo'],c.BRAND_NO)
        self.assertEqual(page.call_count,2)

    def test_mixed_gallery_filters_seller_photos_and_marks_detail_unsafe(self):
        main=source()['itemMain'];main['itemImage']=[dict(imageDcode=kind,imagePath=name+'.jpg') for kind,name in [('010','studio'),('030','seller'),('030','studio2')]]
        def inspect(ctx,url,cache):return ('JPEG',1000,1000,url,'seller' not in url)
        with patch.object(c,'image_info',side_effect=inspect):photos=c.gallery(Context(),main,{})
        self.assertEqual(len(photos),2);self.assertFalse(photos.detail_safe)
        self.assertFalse(any('seller' in url for url,_ in photos))

    def test_mixed_first_link_uses_second_clean_link_detail_only(self):
        for clean_second in [True,False]:
            ctx=Context(['main_image','detail_page']);first=c.GalleryPhotos();first.append(('photo1','hash1'));first.detail_safe=False
            second=c.GalleryPhotos();second.append(('photo2','hash2'));second.detail_safe=clean_second
            links=[LINK,LINK+'&test=second']
            with patch.object(c,'studio_product',return_value=True),patch.object(c,'search',return_value=links),patch.object(c,'product',return_value=source()),patch.object(c,'options',side_effect=options),patch.object(c,'gallery',side_effect=[first,second]),patch.object(c,'detail_images',return_value=['clean-body']) as detail:
                c.crawl(ctx)
            self.assertFalse(ctx.errors);self.assertEqual(len(ctx.rows),2)
            self.assertEqual(detail.call_count,1 if clean_second else 0)
            for row in ctx.rows:
                self.assertEqual(row[3].count('<img'),1 if clean_second else 0)
                self.assertEqual(row[0]['product_url'],links[1] if clean_second else links[0])

    def test_actual_two_digit_color_codes(self):
        colors,actual=c.colors(Context(),source('colors'))
        self.assertEqual(colors,[dict(color_name='Gray',color_code='15'),dict(color_name='Navy',color_code='59')])
        rows=source('colors');rows[0]['itemOptionName']='(03)Cream';rows[0]['soldoutYn']='Y'
        colors,actual=c.colors(Context(),rows)
        self.assertEqual(colors[0],dict(color_name='Cream',color_code='03'));self.assertEqual(actual[0]['soldoutYn'],'Y')

    def test_api_rows_from_other_vendor_are_excluded_not_mixed(self):
        rows=source('navy');ctx=Context()
        payload=json.dumps(dict(resultCode='200',data=dict(itemOption=rows))).encode()
        with patch.object(c,'fetch',return_value=payload):actual=c.options(ctx,source()['itemMain'],'26094770710')
        self.assertEqual([row['itemOptionName'] for row in actual],['M','L'])
        self.assertTrue(all(row['lowerVendNo']==c.VENDOR_NO for row in actual));self.assertTrue(ctx.logs)

    def test_all_original_body_base_and_gosi_images_kept(self):
        d=source();ctx=Context()
        with patch.object(c,'image_info',return_value=('JPEG',1080,12000,'digest',False)) as loader:
            urls=c.detail_images(ctx,d,'CVJHG4V06M',{})
        self.assertEqual(len(urls),3)
        self.assertTrue(urls[0].endswith('CVJHG4V06M_detail.jpg'))
        self.assertIn('-BASE-',urls[1]);self.assertIn('-GOSI-',urls[2])
        self.assertEqual([call.args[1] for call in loader.call_args_list],urls)
        self.assertTrue(all(call.kwargs==dict(detail=True) for call in loader.call_args_list))

    def test_coupon_excluded_but_size_image_with_unrelated_name_kept(self):
        d=source();coupon='https://item.elandrs.com/r/image/item/coupon.jpg';size='https://item-imghub.eland.co.kr/detail-pages/shared-size.jpg'
        d['itemDesc']['itemDescdocumentCon']+='<img src="'+coupon+'"><img data-src="'+size+'">'
        d['itemDesc']['itemAnnounceList']=[dict(itemAnnounceCon='<img src="'+coupon+'">')]
        with patch.object(c,'image_info',return_value=('JPEG',1080,2200,'digest',False)) as loader:
            urls=c.detail_images(Context(),d,'CVJHG4V06M',{})
        self.assertEqual(len(urls),4);self.assertIn(size,urls);self.assertNotIn(coupon,urls)
        self.assertFalse(any(call.args[1]==coupon for call in loader.call_args_list))

    def test_body_error_does_not_silently_omit_gosi(self):
        with patch.object(c,'image_info',side_effect=c.CollectionError('timeout')):
            with self.assertRaises(c.CollectionError):c.detail_images(Context(),source(),'CVJHG4V06M',{})

    def test_price_combinations_and_unselected_detail(self):
        for selling,normal in [(True,False),(False,True),(True,True),(False,False)]:
            ctx=Context(['main_image']+[key for key,yes in [('price',selling),('regular_price',normal)] if yes])
            with patch.object(c,'studio_product',return_value=True),patch.object(c,'search',return_value=[LINK]),patch.object(c,'product',return_value=source()),patch.object(c,'options',side_effect=options),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/r/image/item/photo.jpg','hash')]),patch.object(c,'detail_images') as detail:
                c.crawl(ctx)
            self.assertFalse(ctx.errors);row=ctx.rows[0][0];detail.assert_not_called()
            self.assertEqual(row.get('price'),199000 if selling else None)
            self.assertEqual(row.get('regular_price'),199000 if normal else None)
            self.assertEqual('price' in row,selling);self.assertEqual('regular_price' in row,normal)

    def test_invalid_normal_price_is_nonfatal(self):
        for value in [None,0,'문의']:
            main=source()['itemMain'];main['orgSellprice']=value;ctx=Context(['regular_price']);record={}
            c.optional_values(ctx,main,[],record)
            self.assertIsNone(record['regular_price']);self.assertTrue(ctx.logs)
        with patch.object(c,'money',side_effect=RuntimeError('parse error')):
            ctx=Context(['regular_price']);record={};c.optional_values(ctx,source()['itemMain'],[],record)
        self.assertIsNone(record['regular_price']);self.assertFalse(ctx.errors)

    def test_detail_output_contains_only_original_images_no_generated_tables(self):
        ctx=Context(['main_image','detail_page'])
        with patch.object(c,'studio_product',return_value=True),patch.object(c,'search',return_value=[LINK]),patch.object(c,'product',return_value=source()),patch.object(c,'options',side_effect=options),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/r/image/item/photo.jpg','hash')]),patch.object(c,'image_info',return_value=('JPEG',1080,2200,'digest',False)):
            c.crawl(ctx)
        markup=ctx.rows[0][3];self.assertEqual(markup.count('<img'),3);self.assertNotIn('<table',markup)
        self.assertEqual([row['color_code'] for row in ctx.rows[0][1]],['15','59'])

if __name__=='__main__':unittest.main()
