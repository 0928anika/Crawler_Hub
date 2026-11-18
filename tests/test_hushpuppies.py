import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import hushpuppies_official as c

class Context:
    product_code='3FS6007'
    allow_automatic_results=False
    def __init__(self):self.rows=[];self.failures=[]
    def enabled(self,key):return key in ('size','price','regular_price','main_image','additional_images','detail_page')
    def checkpoint(self):pass
    def stage(self,*a):pass
    def log(self,*a):pass
    def fail_item(self,message):self.failures.append(message)
    def emit_result(self,row):self.rows.append(row)
    def emit_unmapped(self,*args,**kwargs):raise AssertionError('single colour should map automatically')

class Tests(unittest.TestCase):
    def setUp(self):
        self.source=(ROOT/'tests/fixtures/hushpuppies/3FS6007.html').read_text(encoding='utf-8');self.doc=c.soup(self.source)

    def test_exact_identity_name_and_independent_prices(self):
        self.assertEqual(c.identity(self.doc,'3FS6007')[0],'여성 소가죽 플랫폼 털 부츠')
        self.assertIsNone(c.identity(self.doc,'3FS60070'))
        self.assertEqual(c.amount(c.field(self.doc,'소비자가')),215000)
        self.assertEqual(c.palette(self.source)[0]['prices'][0],86000)

    def test_sizes_include_every_soldout_combination_without_guessed_color_code(self):
        colors=c.palette(self.source)
        self.assertEqual(len(colors),1);self.assertEqual(colors[0]['color_name'],'블랙');self.assertEqual(colors[0]['color_code'],'')
        self.assertEqual(colors[0]['sizes'],['225','230','235','240','245','250'])
        self.assertTrue(any(row.get('is_auto_soldout')=='T' for row in c.stock_rows(self.source)))

    def test_attached_footer_and_notices_are_excluded_before_request(self):
        urls=c.detail_urls(self.doc,c.SITE,'3FS6007')
        self.assertEqual(len(urls),5)
        self.assertFalse(any('bottom.jpg' in url or '/notice/' in url for url in urls))
        with patch.object(c,'checked',return_value={}) as checked:
            result=c.detail(Context(),self.doc,c.SITE,'3FS6007',{})
        self.assertEqual(checked.call_count,5);self.assertEqual(result.count('<img'),5)
        self.assertNotIn('<table',result);self.assertNotIn('bottom.jpg',result)

    def test_other_product_and_other_account_cannot_enter_detail(self):
        area=self.doc.select_one('#prdDetail')
        for url in ['https://gi.esmplus.com/OTHER/products/3FS6007.jpg','https://gi.esmplus.com/hpk1209/products/3FS60070.jpg','https://evil.example/hpk1209/products/3FS6007.jpg']:
            area.append(self.doc.new_tag('img',src=url))
        self.assertEqual(len(c.detail_urls(self.doc,c.SITE,'3FS6007')),5)

    def test_gallery_urls_are_declared_not_guessed(self):
        urls=c.gallery_urls(self.doc,c.SITE)
        self.assertEqual(len(urls),4)
        self.assertTrue(any('/extra/small/' in url for url in urls))
        self.assertFalse(any('/extra/big/' in url for url in urls))

    def test_five_slots_reuse_valid_photos(self):
        for count in [1,2,4,6]:
            urls=['https://hushpuppies.co.kr/'+str(i)+'.jpg' for i in range(count)]
            extra=c.extra_slots(urls).split(',');self.assertEqual(len(extra),5)
            self.assertTrue(all(url in urls for url in extra))

    def test_single_color_crawl_and_registration(self):
        ctx=Context();url=c.SITE+'/product/detail.html?product_no=1397'
        with patch.object(c,'search',return_value=[url]),patch.object(c,'fetch',return_value=self.source),patch.object(c,'gallery',return_value=['https://hushpuppies.co.kr/photo.jpg']),patch.object(c,'checked',return_value={}):c.crawl(ctx)
        row=ctx.rows[0];self.assertEqual(row['size'],'225,230,235,240,245,250')
        self.assertEqual(len(row['additional_images'].split(',')),5);self.assertFalse(ctx.failures)
        from registry import get_brand
        self.assertTrue(get_brand('hushpuppies_official')['available'])

    def test_multiple_colors_keep_own_sizes_without_guessing_photo_color(self):
        ctx=Context();raw=[];ctx.emit_unmapped=lambda record,colors,url,**kw:raw.append((record,colors,url))
        colors=[dict(color_name='블랙',color_code='',sizes=['225','230'],prices=[86000]),dict(color_name='브라운',color_code='',sizes=['240','250'],prices=[86000])]
        with patch.object(c,'search',return_value=[c.SITE+'/product/detail.html?product_no=1397']),patch.object(c,'fetch',return_value=self.source),patch.object(c,'palette',return_value=colors),patch.object(c,'gallery',return_value=['https://hushpuppies.co.kr/photo.jpg']),patch.object(c,'checked',return_value={}):c.crawl(ctx)
        self.assertFalse(ctx.rows);self.assertEqual(len(raw),1)
        self.assertEqual([color['size'] for color in raw[0][1]],['225,230','240,250'])

if __name__=='__main__':unittest.main()
