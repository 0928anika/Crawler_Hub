import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import redface_fashionplus as c
FIX=ROOT/'tests/fixtures/redface_fashionplus'

class Context:
    product_code='REEFHAW24500'
    def __init__(self):self.rows=[];self.errors=[];self.logs=[]
    def checkpoint(self):pass
    def stage(self,*a):pass
    def log(self,*a):self.logs.append(a)
    def enabled(self,k):return k in ('main_image','additional_images','detail_page','size','price','regular_price')
    def fail_item(self,e):self.errors.append(e)
    def emit_unmapped(self,*a,**kw):self.rows.append((a,kw))

def source(n,kind):return c.BeautifulSoup((FIX/(n+'_'+kind+'.html')).read_bytes(),'html.parser')
def photo():
    image=Image.new('RGB',(800,1000),'#eeeeee')
    ImageDraw.Draw(image).ellipse((200,200,600,800),fill='black')
    return image
def jpeg(size):
    stream=io.BytesIO();Image.new('RGB',size,'white').save(stream,format='JPEG');return stream.getvalue()

class Tests(unittest.TestCase):
    def test_name_removes_all_brackets_and_shop_but_preserves_product(self):
        self.assertEqual(c.clean_name('[레드페이스 본사] [특가] 대전점 여성 웜부클2우먼햇 REEFHAW24500_EPT','REEFHAW24500'),'여성 웜부클2우먼햇')
        self.assertEqual(c.clean_name('레드페이스 강남아울렛 이중점퍼 REEFHAW24500','REEFHAW24500'),'이중점퍼')

    def test_uniform_background_passes_and_landscape_fails(self):
        self.assertTrue(c.studio_background(photo())[0])
        image=Image.new('RGB',(800,1000));draw=ImageDraw.Draw(image)
        for y in range(0,1000,10):draw.rectangle((0,y,800,y+9),fill=('white' if y%20 else 'black'))
        self.assertFalse(c.studio_background(image)[0])

    def test_long_body_only_no_shipping_fetch(self):
        urls=[]
        def fetch(ctx,url,limit):urls.append(url);return jpeg((747,6895))
        with patch.object(c,'soup_get',return_value=source('425911524','detail')),patch.object(c,'fetch',side_effect=fetch):
            body=c.detail_images(Context(),'https://www.fashionplus.co.kr/goods/detail/425911524','REEFHAW24500')
        self.assertEqual(len(body),1);self.assertEqual(urls,body)
        self.assertTrue(body[0].endswith('/REEFHAW24500_0.jpg'))

    def test_short_photos_never_substitute_for_long_detail(self):
        with patch.object(c,'soup_get',return_value=source('443139434','detail')),patch.object(c,'fetch',return_value=jpeg((800,1000))):
            with self.assertRaises(c.MissingDetail):c.detail_images(Context(),'link','REEFHAW24500')

    def test_chooses_one_model_body_among_other_images(self):
        doc=c.BeautifulSoup('<div class="m_prodetail-desc"><div class="mm_pinchzoom-wrapper"><img src="https://example.com/short.jpg"><img src="https://example.com/REEFHAW24500.jpg"><img src="https://example.com/other.jpg"></div></div>','html.parser')
        def fetch(ctx,url,limit):return jpeg((800,1000 if 'short' in url else 4000 if 'REEF' in url else 6000))
        with patch.object(c,'soup_get',return_value=doc),patch.object(c,'fetch',side_effect=fetch):
            self.assertEqual(c.detail_images(Context(),'link','REEFHAW24500'),['https://example.com/REEFHAW24500.jpg'])

    def test_options_and_independent_prices_from_real_page(self):
        doc=source('425911524','product');ctx=Context()
        with patch.object(c,'fetch',return_value=(FIX/'425911524_options.json').read_bytes()):
            colors,sizes=c.options(ctx,'link',True,doc=doc,code=ctx.product_code)
        self.assertTrue(colors);self.assertTrue(sizes)
        self.assertIsNotNone(c.read_price(doc));self.assertIsNotNone(c.read_price(doc,True))

    def test_all_colors_share_one_body_from_one_link_with_fallback(self):
        ctx=Context();first=c.BASE+'/goods/detail/443139434';second=c.BASE+'/goods/detail/425911524'
        def doc(ctx,url):return source(url.rsplit('/',1)[-1],'product')
        def detail(ctx,url,code):
            if url==first:raise c.MissingDetail('No long body')
            return ['https://theredface.diskn.com/Online/24FW/page/REEFHAW24500_0.jpg']
        with patch.object(c,'search',return_value=[(first,'REEFHAW24500','https://example.com/1.jpg'),(second,'REEFHAW24500','https://example.com/2.jpg')]),patch.object(c,'soup_get',side_effect=doc),patch.object(c,'image_get',return_value=photo()),patch.object(c,'options',return_value=([dict(color_name='BLACK',color_code='')],['057'])),patch.object(c,'detail_images',side_effect=detail) as body:
            c.crawl(ctx)
        self.assertEqual(body.call_count,2);self.assertTrue(ctx.rows);self.assertFalse(ctx.errors)
        self.assertTrue(all(kw['shared_detail_html'].count('<img ')==1 for _,kw in ctx.rows))
        self.assertEqual(len({kw['shared_detail_html'] for _,kw in ctx.rows}),1)

    def test_closed_image_host_does_not_discard_later_valid_body(self):
        doc=c.BeautifulSoup('<div class="m_prodetail-desc"><div class="mm_pinchzoom-wrapper"><img src="https://dead.example/intro_23SS.jpg"><img src="https://dead.example/product.jpg"><img src="https://example.com/REEFHAW24500.jpg"></div></div>','html.parser')
        requested=[]
        def fetch(ctx,url,limit):
            requested.append(url)
            if 'dead.example' in url:raise c.CollectionError('getaddrinfo failed')
            return jpeg((747,6895))
        with patch.object(c,'soup_get',return_value=doc),patch.object(c,'fetch',side_effect=fetch):
            self.assertEqual(c.detail_images(Context(),'link','REEFHAW24500'),['https://example.com/REEFHAW24500.jpg'])
        self.assertFalse(any('intro_' in u for u in requested))

    def test_missing_body_warns_and_keeps_valid_photos_options(self):
        ctx=Context();link=c.BASE+'/goods/detail/425911524'
        with patch.object(c,'search',return_value=[(link,'REEFHAW24500','https://example.com/1.jpg')]),patch.object(c,'soup_get',return_value=source('425911524','product')),patch.object(c,'image_get',return_value=photo()),patch.object(c,'options',return_value=([dict(color_name='BLACK',color_code='')],['057'])),patch.object(c,'detail_images',side_effect=c.MissingDetail('No long body')):
            c.crawl(ctx)
        self.assertTrue(ctx.rows);self.assertFalse(ctx.errors)
        self.assertTrue(all(not kw['shared_detail_html'] and args[0]['size']=='057' for args,kw in ctx.rows))
        self.assertTrue(any('상세페이지 미수집' in msg and level=='WARNING' for msg,level in ctx.logs))

    def test_empty_gallery_of_one_seller_does_not_fail_other_seller(self):
        ctx=Context();first=c.BASE+'/goods/detail/443139434';second=c.BASE+'/goods/detail/425911524'
        def doc(ctx,url):
            value=source(url.rsplit('/',1)[-1],'product')
            if url==first:
                for node in value.select('.m_prodetail-head-thumbnail'):node.decompose()
            return value
        with patch.object(c,'search',return_value=[(first,'REEFHAW24500','https://example.com/1.jpg'),(second,'REEFHAW24500','https://example.com/2.jpg')]),patch.object(c,'soup_get',side_effect=doc),patch.object(c,'image_get',return_value=photo()),patch.object(c,'options',return_value=([dict(color_name='BLACK',color_code='')],['057'])),patch.object(c,'detail_images',return_value=['https://example.com/REEFHAW24500.jpg']):
            c.crawl(ctx)
        self.assertTrue(ctx.rows);self.assertFalse(ctx.errors)
        self.assertTrue(all(args[0]['product_url']==second for args,kw in ctx.rows))

    def test_no_usable_seller_still_fails_instead_of_emitting_empty_success(self):
        ctx=Context();link=c.BASE+'/goods/detail/425911524'
        doc=source('425911524','product')
        for node in doc.select('.m_prodetail-head-thumbnail'):node.decompose()
        with patch.object(c,'search',return_value=[(link,'REEFHAW24500','https://example.com/1.jpg')]),patch.object(c,'soup_get',return_value=doc),patch.object(c,'image_get',return_value=photo()),patch.object(c,'options',return_value=([dict(color_name='BLACK',color_code='')],['057'])):
            with self.assertRaisesRegex(c.CollectionError,'유효 사진 없음'):c.crawl(ctx)
        self.assertFalse(ctx.rows);self.assertTrue(ctx.errors)

if __name__=='__main__':unittest.main()
