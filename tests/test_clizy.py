"""Verify Clizy against the real Hub SDK, without network or personal state."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from crawler_sdk import CrawlContext

spec = importlib.util.spec_from_file_location('clizy_crawler_test', ROOT / 'brands/clizy_official.py')
clizy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clizy)
META = json.loads((ROOT / 'brands/clizy_official.json').read_text(encoding='utf-8'))
LINK = clizy.BASE + '/goods/detail/123456789'


def product(retail='97,000원'):
    gallery = ''.join('<button class="btn_thumbnail"><i data-preload="{ &apos;_src&apos;: '
        '&apos;https://public.example/%s.jpg&apos; }"></i></button>' % i for i in range(12))
    return BeautifulSoup('<div class="m__product-info"><p class="text_product">골지 니트 (TEST001)</p></div>'
        '<dl class="discount-detail__row"><dt>희망소비자가 (택가)</dt><dd>%s</dd></dl>'
        '<dl class="discount-detail__row"><dt>판매가 (위탁판매자 공급가)</dt><dd>25,000원</dd></dl>'
        '<div class="m_prodetail-head-thumbnail">%s</div>' % (retail, gallery), 'html.parser')


class ClizyTests(unittest.TestCase):
    def test_required_fields_are_enabled_in_registered_definition(self):
        for field in ('product_code', 'product_name', 'color', 'product_url'):
            self.assertTrue(META['fields'][field]['supported'], field)

    def test_sdk_price_choices_missing_regular_and_all_photo_candidates(self):
        options = [{'_name': '니트', 'options': [{'_name': 'BEIGE FREE', '_stock': 0},
                                               {'_name': 'IVORY FREE', '_stock': 1}]}]
        for price_on in (False, True):
            for retail_on in (False, True):
                for retail in ('97,000원', '0원', '', '가격문의'):
                    with self.subTest(price=price_on, regular=retail_on, value=retail), tempfile.TemporaryDirectory() as tmp:
                        emitted = []
                        fields = ['main_image', 'size'] + (['price'] if price_on else []) + (['regular_price'] if retail_on else [])
                        ctx = CrawlContext(Path(tmp)/'input.xlsx', Path(tmp)/'result.xlsx', 0,
                            fields, META['output_headers'], lambda: None,
                            lambda kind, **data: emitted.append((kind, data)),
                            input_item={'품번': 'TEST001'}, mapping_mode='manual')
                        with patch.object(clizy, 'search', return_value=[(LINK, '', 'https://public.example/thumb.jpg')]), \
                             patch.object(clizy, 'soup_get', return_value=product(retail)), \
                             patch.object(clizy, 'fetch', return_value=json.dumps(options).encode()), \
                             patch.object(clizy, 'image_get', return_value=Image.new('RGB', (800,1072), 'gray')):
                            clizy.crawl(ctx)
                            ctx.finish()
                        rows = [data for kind, data in emitted if kind == 'row' and not data.get('header')]
                        self.assertEqual(len(rows), 12)
                        row = dict(zip(ctx.headers, rows[0]['row']))
                        self.assertEqual(row['상품 링크'], LINK)
                        self.assertEqual(row['상품명'], '골지 니트')
                        self.assertEqual(row['사이즈'], 'FREE')
                        self.assertEqual(row.get('판매가'), 25000 if price_on else None)
                        expected = 97000 if retail_on and retail == '97,000원' else None
                        self.assertEqual(row.get('정상가'), expected)
                        self.assertEqual(len(rows[0]['unmapped']['colors']), 2)
                        self.assertFalse(ctx._failures)

    def test_seller_thumbnail_never_opens_product(self):
        class Context:
            product_code = 'TEST001'
            enabled = lambda self, field: field == 'main_image'
            checkpoint = lambda self: None
            stage = lambda self, message: None
            log = lambda self, *args: None
            fail_item = lambda self, message: None
        with patch.object(clizy, 'search', return_value=[(LINK, '', 'https://public.example/thumb.jpg')]), \
             patch.object(clizy, 'image_get', return_value=Image.new('RGB', (400,536))), \
             patch.object(clizy, 'studio_background', return_value=(False, '매장 사진')), \
             patch.object(clizy, 'soup_get') as forbidden:
            with self.assertRaises(clizy.CollectionError):
                clizy.crawl(Context())
            forbidden.assert_not_called()

    def test_product_body_keeps_original_and_excludes_notice(self):
        body = BeautifulSoup('<div class="m_prodetail-desc"><div class="mm_pinchzoom-wrapper">'
            '<img src="https://fash4you2.speedgabia.com/clizy/N7/JPG/TEST001.jpg">'
            '<img src="https://fash4you.speedgabia.com/notice/2021/BNP-delivery.jpg">'
            '</div></div>', 'html.parser')
        class Context:
            checkpoint = lambda self: None
            log = lambda self, *args: None
        with patch.object(clizy, 'soup_get', return_value=body):
            self.assertEqual(clizy.detail_images(Context(), LINK, 'TEST001'),
                ['https://fash4you2.speedgabia.com/clizy/N7/JPG/TEST001.jpg'])


if __name__ == '__main__':
    unittest.main()
