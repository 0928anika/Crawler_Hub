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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from crawler_sdk import CrawlContext

spec = importlib.util.spec_from_file_location('clizy_fi_dj_test', ROOT/'brands/clizy_fashionplus_fi_dj.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
FIXTURES = ROOT/'tests/fixtures/clizy_fi_dj'
META = json.loads((ROOT/'brands/clizy_fashionplus_fi_dj.json').read_text(encoding='utf-8'))
LINK = c.BASE+'/goods/detail/439090783'


def fixture(name):
    return (FIXTURES/name).read_text(encoding='utf-8')


class SellerTests(unittest.TestCase):
    def test_observed_return_address_and_sku_namespace(self):
        soup = BeautifulSoup(fixture('product.html'), 'html.parser')
        accepted, addresses, marker = c.seller_identity(soup)
        self.assertTrue(accepted)
        self.assertEqual(marker, '30989')
        self.assertEqual(addresses, ['대전광역시 동구 은어송로 72 패션아일랜드 1층 클리지'])

    def test_only_labelled_return_address_authorizes_seller(self):
        cases = [
            ('대전 패션아일랜드', True),
            ('대전광역시 동구 은어송로 72 패션아일랜드 1층 클리지', True),
            ('서울 패션아일랜드', False),
            ('대전광역시 다른 매장', False),
            ('', False),
        ]
        for address, accepted in cases:
            with self.subTest(address=address):
                soup = BeautifulSoup('<table><tr><th>반품지주소</th><td>'+address+'</td></tr></table>', 'html.parser')
                self.assertEqual(c.seller_identity(soup)[0], accepted)
        footer = BeautifulSoup('<footer>대전 패션아일랜드</footer><script type="application/ld+json">'
            '{"@type":"Product","sku":"30989_ABC"}</script>', 'html.parser')
        self.assertFalse(c.seller_identity(footer)[0])
        conflicting = BeautifulSoup('<table><tr><th>반품지주소</th><td>대전 패션아일랜드</td></tr>'
            '<tr><th>반품지주소</th><td>서울 다른 판매자</td></tr></table>', 'html.parser')
        self.assertFalse(c.seller_identity(conflicting)[0])

    def test_address_is_authority_not_undocumented_id(self):
        soup = BeautifulSoup(fixture('product.html').replace('30989_', '88888_'), 'html.parser')
        self.assertTrue(c.seller_identity(soup)[0])

    def test_real_sdk_all_originals_and_optional_fields(self):
        body = BeautifulSoup(fixture('body.html'), 'html.parser')
        options = fixture('options.json').encode()
        for detail_on, price_on, regular_on in itertools.product((False, True), repeat=3):
            with self.subTest(detail=detail_on, price=price_on, regular=regular_on), tempfile.TemporaryDirectory() as tmp:
                emitted, image_requests = [], []
                selected = ['main_image'] + (['detail_page'] if detail_on else []) + \
                    (['price'] if price_on else []) + (['regular_price'] if regular_on else [])
                ctx = CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,selected,
                    META['output_headers'],lambda:None,lambda kind,**data:emitted.append((kind,data)),
                    input_item={'품번':'CQ7WBL17'},mapping_mode='manual')
                def soup_get(ctx, url):
                    return body if url.endswith('/description-iframe') else BeautifulSoup(fixture('product.html'),'html.parser')
                def image_get(ctx, url):
                    image_requests.append(url)
                    if '/plgu30989_' in url:
                        raise c.MissingResource('fixture 404')
                    return Image.new('RGB',(1000,1000),'gray')
                calls = []
                reader = c.read_price
                def read_price(soup, regular=False):
                    calls.append(regular)
                    return reader(soup,regular)
                with patch.object(c,'search',return_value=[(LINK,'','https://unused/thumb.jpg')]), \
                     patch.object(c,'soup_get',side_effect=soup_get), \
                     patch.object(c,'fetch',return_value=options), \
                     patch.object(c,'image_get',side_effect=image_get), \
                     patch.object(c,'read_price',side_effect=read_price):
                    c.crawl(ctx)
                    ctx.finish()
                rows=[data for kind,data in emitted if kind=='row' and not data.get('header')]
                self.assertEqual(len(rows),9)
                self.assertEqual(len(image_requests),10)
                self.assertTrue(all('drive.google.com' not in u for u in image_requests))
                self.assertEqual(calls,([False] if price_on else [])+([True] if regular_on else []))
                row=dict(zip(ctx.headers,rows[0]['row']))
                self.assertEqual(row['상품 링크'],LINK)
                self.assertIn('다크 브라운',row['색상'])
                self.assertEqual(len(rows[0]['unmapped']['colors']),1)
                if detail_on:
                    self.assertEqual(row['상세페이지 HTML'].count('<img '),9)
                    self.assertIn('총장52cm',row['상세페이지 HTML'])
                    self.assertNotIn('drive.google.com',row['상세페이지 HTML'])
                    self.assertNotIn('반품지주소',row['상세페이지 HTML'])
                    self.assertNotIn('<table',row['상세페이지 HTML'])
                else:
                    self.assertFalse(row['상세페이지 HTML'])

    def test_other_seller_never_requests_options_body_or_images(self):
        class Context:
            product_code='CQ7WBL17'
            enabled=lambda self,key:key=='main_image'
            checkpoint=lambda self:None
            stage=lambda self,*args:None
            log=lambda self,*args:None
            fail_item=lambda self,*args:None
        soup=BeautifulSoup(fixture('product.html').replace('대전광역시','서울특별시'),'html.parser')
        with patch.object(c,'search',return_value=[(LINK,'','https://unused/thumb.jpg')]), \
             patch.object(c,'soup_get',return_value=soup) as page, \
             patch.object(c,'options') as opts,patch.object(c,'image_get') as images:
            with self.assertRaises(c.CollectionError):
                c.crawl(Context())
            page.assert_called_once_with(unittest.mock.ANY,LINK)
            opts.assert_not_called()
            images.assert_not_called()


if __name__=='__main__':
    unittest.main()
