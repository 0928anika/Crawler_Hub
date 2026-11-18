import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('plastic_island', ROOT / 'brands/plastic_island_official.py')
crawler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crawler)


class Context:
    product_code = 'PS4AM520'

    def __init__(self):
        self.messages = []
        self.failures = []

    def log(self, message, level='INFO'):
        self.messages.append(message)

    def fail_item(self, message):
        self.failures.append(message)


class PlasticIslandTests(unittest.TestCase):
    def test_wash_only_placeholder_does_not_abort_accessory(self):
        ctx = Context()
        model = dict(contentsmap=dict(CONTENT='<img src="https://mall.theacdn.com/data/external/pla/s4/PS4AM520/item.jpg">'),
                     goodsSizeList=[dict(SIZETITLE='FF', SIZE1='0', SIZE2='0', SIZEGROUP='', WASHLABEL='손세탁7')],
                     goodsNotifyList=[dict(notifyname='제품소재', notifydata='폴리에스터100%'),
                                      dict(notifyname='세탁방법 및 취급시 주의사항', notifydata='손세탁')])
        html = crawler.detail(ctx, model, crawler.soup(''), 'PS4AM520')
        self.assertIn('폴리에스터100%', html)
        self.assertNotIn('실측사이즈', html)
        self.assertNotIn('세탁', html)
        self.assertTrue(any('실측표 생략' in message for message in ctx.messages))

    def test_real_measurements_remain_with_titles_and_zero_values(self):
        model = dict(contentsmap=dict(CONTENT='<img src="https://mall.theacdn.com/data/external/pla/y6/PY6WZ062/item.jpg">'),
                     goodsSizeList=[dict(SIZETITLE='S', SIZETITLENAMELIST=['총길이', '어깨'], SIZE1='83.5', SIZE2='0')])
        html = crawler.detail(Context(), model, crawler.soup('<div class="js-detailer-size-area"><img src="/img/sizeChart/A1.jpg"></div>'), 'PY6WZ062')
        self.assertIn('실측사이즈', html)
        self.assertIn('83.5', html)
        self.assertIn('<td>0</td>', html)
        self.assertIn('/img/sizeChart/A1.jpg', html)

    def test_reissue_keeps_official_old_model_photos_from_verified_content(self):
        old = 'https://www.theamall.com/external/pla/s1/PS1WC285/modeldetail_01.jpg'
        model = dict(contentsmap=dict(CONTENT='<img src="' + old + '"><img src="https://mall.theacdn.com/data/event/banner.jpg">'))
        html = crawler.detail(Context(), model, crawler.soup(''), 'PS4WC561')
        self.assertIn(old, html)
        self.assertNotIn('banner.jpg', html)

    def test_editor_product_photos_keep_order_and_trim_line_breaks(self):
        urls = ['https://mall.theacdn.com/data/editor/202511/de8af4c77b894cd68dbbb4a827f3496a.jpg',
                'https://mall.theacdn.com/data/editor/202510/707211c286c3489d929a074f3fde4154.jpg',
                'https://mall.theacdn.com/data/editor/202510/978978ea010147ec988c0b62be648d3a.jpg']
        model = dict(contentsmap=dict(CONTENT=''.join('<img src="' + url + '\r\n\r\n">' for url in urls),
                                      CONTENTDELIVERY='<img src="https://mall.theacdn.com/data/editor/202601/delivery.jpg">'))
        html = crawler.detail(Context(), model, crawler.soup(''), 'PR4CL581')
        images = crawler.soup(html).select('img[src]')
        self.assertEqual([node['src'] for node in images], urls)
        self.assertNotIn('delivery.jpg', html)

    def test_empty_search_failure_is_structured(self):
        from unittest.mock import patch
        ctx = Context()
        ctx.enabled = lambda key: True
        ctx.stage = lambda message: None
        with patch.object(crawler, 'discover', return_value=[]):
            with self.assertRaisesRegex(crawler.CrawlError, '공홈 검색 결과 0건'):
                crawler.crawl(ctx)
        self.assertEqual(len(ctx.failures), 1)
        self.assertIn('PS4AM520', ctx.failures[0])


if __name__ == '__main__':
    unittest.main()
