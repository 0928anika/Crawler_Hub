import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / p) for p in ('app', 'brands')]
import pat_official_naver_search as c

class Context:
    def checkpoint(self): pass
    def stage(self, *args): pass
    def log(self, *args): pass

class Response:
    headers = __import__('email.message', fromlist=['Message']).Message()
    def __init__(self, text): self.text = text
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def geturl(self): return 'https://search.naver.com/search.naver?query=1k65434'
    def read(self, size): return self.text.encode()

class Tests(unittest.TestCase):
    def test_search_failure_reaches_user_failure_event(self):
        from unittest.mock import Mock
        ctx = Mock(product_code='1J51202')
        reason = '네이버 검색 결과에서 PAT 공홈 상품 링크를 찾지 못했습니다: 1J51202 / 공홈 상품 부재를 의미하지 않습니다.'
        with patch.object(c, 'search', side_effect=ValueError(reason)):
            with self.assertRaisesRegex(ValueError, '1J51202'):
                c.crawl(ctx)
        ctx.fail_item.assert_called_once_with(reason)

    def test_wrong_search_hit_retries_and_reports_actual_code(self):
        from unittest.mock import Mock
        ctx = Mock()
        with patch.object(c, 'search', side_effect=[{'1': c.SITE + '/goods/goods_view.php?goodsNo=1'}, {}]), patch.object(c, 'fetch', return_value=('<div class="item_detail_tit"><span class="prd-goodscd">1K83401</span><span class="prd-brand">PAT</span></div>', c.SITE)):
            with self.assertRaisesRegex(ValueError, '요청 QK81631 / 확인된 품번 1K83401'):
                c.verified_products(ctx, 'QK81631')

    def test_restricted_search_can_recover_exact_product(self):
        from unittest.mock import Mock
        pages = ['<div class="item_detail_tit"><span class="prd-goodscd">OTHER</span><span class="prd-brand">PAT</span></div>', '<div class="item_detail_tit"><span class="prd-goodscd">QK81631</span><span class="prd-brand">PAT</span></div>']
        with patch.object(c, 'search', side_effect=[{'1': c.SITE}, {'2': c.SITE}]), patch.object(c, 'fetch', side_effect=[(x, c.SITE) for x in pages]):
            result = c.verified_products(Mock(), 'QK81631')
        self.assertEqual(result[0][0], '2')

    def test_official_links_only_and_deduplicated(self):
        source = ('https://www.dlm1947.com/goods/goods_view.php?goodsNo=123&amp;x=1 '
                  'https://dlm1947.com/goods/goods_view.php?goodsNo=123 '
                  'https://evil.example/goods/goods_view.php?goodsNo=456 '
                  'https://www.dlm1947.com.evil.example/goods/goods_view.php?goodsNo=789')
        self.assertEqual(c.search_links(source), {'123': c.SITE + '/goods/goods_view.php?goodsNo=123'})
    def test_encoded_redirect_target(self):
        self.assertEqual(list(c.search_links('https%3A%2F%2Fwww.dlm1947.com%2Fgoods%2Fgoods_view.php%3FgoodsNo%3D123')), ['123'])
    def test_direct_naver_search_without_shop_search(self):
        with patch.object(c, 'urlopen', return_value=Response('<a href="https://www.dlm1947.com/goods/goods_view.php?goodsNo=123">상품</a>')) as request:
            self.assertIn('123', c.search(Context(), '1K65434'))
        self.assertIn('search.naver.com', request.call_args.args[0].full_url)
        self.assertIn('query=1k65434', request.call_args.args[0].full_url)
    def test_access_failure_not_missing_product(self):
        with patch.object(c, 'urlopen', side_effect=HTTPError('url', 429, 'limited', {}, None)):
            with self.assertRaisesRegex(ValueError, '상품 없음이 아닙니다'):
                c.search(Context(), '1K65434')
    def test_blocked_and_empty_results_differ(self):
        for source, reason in [('<title>접근 제한</title>자동입력 방지', '접근 제한'), ('<title>1K : 네이버 검색</title>', '공홈 상품 링크를 찾지 못했습니다')]:
            with patch.object(c, 'urlopen', return_value=Response(source)):
                with self.assertRaisesRegex(ValueError, reason):
                    c.search(Context(), '1K65434')

if __name__ == '__main__': unittest.main()
