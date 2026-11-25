"""올리비아로렌(ovlr.kr) 수동 매핑 오프라인 검사.

실사이트 응답이 아니라 사용자 단독 올리비아로렌 크롤러(2026-11-25)에 적힌 구조와 저장소의 실제
Cafe24 option_stock_data 형식을 본뜬 fixture다. 실사이트 실행 확인은 별도.
"""
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
import zlib
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from crawler_sdk import CrawlContext  # noqa: E402
from PIL import Image  # noqa: E402

KEY = 'olivialauren_official'
ALL = ['size', 'price', 'regular_price', 'category', 'main_image', 'additional_images', 'detail_page']
SITE = 'https://ovlr.kr'
EC = 'https://ecimg.cafe24img.com/pg123/ovlr/web/product/'
SG = 'https://ovlr.speedgabia.com/'


def load():
    spec = importlib.util.spec_from_file_location(KEY + '_test', ROOT / 'brands' / (KEY + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REQUEST_GAP = module.LIST_GAP = 0
    module.RETRY_CONN = (0, 0)
    module.RETRY_HTTP = (0,)
    return module


def png(size, url):
    tint = zlib.crc32(url.encode()) & 0xFFFFFF
    buf = io.BytesIO()
    Image.new('RGB', size, ((tint >> 16) & 255, (tint >> 8) & 255, tint & 255)).save(buf, 'PNG')
    return buf.getvalue()


def stock_js(entries):
    data = {}
    for i, (color, size, extra) in enumerate(entries):
        item = {'use_stock': True, 'is_display': 'T', 'is_selling': 'T', 'is_auto_soldout': 'F', 'stock_number': None,
                'option_name': '색상#$%사이즈', 'option_value': f'{color}-{size}',
                'option_value_orginal': [color, size], 'option_name_original': ['색상', '사이즈']}
        item.update(extra)
        data['P000%04d' % i] = item
    return "var option_stock_data = '" + json.dumps(json.dumps(data))[1:-1].replace('/', '\\/') + "';"


def detail(title, entries, gallery, details, sale='39,000원', custom='<span id="span_product_price_custom">89,000원</span>', notice=True):
    imgs = ''.join(f'<img src="{u}">' for u in gallery)
    det = ''.join(f'<img ec-data-src="{u}">' for u in details)
    rows = ('<table id="desctable"><tr><td>소재</td><td>면 100%</td></tr><tr><td>제조국</td><td>대한민국</td></tr>'
            '<tr><td>세탁방법 및 취급시 주의사항</td><td>단독 손세탁</td></tr></table>') if notice else ''
    return f"""<html><head><script>var product_name = '{title}';
{stock_js(entries)}</script></head><body>
<div class="xans-product-headcategory"><ol><li><a>홈</a></li><li><a>기획전</a></li><li><a>TOP</a></li><li><a>티셔츠</a></li></ol></div>
<div class="xans-product-image">{imgs}</div><div class="xans-product-relation"><div class="listitem"><img src="{EC}big/relation.jpg"></div></div>
<div class="infoArea"><table><tr rel="상품요약정보"><th>상품요약정보</th><td><span>드랄론 소재 원포인트 티셔츠</span></td></tr></table>
<span id="span_product_price_text">{sale}</span>{custom}</div>
<select option_title="색상" product_type="product_option"></select><select option_title="사이즈" product_type="product_option"></select>
<div id="prdDetail">{det}</div>{rows}
<div class="xans-product-action"><a class="btnSoldout displaynone">SOLD OUT</a></div></body></html>"""


def listing(items):
    links = ''.join(f'<li><a href="/product/{name.replace(" ", "-")}/{no}/category/104/display/1/">{name}</a></li>' for no, name in items)
    return f'<html><body><div id="header"><a href="/product/header/999/">헤더 OLBAL26W9181</a></div><ul>{links}</ul></body></html>'


def site():
    style = 'OLBAL26W9181'
    entries = [('챠콜 그레이(05)', '090', {}), ('챠콜 그레이(05)', '095', {'is_display': 'F'}),
               ('아이보리(15)', '090', {}), ('아이보리(15)', '095', {}), ('아이보리(15)', '100', {'is_selling': 'F'})]
    gallery = [f'{EC}big/20261101/main.jpg', f'{EC}extra/small/20261101/add1.jpg', f'{EC}extra/big/20261101/size_guide.jpg']
    details = [f'{SG}product/00_intro.jpg', f'{SG}product/{style}/01_{style}05_1.jpg', f'{SG}product/{style}/02_{style}15_1.jpg',
               f'{SG}product/{style}/03_{style}_long.jpg', f'{SG}promotion/2611/banner.jpg', f'{SITE}/web/upload/NNEditor/event.jpg']
    return {
        'list:104:1': listing([('750', f'플라워 원포인트 티셔츠_{style}'), ('740', '스트라이프 셔츠_OLBAL26W9100')]),
        'list:104:2': listing([('750', f'플라워 원포인트 티셔츠_{style}')]),
        'list:114:1': listing([('751', '플라워 티셔츠_OLBAL26W91812')]),           # 비슷한 다른 품번
        '750': detail(f'[올리비아로렌] 플라워 원포인트 티셔츠_{style}', entries, gallery, details),
        '740': detail('스트라이프 셔츠_OLBAL26W9100', [('블루(30)', '090', {})], [f'{EC}big/other.jpg'], []),
    }


class Net:
    def __init__(self, module, pages):
        self.pages, self.requested, self.checked, self.error = pages, [], [], module.CrawlError
        self.specs = set()                                                         # 있는 details/품번_details.jpg
        self.api = {}                                                              # 세정 사이즈 API 응답(resultData)
        module.fetch_json = self.json
        module.fetch_text = self.fetch
        module.check_public_image = self.check

    def fetch(self, ctx, url, limit=0):
        self.requested.append(url)
        if 'list.html' in url:
            cate = url.split('cate_no=')[1].split('&')[0]
            key = f"list:{cate}:{url.split('page=')[1]}"
            return self.pages.get(key, listing([])), url
        if 'image_zoom2' in url:
            return self.pages.get('zoom:' + url.split('=')[-1], '<html></html>'), url
        key = url.split('product_no=')[1]
        if key not in self.pages:
            raise self.error('HTTP 404: ' + url)
        return self.pages[key], url

    def json(self, ctx, url):
        self.requested.append(url)
        return self.api.get(url.split('/searchOnlineStyleInfo/')[1].split('?')[0])

    def check(self, url, **kw):
        self.checked.append(url)
        if '/details/' in url and url not in self.specs:                           # speedgabia: 없는 파일 → err_404.gif
            gif = 'https://11475speedgabia.speedgabia.com/err_img/err_404.gif'
            return dict(status='accessible', url=url, final_url=gif, data=png((600, 600), gif), reasons=[], warnings=[],
                        width=600, height=600, format='GIF')
        size = (300, 300) if 'small' in url else (700, 3000) if 'long' in url else (900, 1100)
        return dict(status='accessible', url=url, final_url=url, data=png(size, url), reasons=[], warnings=[],
                    width=size[0], height=size[1], format='PNG')


def execute(module, code, selected=ALL, folder=None):
    meta = json.loads((ROOT / 'brands' / (KEY + '.json')).read_text(encoding='utf-8'))
    events, error = [], None
    temp = tempfile.TemporaryDirectory() if folder is None else None
    root = Path(temp.name if temp else folder)
    ctx = CrawlContext('unused', root / 'result.xlsx', 0, selected, meta['output_headers'], lambda: None,
                       lambda kind, **data: events.append((kind, data)), input_item={'품번': code},
                       mapping_mode=meta['mapping_mode'], image_mode=meta['image_mode'])
    try:
        module.crawl(ctx)
        ctx.finish()
    except Exception as exc:
        error = exc
    if temp:
        temp.cleanup()
    headers = [d['row'] for k, d in events if k == 'row' and d.get('header')]
    headers = headers[-1] if headers else meta['output_headers']
    rows = [(dict(zip(headers, d['row'])), d) for k, d in events if k == 'row' and not d.get('header')]
    return rows, [d['message'] for k, d in events if k == 'item_failure'], error, [str(d.get('message')) for k, d in events if k == 'log']


def palette_of(event):
    for key in ('unmapped', 'mapping', 'manual'):
        if isinstance(event.get(key), dict) and 'colors' in event[key]:
            return event[key]['colors']
    raise AssertionError('매핑 메타데이터 없음: ' + ','.join(event))


class OliviaLaurenTest(unittest.TestCase):
    def test_base_code_emits_every_candidate_photo_with_real_palette(self):
        module = load()
        net = Net(module, site())
        rows, failures, error, logs = execute(module, 'OLBAL26W9181')
        self.assertIsNone(error, failures)
        urls = [r['대표이미지'] for r, _ in rows]
        self.assertEqual(urls, [f'{EC}big/20261101/main.jpg', f'{EC}extra/big/20261101/add1.jpg',
                                f'{SG}product/OLBAL26W9181/01_OLBAL26W918105_1.jpg', f'{SG}product/OLBAL26W9181/02_OLBAL26W918115_1.jpg'])
        first, event = rows[0]
        self.assertEqual(first['품번'], 'OLBAL26W9181')
        self.assertEqual(first['상품명'], '플라워 원포인트 티셔츠')
        self.assertEqual(first['원본 상품명'], '[올리비아로렌] 플라워 원포인트 티셔츠_OLBAL26W9181')
        self.assertEqual(first['상품 링크'], SITE + '/product/detail.html?product_no=750')
        self.assertEqual(first['색상'], '챠콜 그레이 (05),아이보리 (15)')
        self.assertEqual(first['판매가'], 39000)
        self.assertEqual(first['정상가'], 89000)
        self.assertEqual(first['카테고리'], 'TOP > 티셔츠')
        self.assertIn(first['사이즈'], ('', None))                                  # 색상마다 달라 공통값 없음
        self.assertEqual(first['색상별 사이즈 재고'], '챠콜 그레이(05): 090:있음,095:0 | 아이보리(15): 090:있음,095:있음,100:0')
        colors = palette_of(event)
        self.assertEqual([(c['color_name'], c['color_code'], c['size']) for c in colors],
                         [('챠콜 그레이', '05', '090,095'), ('아이보리', '15', '090,095,100')])
        html = first['상세페이지 HTML']
        self.assertIn('01_OLBAL26W918105_1.jpg', html)
        self.assertIn('02_OLBAL26W918115_1.jpg', html)                            # 모든 색상 공통 상세
        self.assertIn('상품 상세정보</h3>', html)
        self.assertIn('단독 손세탁', html)                                            # 고시 항목은 그대로
        self.assertIn('드랄론 소재 원포인트 티셔츠', html)                              # 상품요약정보
        self.assertIn('챠콜 그레이(05) / 아이보리(15)', html)
        self.assertIn('사이즈 정보</h3>', html)
        self.assertIn('090 / 095 / 100', html)
        self.assertIn('실측 사이즈가 제공되지 않아', html)
        self.assertNotIn('err_404', html)
        self.assertIn(SG + 'details/OLBAL26W9181_details.jpg', net.checked)
        self.assertNotIn('00_intro', html)
        self.assertFalse(any('00_intro' in u or 'promotion' in u or '/web/upload/' in u or 'size_guide' in u or 'relation' in u
                             for u in net.checked))
        self.assertTrue(any('05=챠콜 그레이 1장' in m for m in logs), logs)
        self.assertFalse(any('product_no=740' in u or 'product_no=751' in u or 'product_no=999' in u for u in net.requested))

    def test_index_is_reused_within_the_same_job_folder(self):
        module = load()
        with tempfile.TemporaryDirectory() as folder:
            net = Net(module, site())
            execute(module, 'OLBAL26W9181', folder=folder)
            self.assertTrue((Path(folder) / module.INDEX_FILE).exists())
            module = load()
            net = Net(module, site())
            rows, failures, error, _ = execute(module, 'OLBAL26W9181', folder=folder)
            self.assertIsNone(error, failures)
            self.assertFalse(any('list.html' in u for u in net.requested))

    def test_stale_index_without_code_is_rebuilt_once(self):
        module = load()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / module.INDEX_FILE).write_text(json.dumps({'ts': time.time() - 3600, 'items': {'1': '옛 상품_OLBAL25W0001'}}), encoding='utf-8')
            net = Net(module, site())
            rows, failures, error, logs = execute(module, 'OLBAL26W9181', folder=folder)
            self.assertIsNone(error, failures)
            self.assertTrue(any('색인을 새로 만들어' in m for m in logs))
            self.assertTrue(any('list.html' in u for u in net.requested))

    def test_product_number_and_url_skip_index(self):
        for value in ('750', 'https://ovlr.kr/product/detail.html?product_no=750', 'https://ovlr.kr/product/flower/750/category/104/'):
            module = load()
            net = Net(module, site())
            rows, failures, error, _ = execute(module, value)
            self.assertIsNone(error, (value, failures))
            self.assertEqual(rows[0][0]['품번'], 'OLBAL26W9181')
            self.assertFalse(any('list.html' in u for u in net.requested))

    def test_color_code_input_falls_back_to_base_with_warning(self):
        module = load()
        Net(module, site())
        rows, failures, error, logs = execute(module, 'OLBAL26W918105')
        self.assertIsNone(error, failures)
        self.assertEqual(rows[0][0]['품번'], 'OLBAL26W9181')
        self.assertEqual(len(palette_of(rows[0][1])), 2)                       # 전체 색상
        self.assertTrue(any('색상 지정 입력은 지원하지 않아' in m for m in logs))

    def test_unknown_code_fails(self):
        module = load()
        Net(module, site())
        rows, failures, error, _ = execute(module, 'OLBAL26W0000')
        self.assertEqual(rows, [])
        self.assertIsNotNone(error)
        self.assertTrue(any('찾지 못함' in f for f in failures), failures)

    def test_images_required_and_optional_fields_skip_work(self):
        module = load()
        Net(module, site())
        rows, failures, error, _ = execute(module, 'OLBAL26W9181', selected=['size', 'price'])
        self.assertIsNotNone(error)
        self.assertTrue(any('대표이미지 또는 추가이미지' in f for f in failures))
        module = load()
        net = Net(module, site())
        rows, failures, error, _ = execute(module, 'OLBAL26W9181', selected=['additional_images'])
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 4)
        self.assertIn(rows[0][0].get('상세페이지 HTML'), ('', None))
        self.assertIn(rows[0][0].get('판매가'), ('', None))
        self.assertEqual(len(net.checked), 5)                                   # 후보 5장 검사만, 상세용 검사 없음
        self.assertNotIn('size', palette_of(rows[0][1])[0])

    def test_two_links_merge_colors_and_blank_conflicting_price(self):
        module = load()
        pages = site()
        pages['list:114:1'] = listing([('760', '플라워 원포인트 티셔츠(재입고)_OLBAL26W9181')])
        pages['760'] = detail('플라워 원포인트 티셔츠(재입고)_OLBAL26W9181', [('블랙(19)', '090', {})],
                              [f'{EC}big/20261101/main.jpg', f'{EC}big/20261120/restock.jpg'], [], sale='35,000원')
        Net(module, pages)
        rows, failures, error, logs = execute(module, 'OLBAL26W9181')
        self.assertIsNone(error, failures)
        colors = palette_of(rows[0][1])
        self.assertEqual([c['color_code'] for c in colors], ['19', '05', '15'])   # 최신 상품번호 760 먼저
        self.assertIn(rows[0][0]['판매가'], ('', None))
        self.assertEqual(rows[0][0]['상품 링크'], SITE + '/product/detail.html?product_no=760')
        urls = [r['대표이미지'] for r, _ in rows]
        self.assertEqual(urls.count(f'{EC}big/20261101/main.jpg'), 1)            # 같은 사진 한 번만
        self.assertIn(f'{EC}big/20261120/restock.jpg', urls)

    def test_one_broken_link_keeps_candidates_and_fails_item(self):
        module = load()
        pages = site()
        pages['list:114:1'] = listing([('760', '플라워 원포인트 티셔츠_OLBAL26W9181')])
        Net(module, pages)                                                        # 760 상세 없음 → HTTP 404
        rows, failures, error, _ = execute(module, 'OLBAL26W9181')
        self.assertEqual(len(rows), 4)
        self.assertIsNotNone(error)
        self.assertTrue(any('760' in f and 'HTTP 404' in f for f in failures), failures)

    def test_no_notice_still_writes_info_and_size_tables(self):
        module = load()
        pages = site()
        pages['750'] = pages['750'].replace('id="desctable"', 'id="other"')
        Net(module, pages)
        rows, failures, error, logs = execute(module, 'OLBAL26W9181')
        self.assertIsNone(error, failures)
        html = rows[0][0]['상세페이지 HTML']
        self.assertIn('상품 상세정보</h3>', html)
        self.assertIn('플라워 원포인트 티셔츠', html)
        self.assertIn('사이즈 정보</h3>', html)
        self.assertNotIn('단독 손세탁', html)
        self.assertTrue(any('상품정보고시' in m for m in logs))

    def test_spec_image_is_appended_when_it_exists(self):
        module = load()
        net = Net(module, site())
        spec = SG + 'details/OLBAL26W9181_details.jpg'
        net.specs.add(spec)
        rows, failures, error, logs = execute(module, 'OLBAL26W9181')
        self.assertIsNone(error, failures)
        html = rows[0][0]['상세페이지 HTML']
        self.assertIn(spec, html)
        self.assertLess(html.index('02_OLBAL26W918115_1.jpg'), html.index(spec))     # 모델컷 뒤 → 정보 표 앞
        self.assertLess(html.index(spec), html.index('상품 상세정보'))
        self.assertIn('실측 사이즈를 확인', html)
        self.assertNotIn(spec, [r['대표이미지'] for r, _ in rows])                   # 매핑 후보 아님

    def test_sejung_api_builds_info_and_measurement_tables(self):
        module = load()
        net = Net(module, site())
        spec = SG + 'details/OLBAL26W9181_details.jpg'
        net.specs.add(spec)
        net.api = {
            'getStyleInfo': {'style_nm': '플라워 원포인트 티셔츠', 'color': '챠콜 그레이(05)/와인(15)', 'size': '090/095',
                             'material': '겉감:면 61.00%,폴리에스터 36.00%', 'sex': '여성', 'season': '겨울',
                             'made_in': 'MADE IN VIETNAM', 'wash': '단독 손세탁', 'prod_date': '202607'},
            'getSizeSpec1': [{'msmt_desc': '어깨', 'input_seq_num': '2'}, {'msmt_desc': '총기장', 'input_seq_num': '1'}],
            'getSizeSpec2': [{'size_cd': '095', 'msmt_desc': '총기장', 'msmt_val': '64.2', 'input_seq_num': '1'},
                             {'size_cd': '090', 'msmt_desc': '총기장', 'msmt_val': '63', 'input_seq_num': '1'},
                             {'size_cd': '090', 'msmt_desc': '어깨', 'msmt_val': '40', 'input_seq_num': '2'},
                             {'size_cd': '090', 'msmt_desc': '암홀', 'msmt_val': '20.5', 'input_seq_num': '8'}],
        }
        rows, failures, error, logs = execute(module, 'OLBAL26W9181')
        self.assertIsNone(error, failures)
        html = rows[0][0]['상세페이지 HTML']
        self.assertTrue(any('getSizeSpec2?style_cd=OLBAL26W9181' in u for u in net.requested))
        self.assertIn('겉감:면 61.00%,폴리에스터 36.00%', html)
        self.assertIn('202607', html)
        self.assertIn('[단위 : CM]', html)
        self.assertNotIn('암홀', html)                                               # 헤더에 없는 항목은 화면처럼 제외
        body = html[html.index('사이즈 정보'):]
        self.assertLess(body.index('총기장'), body.index('어깨'))                     # input_seq_num 순
        self.assertLess(body.index('>090<'), body.index('>095<'))
        self.assertIn('>64.2<', body)
        self.assertIn('>-<', body)                                                  # 095 어깨 없음
        self.assertNotIn(spec, html)                                                 # API 실측이 있으면 details 이미지 생략
        self.assertNotIn('옵션 사이즈만', html)
        self.assertTrue(any('세정 사이즈 API 실측표 사용' in m for m in logs))

    def test_size_rows_by_color_when_sizes_differ(self):
        module = load()
        colors = OrderedDict([('블랙(06)', ['S', 'M']), ('그레이(04)', ['S'])])
        self.assertEqual(module.size_rows(colors, ['S', 'M']), [('블랙(06)', 'S / M'), ('그레이(04)', 'S')])
        same = OrderedDict([('블랙(06)', ['S', 'M']), ('그레이(04)', ['S', 'M'])])
        self.assertEqual(module.size_rows(same, ['S', 'M']), [('사이즈', 'S / M')])

    def test_helpers(self):
        module = load()
        self.assertEqual(module.split_color('챠콜 그레이(05)'), ('챠콜 그레이', '05'))
        self.assertEqual(module.split_color('IVORY'), ('IVORY', ''))
        self.assertEqual(module.direct_number('https://ovlr.kr/product/x/750/category/1/'), '750')
        self.assertEqual(module.direct_number('https://other.kr/product/detail.html?product_no=750'), '')
        self.assertTrue(module.has_code('티셔츠_OLBAL26W9181', 'OLBAL26W9181'))
        self.assertFalse(module.has_code('티셔츠_OLBAL26W91812', 'OLBAL26W9181'))
        text = (ROOT / 'tests' / 'fixtures' / 'agabang_55675.html').read_text(encoding='utf-8')
        self.assertTrue(module.option_stock(text))


if __name__ == '__main__':
    unittest.main()
