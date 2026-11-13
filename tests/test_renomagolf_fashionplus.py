import importlib.util
import itertools
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
spec=importlib.util.spec_from_file_location('renoma_fp_test',ROOT/'brands/renomagolf_fashionplus.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
FIX=ROOT/'tests/fixtures/renomagolf_fashionplus'
def source(number,kind='product'):return c.BeautifulSoup((FIX/(number+'_'+kind+'.html')).read_bytes(),'html.parser')
class Context:
    product_code='RMDVM4382'
    def __init__(self,fields=()):self.fields=fields;self.rows=[];self.failures=[];self.logs=[]
    def checkpoint(self):pass
    def enabled(self,key):return key in self.fields
    def stage(self,*a):pass
    def log(self,*a):self.logs.append(a)
    def fail_item(self,msg):self.failures.append(msg)
    def emit_unmapped(self,record,colors,url,shared_detail_html=''):self.rows.append((record,colors,url,shared_detail_html))

def shot():
    image=Image.new('RGB',(800,1072),'#eeeeee');draw=ImageDraw.Draw(image)
    draw.rectangle((240,180,560,850),fill='black');return image

class RenomaFashionplusTests(unittest.TestCase):
    def test_uniform_background_and_foreground_without_logo(self):
        for color in ['white','#dedede','#b0b090']:
            image=Image.new('RGB',(400,536),color);ImageDraw.Draw(image).rectangle((130,100,270,430),fill='black')
            self.assertTrue(c.studio_background(image)[0])
        self.assertFalse(c.studio_background(Image.new('RGB',(400,536),'white'))[0])

    def test_complex_store_background_is_rejected(self):
        image=Image.new('RGB',(400,536));draw=ImageDraw.Draw(image)
        for y in range(0,536,20):
            for x in range(0,400,20):draw.rectangle((x,y,x+19,y+19),fill='brown' if (x+y)//20%2 else 'lightblue')
        self.assertFalse(c.studio_background(image)[0])
        ctx=Context(['main_image','detail_page'])
        with patch.object(c,'search',return_value=[(c.BASE+'/goods/detail/1','name','https://example.com/thumb.jpg')]),patch.object(c,'image_get',return_value=image),patch.object(c,'soup_get') as detail:
            with self.assertRaises(c.CollectionError):c.crawl(ctx)
        detail.assert_not_called()

    def test_hansung_common_top_bottom_removed_original_body_kept(self):
        with patch.object(c,'soup_get',return_value=source('432217746','detail')),patch.object(c,'image_get',return_value=Image.new('RGB',(950,8000))) as loader:
            urls=c.detail_images(Context(),c.BASE+'/goods/detail/432217746','RMDVM4382')
        self.assertEqual(urls,['https://hansungimall.openhost.cafe24.com/renoma/detail/RMDVM4382-199.jpg'])
        self.assertEqual(loader.call_count,1)

    def test_moda_chuncheon_and_incheon_banners_excluded_before_fetch(self):
        for number in ['313560764','381140025']:
            with patch.object(c,'soup_get',return_value=source(number,'detail')),patch.object(c,'image_get',return_value=Image.new('RGB',(950,2500))) as loader:
                urls=c.detail_images(Context(),c.BASE+'/goods/detail/'+number,'RWSCN8208')
            self.assertEqual(len(urls),10)
            self.assertTrue(all('/mall/assets/product_img/' in url for url in urls))
            self.assertEqual(loader.call_count,10)
            self.assertFalse(any('/brand_info/' in call.args[1] or '/event/' in call.args[1] or '/md_image/' in call.args[1] for call in loader.call_args_list))

    def test_only_images_not_html_store_notice_or_tables_are_emitted(self):
        ctx=Context(['main_image','detail_page'])
        link=c.BASE+'/goods/detail/432217746'
        def soup(ctx,url):return source('432217746','detail' if url.endswith('/description-iframe') else 'product')
        with patch.object(c,'search',return_value=[(link,'RMDVM4382','https://example.com/thumb.jpg')]),patch.object(c,'soup_get',side_effect=soup),patch.object(c,'image_get',return_value=shot()),patch.object(c,'options',return_value=([dict(color_name='BLACK',color_code='')],[])):
            c.crawl(ctx)
        self.assertTrue(ctx.rows);markup=ctx.rows[0][3]
        self.assertIn('RMDVM4382-199.jpg',markup)
        self.assertFalse(c.BeautifulSoup(markup,'html.parser').find_all(['table','p','h3']))
        self.assertNotIn('top.jpg',markup);self.assertNotIn('매장',markup)

    def test_size_only_option_uses_official_title_color_code(self):
        with patch.object(c,'fetch',return_value=(FIX/'381140025_options.json').read_bytes()):
            colors,sizes=c.options(Context(),c.BASE,True,doc=source('381140025'),code='RWSCO8209')
        self.assertTrue(all(row==dict(color_name='',color_code='509') for row in colors))
        self.assertEqual(sizes,['100','105','90','95'])

    def test_independent_prices_and_optional_detail_skip(self):
        for selling,normal in itertools.product([False,True],repeat=2):
            ctx=Context(['main_image']+[key for key,yes in [('price',selling),('regular_price',normal)] if yes])
            with patch.object(c,'search',return_value=[(c.BASE+'/goods/detail/432217746','RMDVM4382','https://example.com/thumb.jpg')]),patch.object(c,'soup_get',return_value=source('432217746')),patch.object(c,'image_get',return_value=shot()),patch.object(c,'options',return_value=([dict(color_name='BLACK',color_code='')],[])),patch.object(c,'detail_images',side_effect=AssertionError('unselected detail')):
                c.crawl(ctx)
            row=ctx.rows[0][0]
            self.assertEqual(row.get('price'),99000 if selling else None)
            self.assertEqual(row.get('regular_price'),319000 if normal else None)
            self.assertEqual('price' in row,selling);self.assertEqual('regular_price' in row,normal)

    def test_name_keeps_product_only(self):
        self.assertEqual(c.clean_name('[본사공식(시크릿)]남성 튜브 구스 다운 베스트 RMDVM4382-199','RMDVM4382'),'남성 튜브 구스 다운 베스트')

    def test_removed_template_image_is_warned_not_fabricated(self):
        def load(ctx,url):
            if 'plgw' in url:raise c.MissingResource('404')
            return Image.new('RGB',(950,2500))
        ctx=Context()
        with patch.object(c,'soup_get',return_value=source('381140025','detail')),patch.object(c,'image_get',side_effect=load):
            urls=c.detail_images(ctx,c.BASE,'RWSCO8209')
        self.assertEqual(len(urls),9);self.assertTrue(any('404' in log[0] for log in ctx.logs))
        with patch.object(c,'soup_get',return_value=source('381140025','detail')),patch.object(c,'image_get',side_effect=c.CollectionError('TLS timeout')):
            with self.assertRaises(c.CollectionError):c.detail_images(ctx,c.BASE,'RWSCO8209')

    def test_regular_price_parse_failure_is_nonfatal(self):
        ctx=Context(['main_image','regular_price'])
        with patch.object(c,'search',return_value=[(c.BASE+'/goods/detail/432217746','RMDVM4382','https://example.com/thumb.jpg')]),patch.object(c,'soup_get',return_value=source('432217746')),patch.object(c,'image_get',return_value=shot()),patch.object(c,'options',return_value=([dict(color_name='BLACK',color_code='')],[])),patch.object(c,'read_price',side_effect=RuntimeError('parse error')):
            c.crawl(ctx)
        self.assertTrue(ctx.rows);self.assertIsNone(ctx.rows[0][0]['regular_price']);self.assertFalse(ctx.failures)

if __name__=='__main__':unittest.main()
