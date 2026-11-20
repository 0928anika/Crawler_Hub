"""마운티아(카테고리 색인 검색)·하프클럽 품번(수동 매핑) 오프라인 검사.

실사이트가 아니라 사용자 단독 크롤러(2026-11-20)에 적힌 사이트 구조를 본뜬 fixture다.
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from crawler_sdk import CrawlContext  # noqa: E402
from PIL import Image  # noqa: E402

ALL = ['size', 'price', 'regular_price', 'category', 'main_image', 'additional_images', 'detail_page']


def load(key):
    spec = importlib.util.spec_from_file_location(key + '_mh_test', ROOT / 'brands' / (key + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png(size=(800, 800), color=(90, 90, 90)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'PNG')
    return buf.getvalue()


def public(sizes=None):
    sizes = sizes or {}

    def check(url, **kw):
        size = sizes.get(url, (800, 800))
        if size is None:
            return dict(status='inaccessible', url=url, final_url=url, data=None, reasons=['HTTP 404'], warnings=[])
        import zlib
        tint = zlib.crc32(url.encode()) & 0xFFFFFF
        return dict(status='accessible', url=url, final_url=url, data=png(size, ((tint >> 16) & 255, (tint >> 8) & 255, tint & 255)), reasons=[], warnings=[], width=size[0], height=size[1], format='PNG')
    return check


def run(key, code, selected=ALL):
    meta = json.loads((ROOT / 'brands' / (key + '.json')).read_text(encoding='utf-8'))
    module = load(key)
    return meta, module


def execute(module, meta, code, selected=ALL):
    events, error = [], None
    with tempfile.TemporaryDirectory() as folder:
        ctx = CrawlContext('unused', Path(folder) / 'result.xlsx', 0, selected, meta['output_headers'], lambda: None,
                           lambda kind, **data: events.append((kind, data)), input_item={'품번': code},
                           mapping_mode=meta['mapping_mode'], image_mode=meta['image_mode'])
        try:
            module.crawl(ctx)
            ctx.finish()
        except Exception as exc:
            error = exc
    headers = [d['row'] for k, d in events if k == 'row' and d.get('header')][-1]
    rows = [dict(dict(zip(headers, d['row'])), _unmapped=d.get('unmapped')) for k, d in events if k == 'row' and not d.get('header')]
    return rows, [d['message'] for k, d in events if k == 'item_failure'], error, [d.get('message') for k, d in events if k == 'log']


# ───────────────────────── 마운티아 ─────────────────────────
PIC = 'https://picweb.blackyak.com/Upload/ProductImage/'


def mountia_list(items):
    cards = ''.join('<li><a href="/mountia/shop/detail.php?pno=%s"><img data-src="%s%s_%s/%s_%s_WMain_list_275_FRONT.jpg"></a></li>'
                    % (pno, PIC, style, cc, style, cc) for pno, style, cc in items)
    return '<html><body><div class="prd-recommend"><a href="/mountia/shop/detail.php?pno=ZZZ"><img src="%sX/1MQTSWV010_ZZ_WMain_list_275_FRONT.jpg"></a></div><ul>%s</ul></body></html>' % (PIC, cards)


def mountia_detail(pno, cc, color, other):
    sku = '1MQTSWV010' + cc
    gallery = ''.join('<div class="prd-thumb__item" data-type="image"><img data-src="%s%s/%s_WMain_view_860_%s.jpg"></div>' % (PIC, sku, '1MQTSWV010_' + cc, v) for v in ('FRONT', 'BACK'))
    return ('<html><head><meta property="og:url" content="https://www.byn.kr/mountia/shop/detail.php?pno=%s&hta_brand=mountia"></head><body>'
            '<script>var currency = "원";</script>'
            '<div class="prd-info__name">타이카라티셔츠#3</div><div class="prd-info__color-name">%s</div>'
            '<a class="prd-info__color-chip" href="/mountia/shop/detail.php?pno=%s"></a>'
            '<div class="prd-info__price-current" data-sell-price="59000"><span class="prd-info__price-num">59,000</span></div>'
            '<div class="prd-info__price-original"><span class="prd-info__price-num">79,000</span></div>'
            '<button class="prd-info__size-chip" data-qty="3">095</button><button class="prd-info__size-chip" data-qty="0" disabled>100</button>'
            '<input name="toss_cate_name" value="남성 > 티셔츠">'
            '<div class="prd-desc__code-value">%s</div>%s'
            '<script>var d="//picweb.blackyak.com/Upload/ProductImage/%s/1MQTSWV010_%s_WDetail_view_860_D1.jpg";</script>'
            '<div class="prd-notice__accordion"><div class="prd-notice__accordion-title">상품정보제공고시</div>'
            '<dl class="prd-notice__dl"><dt>소재</dt><dd>폴리에스터 100%%</dd></dl></div></body></html>'
            % (pno, color, other, sku, gallery, sku, cc))


class MountiaIndexSearch(unittest.TestCase):
    def setUp(self):
        self.meta, self.module = run('mountia_official', '1MQTSWV010')
        self.module.time.sleep = lambda s: None
        self.module.check_public_image = public()
        self.module._INDEX.update(ts=0.0, items={})
        self.calls = []
        pages = {
            ('2011', 1): mountia_list([('AAA', '1MQTSWV010', 'DV'), ('BBB', '1MQTSWV010', 'BK'), ('CCC', '1MQTSWV0101', 'XX')]),
            ('2011', 2): mountia_list([('AAA', '1MQTSWV010', 'DV')]),          # 새 상품 없음 → 다음 카테고리
        }
        details = {'AAA': mountia_detail('AAA', 'DV', '다크네이비', 'BBB'), 'BBB': mountia_detail('BBB', 'BK', '블랙', 'AAA')}

        def fetch(ctx, url):
            self.calls.append(url)
            from urllib.parse import urlsplit, parse_qs
            q = parse_qs(urlsplit(url).query)
            if 'search_result.php' in url:
                return '<html><body><div id="prd_basic"></div></body></html>', url
            if 'big_section.php' in url:
                return pages.get((q['cno1'][0], int(q['page'][0])), '<html></html>'), url
            if 'detail.php' in url:
                return details[q['pno'][0]], url
            raise AssertionError(url)
        self.module.fetch = fetch

    def test_site_search_empty_uses_category_index(self):
        rows, failures, error, logs = execute(self.module, self.meta, '1MQTSWV010')
        self.assertIsNone(error, failures)
        self.assertEqual([r['색상코드'] for r in rows], ['DV', 'BK'])
        self.assertTrue(any('big_section.php' in u for u in self.calls))
        self.assertTrue(any('카테고리 색인' in (m or '') for m in logs))
        row = rows[0]
        self.assertEqual((row['판매가'], row['정상가'], row['사이즈']), (59000, 79000, '095,100'))
        self.assertIn('WDetail_view_860_D1', row['추가이미지'])           # 그 색상 상세컷도 후보
        self.assertNotIn('_ZZ_', json.dumps(rows, ensure_ascii=False))   # 추천 영역 상품 무시
        self.assertNotIn('1MQTSWV0101', json.dumps(rows, ensure_ascii=False))

    def test_color_code_input_and_index_reuse(self):
        rows, failures, error, _ = execute(self.module, self.meta, '1MQTSWV010BK')
        self.assertIsNone(error, failures)
        self.assertEqual([r['색상코드'] for r in rows], ['BK'])
        before = len([u for u in self.calls if 'big_section' in u])
        execute(self.module, self.meta, '1MQTSWV010DV')
        self.assertEqual(len([u for u in self.calls if 'big_section' in u]), before)  # 12시간 안 재사용

    def test_not_found_anywhere_fails(self):
        rows, failures, error, _ = execute(self.module, self.meta, '1MQTSWV999')
        self.assertIsNotNone(error)
        self.assertEqual(rows, [])
        self.assertTrue(failures)


# ───────────────────────── 하프클럽 ─────────────────────────
def halfclub_page(no, code='RHHAX2675', brand='리복', colors=('블랙', '화이트'), norm=69000, price=39900, desc=None):
    stock = []
    color_values, size_values = [], []
    for i, c in enumerate(colors):
        for j, s in enumerate(('230', '240')):
            n = i * 10 + j
            color_values.append({'optValueNm': c, 'stockNo': n})
            size_values.append({'optValueNm': s, 'stockNo': n})
    info = {'prdNo': no, 'prdNm': '[%s] 클래식 스니커즈 %s' % (brand, code), 'prdCd': code + '_01', 'brandMainNmKr': brand,
            'dispCtgr': {'dispCtgrNm1': '신발', 'dispCtgrNm2': '스니커즈'},
            'optionItem': ([{'optItemNm': '색상', 'optValueList': color_values}] if colors else []) + [{'optItemNm': '사이즈', 'optValueList': size_values}],
            'productImage': {'basicExtNm': 'prd/%s/basic.jpg' % no, 'add1ExtNm': 'prd/%s/add1.jpg' % no, 'add2ExtNm': 'prd/%s/small.jpg' % no},
            'productDesc': {'prdDescContClob': desc if desc is not None else
                            '<img src="https://cdn2.halfclub.com/styleid_img/event/top.jpg"><img src="//cdn2.halfclub.com/desc/%s_1.jpg"><img src="https://img.seller.com/banner/a.jpg">' % no},
            'notiItemMap': [{'notiItemTitle': '제조국', 'notiItemValue': '베트남'}, {'notiItemTitle': '소재', 'notiItemValue': '합성피혁'}]}
    data = {'productInfo': info, 'prductPriceInfo': {'price': price, 'selPrc': 45000, 'normPrc': norm}}
    return '<html><body><script id="pdp-data" type="application/json">%s</script></body></html>' % json.dumps(data, ensure_ascii=False)


class HalfclubItem(unittest.TestCase):
    def setUp(self):
        self.meta, self.module = run('halfclub_item', 'RHHAX2675')
        self.module.time.sleep = lambda s: None
        self.module.check_public_image = public({'https://cdn2.halfclub.com/prd/111/small.jpg': (300, 300),
                                                 'https://cdn2.halfclub.com/prd/222/small.jpg': (300, 300)})
        self.routes = {}
        self.search_hits = [
            {'_id': '111', '_source': {'prdNo': '111', 'prdCd': 'RHHAX2675_01', 'prdNm': '클래식 스니커즈 RHHAX2675'}},
            {'_id': '999', '_source': {'prdNo': '999', 'prdCd': 'RHHAX26751_01', 'prdNm': '다른 상품 RHHAX26751'}},
        ]
        self.requests = []

        def fetch(ctx, url, limit=0):
            self.requests.append(url)
            if 'searches/prdList' in url:
                if 'hapix' in url and getattr(self, 'hapix_down', False):
                    raise self.module.CollectionError('HTTP 503')
                return json.dumps({'data': {'result': {'hits': {'total': {'value': len(self.search_hits)}, 'hits': self.search_hits}}}}).encode()
            if url in self.routes:
                return self.routes[url].encode()
            raise self.module.MissingResource('HTTP 404: ' + url)
        self.module.fetch = fetch
        self.routes['https://www.halfclub.com/product/111'] = halfclub_page('111')

    def test_exact_code_manual_candidates_prices_and_detail(self):
        rows, failures, error, _ = execute(self.module, self.meta, 'RHHAX2675')
        self.assertIsNone(error, failures)
        self.assertNotIn('https://www.halfclub.com/product/999', self.requests)   # 다른 품번 제외
        self.assertEqual([r['대표이미지'] for r in rows], ['https://cdn2.halfclub.com/prd/111/basic.jpg', 'https://cdn2.halfclub.com/prd/111/add1.jpg'])
        row = rows[0]
        self.assertEqual((row['품번'], row['상품명'], row['판매가'], row['정상가']), ('RHHAX2675', '[리복] 클래식 스니커즈', 39900, 69000))
        self.assertEqual((row['사이즈'], row['카테고리'], row['브랜드']), ('230,240', '신발 > 스니커즈', '리복'))
        self.assertEqual([c['label'] for c in row['_unmapped']['colors']], ['블랙', '화이트'])
        self.assertIsNone(row['_unmapped'].get('preassigned_color_id'))         # 색상 추측 없음
        detail = row['상세페이지 HTML']
        self.assertIn('desc/111_1.jpg', detail)
        self.assertNotIn('styleid_img/event', detail)
        self.assertNotIn('banner', detail)
        self.assertIn('제조국', detail)
        self.assertIn('베트남', detail)

    def test_missing_regular_price_is_blank_not_sale_price(self):
        self.routes['https://www.halfclub.com/product/111'] = halfclub_page('111', norm=0)
        rows, failures, error, logs = execute(self.module, self.meta, 'RHHAX2675')
        self.assertIsNone(error, failures)
        self.assertIn(rows[0]['정상가'], ('', None))
        self.assertEqual(rows[0]['판매가'], 39900)

    def test_selected_fields_only(self):
        rows, failures, error, _ = execute(self.module, self.meta, 'RHHAX2675', ['main_image'])
        self.assertIsNone(error, failures)
        self.assertIn(rows[0]['판매가'], ('', None))
        self.assertIn(rows[0]['상세페이지 HTML'], ('', None))
        self.assertFalse(any('desc/' in u for u in self.requests))

    def test_no_color_option_fails_without_fake_color(self):
        self.routes['https://www.halfclub.com/product/111'] = halfclub_page('111', colors=())
        rows, failures, error, _ = execute(self.module, self.meta, 'RHHAX2675')
        self.assertIsNotNone(error)
        self.assertEqual(rows, [])
        self.assertIn('임의 색상 생성 안 함', ' '.join(failures))

    def test_second_search_endpoint_and_direct_url(self):
        self.hapix_down = True
        rows, failures, error, _ = execute(self.module, self.meta, 'RHHAX2675')
        self.assertIsNone(error, failures)
        self.assertTrue(any('cf-hapi' in u for u in self.requests))
        self.requests.clear()
        rows, failures, error, _ = execute(self.module, self.meta, 'https://www.halfclub.com/product/111')
        self.assertIsNone(error, failures)
        self.assertFalse(any('searches' in u for u in self.requests))
        self.assertEqual(rows[0]['품번'], 'RHHAX2675')

    def test_two_sellers_keep_all_photos_blank_differing_price(self):
        self.search_hits.append({'_id': '222', '_source': {'prdNo': '222', 'prdCd': 'RHHAX2675_02', 'prdNm': 'RHHAX2675'}})
        self.routes['https://www.halfclub.com/product/222'] = halfclub_page('222', brand='리복', price=35000)
        rows, failures, error, _ = execute(self.module, self.meta, 'RHHAX2675')
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r['판매가'] in ('', None) for r in rows))
        self.assertTrue(all(r['정상가'] == 69000 for r in rows))

    def test_nuxt_data_decoding(self):
        array = [{'data': 1}, {'productDetailData-555': 2, 'productPrice-555': 4}, ['Reactive', 3], {'prdNo': 5, 'prdNm': 6}, {'normPrc': 7}, '555', '이름', 10000]
        text = '<script type="application/json" id="__NUXT_DATA__">%s</script>' % json.dumps(array, ensure_ascii=False)
        self.routes['https://www.halfclub.com/product/555'] = text
        info, prices, url = self.module.product_page(type('C', (), {'checkpoint': lambda self: None})(), '555')
        self.assertEqual((info['prdNm'], prices['normPrc']), ('이름', 10000))


if __name__ == '__main__':
    unittest.main()
