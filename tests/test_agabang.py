import io
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import agabang_official as c


class Context:
    def __init__(self,folder='.',code='01t721012',fields=()):
        self.output_dir=Path(folder);self.product_code=code;self.fields=fields
        self.rows=[];self.failures=[];self.logs=[]
    def enabled(self,key):return key in self.fields
    def checkpoint(self):return None
    def stage(self,message):return None
    def log(self,message,level='INFO'):self.logs.append((level,message))
    def emit_result(self,row):self.rows.append(row)
    def fail_item(self,message):self.failures.append(message)


class AgabangTests(unittest.TestCase):
    def setUp(self):
        self.source=(ROOT/'tests/fixtures/agabang_55628.html').read_text(encoding='utf-8')
        self.doc=c.soup(self.source)

    def test_official_identity_numeric_color_and_brandless_name(self):
        row=c.identity(self.doc,'01T721012')
        self.assertEqual(row['color_code'],'93')
        self.assertEqual(row['color'],'RED')
        self.assertEqual(row['product_name'],'노엘메리후드맨투맨')
        self.assertIsNone(c.identity(self.doc,'01T717601'))
        self.assertEqual(c.identity(c.soup(self.source.replace('01T72101293','01T72101203')),'01T721012')['color_code'],'03')

    def test_search_does_not_fetch_first_page_again_via_pagination(self):
        doc=c.soup('<ul class="prdList"><li><div class="name"><a href="/product/name/55628/">item</a></div></li></ul><div class="xans-search-paging"><a href="?keyword=01T721012&amp;page=1">1</a><a href="?keyword=01T721012&amp;page=2">2</a></div>')
        with patch.object(c,'visit',side_effect=lambda ctx,page,url:(doc,url)) as visit:
            links=c.search(Context(),None,'01T721012')
        self.assertEqual(visit.call_count,2)
        self.assertEqual(len(links),1)

    def test_prefetch_only_selected_images_and_legacy_worker_stays_sequential(self):
        ctx=Context(fields=('main_image','detail_page'))
        calls=[]
        def check(url,**kwargs):
            calls.append(url)
            return {'status':'inaccessible','reasons':['test']}
        with patch.object(sys.modules['__main__'],'EMIT_LOCK',threading.Lock(),create=True), patch.object(c,'check_public_image',side_effect=check):
            cache={};c.prefetch_images(ctx,self.doc,c.SITE,'01T721012','93',cache)
            self.assertEqual(len(cache),2)
            self.assertEqual(len(calls),2)
            self.assertFalse(any('event_bn' in u or '_brand_' in u for u in calls))
            calls.clear()
            c.prefetch_images(Context(),self.doc,c.SITE,'01T721012','93',{})
            self.assertFalse(calls)
        with patch.object(sys.modules['__main__'],'EMIT_LOCK',None,create=True), patch.object(c,'check_public_image',side_effect=AssertionError('legacy background request')):
            c.prefetch_images(ctx,self.doc,c.SITE,'01T721012','93',{})

    def test_stock_includes_every_official_size(self):
        variants=c.matching_stocks(self.doc,'RED')
        self.assertEqual([v['option_value_orginal'][1] for v in variants],['90','100','110','120'])
        self.assertTrue(all(v['option_value_orginal'][0]=='RED' for v in variants))
        self.assertEqual(c.selling_price(Context(),variants),49000)

    def test_optional_work_and_price_combinations(self):
        for fields in ((),('price',),('regular_price',),('price','regular_price')):
            for code in ('01t721012','01T72101293','01T721012_93','01T721012-93'):
                with self.subTest(fields=fields,code=code),tempfile.TemporaryDirectory() as folder:
                    ctx=Context(folder,code,fields)
                    with patch('playwright.sync_api.sync_playwright',return_value=MagicMock()), \
                         patch.object(c,'search',return_value=['https://agabangmall.com/product/detail.html?product_no=55628']), \
                         patch.object(c,'visit',return_value=(self.doc,'https://agabangmall.com/product/detail.html?product_no=55628')), \
                         patch.object(c,'gallery',side_effect=AssertionError('unselected gallery')), \
                         patch.object(c,'detail',side_effect=AssertionError('unselected detail')), \
                         patch.object(c,'matching_stocks',wraps=c.matching_stocks) as variants:
                        c.crawl(ctx)
                    self.assertFalse(ctx.failures)
                    self.assertEqual(len(ctx.rows),1)
                    self.assertEqual('price' in ctx.rows[0],'price' in fields)
                    self.assertEqual('regular_price' in ctx.rows[0],'regular_price' in fields)
                    self.assertEqual(variants.call_count,1 if 'price' in fields else 0)
                    self.assertTrue(ctx.rows[0]['product_url'].startswith('https://'))

    def test_regular_price_missing_zero_bad_format_and_failure_are_optional(self):
        ctx=Context()
        for text in ('0원','잘못된 가격',''):
            doc=c.soup(self.source)
            row=next(r for r in doc.select('.infoArea tr') if r.find('th').get_text(' ',strip=True)=='소비자가')
            row.find('td').clear();row.find('td').append(text)
            self.assertIsNone(c.regular_price(ctx,doc))
        with patch.object(c,'field',side_effect=OSError('connection failed')):
            self.assertIsNone(c.regular_price(ctx,self.doc))
        self.assertFalse(ctx.failures)
        self.assertIsNone(c.selling_price(ctx,[{'option_price':100},{'option_price':200}]))

    def test_detail_preserves_product_image_and_excludes_common_banners(self):
        urls=c.detail_urls(self.doc,c.SITE,'01T721012','93')
        self.assertEqual(urls,['https://item.nextmom.co.kr/magaline_img/goods/agabang/2026/01T72101293.jpg'])
        with patch.object(c,'public_image',return_value={'status':'accessible'}):
            markup=c.detail(Context(),self.doc,c.SITE,'01T721012','93',{})
        self.assertIn('01T72101293.jpg',markup)
        self.assertNotIn('<table',markup)
        self.assertNotIn('event_bn',markup)
        with patch.object(c,'public_image',return_value=None):
            with self.assertRaises(ValueError):c.detail(Context(),self.doc,c.SITE,'01T721012','93',{})

    def test_single_gallery_image_pads_slots_but_small_or_broken_files_do_not(self):
        for size,valid in (((600,600),True),((100,100),False)):
            stream=io.BytesIO();Image.new('RGB',size,'white').save(stream,format='PNG')
            result={'status':'accessible','data':stream.getvalue(),'width':size[0],'height':size[1],'format':'PNG'}
            with patch.object(c,'public_image',return_value=result):
                main,extra,_=c.gallery(Context(),self.doc,c.SITE,{})
            self.assertEqual(bool(main),valid)
            self.assertEqual(len(extra),5 if valid else 0)


if __name__=='__main__':unittest.main()
