import importlib.util
import itertools
from pathlib import Path
import sys
import unittest
from unittest.mock import patch,MagicMock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('absorba_test',ROOT/'brands/happyland_official.py')
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
    def test_happyland_identity_and_original_h_body_for_both_models(self):
        for code in ['H1110306','HL2651AN06']:
            doc=c.parse((FIX/(code+'.html')).read_bytes())
            row=c.identity(doc,code)
            self.assertNotIn('[해피랜드]',row['product_name'])
            with patch.object(c,'image',return_value=dict(width=800,height=3000)):
                result=c.detail(Context(),doc,c.SITE,{})
            urls=[n['src'] for n in c.parse(result).select('img')]
            self.assertEqual(len(urls),2)
            self.assertTrue(all('/h/' in url for url in urls))
            self.assertTrue(any(code+'_DC.jpg' in url for url in urls))
            self.assertTrue(any('notice_size.jpg' in url for url in urls))

    def test_brand_id_and_title_must_agree(self):
        doc=source();doc.select_one('input[name="brandCd"]')['value']='002'
        with self.assertRaises(c.CrawlError):c.identity(doc,'AB2711BU04')

    def test_image_attempt_timeout_still_has_fresh_retry_budget(self):
        now=[0.0];first=MagicMock();second=MagicMock();ctx=Context()
        def stalled(*args,**kwargs):
            now[0]=91.0
            raise TimeoutError('slow image attempt')
        first.open.side_effect=stalled
        response=second.open.return_value.__enter__.return_value
        response.headers={};response.read1.side_effect=[b'complete image bytes',b'']
        with patch.object(c.time,'monotonic',side_effect=lambda:now[0]),patch.object(c.time,'sleep'),patch.object(c,'public_opener',side_effect=[first,second]) as opener:
            result=c.fetch(ctx,'https://img.efe.co.kr/a/image.jpg',request_seconds=180,attempt_seconds=90)
        self.assertEqual(result,b'complete image bytes')
        self.assertEqual([call.args[1] for call in opener.call_args_list],[90,180])
        self.assertTrue(any('재시도' in log[0] for log in ctx.logs))

    def test_new_official_detail_cdn_for_all_three_models(self):
        for code in ['AB27A5RT01','A1510604','A1A10303']:
            doc=c.parse((FIX/(code+'.html')).read_bytes());ctx=Context()
            c.identity(doc,code)
            with patch.object(c,'image',return_value=dict(width=800,height=3000)) as loader:
                result=c.detail(ctx,doc,c.SITE,{})
            urls=[n['src'] for n in c.parse(result).select('img')]
            self.assertEqual(len(urls),2)
            self.assertTrue(all(c.urlsplit(url).hostname==c.DETAIL_CDN for url in urls))
            self.assertTrue(any(code+'_DC.jpg' in url for url in urls))
            self.assertTrue(any('notice_size.jpg' in url for url in urls))
            self.assertTrue(all('/notice/notice.jpg' not in call.args[1] for call in loader.call_args_list))
            self.assertFalse(c.parse(result).select('table'))

    def test_other_r2_bucket_is_not_trusted(self):
        with self.assertRaises(c.CrawlError):c.source_url('https://other.r2.dev/a/2027/item.jpg')

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

    def test_multiple_colors_use_same_photos_and_own_sizes(self):
        d=source();node=d.new_tag('option',value='1122993||0||||0^|^아이보리:75');node['alt']='아이보리:75';d.select_one('select[name="optionSnoInput"]').append(node)
        ctx=Context(['main_image','additional_images','size']);photo=dict(url='https://img.efe.co.kr/a/photo.jpg',validation=dict(passed=True))
        with patch.object(c,'search',return_value=[c.SITE+'/goods/goods_view.php?goodsNo=1000001384']),patch.object(c,'fetch',return_value=str(d).encode()),patch.object(c,'gallery',return_value=[photo]):c.crawl(ctx)
        self.assertFalse(ctx.unmapped);self.assertEqual(len(ctx.rows),2)
        self.assertEqual([p['color_name'] for p in ctx.rows],['브라운','아이보리'])
        self.assertEqual([p['size'] for p in ctx.rows],['75,80','75'])
        self.assertEqual(ctx.rows[0]['main_image'],ctx.rows[1]['main_image'])
        self.assertEqual(ctx.rows[0]['additional_images'],ctx.rows[1]['additional_images'])

    def test_actual_multicolor_product_finishes_without_manual_mapping(self):
        raw=(FIX/'A1510604.html').read_bytes();ctx=Context(['main_image','additional_images','detail_page','size'])
        ctx.product_code='A1510604'
        shared='<img src="https://img.efe.co.kr/a/shared.jpg">'
        photo=dict(url='https://img.efe.co.kr/a/photo.jpg',validation=dict(passed=True))
        with patch.object(c,'search',return_value=[c.SITE+'/goods/goods_view.php?goodsNo=1000000765']),patch.object(c,'fetch',return_value=raw),patch.object(c,'gallery',return_value=[photo]),patch.object(c,'detail',return_value=shared):c.crawl(ctx)
        self.assertEqual([row['color_name'] for row in ctx.rows],['노랑','베이지','핑크'])
        self.assertEqual(len({row['main_image'] for row in ctx.rows}),1)
        self.assertEqual({row['detail_page'] for row in ctx.rows},{shared})
        self.assertFalse(ctx.unmapped);self.assertFalse(ctx.failures)

    def test_unknown_image_owner_is_not_trusted(self):
        with self.assertRaises(c.CrawlError):c.source_url('https://godomall-storage.cdn-nhncommerce.com/other-store/goods/1.jpg')

    def test_disabled_size_is_preserved(self):
        d=source();d.select_one('option[alt="브라운:80"]')['disabled']='disabled'
        self.assertEqual(c.option_groups(d)['브라운']['sizes'],['75','80'])

if __name__=='__main__':unittest.main()
