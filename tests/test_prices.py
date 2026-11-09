import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from regular_price import amount, extract_regular_price, collect_regular_price, merge_regular_prices
from schema import FIELDS, field_for, output_headers, preview, review_result
from crawler_sdk import CrawlContext
from openpyxl import load_workbook


class Context:
    def __init__(self, selected=True):
        self.selected = selected
        self.logs = []
    def enabled(self, field):
        return self.selected if field == 'regular_price' else False
    def checkpoint(self):
        pass
    def log(self, message, level):
        self.logs.append((message, level))
    def fail_item(self, message):
        raise AssertionError('Optional normal price must never fail an item: ' + message)


class PriceTests(unittest.TestCase):
    def test_labels_and_legacy_aliases(self):
        self.assertEqual(FIELDS['price'], '판매가')
        self.assertEqual(field_for('가격'), 'price')
        self.assertEqual(field_for('판매가'), 'price')
        self.assertEqual(field_for('정상가'), 'regular_price')
        self.assertEqual(field_for('regular_price'), 'regular_price')

    def test_parse_won_positive_integer_only(self):
        self.assertEqual(amount('398,000원'), 398000)
        self.assertEqual(amount('398000.0'), 398000)
        for value in (None, True, 0, '0', '-100', '1~1000', '1,000달러', '12.34', 'NaN'):
            with self.subTest(value=value):
                self.assertIsNone(amount(value))

    def test_observed_cafe24_retail_field_and_no_sale_fallback(self):
        self.assertEqual(extract_regular_price('<span id="span_product_price_custom"><strike>469,000원</strike></span>', 'cafe24'), 469000)
        self.assertEqual(extract_regular_price('<script>var product_custom = "89000";</script>', 'cafe24'), 89000)
        self.assertIsNone(extract_regular_price('<span id="span_product_price_text">79,000원</span>', 'cafe24'))
        self.assertIsNone(extract_regular_price('<span id="span_product_price_custom">0원</span>', 'cafe24'))

    def test_foreign_currency_not_assumed_won(self):
        html = '<meta property="product:price:currency" content="USD"><span id="span_product_price_custom">100</span>'
        self.assertIsNone(extract_regular_price(html, 'cafe24'))

    def test_daehyun_consumer_and_discount_price_are_distinct(self):
        html = '<div class="info_price"><span class="prc consumer">459,000원</span><span class="prc dis_price">321,300원</span></div>'
        self.assertEqual(extract_regular_price(html, 'daehyun'),459000)

    def test_beentrill_verified_custom_price_attribute(self):
        html = '<div class="infoArea"><span class="salePrice">KRW 61,600</span><span class="prdPrice strike">KRW 88,000</span><div id="ec-product-price-info" ec-data-custom="KRW 88,000" ec-data-price="88000"></div></div>'
        self.assertEqual(extract_regular_price(html,'beentrill'),88000)
        self.assertIsNone(extract_regular_price('<span class="salePrice">KRW 61,600</span>','beentrill'))

    def test_observed_eland_field_is_not_sellprice(self):
        self.assertEqual(extract_regular_price(dict(orgSellprice=39900, sellprice=23940), 'eland'), 39900)
        self.assertIsNone(extract_regular_price(dict(sellprice=23940), 'eland'))

    def test_observed_babathe_original_and_sale_are_distinct(self):
        self.assertEqual(extract_regular_price('{orgAmt : 518000 // 원본가격\n, saleAmt:233100}', 'babathe'), 518000)
        self.assertIsNone(extract_regular_price('{saleAmt:233100}', 'babathe'))

    def test_observed_style24_original_and_sale_are_distinct(self):
        html = '<div class="price_blk"><span class="sale_price"><em>7,200</em>원</span><del class="org_price"><em>59,000</em>원</del></div>'
        self.assertEqual(extract_regular_price(html, 'style24'), 59000)

    def test_lynx_normal_and_regular_price_are_actually_selling_prices(self):
        html = '<input id="regular_price" value="339,000"><div class="goodsPrice"><span class="normal">339,000원</span><span class="consumer">718,000 원</span></div>'
        self.assertEqual(extract_regular_price(html, 'lynx'), 718000)
        self.assertIsNone(extract_regular_price('<input id="regular_price" value="339,000">', 'lynx'))

    def test_not_selected_never_parses_or_emits(self):
        record = {}
        with patch('regular_price.extract_regular_price', side_effect=AssertionError('should not parse')):
            collect_regular_price(Context(False), record, object(), 'cafe24')
        self.assertEqual(record, {})

    def test_missing_malformed_or_unsupported_never_fails(self):
        for source, profile in [('<html></html>', 'cafe24'), ('<span id="span_product_price_custom">문의</span>', 'cafe24'), (None, 'unsupported')]:
            with self.subTest(profile=profile):
                ctx, record = Context(), {}
                collect_regular_price(ctx, record, source, profile)
                self.assertEqual(record, dict(regular_price=None))
                self.assertTrue(ctx.logs)

    def test_unexpected_optional_parser_error_does_not_fail(self):
        ctx, record = Context(), {}
        with patch('regular_price.extract_regular_price', side_effect=RuntimeError('site changed')):
            collect_regular_price(ctx, record, '', 'cafe24')
        self.assertIsNone(record['regular_price'])

    def test_checkpoint_errors_are_not_swallowed(self):
        ctx = Context()
        with patch.object(ctx, 'checkpoint', side_effect=RuntimeError('stopped')):
            with self.assertRaisesRegex(RuntimeError, 'stopped'):
                collect_regular_price(ctx, {}, '', 'cafe24')

    def test_different_or_missing_prices_are_not_arbitrarily_combined(self):
        for records in ([{'regular_price':100}, {'regular_price':200}], [{'regular_price':100}, {}], []):
            ctx, output = Context(), {}
            merge_regular_prices(ctx, output, records)
            self.assertIsNone(output['regular_price'])
        output = {}
        merge_regular_prices(Context(), output, [{'regular_price':100}, {'regular_price':100}])
        self.assertEqual(output['regular_price'], 100)

    def test_optional_price_columns_are_independent(self):
        headers = ['품번', '상품명', '색상', '상품 링크', '판매가', '정상가']
        self.assertNotIn('정상가', output_headers(headers, ['price']))
        self.assertNotIn('판매가', output_headers(headers, ['regular_price']))
        self.assertIn('정상가', output_headers(headers, ['regular_price']))

    def test_sdk_missing_retail_still_finishes_and_exports_empty_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.xlsx'
            headers = ['품번','상품명','색상','상품 링크','판매가','정상가']
            emitted = []
            ctx = CrawlContext('unused', path, 0, ['price','regular_price'], headers, lambda:None,
                lambda kind, **data: emitted.append((kind,data)), input_item={'품번':'ABC'})
            record = dict(product_code='ABC', product_name='상품', color='검정', product_url='https://example.com/product', price=100)
            collect_regular_price(ctx, record, '<html></html>', 'cafe24')
            ctx.emit_result(record)
            ctx.finish()
            wb = load_workbook(path, read_only=True)
            try:
                rows = list(wb.active.values)
                self.assertEqual(rows[1][-2:], (100,None))
                item = preview(list(rows[0]), list(rows[1]), ['price','regular_price'])
                self.assertEqual(review_result(item, ['price','regular_price']), ('normal',[]))
            finally:
                wb.close()

    def test_all_23_ids_have_selection_guarded_collector_and_valid_metadata(self):
        files = sorted((ROOT/'brands').glob('*.json'))
        self.assertEqual(len(files), 23)
        unsupported = []
        for path in files:
            meta = json.loads(path.read_bytes())
            code = (ROOT/'brands'/meta['script']).read_bytes()
            tree = ast.parse(code)
            with self.subTest(id=meta['id']):
                self.assertIn('판매가',meta['output_headers'])
                self.assertIn('정상가',meta['output_headers'])
                self.assertNotIn('가격',meta['output_headers'])
                self.assertEqual(hashlib.sha256(code).hexdigest(),meta['code_hash'])
                self.assertTrue(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='collect_regular_price' for n in ast.walk(tree)))
                self.assertEqual(meta['fields']['regular_price']['supported'], meta['capabilities']['regular_price']['supported'])
                if not meta['capabilities']['regular_price']['supported']:
                    unsupported.append(meta['id'])
        self.assertEqual(set(unsupported),{'egoist_naver_ncys','nepa_official'})

    def test_three_prompts_include_separate_optional_contract(self):
        paths = list(ROOT.glob('브랜드_크롤러_제작프롬프트*.txt'))
        self.assertEqual(len(paths),3)
        for path in paths:
            text = path.read_text(encoding='utf-8')
            self.assertIn('regular_price',text)
            self.assertIn('"판매가", "정상가"',text)
            self.assertIn('정상가 단독 누락으로 ctx.fail_item',text)


if __name__ == '__main__':
    unittest.main()
