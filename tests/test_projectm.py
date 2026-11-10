import copy
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from crawler_sdk import CrawlContext

spec=importlib.util.spec_from_file_location('projectm_test',ROOT/'brands/projectm_official.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/projectm'
META=json.loads((ROOT/'brands/projectm_official.json').read_text(encoding='utf-8'))


def goods(sku='EPG3PP1105BK'):
    return json.loads((FIX/(sku+'.json')).read_text(encoding='utf-8'))['goods']


def products():
    return [goods(sku) for sku in ('EPG3PP1105BK','EPG3PP1105BE','EPG3PP1105DBR')]


class Context:
    checkpoint=lambda self:None
    stage=lambda self,*args:None
    log=lambda self,*args:None
    fail_item=lambda self,*args:None


def fake_picture(ctx,url,cache,**kwargs):
    return {'url':url,'digest':url,'width':720,'height':720,'format':'JPEG',
            'validation':{'passed':True},'small_label':False}


def fake_assets(ctx,g,cache,need_gallery,need_detail):
    god=g['god'];sku=god['godNo'];color=god['colorCd']
    rows=[(g['godImgList'][0]['imgUrl'],sku+'-main',True),
          (next(row['imgUrl'] for row in g['godImgList'] if row['imgTurn']==211),'shared-three-colors',True),
          (next(row['imgUrl'] for row in g['godImgList'] if row['imgTurn']==210),sku+'-label',False)]
    return [dict(url=c.IMAGES+url,digest=digest,active=True,detail_role=True,sku=sku,color=color,
                 validation={'passed':valid,'reasons':['paper label'] if not valid else []})
            for url,digest,valid in rows]


class ProjectMTests(unittest.TestCase):
    def test_api_brand_and_group_do_not_depend_on_title(self):
        g=goods();g['god']['godNm']='파워무빙 세미와이드핏 슬랙스'
        with patch.object(c,'fetch',return_value=json.dumps({'result':'Y','goods':g}).encode()):
            self.assertIsNotNone(c.load_product(Context(),'EPG3PP1105BK','EPG3PP1105'))
            self.assertIsNone(c.load_product(Context(),'EPG3PP1105BK','EPG3PP11050'))
        g['sysBrnd']['brndId']='OTHER'
        with patch.object(c,'fetch',return_value=json.dumps({'result':'Y','goods':g}).encode()):
            self.assertIsNone(c.load_product(Context(),'EPG3PP1105BK','EPG3PP1105'))

    def test_color_graph_expands_all_actual_ids_from_one_seed(self):
        records={g['god']['godNo']:g for g in products()};calls=[]
        def load(ctx,sku,code):calls.append(sku);return records[sku]
        with patch.object(c,'search',return_value=['EPG3PP1105BK']),patch.object(c,'load_product',side_effect=load):
            result,errors=c.group_products(Context(),'EPG3PP1105')
        self.assertEqual(len(result),3);self.assertFalse(errors)
        self.assertEqual(set(calls),set(records))

    def test_product_specific_size_figure_before_sanitized_table(self):
        g=goods()
        with patch.object(c,'picture',side_effect=fake_picture):
            markup=c.detail_markup(Context(),[g],[],{})
        self.assertLess(markup.index('사이즈 측정 그림'),markup.index('<table'))
        self.assertEqual(markup.count('<table'),1)
        soup=BeautifulSoup(markup,'html.parser')
        self.assertEqual(soup.find('table').find_all('th')[1].get_text(),'S(29)')
        self.assertIn('62.6',soup.find('table').get_text())
        self.assertIn('허리둘레',markup);self.assertIn('단위 : cm',markup)
        self.assertIn('1~3cm',markup)
        self.assertNotIn('제품 주소재',markup)
        self.assertNotIn('AFF_NTFC',markup);self.assertNotIn('<script',markup)
        self.assertNotIn('onclick',markup);self.assertNotIn('data-',markup)

    def test_actual_long_body_replaces_gallery_and_is_deduplicated(self):
        with patch.object(c,'picture',side_effect=fake_picture):
            markup=c.detail_markup(Context(),products(),fake_assets(Context(),goods(),{},True,True),{})
        self.assertEqual(markup.count('/ai_goods/detail/EPG3PP1105/EPG3PP1105-1.jpg'),1)
        self.assertNotIn('_MDL_',markup);self.assertNotIn('EPG3PP1105BK_M.jpg',markup)
        self.assertLess(markup.index('/ai_goods/detail/'),markup.index('사이즈 측정 그림'))

    def test_missing_body_cannot_succeed_with_gallery_and_size_only(self):
        g=goods();g.pop('godAiImgList',None)
        with patch.object(c,'picture',side_effect=fake_picture),self.assertRaises(c.CollectionError):
            c.detail_markup(Context(),[g],fake_assets(Context(),g,{},True,True),{})

    def test_original_table_structure_and_escaping_are_preserved(self):
        raw='<table onclick="bad()"><thead><tr><th rowspan="2">사이즈</th><th colspan="2">치수 cm</th></tr>' \
            '<tr><th>가슴</th><th>총장</th></tr></thead><tbody><tr><th>S &amp; M</th><td>90</td>' \
            '<td><script>bad()</script>60</td></tr></tbody></table>'
        out=c.clean_table(raw)[0]
        self.assertIn('rowspan="2"',out);self.assertIn('colspan="2"',out)
        self.assertIn('<thead>',out);self.assertIn('<tbody>',out)
        self.assertIn('S &amp; M',out);self.assertNotIn('bad()',out);self.assertNotIn('onclick',out)

    def test_size_fallbacks_and_actual_absence(self):
        g=goods();g['gdSizeLktb']=''
        with patch.object(c,'picture',side_effect=fake_picture):
            diagram,tables=c.size_material(Context(),g,{})
        self.assertTrue(diagram);self.assertTrue(tables)
        g['godDetailList']=[];g['godCaUseYn']='N';g['acmsrImgUrl']=''
        g['god']['godDetailCont']='<h3>실측사이즈 (cm)</h3><p>S: 가슴 100 cm / 총장 60 cm</p><p>M: 가슴 104 cm / 총장 62 cm</p>'
        diagram,tables=c.size_material(Context(),g,{})
        self.assertFalse(diagram);self.assertEqual(len(tables),1)
        self.assertIn('104 cm',tables[0]);self.assertIn('총장',tables[0])
        g['god']['godDetailCont']=''
        self.assertEqual(c.size_material(Context(),g,{}),('',[]))
        g['gdSizeLktb']='<div>표 제목만 남은 형식 변경</div>'
        with self.assertRaises(c.CollectionError):c.size_material(Context(),g,{})

    def test_detail_only_does_not_run_gallery_validator_and_label_is_not_product_cut(self):
        raw=(FIX/'product.jpg').read_bytes()
        with patch.object(c,'fetch',return_value=raw),patch.object(c,'validate_gallery_image',wraps=c.validate_gallery_image) as validator:
            info=c.picture(Context(),'https://public.example/product.jpg',{},gallery=False)
            validator.assert_not_called();self.assertFalse(info['gallery_checked'])
            self.assertTrue(c.picture(Context(),'https://public.example/product.jpg',{},gallery=True)['validation']['passed'])
            self.assertEqual(validator.call_count,1)
        with patch.object(c,'fetch',return_value=(FIX/'label.jpg').read_bytes()):
            info=c.picture(Context(),'https://public.example/label.jpg',{},gallery=True)
        self.assertTrue(info['small_label']);self.assertFalse(info['validation']['passed'])

    def test_real_sdk_all_colors_prices_and_shared_html(self):
        for price_on,retail_on,detail_on in itertools.product((False,True),repeat=3):
            with self.subTest(price=price_on,retail=retail_on,detail=detail_on),tempfile.TemporaryDirectory() as tmp:
                selected=['main_image','additional_images','size']+(['price'] if price_on else [])+ \
                    (['regular_price'] if retail_on else [])+(['detail_page'] if detail_on else [])
                emitted=[]
                ctx=CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,selected,META['output_headers'],lambda:None,
                    lambda kind,**d:emitted.append((kind,d)),input_item={'품번':'EPG3PP1105'})
                with patch.object(c,'group_products',return_value=(products(),[])), \
                     patch.object(c,'gallery_assets',side_effect=fake_assets), \
                     patch.object(c,'picture',side_effect=fake_picture),patch.object(c,'category') as category, \
                     patch.object(c,'size_material',wraps=c.size_material) as size_material:
                    c.crawl(ctx);ctx.finish()
                    category.assert_not_called()
                    self.assertEqual(size_material.call_count,3 if detail_on else 0)
                rows=[dict(zip(ctx.headers,d['row'])) for kind,d in emitted if kind=='row' and not d.get('header')]
                self.assertEqual(len(rows),3)
                self.assertEqual({r['색상코드'] for r in rows},{'BK','BE','DBR'})
                for row in rows:
                    self.assertNotIn('shared',row['대표이미지'])
                    self.assertEqual(len(row['추가이미지'].split(',')),5)
                    # With only one unique valid cut, the SDK slots may reuse it;
                    # a label or common multicolour image must never fill a slot.
                    self.assertTrue(all(url==row['대표이미지'] for url in row['추가이미지'].split(',')))
                    self.assertIn('34',row['사이즈'].split(','))
                    self.assertEqual(row['판매가'],39900 if price_on else None)
                    self.assertEqual(row['정상가'],69900 if retail_on else None)
                    if detail_on:
                        self.assertLess(row['상세페이지 HTML'].index('사이즈 측정 그림'),row['상세페이지 HTML'].index('<table'))
                        self.assertEqual(row['상세페이지 HTML'].count('<table'),1)
                    else:self.assertFalse(row['상세페이지 HTML'])
                if detail_on:self.assertEqual(len({r['상세페이지 HTML'] for r in rows}),1)

    def test_specific_color_underscore_and_hyphen(self):
        for typed,expected in (('EPG3PP1105_BK','BK'),('EPG3PP1105-DBR','DBR')):
            with self.subTest(typed=typed),tempfile.TemporaryDirectory() as tmp:
                emitted=[]
                ctx=CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,[],META['output_headers'],lambda:None,
                    lambda kind,**d:emitted.append((kind,d)),input_item={'품번':typed})
                with patch.object(c,'group_products',return_value=(products(),[])), \
                     patch.object(c,'gallery_assets') as images,patch.object(c,'size_material') as size:
                    c.crawl(ctx);ctx.finish();images.assert_not_called();size.assert_not_called()
                rows=[dict(zip(ctx.headers,d['row'])) for kind,d in emitted if kind=='row' and not d.get('header')]
                self.assertEqual(len(rows),1);self.assertEqual(rows[0]['색상코드'],expected)

    def test_optional_normal_price_failures_never_fail_color(self):
        for value in (None,0,'bad','1,20'):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as tmp:
                g=goods();g['god']['cvrPrc']=value;emitted=[]
                ctx=CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,['regular_price'],META['output_headers'],lambda:None,
                    lambda kind,**d:emitted.append((kind,d)),input_item={'품번':'EPG3PP1105'})
                with patch.object(c,'group_products',return_value=([g],[])):
                    c.crawl(ctx);ctx.finish()
                rows=[dict(zip(ctx.headers,d['row'])) for kind,d in emitted if kind=='row' and not d.get('header')]
                self.assertIsNone(rows[0]['정상가']);self.assertFalse(ctx._failures)
        self.assertEqual(c.amount(69900.0),69900)
        self.assertEqual(c.amount('69,900'),69900)
        self.assertIsNone(c.amount(69900.1))

    def test_partial_color_failure_preserves_successful_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            emitted=[]
            ctx=CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,['category'],META['output_headers'],lambda:None,
                lambda kind,**d:emitted.append((kind,d)),input_item={'품번':'EPG3PP1105'})
            with patch.object(c,'group_products',return_value=(products(),[])), \
                 patch.object(c,'category',side_effect=['팬츠',c.CollectionError('fixture timeout'),'팬츠']):
                with self.assertRaises(c.CollectionError):c.crawl(ctx)
            self.assertEqual(len([d for k,d in emitted if k=='row' and not d.get('header')]),2)
            self.assertTrue(ctx._failures)


if __name__=='__main__':unittest.main()
