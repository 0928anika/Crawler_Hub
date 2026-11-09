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

    def test_shared_color_identity_does_not_invent_numeric_mapping(self):
        for number,base,names,page_code in [('55675','01T752007',['O/WHITE','BLACK'],''),('55638','01T752004',['PINK','CREAM'],'58')]:
            doc=c.soup((ROOT/('tests/fixtures/agabang_'+number+'.html')).read_text(encoding='utf-8'))
            records=c.identities(doc,base)
            self.assertEqual([r['color'] for r in records],names)
            self.assertTrue(all(r['_shared_gallery'] and r['color_code']=='' and r['_page_code']==page_code for r in records))
            self.assertEqual(len(c.detail_urls(doc,c.SITE,base,'')),2)
            self.assertEqual(c.identities(doc,'01T721012'),[])

    def test_shared_photos_emit_both_colors_with_all_six_slots_identical(self):
        doc=c.soup((ROOT/'tests/fixtures/agabang_55638.html').read_text(encoding='utf-8'))
        stream=io.BytesIO();Image.new('RGB',(600,600),'white').save(stream,format='PNG')
        image={'status':'accessible','data':stream.getvalue(),'width':600,'height':600,'format':'PNG'}
        ctx=Context(fields=('main_image','additional_images','size','price','detail_page'))
        with patch.object(c,'public_image',return_value=image):
            rows=[c.collect_record(ctx,doc,c.SITE,'01T752004',r,{}) for r in c.identities(doc,'01T752004')]
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['main_image'],rows[1]['main_image'])
        for row in rows:
            self.assertEqual(row['additional_images'].split(','),[row['main_image']]*5)
            self.assertTrue(row['size'])
            self.assertEqual(row['price'],25900)
            self.assertEqual(row['이미지 구분'],'다색상 공통 사진')
            self.assertNotIn('_page_code',row)
            self.assertEqual(row['detail_page'].count('<img '),2)

    def test_shared_full_page_identifier_does_not_drop_second_color(self):
        doc=c.soup((ROOT/'tests/fixtures/agabang_55638.html').read_text(encoding='utf-8'))
        for code in ('01T752004','01T75200458'):
            with tempfile.TemporaryDirectory() as folder:
                ctx=Context(folder,code)
                with patch('playwright.sync_api.sync_playwright',return_value=MagicMock()),patch.object(c,'search',return_value=[c.SITE+'/product/test/55638/']),patch.object(c,'visit',return_value=(doc,c.SITE+'/product/test/55638/')):
                    c.crawl(ctx)
                self.assertEqual([r['color'] for r in ctx.rows],['PINK','CREAM'])
                self.assertFalse(ctx.failures)

    def test_multicolor_without_title_palette_uses_official_options(self):
        doc=c.soup((ROOT/'tests/fixtures/agabang_55784.html').read_text(encoding='utf-8'))
        records=c.identities(doc,'01T751006')
        self.assertEqual([r['color'] for r in records],['OATMEAL','MELAN GREY','YELLOW'])
        self.assertTrue(all(r['product_name']=='포포기모맨투맨' and r['_shared_gallery'] for r in records))
        self.assertEqual(len(c.detail_urls(doc,c.SITE,'01T751006','')),3)
        for code in ('01T751006','01T75100609'):
            with tempfile.TemporaryDirectory() as folder:
                ctx=Context(folder,code,('size','price'))
                with patch('playwright.sync_api.sync_playwright',return_value=MagicMock()),patch.object(c,'search',return_value=[c.SITE+'/product/test/55784/']),patch.object(c,'visit',return_value=(doc,c.SITE+'/product/test/55784/')):
                    c.crawl(ctx)
                self.assertEqual(len(ctx.rows),3)
                self.assertFalse(ctx.failures)
                self.assertTrue(all(r['price']==25900 and r['size'] for r in ctx.rows))

    def test_missing_color_caption_does_not_erase_product_description(self):
        source=(ROOT/'tests/fixtures/agabang_45766.html').read_text(encoding='utf-8')
        records=c.identities(c.soup(source.replace('(PINK)_79R717552','_79R717552')),'79R717552')
        self.assertEqual(records[0]['product_name'],'베이비니키우주복(턱받이)')
        self.assertEqual(records[0]['color'],'PINK')

    def test_dead_detail_image_is_omitted_but_transient_failure_still_fails(self):
        doc=c.soup((ROOT/'tests/fixtures/agabang_55650.html').read_text(encoding='utf-8'))
        urls=c.detail_urls(doc,c.SITE,'01T753002','')
        self.assertEqual(len(urls),2)
        ctx=Context()
        cache={urls[0]:{'status':'accessible'},urls[1]:{'status':'inaccessible','reasons':['공개 GET 거부: HTTP 404']}}
        markup=c.detail(ctx,doc,c.SITE,'01T753002','',cache)
        self.assertEqual(markup.count('<img '),1)
        self.assertIn('01T75300238.jpg',markup)
        self.assertNotIn('01T75300273.jpg',markup)
        for status,reason in [('unknown','통신 시간 초과'),('inaccessible','공개 GET 거부: HTTP 403')]:
            cache[urls[1]]={'status':status,'reasons':[reason]}
            with self.assertRaises(ValueError):c.detail(ctx,doc,c.SITE,'01T753002','',cache)
        cache={u:{'status':'inaccessible','reasons':['HTTP 404']} for u in urls}
        with self.assertRaises(ValueError):c.detail(ctx,doc,c.SITE,'01T753002','',cache)

    def test_disney_brand_is_preserved_without_rejecting_product(self):
        doc=c.soup((ROOT/'tests/fixtures/agabang_45766.html').read_text(encoding='utf-8'))
        record=c.identities(doc,'79R717552')[0]
        self.assertEqual(record['color'],'PINK')
        self.assertEqual(record['product_name'],'베이비니키우주복(턱받이)')
        self.assertEqual(record['사이트브랜드'],'디즈니베이비')
        self.assertEqual(record['color_code'],'')
        variants=c.matching_stocks(doc,'PINK')
        self.assertEqual([v['option_value_orginal'][1] for v in variants],['75','80','90'])
        self.assertEqual(variants[0]['stock_number'],0)

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
