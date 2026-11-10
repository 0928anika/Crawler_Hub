import copy
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from crawler_sdk import CrawlContext

spec=importlib.util.spec_from_file_location('entetee_test',ROOT/'brands/entetee_eland.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/entetee'
META=json.loads((ROOT/'brands/entetee_eland.json').read_text(encoding='utf-8'))
LINK=c.SITE+'/i/item?itemNo=2409495989&lowerVendNo=CN003623'


def data(name='entetee_sample'):
    return json.loads((FIX/(name+'.json')).read_text(encoding='utf-8'))


def opts(name='entetee_sample'):
    return json.loads((FIX/(name+'_options.json')).read_text(encoding='utf-8'))['data']['itemOption']


def image_info(ctx,url,cache):
    return ('JPEG',1000,1000,hashlib.sha256(url.encode()).hexdigest(),True)


class Context:
    def __init__(self):self.logs=[]
    checkpoint=lambda self:None
    def log(self,*args):self.logs.append(args)


class EnteteeTests(unittest.TestCase):
    def test_campaign_badge_and_trailing_size_are_not_product_name(self):
        for name in ('[*한파특가] 체크 플란넬 원피스 파자마 110 ATJP25T61',
                     '[한파특가] 체크 플란넬 원피스 파자마 ATJP25T61 110',
                     '[쿠폰특가] 체크 플란넬 원피스 파자마 110호 ATJP25T61_충장점',
                     '【한파특가】 체크 플란넬 원피스 파자마 (110) ATJP25T61'):
            with self.subTest(name=name):
                self.assertEqual(c.clean_name(name,'ATJP25T61'),'체크 플란넬 원피스 파자마')
        self.assertEqual(c.clean_name('[키즈] 2매입 7부 파자마 (주니어) ATLA24376','ATLA24376'),
                         '[키즈] 2매입 7부 파자마 (주니어)')
    def test_name_keeps_meaningful_parentheses_and_removes_store_suffix(self):
        self.assertEqual(c.clean_name('FG 라운지 7부 세트 B (주니어) ATLA24376','ATLA24376'),
                         'FG 라운지 7부 세트 B (주니어)')
        self.assertEqual(c.clean_name('튤립 레이온 원피스 파자마 ATJP25161_NC충장점','ATJP25161'),
                         '튤립 레이온 원피스 파자마')

    def test_any_branch_suffix_is_removed_without_touching_product_qualifiers(self):
        for suffix in ('_충장점','_중동점','_평촌점','_NC충장점','_NC 충장점',
                       '_뉴코아 부천점','[평촌점]','(뉴코아 부천점)',' 충장점',
                       ' NC 충장점','-강서점','|천안지점','/경산점'):
            with self.subTest(suffix=suffix):
                self.assertEqual(c.clean_name('앙떼떼 원마일웨어 레몬 원피스 '+suffix,'ATJP25161'),
                                 '원마일웨어 레몬 원피스')
                self.assertEqual(c.clean_name('원마일웨어 레몬 원피스 '+suffix+' ATJP25161','ATJP25161'),
                                 '원마일웨어 레몬 원피스')
        self.assertEqual(c.clean_name('[앙떼떼] FG 라운지 (주니어) ATLA24376_충장점','ATLA24376'),
                         'FG 라운지 (주니어)')
        self.assertEqual(c.clean_name('원마일웨어-레몬 원피스 충장점','ATJP25161'),
                         '원마일웨어-레몬 원피스')
        self.assertEqual(c.clean_name('원마일웨어 레몬-원피스_충장점','ATJP25161'),
                         '원마일웨어 레몬-원피스')
    def test_scene_is_not_rejected_by_background_or_first_position(self):
        self.assertFalse(c.white_background(Image.open(FIX/'scene.jpg')))
        self.assertTrue(c.white_background(Image.open(FIX/'flat.jpg')))
        main=data()['itemMain']; first='https://item.elandrs.com/'+main['itemImage'][0]['imagePath']
        def info(ctx,url,cache):
            return ('JPEG',1000,1000,url,url!=first)
        ctx=Context()
        with patch.object(c,'image_info',side_effect=info):
            photos=c.gallery(ctx,main,{})
        self.assertEqual(len(photos),9)
        self.assertEqual(photos[0][0],first)
        self.assertTrue(any('장면형' in text for text,*_ in ctx.logs))
        with patch.object(c,'image_info',return_value=('JPEG',1000,1000,'digest',False)):
            self.assertEqual(len(c.gallery(Context(),main,{})),9)

    def test_explicit_seller_metadata_is_excluded_not_unmarked_scene(self):
        main=data()['itemMain']; main['itemImage'][0]['imageName']='매장 직접촬영 셀러컷'
        with patch.object(c,'image_info',side_effect=image_info) as fetch:
            self.assertEqual(len(c.gallery(Context(),main,{})),8)
            self.assertEqual(fetch.call_count,8)

    def test_branch_vendor_and_independent_model_no(self):
        d=data('entetee_ATJP25161')
        d['itemMain']['itemName']='튤립 레이온 원피스 파자마'
        self.assertEqual(d['itemMain']['lowerVendNo'],'LV19015754')
        markup='<script>const itemDetailJson = '+json.dumps({'itemDetail':d})+';</script>'
        link=c.SITE+'/i/item?itemNo=2607516147&lowerVendNo=LV19015754'
        with patch.object(c,'page',return_value=BeautifulSoup(markup,'html.parser')):
            self.assertIsNotNone(c.product(Context(),link,'ATJP25161'))
            self.assertIsNone(c.product(Context(),link,'ATJP251610'))
        d['itemMain']['brandNo']='another-brand'
        markup='<script>const itemDetailJson = '+json.dumps({'itemDetail':d})+';</script>'
        with patch.object(c,'page',return_value=BeautifulSoup(markup,'html.parser')):
            self.assertIsNone(c.product(Context(),link,'ATJP25161'))

    def test_soldout_real_colors_preserved_placeholder_removed_and_zero_code_kept(self):
        palette,actual=c.colors(Context(),opts('entetee_second'))
        self.assertEqual([p['color_code'] for p in palette],['13','52','62'])
        self.assertEqual(actual[0]['soldoutYn'],'Y')
        self.assertNotIn('대표칼라',[p['color_name'] for p in palette])
        rows=opts();rows[0]['itemOptionName']='01 IVORY'
        self.assertEqual(c.colors(Context(),rows)[0][0]['color_code'],'01')

    def test_detail_keeps_original_product_and_size_omits_banner_and_copyright_request(self):
        d=data('entetee_ATJP25161')
        d['itemDesc']['itemDescdocumentCon'] += '&lt;img src="https://item.elandrs.com/r/image/item/copyright.jpg"&gt;'
        with patch.object(c,'image_info',side_effect=image_info) as fetch:
            urls=c.detail_images(Context(),d,'ATJP25161',{})
        self.assertEqual(len(urls),2)
        self.assertTrue(any('/ATJP25161.jpg' in u for u in urls))
        self.assertTrue(any('/AT21-SIZE.jpg' in u for u in urls))
        self.assertFalse(any('BANNER' in u or 'copyright' in u for u in urls))
        self.assertEqual(fetch.call_count,2)

    def test_real_sdk_price_choices_skip_optional_requests_and_missing_regular_does_not_fail(self):
        for price_on,regular_on in itertools.product((False,True),repeat=2):
            for retail in (29900,None,0,'잘못된 가격'):
                with self.subTest(price=price_on,regular=regular_on,retail=retail),tempfile.TemporaryDirectory() as tmp:
                    d=data();d['itemMain']['orgSellprice']=retail
                    events=[]; selections=['main_image']+(['price'] if price_on else [])+(['regular_price'] if regular_on else [])
                    ctx=CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,selections,META['output_headers'],
                        lambda:None,lambda kind,**v:events.append((kind,v)),input_item={'품번':'ATLS24TT3'},mapping_mode='manual')
                    requests=[]
                    def options(ctx,main,parent=None):
                        requests.append(parent)
                        if parent is None:return opts()
                        return [{'itemOptionTcode':'20','itemOptionName':'100','sellPrice':11960}]
                    with patch.object(c,'search',return_value=[LINK]),patch.object(c,'product',return_value=d), \
                         patch.object(c,'options',side_effect=options),patch.object(c,'image_info',side_effect=image_info), \
                         patch.object(c,'detail_images') as detail:
                        c.crawl(ctx);ctx.finish()
                    rows=[v for k,v in events if k=='row' and not v.get('header')]
                    self.assertEqual(len(rows),9)
                    row=dict(zip(ctx.headers,rows[0]['row']))
                    self.assertEqual(row['상품 링크'],LINK)
                    self.assertEqual(row['판매가'],11960 if price_on else None)
                    self.assertEqual(row['정상가'],29900 if regular_on and retail==29900 else None)
                    self.assertEqual(len(requests),3 if price_on else 1)
                    detail.assert_not_called()
                    self.assertFalse(ctx._failures)

    def test_common_prices_are_not_arbitrarily_selected(self):
        self.assertIsNone(c.common([100,200]))
        self.assertIsNone(c.common([100,None]))
        self.assertEqual(c.common([100,100]),100)
        for value in ('-1','0','1,20','문의',None,False):self.assertIsNone(c.money(value))

    def test_shared_photos_merge_all_link_colors_and_divergent_regular_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            first=data();second=copy.deepcopy(first);second['itemMain']['orgSellprice']=39900
            second_link=LINK+'&example=second'
            events=[]
            ctx=CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,['main_image','regular_price'],META['output_headers'],
                lambda:None,lambda kind,**v:events.append((kind,v)),input_item={'품번':'ATLS24TT3'},mapping_mode='manual')
            def options(ctx,main,parent=None):
                rows=opts()
                if main is second['itemMain']:rows[0]['itemOptionName']='01 PINK'
                return rows
            with patch.object(c,'search',return_value=[LINK,second_link]),patch.object(c,'product',side_effect=[first,second]), \
                 patch.object(c,'options',side_effect=options),patch.object(c,'image_info',side_effect=image_info):
                c.crawl(ctx);ctx.finish()
            rows=[v for k,v in events if k=='row' and not v.get('header')]
            self.assertEqual(len(rows),9)
            self.assertEqual(len(rows[0]['unmapped']['colors']),3)
            self.assertIsNone(dict(zip(ctx.headers,rows[0]['row']))['정상가'])

    def test_partial_failure_preserves_successful_rows_and_stop_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            events=[]
            ctx=CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,['main_image'],META['output_headers'],
                lambda:None,lambda kind,**v:events.append((kind,v)),input_item={'품번':'ATLS24TT3'},mapping_mode='manual')
            with patch.object(c,'search',return_value=[LINK,LINK+'&bad=1']), \
                 patch.object(c,'product',side_effect=[data(),c.CollectionError('fixture timeout')]), \
                 patch.object(c,'options',return_value=opts()),patch.object(c,'image_info',side_effect=image_info):
                with self.assertRaises(c.CollectionError):c.crawl(ctx)
            self.assertEqual(len([v for k,v in events if k=='row' and not v.get('header')]),9)
            self.assertTrue(ctx._failures)
            with patch.object(ctx,'checkpoint',side_effect=RuntimeError('stop')), \
                 patch.object(c,'search',return_value=[LINK]):
                with self.assertRaisesRegex(RuntimeError,'stop'):c.crawl(ctx)


if __name__=='__main__':unittest.main()
