import copy
import html
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import ncbasic_eland_official as c
FIX=ROOT/'tests/fixtures/ncbasic'
LINK=c.SITE+'/i/item?itemNo=2411568415&lowerVendNo=LV24013164'


class Context:
    def __init__(self,fields=(),code='DCJD24A02'):
        self.fields,self.product_code=fields,code
        self.rows,self.errors,self.logs=[],[],[]
    def checkpoint(self):return None
    def enabled(self,key):return key in self.fields
    def log(self,*args):self.logs.append(args)
    def stage(self,message):return None
    def fail_item(self,message):self.errors.append(message)
    def emit_unmapped(self,record,colors,url,shared_detail_html=''):
        self.rows.append(dict(record=record,colors=colors,url=url,detail=shared_detail_html))


def source(code='DCJD24A02'):
    return json.loads((FIX/(code+'.json')).read_text(encoding='utf-8'))


def options(ctx,main,parent=None):
    code=main['modelNo']
    name=code+'_colors.json' if parent is None else code+'_'+str(parent)+'.json'
    return json.loads((FIX/name).read_text(encoding='utf-8'))


def document(data):
    return c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps({'itemDetail':data})+';</script>','html.parser')


class NCBasicTests(unittest.TestCase):
    def test_denied_body_uses_verified_shots_and_original_size(self):
        ctx=Context();data=source('NCJM26T05')
        shots=['https://item.elandrs.com/r/image/item/shot1.jpg','https://item.elandrs.com/r/image/item/shot2.jpg']
        def load(ctx,url,cache,detail=False):
            if url.endswith('/NCJM26T05.jpg'):raise c.AccessDenied('HTTP 403')
            return ('JPEG',780,1857,'size',False)
        with patch.object(c,'image_info',side_effect=load):
            urls=c.detail_images(ctx,data,'NCJM26T05',{},fallback_images=shots)
        self.assertEqual(urls,shots+['https://img-studio.elandmall.com/Winstudio/UI-SIZE.jpg'])
        self.assertTrue(any('상품컷 전체' in log[0] for log in ctx.logs))

    def test_transient_detail_failure_does_not_trigger_fallback(self):
        with patch.object(c,'image_info',side_effect=c.CollectionError('TLS timeout')):
            with self.assertRaises(c.CollectionError):
                c.detail_images(Context(),source('NCJM26T05'),'NCJM26T05',{},fallback_images=['https://item.elandrs.com/shot.jpg'])

    def test_official_shared_size_filename_is_preserved(self):
        ctx=Context();data=source('NCCK26T02')
        with patch.object(c,'image_info',return_value={}) as image:
            urls=c.detail_images(ctx,data,'NCCK26T02',{})
        self.assertEqual(urls,[
            'https://img-studio.elandmall.com/Winstudio/NCCK26T02.jpg',
            'https://img-studio.elandmall.com/Winstudio/UI-SIZE.jpg'])
        self.assertEqual([call.args[1] for call in image.call_args_list],urls)
        self.assertFalse(any('DN-BANNER' in call.args[1] for call in image.call_args_list))

    def test_official_body_filename_need_not_contain_product_code(self):
        data=source('NCCK26T02')
        data['itemDesc']['itemDescdocumentCon']='<img src="https://img-studio.elandmall.com/Winstudio/new_product_body.jpg"><img src="https://img-studio.elandmall.com/Winstudio/UI-SIZE.jpg">'
        with patch.object(c,'image_info',return_value={}):
            urls=c.detail_images(Context(),data,'NCCK26T02',{})
        self.assertEqual(len(urls),2)

    def test_search_counts_all_results_but_returns_only_requested_vendor(self):
        doc=c.BeautifulSoup('<b data-searchresult-total>2</b><a class="ui-item__link" href="/i/item?itemNo=2411568415&amp;lowerVendNo=LV24013164"></a><a class="ui-item__link" href="/i/item?itemNo=2610597760&amp;lowerVendNo=LV26024017"></a>','html.parser')
        with patch.object(c,'page',return_value=doc) as page:
            self.assertEqual(c.search(Context(),'DCJD24A02'),[LINK])
            page.assert_called_once()

    def test_other_vendor_url_is_not_even_requested(self):
        with patch.object(c,'page') as page:
            self.assertIsNone(c.product(Context(),LINK.replace('LV24013164','LV26024017'),'DCJD24A02'))
            page.assert_not_called()

    def test_response_vendor_and_model_are_verified_without_brand_title_requirement(self):
        data=source();data['itemMain']['itemName']='베이직 푸퍼 다운'
        with patch.object(c,'page',return_value=document(data)):
            self.assertIsNotNone(c.product(Context(),LINK,'DCJD24A02'))
            self.assertIsNone(c.product(Context(),LINK,'DCVW24A01'))
        data['itemMain']['lowerVendNo']='LV26024017'
        with patch.object(c,'page',return_value=document(data)):
            self.assertIsNone(c.product(Context(),LINK,'DCJD24A02'))

    def test_real_soldout_color_is_retained_and_aa_placeholder_is_removed(self):
        main=source('DCVW24A01')['itemMain']
        palette,actual=c.colors(Context(),options(Context(),main))
        self.assertEqual([v['color_code'] for v in palette],['09','26','34'])
        self.assertEqual(actual[-1]['soldoutYn'],'Y')

    def test_options_keep_leading_zeros_and_soldout_sizes(self):
        data=source();ctx=Context(('size','price','regular_price'))
        palette,actual=c.colors(ctx,options(ctx,data['itemMain']));record={}
        with patch.object(c,'options',side_effect=options):
            c.optional_values(ctx,data['itemMain'],actual,record)
        self.assertEqual(record,dict(size='00L,00M,00S,0XL',price=39900,regular_price=49900))

    def test_independent_prices_and_missing_regular_price_do_not_fail(self):
        for fields in ((),('regular_price',),('price',),('price','regular_price')):
            with self.subTest(fields=fields):
                data=source();ctx=Context(fields);actual=options(ctx,data['itemMain'])[:2];record={}
                with patch.object(c,'options',side_effect=options) as children:
                    c.optional_values(ctx,data['itemMain'],actual,record)
                    if 'price' not in fields:children.assert_not_called()
                for key in ('price','regular_price'):self.assertEqual(key in record,key in fields)
        for value in (None,0,'잘못된 가격'):
            main=source()['itemMain'];main['orgSellprice']=value;ctx=Context(('regular_price',));record={}
            c.optional_values(ctx,main,[],record)
            self.assertIsNone(record['regular_price']);self.assertFalse(ctx.errors)

    def test_detail_uses_body_only_and_never_requests_coupon_or_commented_images(self):
        ctx=Context();data=source()
        coupon='https://item.elandrs.com/r/image/item/2026-10-28/ee135a0c-a63c-4d04-8b25-3efb9164e524.jpg'
        with patch.object(c,'image_info',return_value=('JPEG',1080,4000,'hash',False)) as info:
            urls=c.detail_images(ctx,data,'DCJD24A02',{})
        self.assertEqual(len(urls),3)
        self.assertNotIn(coupon,urls)
        self.assertEqual(info.call_count,3)
        self.assertTrue(all(call.kwargs=={'detail':True} for call in info.call_args_list))
        data['itemDesc']['itemDescdocumentCon']+=html.escape('<div><img src="'+coupon+'"></div>')
        with patch.object(c,'image_info',return_value=('JPEG',1080,4000,'hash',False)) as info:
            self.assertEqual(c.detail_images(ctx,data,'DCJD24A02',{}),urls)
        self.assertTrue(all(call.args[1]!=coupon for call in info.call_args_list))

    def test_missing_detail_is_allowed_and_no_tables_are_synthesized(self):
        self.assertEqual(c.detail_images(Context(),dict(itemDesc={}), 'DCJD24A02',{}),[])
        ctx=Context(('main_image','detail_page'))
        with patch.object(c,'search',return_value=[LINK]),patch.object(c,'product',return_value=source()),patch.object(c,'options',side_effect=options),patch.object(c,'gallery',return_value=[('https://item.elandrs.com/photo.jpg','digest')]),patch.object(c,'detail_images',return_value=['https://item.elandrs.com/body.jpg']):
            c.crawl(ctx)
        self.assertEqual(len(ctx.rows),1)
        self.assertIn('<img',ctx.rows[0]['detail']);self.assertNotIn('<table',ctx.rows[0]['detail'])
        self.assertEqual(ctx.rows[0]['record']['판매자번호'],'LV24013164')
        self.assertEqual(ctx.rows[0]['record']['원본 상품명'],source()['itemMain']['itemName'])

    def test_unselected_optional_work_is_not_called(self):
        ctx=Context(('main_image',))
        with patch.object(c,'search',return_value=[LINK]),patch.object(c,'product',return_value=source()),patch.object(c,'options',side_effect=options) as api,patch.object(c,'gallery',return_value=[('https://item.elandrs.com/photo.jpg','digest')]),patch.object(c,'detail_images') as detail:
            c.crawl(ctx)
            self.assertEqual(api.call_count,1);detail.assert_not_called()
        self.assertFalse(any(k in ctx.rows[0]['record'] for k in ('price','regular_price','size','category','detail_page')))

    def test_name_removes_only_explicit_brand_and_model_tokens(self):
        self.assertEqual(c.clean_name('NC베이직 [덕다운 8:2] 베이직 푸퍼 다운 DCJD24A02','DCJD24A02'),'[덕다운 8:2] 베이직 푸퍼 다운')


if __name__=='__main__':unittest.main()
