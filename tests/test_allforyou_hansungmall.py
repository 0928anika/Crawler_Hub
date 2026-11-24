"""올포유(한성몰) 오프라인 검사.

실사이트 응답이 아니라 사용자 단독 한성몰 크롤러(2026-11-24)에 적힌 구조와 저장소의 실제 Cafe24
option_stock_data 형식(tests/fixtures/agabang_*.html)을 본뜬 fixture다. 실사이트 실행 확인은 별도.
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

KEY = 'allforyou_hansungmall'
ALL = ['size', 'price', 'regular_price', 'category', 'main_image', 'additional_images', 'detail_page']
SITE = 'https://www.hansungmall.net'
POXO = 'https://cafe24img.poxo.com/hansungimall/web/product/'
OPEN = 'hansungimall.openhost.cafe24.com/allforyou/'


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


def stock_js(entries):
    """실제 Cafe24처럼: JSON → 문자열 이스케이프(\\" , \\uXXXX) → 작은따옴표 JS 문자열."""
    data = {}
    for i, (color, size, extra) in enumerate(entries):
        item = {'use_stock': True, 'is_display': 'T', 'is_selling': 'T', 'is_auto_soldout': 'F',
                'option_name': '색상#$%사이즈', 'option_value': f'{color}-{size}',
                'option_value_orginal': [color, size], 'option_name_original': ['색상', '사이즈'], 'stock_number': 3}
        item.update(extra)
        data['P000%04d' % i] = item
    inner = json.dumps(data)                  # \\uXXXX 한글
    return "var option_stock_data = '" + json.dumps(inner)[1:-1].replace('/', '\\/') + "';"


def detail_page(no, title, code_field, entries, gallery, details, brand='올포유', notice=True, chips='', sale='59,000원',
                custom='<span id="span_product_price_custom">129,000원</span>', selects=('색상', '사이즈')):
    imgs = ''.join(f'<img src="{u}">' for u in gallery)
    det = ''.join(f'<img ec-data-src="{u}">' for u in details)
    sel = ''.join(f'<select option_title="{t}" product_type="product_option"><option value="*">선택</option></select>' for t in selects)
    rows = ('<table id="desctable"><tr><td>소재</td><td>겉감: 폴리에스터 100%</td></tr><tr><td>색상</td><td>블랙</td></tr>'
            '<tr><td>제조국</td><td>베트남</td></tr></table>') if notice else ''
    brand_li = f'<li rel="브랜드">브랜드 {brand}</li>' if brand else ''
    code_li = f'<li rel="품번코드">품번코드 {code_field}</li>' if code_field else ''
    return f"""<html><head><script>var product_name = '{title}';
{stock_js(entries)}</script></head><body>
<div class="xans-product-headcategory"><ol><li><a href="/">홈</a></li><li><a href="/c/1">여성</a></li><li><a href="/c/2">아우터</a></li></ol></div>
<div class="imgArea">{imgs}</div><div class="relation"><div class="listitem"><img src="{POXO}big/other_relation.jpg"></div></div>
<div class="infoArea"><ul>{brand_li}{code_li}</ul>
<span id="span_product_price_sale">{sale}</span><span id="span_product_price_text">69,000원</span>{custom}</div>
{sel}{chips}
<div id="prdDetail2">{det}</div>{rows}
<div class="xans-product-action"><a class="btnSoldout displaynone">SOLD OUT</a></div>
</body></html>"""


def search_page(items, recommend=()):
    lis = ''.join(f'<li id="anchorBoxId_{no}"><div class="name"><a href="/product/x/{no}/">상품명 : {name}</a></div></li>' for no, name in items)
    rec = ''.join(f'<li id="anchorBoxId_{no}"><div class="name"><a>{name}</a></div></li>' for no, name in recommend)
    return f'<html><body><div class="listmain"><ul>{rec}</ul></div><div class="xans-search-result"><ul>{lis}</ul></div></body></html>'


def three_colors():
    pages = {}
    colors = [('101', '403', '블랙'), ('102', '102', '아이보리'), ('103', '199', '네이비')]
    for no, cc, name in colors:
        gallery = [f'{POXO}big/AWJU67301_{cc}_1.jpg', f'{POXO}extra/small/AWJU67301_{cc}_2.jpg',
                   f'{POXO}extra/big/AWJU67301_{cc}_size_chart.jpg', f'{POXO}extra/big/AWJU67301_{cc}_long.jpg',
                   f'{POXO}extra/big/AWJU67301_{cc}_3.jpg']
        details = [f'http://{OPEN}img/26fwtop.jpg', f'http://{OPEN}detail/AWJU67301-{cc}.jpg',
                   f'http://{OPEN}detail/AWJU67301-{cc}_2.jpg', f'http://{OPEN}detail/AWJU67301-102.jpg',
                   f'http://{OPEN}img/bottom.jpg', f'{SITE}/web/upload/NNEditor/event.jpg']
        title = (f'[올포유] 여성 구스다운 패딩 점퍼 {name} AWJU67301-{cc}' if cc != '199'
                 else f'여성 구스다운 패딩 점퍼 AWJU67301-{cc}')       # 브랜드명 없는 상품명도 통과해야 함
        entries = [(name, '090', {}), (name, '095', {'is_display': 'F'}), (name, '100', {'stock_number': 0, 'is_auto_soldout': 'T'})]
        pages[no] = detail_page(no, title, f'AWJU67301-{cc}', entries, gallery, details)
    items = [(no, f'여성 구스다운 패딩 점퍼 AWJU67301-{cc}') for no, cc, _ in colors] + [('900', '남성 니트 AWJU67310-403')]
    pages['search:AWJU67301'] = search_page(items, recommend=[('777', '추천 AWJU67301-403')])
    pages['900'] = detail_page('900', '남성 니트 AWJU67310-403', 'AWJU67310-403', [('블랙', '095', {})], [], [])
    return pages


class Net:
    def __init__(self, module, pages):
        self.pages, self.requested, self.checked = pages, [], []
        self.error = module.CrawlError
        module.fetch_text = self.fetch
        module.check_public_image = self.check

    def fetch(self, ctx, url, limit=0):
        self.requested.append(url)
        if 'search.html' in url:
            key = 'search:' + url.split('keyword=')[1]
        elif 'image_zoom2' in url:
            key = 'zoom:' + url.split('=')[-1]
        else:
            key = url.split('product_no=')[1]
        if key not in self.pages:
            if key.startswith(('zoom:', 'search:')):
                return search_page([]) if key.startswith('search:') else '<html></html>', url
            raise self.error('HTTP 404: ' + url)
        return self.pages[key], url

    def check(self, url, **kw):
        self.checked.append(url)
        size = (300, 300) if 'small' in url else (600, 2000) if 'long' in url else (900, 900)
        return dict(status='accessible', url=url, final_url=url, data=png(size, url), reasons=[], warnings=[],
                    width=size[0], height=size[1], format='PNG')


def execute(module, code, selected=ALL):
    meta = json.loads((ROOT / 'brands' / (KEY + '.json')).read_text(encoding='utf-8'))
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
    headers = [d['row'] for k, d in events if k == 'row' and d.get('header')]
    headers = headers[-1] if headers else meta['output_headers']
    rows = [dict(zip(headers, d['row'])) for k, d in events if k == 'row' and not d.get('header')]
    return rows, [d['message'] for k, d in events if k == 'item_failure'], error, [d.get('message') for k, d in events if k == 'log']


class AllforyouHansungmallTest(unittest.TestCase):
    def test_base_code_collects_every_color_page(self):
        module = load()
        net = Net(module, three_colors())
        rows, failures, error, logs = execute(module, 'AWJU67301')
        self.assertIsNone(error, failures)
        self.assertEqual([(r['색상'], r['색상코드']) for r in rows], [('블랙', '403'), ('아이보리', '102'), ('네이비', '199')])
        first = rows[0]
        self.assertEqual(first['품번'], 'AWJU67301')
        self.assertEqual(first['상품명'], '여성 구스다운 패딩 점퍼')
        self.assertEqual(rows[2]['상품명'], '여성 구스다운 패딩 점퍼')         # 브랜드명 없는 상품명
        self.assertEqual(first['원본 상품명'], '[올포유] 여성 구스다운 패딩 점퍼 블랙 AWJU67301-403')
        self.assertEqual(first['브랜드'], '올포유')
        self.assertEqual(first['상품 링크'], SITE + '/product/detail.html?product_no=101')
        self.assertEqual(first['사이즈'], '090,095,100')                     # 앞자리 0, 숨김·품절 포함
        self.assertEqual(first['사이즈별 재고'], '090:3,095:0,100:0')
        self.assertEqual(first['판매가'], 59000)
        self.assertEqual(first['정상가'], 129000)
        self.assertEqual(first['카테고리'], '여성 > 아우터')
        # 갤러리: size_chart·긴 이미지·추천상품 제외, 작은 extra 썸네일은 big 원본으로
        self.assertEqual(first['대표이미지'], POXO + 'big/AWJU67301_403_1.jpg')
        extras = first['추가이미지'].split(',')
        self.assertEqual(len(extras), 5)
        self.assertEqual(extras[:2], [POXO + 'extra/big/AWJU67301_403_2.jpg', POXO + 'extra/big/AWJU67301_403_3.jpg'])
        self.assertTrue(all('size_chart' not in u and 'long' not in u and '102' not in u for u in extras))
        self.assertFalse(any('size_chart' in u or 'relation' in u for u in net.checked))
        # 상세: 해당 색상 본문만(https), 다른 색상 파일·공통 머리꼬리·행사 이미지는 요청조차 안 함 + 고시 2열 표
        html = first['상세페이지 HTML']
        self.assertIn(f'https://{OPEN}detail/AWJU67301-403.jpg', html)
        self.assertIn(f'https://{OPEN}detail/AWJU67301-403_2.jpg', html)
        self.assertNotIn('AWJU67301-102.jpg', html)
        self.assertIn('<h3>상품정보고시</h3>', html)
        self.assertIn('<th', html)
        self.assertIn('겉감: 폴리에스터 100%', html)
        self.assertFalse(any('/img/' in u or '/web/upload/' in u for u in net.checked))
        self.assertNotIn('사이즈 정보', html)
        self.assertNotIn('세탁', html)
        # 추천 영역·다른 품번 목록 상품은 상세를 열지 않음
        self.assertFalse(any(u.endswith('=777') or u.endswith('=900') for u in net.requested))

    def test_color_code_with_hyphen_or_underscore(self):
        for code in ('AWJU67301-102', 'AWJU67301_102'):
            module = load()
            Net(module, three_colors())
            rows, failures, error, _ = execute(module, code)
            self.assertIsNone(error, (code, failures))
            self.assertEqual([(r['색상'], r['색상코드']) for r in rows], [('아이보리', '102')])

    def test_unknown_color_code_fails(self):
        module = load()
        Net(module, three_colors())
        rows, failures, error, _ = execute(module, 'AWJU67301-555')
        self.assertEqual(rows, [])
        self.assertIsNotNone(error)
        self.assertTrue(failures)

    def test_nothing_selected_skips_optional_requests(self):
        module = load()
        net = Net(module, three_colors())
        rows, failures, error, _ = execute(module, 'AWJU67301', selected=[])
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 3)
        self.assertEqual(net.checked, [])
        self.assertFalse(any('image_zoom2' in u for u in net.requested))
        self.assertTrue(all(r['상품 링크'].startswith(SITE) for r in rows))
        self.assertIn(rows[0]['사이즈별 재고'], ('', None))

    def test_missing_regular_price_is_blank_not_sale_price(self):
        module = load()
        pages = three_colors()
        pages['101'] = pages['101'].replace('<span id="span_product_price_custom">129,000원</span>', '')
        Net(module, pages)
        rows, failures, error, logs = execute(module, 'AWJU67301-403')
        self.assertIsNone(error, failures)
        self.assertIn(rows[0]['정상가'], ('', None))
        self.assertEqual(rows[0]['판매가'], 59000)

    def test_multi_color_page_uses_only_color_chip_photo(self):
        module = load()
        chips = (f'<ul option_title="색상"><li option_value="카멜" link_image="{POXO}big/MULTI_camel.jpg"></li>'
                 f'<li option_value="그레이"></li></ul>')
        entries = [('카멜', 'FREE(999)', {}), ('그레이', 'FREE(999)', {'is_selling': 'F'})]
        page = detail_page('201', '울 머플러 AMSC10001', 'AMSC10001', entries,
                           [f'{POXO}big/MULTI_camel.jpg', f'{POXO}big/MULTI_common.jpg'], [], chips=chips)
        module = load()
        Net(module, {'search:AMSC10001': search_page([('201', '울 머플러 AMSC10001')]), '201': page})
        rows, failures, error, logs = execute(module, 'AMSC10001')
        self.assertIsNone(error, failures)
        self.assertEqual([(r['색상'], r['색상코드'], r['사이즈'], r['사이즈별 재고']) for r in rows],
                         [('카멜', '', 'FREE', 'FREE:3'), ('그레이', '', 'FREE', 'FREE:0')])
        self.assertEqual(rows[0]['대표이미지'], POXO + 'big/MULTI_camel.jpg')
        self.assertEqual(rows[1]['대표이미지'], '')                      # 공용 갤러리를 임의 배정하지 않음
        self.assertTrue(any('색상칩 사진 없음' in (m or '') for m in logs))

    def test_single_gallery_photo_checks_zoom_page(self):
        module = load()
        pages = {'search:AWSK11111': search_page([('301', '스커트 AWSK11111-199')]),
                 '301': detail_page('301', '스커트 AWSK11111-199', 'AWSK11111-199', [('네이비', '055', {})],
                                    [f'{POXO}big/AWSK_1.jpg'], [f'http://{OPEN}detail/AWSK11111-199.jpg'], notice=False),
                 'zoom:301': f'<html><img src="{POXO}extra/big/AWSK_2.jpg"><img src="{POXO}big/AWSK_1.jpg"></html>'}
        net = Net(module, pages)
        rows, failures, error, logs = execute(module, 'AWSK11111')
        self.assertIsNone(error, failures)
        self.assertTrue(any('image_zoom2' in u for u in net.requested))
        self.assertEqual(rows[0]['추가이미지'].split(',')[0], POXO + 'extra/big/AWSK_2.jpg')
        self.assertNotIn('상품정보고시', rows[0]['상세페이지 HTML'])        # 고시 없으면 생략(실패 아님)
        self.assertTrue(any('#desctable' in (m or '') for m in logs))

    def test_no_match_and_code_less_products_fail_clearly(self):
        module = load()
        pages = {'search:ZZZZ99999': search_page([('401', '기획전 쿠폰 안내')]),
                 '401': detail_page('401', '기획전 쿠폰 안내', '', [('블랙', '090', {})], [], [])}
        Net(module, pages)
        rows, failures, error, _ = execute(module, 'ZZZZ99999')
        self.assertEqual(rows, [])
        self.assertIsNotNone(error)
        self.assertTrue(any('품번 근거 없는 상품 1개' in f for f in failures), failures)

    def test_option_stock_parser_reads_real_cafe24_format(self):
        module = load()
        text = (ROOT / 'tests' / 'fixtures' / 'agabang_55675.html').read_text(encoding='utf-8')
        data = module.option_stock(text)
        self.assertTrue(data)
        first = next(iter(data.values()))
        self.assertEqual(first['option_name_original'], ['색상', '사이즈'])
        from bs4 import BeautifulSoup
        colors = module.colors_and_sizes(text, BeautifulSoup(text, 'html.parser'), '')
        self.assertIn('O/WHITE', colors)
        self.assertIn('80', colors['O/WHITE']['sizes'])

    def test_one_broken_color_page_keeps_others_and_fails_item(self):
        module = load()
        pages = three_colors()
        del pages['102']
        Net(module, pages)
        rows, failures, error, _ = execute(module, 'AWJU67301')
        self.assertEqual([r['색상코드'] for r in rows], ['403', '199'])
        self.assertIsNotNone(error)
        self.assertTrue(any('102' in f and 'HTTP 404' in f for f in failures), failures)

    def test_input_format(self):
        module = load()
        self.assertEqual(module.interpretations('AWJU67301-403'), [('AWJU67301', '403'), ('AWJU67301-403', None)])
        self.assertEqual(module.interpretations('AWJU67301_403'), [('AWJU67301', '403')])
        self.assertEqual(module.interpretations('AWJU67301'), [('AWJU67301', None)])
        self.assertEqual(module.interpretations('AWJU67301/403'), [])


if __name__ == '__main__':
    unittest.main()
