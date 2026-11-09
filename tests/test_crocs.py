"""Offline contract regressions; run: python -m unittest discover -s tests -p test_crocs.py."""
import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'brands')]
import crocs_official as c
from bs4 import BeautifulSoup
from PIL import Image


class Context:
    def __init__(self, folder, fields=(), code='10126'):
        self.product_code, self.output_dir, self.fields = code, Path(folder), fields
        self.rows, self.failures, self.logs = [], [], []
    def enabled(self, key): return key in self.fields
    def checkpoint(self): return None
    def stage(self, message): return None
    def log(self, message, level='INFO'): self.logs.append((level, message))
    def fail_item(self, message): self.failures.append(message)
    def emit_result(self, row): self.rows.append(row)


class CrocsTests(unittest.TestCase):
    def test_official_sku_includes_hidden_colors_but_rejects_wrong_base(self):
        product={'variationAttributes':[{'id':'color','values':[{'value':'001','name':'블랙'}, {'value':'6MB','name':'펄핑크'}]}]}
        text='"variants":'+json.dumps([{'productId':'11016-'+cid+'-M4W6','variationValues':{'color':cid,'size':'M4W6'}} for cid in ('001','6MB')])
        self.assertEqual([v['value'] for v in c.verified_colors(product,text,'11016')],['001','6MB'])
        with self.assertRaises(ValueError): c.verified_colors(product,text,'10116')

    def test_visible_swatches_exclude_internal_history_keep_disabled(self):
        product={'variationAttributes':[{'id':'color','values':[{'value':'001','name':'블랙'},
            {'value':'669','name':'핑크 레모네이드'},{'value':'6MB','name':'펄핑크 / 와일드오키드'}]}]}
        doc=BeautifulSoup('''<button data-testid="pdp-colour-unknown" aria-label="블랙"><img src="https://media.crocs.com/image/upload/products/11016_001_ALT100"></button>
            <button data-testid="pdp-colour-unknown" aria-label="핑크 레모네이드" aria-disabled="true"><img src="https://media.crocs.com/image/upload/products/11016_669_ALT100"></button>''','html.parser')
        self.assertEqual([c['value'] for c in c.displayed_colors(doc,product,'11016')],['001','669'])
        with self.assertRaises(ValueError): c.displayed_colors(doc,product,'10116')

    def test_lifestyle_and_review_images_not_downloaded(self):
        url='https://media.crocs.com/images/f_auto%2Cw_900/products/11016_6MB_ALT210/crocs'
        product={'imageGroups':[{'viewType':'standard','variationAttributes':[{'id':'color','values':[{'value':'6MB'}]}],
            'images':[{'link':url,'alt':'상품'}]}]}
        for markup in ('<figure data-slot="pdp-gallery"><img src="'+url+'"></figure>', '<div id="reviews"><img src="'+url+'"></div>'):
            doc=BeautifulSoup(markup,'html.parser')
            with patch.object(c,'check_public_image',side_effect=AssertionError('excluded photo downloaded')):
                main,extras,_=c.gallery(Context('.'),product,'6MB',{},doc)
            self.assertIsNone(main)
            self.assertFalse(extras)

    def test_hidden_color_keeps_exact_asset_and_catalogue_pose(self):
        # DOM can show a different, available color. Never reuse its asset ID.
        doc=BeautifulSoup('<figure data-slot="pdp-gallery"><img src="https://media.crocs.com/images/f_auto%2Cq_auto%2Cw_900%2Ch_900/products/10126_001_ALT100/crocs"></figure>','html.parser')
        product={'id':'10126','imageGroups':[{'viewType':'standard','variationAttributes':[{'id':'color','values':[{'value':'014'}]}],
            'images':[{'link':'https://media.crocs.com/images/t_standard/products/10126_014_ALT100/crocs','alt':'바야 클로그'}]}]}
        data=io.BytesIO()
        Image.new('RGB',(900,900),'white').save(data,format='PNG')
        result={'status':'accessible','data':data.getvalue(),'width':900,'height':900,'format':'PNG','reasons':[]}
        with patch.object(c,'check_public_image',return_value=result) as fetch:
            main,extras,warning=c.gallery(Context('.'),product,'014',{},doc)
        self.assertIn('/10126_014_ALT100/',main)
        self.assertNotIn('/10126_001_',main)
        self.assertEqual(len(extras),5)
        self.assertEqual(fetch.call_count,1)

    def test_price_selection_and_missing_regular_price(self):
        product = {'id':'10126','name':'Crocs 바야 클로그 10126','currency':'KRW',
            'variationAttributes':[{'id':'color','values':[{'value':'100','name':'화이트','orderable':False},
                {'value':'014','name':'그레파이트','orderable':False}]}]}
        def source(retail):
            return '"variants":' + json.dumps([{'productId':'10126-'+color+'-M4W6','price':69900,
                'retailPrice':retail,'variationValues':{'color':color,'size':'M4W6'},'orderable':False} for color in ('100','014')])
        class Page:
            url = c.ORIGIN + '/p/baya-clog/10126.html?cid=100'
            def content(self): return ''
            def locator(self,*args): return self
            @property
            def first(self): return self
            def wait_for(self,**kwargs): return None
        class Browser:
            def new_page(self): return Page()
            def close(self): return None
        class Playwright:
            chromium = SimpleNamespace(launch_persistent_context=lambda *a,**k:Browser())
            def __enter__(self): return self
            def __exit__(self,*args): return False
        for fields in ((), ('price',), ('regular_price',), ('price','regular_price')):
            for retail in (69900, None, 0, 'bad'):
                for code in ('10126','10126_100','10126-100'):
                    with self.subTest(fields=fields,retail=retail,code=code), tempfile.TemporaryDirectory() as folder:
                        ctx=Context(folder,fields,code)
                        with patch('playwright.sync_api.sync_playwright',return_value=Playwright()), \
                             patch.object(c,'find_product',return_value=Page.url), \
                             patch.object(c,'displayed_colors',return_value=product['variationAttributes'][0]['values']), \
                             patch.object(c,'visit') as visit, patch.object(c,'wait_product',return_value=(None,source(retail),product)) as load, \
                             patch.object(c,'gallery',side_effect=AssertionError('unselected images')), \
                             patch.object(c,'size_table',side_effect=AssertionError('unselected detail')):
                            c.crawl(ctx)
                        self.assertEqual(len(ctx.rows),2 if code=='10126' else 1)
                        self.assertEqual(load.call_count,1)
                        self.assertEqual(visit.call_count,0)
                        self.assertFalse(ctx.failures)
                        row=ctx.rows[0]
                        self.assertEqual(row['product_name'],'바야 클로그')
                        self.assertEqual(row['color_code'],'100')
                        self.assertEqual('price' in row,'price' in fields)
                        self.assertEqual('regular_price' in row,'regular_price' in fields)
                        if 'regular_price' in fields:
                            self.assertEqual(row['regular_price'],69900 if retail==69900 else None)
                        self.assertTrue(row['product_url'].startswith('https://'))

    def test_normal_price_invalid_or_divergent(self):
        ctx=Context('.')
        for bad in (None,0,-1,'100',False,float('nan'),float('inf')):
            self.assertIsNone(c.money(ctx,[{'retailPrice':bad}],'retailPrice','KRW'))
        self.assertIsNone(c.money(ctx,[{'retailPrice':10},{'retailPrice':20}],'retailPrice','KRW'))

    def test_notice_is_text_and_absent_notice_is_omitted(self):
        ctx=Context('.')
        h=c.detail(ctx,{'outerMaterial':'A & B','importer':'크록스코리아'},[],'')
        self.assertIn('A &amp; B',h)
        self.assertNotIn('<table',h)
        self.assertEqual(c.detail(ctx,{},[],''),'<div></div>')

    def test_rsc_decoding_and_product_identity(self):
        payload='1:["$","X",null,{"product":'+json.dumps({'id':'10126','imageGroups':[{}]})+'}]'
        source='<script>self.__next_f.push('+json.dumps([1,payload])+')</script>'
        self.assertEqual(c.parse_product(source,'10126')[2]['id'],'10126')
        with self.assertRaises(ValueError): c.parse_product(source,'99999')


if __name__ == '__main__':
    unittest.main()
