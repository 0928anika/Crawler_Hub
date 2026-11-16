import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import benetton_official as c
FIX=ROOT/'tests/fixtures/benetton'

def source(color='BK'):return BeautifulSoup((FIX/(color+'.html')).read_bytes(),'html.parser')
def info(color='BK'):return c.inline_json(source(color),'productInfo')

class Context:
    product_code='BACTF2661'
    def __init__(self,fields=()):self.fields=fields;self.rows=[];self.errors=[];self.logs=[]
    def checkpoint(self):pass
    def stage(self,*a):pass
    def log(self,*a):self.logs.append(a)
    def enabled(self,k):return k in self.fields
    def emit_result(self,r):self.rows.append(r)
    def fail_item(self,e):self.errors.append(e)

def fetch(ctx,url,payload=None):
    if payload is not None:return (FIX/'search.json').read_bytes()
    pid=c.parse_qs(c.urlsplit(url).query)['productcode'][0]
    return (FIX/('BK.html' if pid.endswith('67806') else 'BG.html')).read_bytes()

class Tests(unittest.TestCase):
    def test_public_search_exact_model_and_brand_only(self):
        result=json.loads((FIX/'search.json').read_bytes())
        other=dict(result['result'][0],productcode='999',prodcode='BACTF26610')
        wrong_brand=dict(result['result'][0],productcode='998',shop_div_code='S')
        result['result']+=[other,wrong_brand];result['count']=4
        with patch.object(c,'fetch',return_value=json.dumps(result).encode()):
            self.assertEqual(c.search(Context(),'BACTF2661'),['004002000000067806','004002000000067805'])

    def test_incomplete_or_changing_search_is_not_no_results(self):
        with patch.object(c,'fetch',return_value=b'{}'):
            with self.assertRaisesRegex(ValueError,'검색 응답'):c.search(Context(),'BACTF2661')
        result=json.loads((FIX/'search.json').read_bytes());result['count']=41
        changed=dict(result,count=42)
        with patch.object(c,'fetch',side_effect=[json.dumps(x).encode() for x in [result,changed]]):
            with self.assertRaisesRegex(ValueError,'상품 수'):c.search(Context(),'BACTF2661')

    def test_product_model_identity_and_numeric_id(self):
        with patch.object(c,'fetch',side_effect=fetch):
            self.assertIsNotNone(c.product(Context(),'004002000000067806','BACTF2661'))
            self.assertIsNone(c.product(Context(),'004002000000067806','BACTF26610'))
        with self.assertRaises(ValueError):c.product(Context(),'invalid','BACTF2661')

    def test_gallery_uses_declared_large_urls_only_for_current_color(self):
        for color in ['BK','BG']:
            urls=c.gallery_urls(source(color),info(color))
            self.assertGreaterEqual(len(urls),6)
            self.assertTrue(all('/BACTF2661'+color+'_PB_' in url for url in urls))
            self.assertFalse(any('_PS_' in url or 'review' in url for url in urls))
        self.assertEqual(c.image_url('https://example.com/PARTS/image.jpg'),'')
        self.assertEqual(c.image_url('https://img.benettonmall.com/reviews/test.jpg'),'')

    def test_detail_preserves_notice_actual_table_diagram_and_wash(self):
        ctx=Context();doc=source();data=info()
        with patch.object(c,'image_info',return_value={'status':'accessible'}):markup=c.detail(ctx,doc,data,{})
        d=BeautifulSoup(markup,'html.parser');text=d.get_text(' ',strip=True)
        for value in ['BACTF2661BK','AS 책임자','1544-2855','132.1','83.5','단위 (cm)','모 59%']:
            self.assertIn(value,text)
        self.assertTrue(any('/real_size/' in n['src'] for n in d.select('img')))
        self.assertTrue(any('/wash/' in n['src'] for n in d.select('img')))
        stretch=next(row for row in d.select('tr') if row.find('th') and row.find('th').get_text()=='신축성')
        self.assertEqual(stretch.get_text(' ',strip=True),'신축성 없음')
        self.assertNotIn('약간있음',stretch.get_text())
        self.assertNotIn('상품평',text);self.assertNotIn('배송안내',text);self.assertNotIn('GLOBAL SIZE',text)
        self.assertFalse(d.select('script,input,button'))

    def test_missing_size_notice_and_wash_are_optional(self):
        ctx=Context();doc=source()
        for selector in ['.prd-info-list','.prd-info-clean','#prd-detail-size']:
            for n in doc.select(selector):n.decompose()
        with patch.object(c,'image_info',return_value={'status':'accessible'}):markup=c.detail(ctx,doc,info(),{})
        self.assertIn('핸드메이드',markup);self.assertNotIn('<table',markup)
        self.assertTrue(any('상세 구성 요소 없음' in msg for msg,level in ctx.logs))

    def test_two_colors_prices_and_soldout_size_preserved(self):
        ctx=Context(('size','price','regular_price','detail_page'))
        def get(ctx,url,payload=None):
            raw=fetch(ctx,url,payload)
            if payload is None:
                d=BeautifulSoup(raw,'html.parser');n=d.select('.prd-size-select')[-1];n['disabled']='';n['data-opt-qty']='0'
                return str(d).encode()
            return raw
        with patch.object(c,'fetch',side_effect=get),patch.object(c,'image_info',return_value={'status':'accessible'}):c.crawl(ctx)
        self.assertEqual({r['color_code'] for r in ctx.rows},{'BG','BK'});self.assertFalse(ctx.errors)
        for row in ctx.rows:
            self.assertEqual(row['price'],359400);self.assertEqual(row['regular_price'],599000)
            self.assertEqual(row['size'],'42,44');self.assertIn('real_size',row['detail_page'])
            self.assertIn('BACTF2661'+row['color_code'],BeautifulSoup(row['detail_page'],'html.parser').get_text())

    def test_no_unselected_images_or_prices_are_collected(self):
        ctx=Context()
        with patch.object(c,'fetch',side_effect=fetch),patch.object(c,'gallery',side_effect=AssertionError('unselected gallery')),patch.object(c,'detail',side_effect=AssertionError('unselected detail')),patch.object(c,'money',side_effect=AssertionError('unselected prices')):
            c.crawl(ctx)
        self.assertEqual(len(ctx.rows),2)
        self.assertTrue(all('price' not in row and 'size' not in row for row in ctx.rows))

    def test_direct_product_link_expands_official_color_links(self):
        ctx=Context();ctx.product_code='https://benettonmall.com/product/view?productcode=004002000000067806'
        with patch.object(c,'fetch',side_effect=fetch),patch.object(c,'search',side_effect=AssertionError('direct link')):c.crawl(ctx)
        self.assertEqual({r['color_code'] for r in ctx.rows},{'BK','BG'})

    def test_normal_price_is_independent_and_missing_stays_empty(self):
        self.assertIsNone(c.money(Context(),0,'정상가'))
        self.assertIsNone(c.money(Context(),'','정상가'))
        self.assertEqual(c.money(Context(),'599000','정상가'),599000)

if __name__=='__main__':unittest.main()
