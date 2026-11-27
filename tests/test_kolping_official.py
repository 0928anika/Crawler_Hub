"""콜핑몰(ekolping.co.kr) 수동 매핑 오프라인 검사.

상세 본문은 2026-11-27 실사이트(product_no=18982 틸로(남), 모델명 KVP9610M)에서 옵션·스크립트를 뺀 HTML,
option_stock_data는 같은 상품의 실제 형식(BEIGE-086·NAVY-086 품절)을 본떠 만듦.
"""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from crawler_sdk import CrawlContext  # noqa: E402
from PIL import Image  # noqa: E402

KEY = 'kolping_official'
ALL = ['size', 'price', 'regular_price', 'category', 'main_image', 'additional_images', 'detail_page']
SITE = 'https://ekolping.co.kr'
WEB = 'https://ekolping.co.kr/web/product/'
KT = 'https://sno525570-sno525570.ktcdn.co.kr/ekolping/img/'
BODY = (ROOT / 'tests/fixtures/kolping/18982_body.html').read_text(encoding='utf-8')
SIZES = ['076', '081', '084', '086', '096', '104']


def load():
    spec = importlib.util.spec_from_file_location(KEY + '_test', ROOT / 'brands' / (KEY + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REQUEST_GAP = 0
    module.RETRY_CONN = (0, 0)
    module.RETRY_HTTP = (0,)
    return module


def png(size, url):
    tint = zlib.crc32(url.encode()) & 0xFFFFFF
    buf = io.BytesIO()
    Image.new('RGB', size, ((tint >> 16) & 255, (tint >> 8) & 255, tint & 255)).save(buf, 'PNG')
    return buf.getvalue()


def stock_js(colors=('BEIGE', 'BLACK', 'NAVY'), sold=(('BEIGE', '086'), ('NAVY', '086'))):
    data = {}
    for color in colors:
        for size in SIZES:
            off = (color, size) in sold
            data['P000BCCC%04d' % len(data)] = {
                'use_stock': True, 'is_display': 'F' if off else 'T', 'is_selling': 'F' if off else 'T',
                'is_auto_soldout': 'T' if off else 'F', **({'stock_number': 0} if off else {}),
                'option_name': '색상#$%사이즈', 'option_value': f'{color}-{size}', 'option_price': 35000,
                'option_value_orginal': [color, size], 'option_name_original': ['색상', '사이즈']}
    return "var option_stock_data = '" + json.dumps(json.dumps(data))[1:-1].replace('/', '\\/') + "';"


def page(model='KVP9610M', name='틸로(남)', body=BODY, **kw):
    text = body.replace('KVP9610M', model).replace('틸로(남)', name)
    return f"<html><body><script>var product_name = '{name}';\n{stock_js(**kw)}</script>{text}</body></html>"


def search_page(*numbers):
    links = ''.join(f'<li><a href="/product/%EC%83%81%ED%92%88/{n}/category/42/display/1/">상품</a></li>' for n in numbers)
    return f'<html><body><ul class="prdList">{links}</ul><a href="/product/search.html?keyword=x&page=2">2</a></body></html>'


class Net:
    def __init__(self, module, pages):
        self.pages, self.requested, self.checked, self.error = pages, [], [], module.CrawlError
        module.fetch_text = self.fetch
        module.check_public_image = self.check

    def fetch(self, ctx, url, limit=0):
        self.requested.append(url)
        if 'search.html' in url:
            return self.pages.get('search:' + url.split('keyword=')[1], search_page()), url
        if 'image_zoom2' in url:
            return '<html></html>', url
        key = url.split('product_no=')[1]
        if key not in self.pages:
            raise self.error('HTTP 404: ' + url)
        return self.pages[key], url

    def check(self, url, **kw):
        self.checked.append(url)
        size = (300, 300) if '/small/' in url else (758, 24384) if '_spec' in url else (1000, 1000)
        return dict(status='accessible', url=url, final_url=url, data=png(size, url), reasons=[], warnings=[],
                    width=size[0], height=size[1], format='PNG')


def execute(module, code, selected=ALL):
    meta = json.loads((ROOT / 'brands' / (KEY + '.json')).read_text(encoding='utf-8'))
    events, error = [], None
    with tempfile.TemporaryDirectory() as temp:
        ctx = CrawlContext('unused', Path(temp) / 'result.xlsx', 0, selected, meta['output_headers'], lambda: None,
                           lambda kind, **data: events.append((kind, data)), input_item={'품번': code},
                           mapping_mode=meta['mapping_mode'], image_mode=meta['image_mode'])
        try:
            module.crawl(ctx)
            ctx.finish()
        except Exception as exc:
            error = exc
    headers = [d['row'] for k, d in events if k == 'row' and d.get('header')]
    headers = headers[-1] if headers else meta['output_headers']
    rows = [(dict(zip(headers, d['row'])), d) for k, d in events if k == 'row' and not d.get('header')]
    return rows, [d['message'] for k, d in events if k == 'item_failure'], error, [str(d.get('message')) for k, d in events if k == 'log']


def palette_of(event):
    for key in ('unmapped', 'mapping', 'manual'):
        if isinstance(event.get(key), dict) and 'colors' in event[key]:
            return event[key]['colors']
    raise AssertionError('매핑 메타데이터 없음')


class KolpingTest(unittest.TestCase):
    def test_model_search_emits_gallery_candidates_with_palette(self):
        module = load()
        net = Net(module, {'search:KVP9610M': search_page('18982'), '18982': page()})
        rows, failures, error, logs = execute(module, 'kvp9610m')
        self.assertIsNone(error, failures)
        self.assertIn(SITE + '/product/search.html?keyword=KVP9610M', net.requested)
        urls = [r['대표이미지'] for r, _ in rows]
        self.assertEqual(urls, [WEB + 'big/202410/2f3f9804ac0feec8846d833655567130.jpg',
                                WEB + 'big/202410/17c835d6f1acde328b3704d9e1bbc30d.jpg',            # small → big 원본
                                WEB + 'extra/big/202409/0e1b59ad2f2acfb7707c5a5649b69ff8.jpg',
                                WEB + 'extra/big/202409/0ee895b81d33de205074d55020b4f413.jpg'])
        first, event = rows[0]
        self.assertEqual((first['품번'], first['상품명'], first['브랜드']), ('KVP9610M', '틸로(남)', 'KOLPING'))
        self.assertEqual((first['판매가'], first['정상가']), (35000, 115000))
        self.assertEqual(first['카테고리'], '온라인단독 > 바지 > 봄/가을 바지')
        self.assertEqual(first['사이즈'], '076,081,084,086,096,104')
        self.assertIn('BEIGE: 076:있음,081:있음,084:있음,086:0', first['색상별 사이즈 재고'])        # 품절 사이즈 유지
        palette = palette_of(event)
        self.assertEqual([c['label'] if 'label' in c else c['color_name'] for c in palette][:3], ['BEIGE', 'BLACK', 'NAVY'])
        self.assertTrue(all(not c.get('color_code') for c in palette))                               # 공식 색상코드 없음

    def test_detail_skips_brand_banner_and_keeps_spec_and_notice(self):
        module = load()
        Net(module, {'search:KVP9610M': search_page('18982'), '18982': page()})
        rows, failures, error, _ = execute(module, 'KVP9610M')
        self.assertIsNone(error, failures)
        markup = rows[0][0]['상세페이지 HTML']
        self.assertIn(KT + 'prodDetail/KVP9610M_spec.jpg', markup)
        self.assertNotIn('apparel_top_sp', markup)                                   # 무단도용·CCTV 안내 공통 배너
        self.assertIn('상품정보제공고시', markup)
        self.assertIn('단독세탁', markup)
        self.assertNotIn('<th>소재</th>', markup)                                     # 빈 고시 항목 생략
        self.assertNotIn(KT + 'prodDetail/KVP9610M_spec.jpg', [r['대표이미지'] for r, _ in rows])  # 스펙 통이미지는 매핑 후보 아님

    def test_model_named_detail_photos_become_candidates(self):
        module = load()
        body = BODY.replace('prodDetail/apparel_top_sp.jpg', 'prod/shop_top.jpg').replace(
            'prodDetail/KVP9610M_spec.jpg">', 'prodDetail/KVP9610M_spec.jpg"><img ec-data-src="' + KT + 'prod/KVP9610M_1.jpg">')
        Net(module, {'search:KVP9610M': search_page('18982'), '18982': page(body=body)})
        rows, failures, error, _ = execute(module, 'KVP9610M')
        self.assertIsNone(error, failures)
        urls = [r['대표이미지'] for r, _ in rows]
        self.assertEqual(urls[-1], KT + 'prod/KVP9610M_1.jpg')
        self.assertNotIn('shop_top', rows[0][0]['상세페이지 HTML'])

    def test_search_hit_with_other_model_is_excluded(self):
        module = load()
        Net(module, {'search:KVP9610M': search_page('20000', '18982'), '20000': page(model='KVP9610W', name='틸로(여)'),
                     '18982': page()})
        rows, failures, error, logs = execute(module, 'KVP9610M')
        self.assertIsNone(error, failures)
        self.assertEqual({r['원본 상품명'] for r, _ in rows}, {'틸로(남)'})
        self.assertTrue(any('KVP9610W ≠ KVP9610M' in m for m in logs))

    def test_two_links_merge_colors(self):
        module = load()
        Net(module, {'search:KVP9610M': search_page('18982', '19000'), '18982': page(),
                     '19000': page(name='틸로(남) 아울렛', colors=('BLACK', 'KHAKI'), sold=())})
        rows, failures, error, _ = execute(module, 'KVP9610M')
        self.assertIsNone(error, failures)
        names = [c.get('color_name') or c.get('label') for c in palette_of(rows[0][1])]
        self.assertEqual(names, ['BEIGE', 'BLACK', 'NAVY', 'KHAKI'])

    def test_direct_number_and_url(self):
        module = load()
        for value in ('18982', 'https://ekolping.co.kr/product/%ED%8B%B8%EB%A1%9C%EB%82%A8/18982/category/1/display/3/'):
            net = Net(module, {'18982': page()})
            rows, failures, error, _ = execute(module, value)
            self.assertIsNone(error, failures)
            self.assertEqual(rows[0][0]['품번'], 'KVP9610M')
            self.assertFalse(any('search.html' in u for u in net.requested))

    def test_not_found_and_bad_input(self):
        module = load()
        Net(module, {})
        _, failures, error, _ = execute(module, 'KVP0000M')
        self.assertIsInstance(error, module.CrawlError)
        self.assertTrue(any('찾지 못함' in f for f in failures))
        _, failures, error, _ = execute(module, '틸로 바지')
        self.assertTrue(any('형식 오류' in f for f in failures))

    def test_images_required(self):
        module = load()
        Net(module, {'search:KVP9610M': search_page('18982'), '18982': page()})
        _, failures, error, _ = execute(module, 'KVP9610M', selected=['size', 'price'])
        self.assertIsInstance(error, module.CrawlError)
        self.assertTrue(any('대표이미지 또는 추가이미지' in f for f in failures))


if __name__ == '__main__':
    unittest.main()
