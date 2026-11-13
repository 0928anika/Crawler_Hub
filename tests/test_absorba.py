import importlib.util
import itertools
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('absorba_test',ROOT/'brands/absorba_official.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/absorba'
def source():return c.parse((FIX/'product.html').read_bytes())
class Context:
    product_code='ab2711bu04'
    def __init__(self,selected=()):self.selected=selected;self.rows=[];self.failures=[];self.logs=[];self.unmapped=[]
    def checkpoint(self):pass
    def stage(self,*args):pass
    def log(self,*args):self.logs.append(args)
    def enabled(self,key):return key in self.selected
    def emit_result(self,row):self.rows.append(row)
    def fail_item(self,message):self.failures.append(message)
    def emit_unmapped(self,record,colors,url,shared_detail_html=''):self.unmapped.append((record,colors,url))

class AbsorbaTests(unittest.TestCase):
    def test_official_identity_and_option_colors(self):
        row=c.identity(source(),'AB2711BU04')
        self.assertEqual(row['product_name'],'포트코튼모달바디슈트조끼모자')
        groups=c.option_groups(source())
        self.assertEqual(list(groups),['브라운']);self.assertEqual(groups['브라운']['sizes'],['75','80'])
        self.assertEqual(groups['브라운']['deltas'],{0})
        with self.assertRaises(c.CrawlError):c.identity(source(),'AB2711BU05')
        d=source();d.select_one('input[name="brandCd"]')['value']='OTHER'
        with self.assertRaises(c.CrawlError):c.identity(d,'AB2711BU04')

    def test_search_only_product_cards(self):
        with patch.object(c,'fetch',return_value=(FIX/'search.html').read_bytes()):links=c.search(Context(),'AB2711BU04')
        self.assertEqual(links,[c.SITE+'/goods/goods_view.php?goodsNo=1000001384'])

    def test_declared_original_not_thumbnail_or_social_banner(self):
        urls=c.gallery_urls(source(),c.SITE)
        self.assertEqual(len(urls),1)
        self.assertIn('/image/magnify/',urls[0]);self.assertNotIn('/thumb/',urls[0])

    def test_original_body_and_size_notice_remain_without_extra_tables(self):
        with patch.object(c,'image',return_value=dict(width=800,height=3000)) as loader:
            result=c.detail(Context(),source(),c.SITE,{})
        urls=[n['src'] for n in c.parse(result).select('img')]
        self.assertEqual(urls,['https://img.efe.co.kr/a/2027/AB2711BU04_DC.jpg','https://img.efe.co.kr/a/2026/notice_size.jpg'])
        self.assertEqual(loader.call_count,2);self.assertFalse(c.parse(result).select('table'))

    def test_prices_independent_and_no_unselected_image_requests(self):
        for selling,normal in itertools.product([False,True],repeat=2):
            ctx=Context([key for key,yes in [('price',selling),('regular_price',normal),('size',True)] if yes])
            with patch.object(c,'search',return_value=[c.SITE+'/goods/goods_view.php?goodsNo=1000001384']),patch.object(c,'fetch',return_value=(FIX/'product.html').read_bytes()),patch.object(c,'gallery',side_effect=AssertionError('unselected')),patch.object(c,'detail',side_effect=AssertionError('unselected')):
                c.crawl(ctx)
            self.assertFalse(ctx.failures);row=ctx.rows[0]
            self.assertEqual(row.get('price'),99000 if selling else None)
            self.assertEqual(row.get('regular_price'),99000 if normal else None)
            self.assertEqual('price' in row,selling);self.assertEqual('regular_price' in row,normal)
            self.assertEqual(row['color_code'],'');self.assertEqual(row['size'],'75,80')

    def test_missing_zero_bad_normal_price_does_not_fail(self):
        for value in [None,'0','문의']:
            d=source();n=d.select_one('input[name="set_goods_fixedPrice"]');ctx=Context()
            if value is None:
                for node in d.select('input[name="set_goods_fixedPrice"]'):node.decompose()
                for dl in d.select('.item_detail_list dl'):
                    if c.text(dl.find('dt'))=='정가':dl.decompose()
            else:n['value']=value
            self.assertIsNone(c.regular_price(ctx,d));self.assertTrue(ctx.logs)

    def test_multiple_colors_are_not_guessed(self):
        d=source();node=d.new_tag('option',value='1122993||0||||0^|^아이보리:75');node['alt']='아이보리:75';d.select_one('select[name="optionSnoInput"]').append(node)
        ctx=Context(['main_image']);photo=dict(url='https://img.efe.co.kr/a/photo.jpg',validation=dict(passed=True))
        with patch.object(c,'search',return_value=[c.SITE+'/goods/goods_view.php?goodsNo=1000001384']),patch.object(c,'fetch',return_value=str(d).encode()),patch.object(c,'gallery',return_value=[photo]):c.crawl(ctx)
        self.assertFalse(ctx.rows);self.assertEqual(len(ctx.unmapped),1)
        self.assertEqual([p['color_name'] for p in ctx.unmapped[0][1]],['브라운','아이보리'])

    def test_unknown_image_owner_is_not_trusted(self):
        with self.assertRaises(c.CrawlError):c.source_url('https://godomall-storage.cdn-nhncommerce.com/other-store/goods/1.jpg')

    def test_disabled_size_is_preserved(self):
        d=source();d.select_one('option[alt="브라운:80"]')['disabled']='disabled'
        self.assertEqual(c.option_groups(d)['브라운']['sizes'],['75','80'])

if __name__=='__main__':unittest.main()
