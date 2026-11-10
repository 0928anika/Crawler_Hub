import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'brands')]
import whoau_official as c


class Context:
    def __init__(self, code='WHMAG4V22U', fields=()):
        self.product_code, self.fields = code, fields
        self.rows, self.errors, self.unmapped = [], [], []
    def checkpoint(self): return None
    def enabled(self, key): return key in self.fields
    def log(self, *args): return None
    def stage(self, message): return None
    def fail_item(self, message): self.errors.append(message)
    def emit_result(self, row): self.rows.append(row)
    def emit_unmapped(self, record, colors, url, shared_detail_html=''): self.unmapped.append((record,colors,url))


class WhoauTests(unittest.TestCase):
    def setUp(self):
        self.sources = {n: (ROOT / 'tests/fixtures/whoau' / (n + '.html')).read_text(encoding='utf-8') for n in ('5696','5692')}
        self.doc = c.parse(self.sources['5696'])

    def test_actual_color_option_links_not_shared_jsonld_images(self):
        colors = c.colors(self.doc, True)
        self.assertEqual([x['color_code'] for x in colors], ['33','60','76'])
        self.assertFalse(colors[1]['image'])
        self.assertNotEqual(colors[0]['image'], colors[2]['image'])
        other = c.colors(c.parse(self.sources['5692']), True)
        self.assertTrue(other[1]['image'])
        self.assertNotEqual(other[1]['image'], other[0]['image'])

    def test_soldout_xl_is_preserved(self):
        self.assertEqual(c.sizes(self.doc, '(33)Oatmeal Melange'), 'S(090),M(095),L(100),XL(105)')

    def test_explicit_prices_and_missing_consumer_price(self):
        ctx = Context()
        self.assertEqual(c.price(ctx,self.doc),53910)
        self.assertEqual(c.price(ctx,self.doc,True),59900)
        with patch.object(c,'field',return_value='가격문의'):
            self.assertIsNone(c.price(ctx,self.doc,True))

    def test_no_optional_fields_skip_image_size_price_and_detail_work(self):
        ctx = Context()
        links = [c.SITE + '/product/test/' + n + '/' for n in self.sources]
        with patch.object(c,'search',return_value=links), patch.object(c,'fetch',side_effect=lambda ctx,url:(self.sources[url.rstrip('/').split('/')[-1]],url)), patch.object(c,'image_url') as image, patch.object(c,'details') as details, patch.object(c,'sizes') as sizes, patch.object(c,'price') as price:
            c.crawl(ctx)
            for helper in (image,details,sizes,price): helper.assert_not_called()
        self.assertEqual(len(ctx.rows),3)
        self.assertTrue(all(r['product_url'].startswith(c.SITE + '/product/') for r in ctx.rows))
        self.assertFalse(ctx.errors)

    def test_detail_images_preserve_size_and_notice_without_tables(self):
        urls = c.details(self.doc,c.SITE)
        self.assertEqual(len(urls),3)
        self.assertTrue(any('GOSI' in u for u in urls))
        self.assertTrue(all('item-imghub.eland.co.kr' in u for u in urls))
        with patch.object(c,'detail_image',return_value={'status':'accessible'}):
            html = c.detail(Context(),urls,{})
        self.assertEqual(html.count('<img'),3)
        self.assertNotIn('<table',html)

    def test_image_hosts_and_korean_path_encoding(self):
        self.assertFalse(c.image_url('https://outside.example/image.jpg'))
        self.assertFalse(c.image_url('https://cafe24.poxo.com/ec01/other/image.jpg'))
        self.assertIn('%EC%83%81', c.image_url('https://item-imghub.eland.co.kr/상세.jpg'))

    def test_alphabetic_color_code_and_unlinked_model_gallery(self):
        doc=c.parse((ROOT/'tests/fixtures/whoau/5600.html').read_text(encoding='utf-8'))
        self.assertEqual([v['color_code'] for v in c.colors(doc,True)],['60','AC'])
        linked={v['image'] for v in c.colors(doc,True)}
        all_urls=c.gallery_urls(doc,c.SITE)
        self.assertEqual(len(all_urls),7)
        self.assertEqual(len(set(all_urls)-linked),5)

    def test_large_detail_has_separate_limit_and_cache(self):
        ctx=Context();url='https://item-imghub.eland.co.kr/large.jpg'
        cache={url:{'status':'inaccessible','reasons':['응답 최대 용량 초과']}}
        with patch.object(c,'check_public_image',return_value={'status':'accessible','data':b'image'}) as check:
            self.assertTrue(c.detail_image(ctx,url,cache))
            self.assertEqual(check.call_args.kwargs['max_bytes'],64*1024*1024)
        self.assertEqual(cache[url]['status'],'inaccessible')

    def test_fully_unlinked_product_emits_manual_photos_without_guessed_rows(self):
        doc=c.parse((ROOT/'tests/fixtures/whoau/5600.html').read_text(encoding='utf-8'))
        for node in doc.select('li[link_image]'):node['link_image']=''
        ctx=Context(code='WHKAG4T35U',fields=('main_image',))
        url=c.SITE+'/product/henley/5600/'
        with patch.object(c,'search',return_value=[url]),patch.object(c,'fetch',return_value=(str(doc),url)),patch.object(c,'prefetch'),patch.object(c,'manual_photos',return_value=['https://example.com/model.jpg']),patch.object(c,'gallery') as gallery:
            c.crawl(ctx)
            gallery.assert_not_called()
        self.assertEqual(ctx.rows,[])
        self.assertEqual(len(ctx.unmapped),1)
        self.assertEqual([v['color_code'] for v in ctx.unmapped[0][1]],['60','AC'])

    def test_official_photo_alias_is_not_sent_to_manual_mapping(self):
        def source(color):
            data=BytesIO();Image.new('RGB',(750,1000),color).save(data,'PNG')
            return dict(status='accessible',data=data.getvalue(),format='PNG',width=750,height=1000)
        official=source('white');unknown=source('gray')
        cache={'linked':official,'alias':official,'unknown':unknown}
        self.assertEqual(c.manual_photos(Context(),['linked','alias','unknown'],{'linked'},cache),['unknown'])


if __name__ == '__main__':
    unittest.main()
