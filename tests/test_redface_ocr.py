"""레드페이스 OCR 추천 버전(redface_official_ocr) 오프라인 검사.

실사이트가 아니라 2026-11-19 실조회한 통이미지 구조(제목 → FRONT VIEW 큰 컷 → COLOR 색상명 한 줄 + 작은 스와치
→ DETAIL VIEW 컷)를 본뜬 fixture다. Windows OCR은 가짜 함수로 대체한다.
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_oberon_brands import ALL, ROOT, CrawlContext, load  # noqa: E402
try:
    from test_oberon_brands import plain_html  # noqa: E402
except ImportError:  # develop의 테스트 도우미에는 없음 — 이 검사는 상세 HTML 문자열을 비교하지 않는다
    def plain_html(record):
        return record
from PIL import Image, ImageDraw  # noqa: E402

BLACK, GRAY, IVORY = (25, 25, 25), (110, 110, 110), (226, 214, 198)


def jpeg(img):
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=92)
    return buf.getvalue()


def product_shot(color, size=(1000, 1000)):
    img = Image.new('RGB', size, 'white')
    ImageDraw.Draw(img).rectangle((200, 150, 800, 900), fill=color)
    return jpeg(img)


def strip():
    img = Image.new('RGB', (1600, 4500), 'white')
    d = ImageDraw.Draw(img)
    d.rectangle((600, 60, 1000, 90), fill=(20, 20, 20))            # 상품명 제목
    d.rectangle((700, 200, 900, 225), fill=(20, 20, 20))           # FRONT VIEW (폭 201)
    d.rectangle((300, 300, 1300, 1300), fill=IVORY)                # 큰 컷(아이보리)
    d.rectangle((730, 1450, 870, 1475), fill=(20, 20, 20))         # COLOR (폭 141)
    for cx, half in ((396, 50), (800, 55), (1204, 60)):            # BLACK / GRAY / IVORY 색상명 한 줄 (폭 101·111·121)
        d.rectangle((cx - half, 1550, cx + half, 1570), fill=(20, 20, 20))
    for (l, r), c in (((228, 564), BLACK), ((632, 968), GRAY), ((1037, 1372), IVORY)):  # 실측 위치의 스와치 336x451, 간격 68
        d.rectangle((l, 1620, r, 2071), fill=c)
    d.rectangle((690, 2200, 910, 2225), fill=(20, 20, 20))         # DETAIL VIEW (폭 221)
    d.rectangle((300, 2300, 1300, 3100), fill=IVORY)               # 디테일 컷(아이보리 + 검은 지퍼)
    d.rectangle((790, 2300, 810, 3100), fill=BLACK)
    d.rectangle((300, 3300, 1300, 3850), fill=IVORY)               # 모델 착용 컷: 상의 아이보리 + 하의 검정(비율 차이 작음)
    d.rectangle((300, 3850, 1300, 4300), fill=BLACK)
    return jpeg(img)


def ocr_by_width(table, seen=None):
    """가짜 Windows OCR: 글자 타일 폭(단어 폭 + 양쪽 여백)으로 어떤 글자인지 정한다."""
    def fake(ctx, tiles, work):
        if seen is not None:
            seen.append(sorted(t.width for t in tiles))
        return {i: table.get(t.width, '') for i, t in enumerate(tiles)}
    return fake


JACKET = {101 + 38: 'BLACK', 111 + 38: 'GRAY', 121 + 38: 'IVORY', 141 + 38: 'COLOR', 201 + 38: 'FRONT VIEW', 221 + 38: 'DETAIL VIEW', 401 + 38: '루미 구스 헤비'}


def mask_strip():
    """REEGMKX24101 형태: 834px 폭, 사진(약 445px) '아래'에 색상명. 네이비 → 네이비(NA) → 그레이 → 그레이(GR)."""
    img = Image.new('RGB', (834, 1700), 'white')
    d = ImageDraw.Draw(img)
    d.rectangle((300, 40, 520, 70), fill=(20, 20, 20))             # 제목 (폭 221)
    d.rectangle((195, 150, 640, 760), fill=(35, 45, 95))           # 네이비 컷 446x611
    d.rectangle((345, 800, 495, 830), fill=(20, 20, 20))           # 네이비(NA) (폭 151) — 사진 바로 아래
    d.rectangle((195, 960, 640, 1570), fill=(185, 185, 185))       # 그레이 컷
    d.rectangle((340, 1610, 500, 1640), fill=(20, 20, 20))         # 그레이(GR) (폭 161)
    return jpeg(img)


MASK = {221 + 20: '아이스 멜란 마스크', 151 + 20: '네이비(NA)', 161 + 20: '그레이(GR)'}  # 834px 폭 → 여백 10px씩


class RedfaceOcrRecommend(unittest.TestCase):
    def run_crawl(self, ocr, selected=ALL, colors=('블랙', '그레이', '아이보리'), detail=None, gallery=(BLACK, IVORY)):
        module = load('redface_official_ocr')
        module.ocr_tiles = ocr
        stock = {}
        for n, color in enumerate(colors):
            for m, size in enumerate(('090', '095')):
                stock['P' + str(n) + str(m)] = {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': [color, size], 'option_price': 249000}
        page = ('<html><head><meta property="og:image" content="https://theredface.com/web/product/big/main.jpg">'
                '<meta property="product:price:amount" content="249000"></head><body>'
                "<script>var product_name = '루미 구스 헤비 다운 우먼 재킷 REWFJKWBF560'; var option_stock_data = '"
                + json.dumps(stock, ensure_ascii=False).replace('"', '\\"') + "';</script>"
                '<div class="xans-product-addimage"><img src="//theredface.com/web/product/extra/small/ex1.jpg"></div>'
                '<ul option_title="색상">' + ''.join('<li option_value="' + c + '" link_image=""></li>' for c in colors) + '</ul>'
                '<div id="prdDetail"><img ec-data-src="https://theredface.diskn.com/Online/26FW/page_1/REWFJKWBF560.jpg"></div></body></html>')
        routes = {
            'https://theredface.com/product/search.html?keyword=REWFJKWBF560':
                '<ul class="prdList"><li><a href="/product/rewfjkwbf560/4413/category/1/display/1/">루미 재킷 REWFJKWBF560</a></li></ul>',
            'https://theredface.com/product/rewfjkwbf560/4413/category/1/display/1/': page,
            'https://theredface.com/web/product/big/main.jpg': product_shot(gallery[0]),
            'https://theredface.com/web/product/extra/big/ex1.jpg': product_shot(gallery[1]),
            'https://theredface.diskn.com/Online/26FW/page_1/REWFJKWBF560.jpg': detail or strip(),
        }

        def request(ctx, url, limit=0, optional=False, site_only=True, timeout=25):
            url = module.public_url(url)
            if url not in routes:
                if optional:
                    return None
                raise module.CrawlError('HTTP 404: ' + url)
            value = routes[url]
            return value if isinstance(value, bytes) else value.encode()
        module.request = request
        meta = json.loads((ROOT / 'brands' / 'redface_official_ocr.json').read_text(encoding='utf-8'))
        events, error = [], None
        with tempfile.TemporaryDirectory() as folder:
            ctx = CrawlContext('unused', Path(folder) / 'result.xlsx', 0, selected, meta['output_headers'], lambda: None,
                               lambda kind, **data: events.append((kind, data)), input_item={'품번': 'REWFJKWBF560'},
                               mapping_mode=meta['mapping_mode'], image_mode=meta['image_mode'])
            try:
                module.crawl(ctx)
                ctx.finish()
            except Exception as exc:
                error = exc
        rows = [d for k, d in events if k == 'row' and not d.get('header')]
        headers = [d['row'] for k, d in events if k == 'row' and d.get('header')][-1]
        records = [plain_html(dict(dict(zip(headers, d['row'])), _unmapped=d.get('unmapped'))) for d in rows]
        return records, [d['message'] for k, d in events if k == 'item_failure'], error

    @staticmethod
    def chosen(row):
        info = row['_unmapped']
        pid = info.get('preassigned_color_id')
        return next((c['color_name'] for c in info['colors'] if c.get('id') == pid), pid) if pid else None

    def test_swatch_labels_recommend_colors_for_big_cuts(self):
        rows, failures, error = self.run_crawl(ocr_by_width(JACKET))
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 5)                      # 갤러리 2 + 큰 컷 + 디테일 + 모델 컷 (스와치는 저장 안 함)
        for r in rows:
            if r['크롭 좌표']:
                l, t, rr, b = json.loads(r['크롭 좌표'])
                self.assertGreaterEqual(min(rr - l, b - t), 500)
        picks = [self.chosen(r) for r in rows]
        self.assertEqual(picks[:4], ['블랙', '아이보리', '아이보리', '아이보리'])
        self.assertIsNone(picks[4])                         # 아이보리 55% vs 검정 45% → 차이 작아 미매핑
        self.assertIn('OCR 스와치 추천', rows[0]['OCR 근거'])
        self.assertIn('BLACK=블랙', rows[0]['OCR 근거'])
        self.assertEqual(rows[4]['OCR 근거'], '')

    def test_unread_swatch_blocks_recommendation(self):
        table = dict(JACKET)
        table[111 + 38] = table[121 + 38] = ''              # 그레이·아이보리 글자를 못 읽음
        rows, failures, error = self.run_crawl(ocr_by_width(table))
        self.assertIsNone(error, failures)
        picks = [self.chosen(r) for r in rows]
        self.assertEqual(picks[0], '블랙')
        self.assertEqual(picks[1:], [None] * 4)             # 1위가 글자 없는 스와치 → 아이보리로 추측하지 않음

    def test_no_ocr_means_manual(self):
        rows, failures, error = self.run_crawl(lambda ctx, tiles, work: {})
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(self.chosen(r) is None for r in rows))

    def test_unread_own_label_does_not_borrow_neighbor(self):
        table = dict(MASK)
        table[161 + 20] = ''                                  # 그레이(GR) 글자를 못 읽음
        rows, failures, error = self.run_crawl(ocr_by_width(table), colors=('그레이', '네이비'), detail=mask_strip(),
                                               gallery=((35, 45, 95), (185, 185, 185)))
        self.assertIsNone(error, failures)
        crops = [r for r in rows if r['크롭 좌표']]
        self.assertEqual([self.chosen(r) for r in crops], ['네이비', None])  # 위의 '네이비(NA)'는 네이비 컷 몫 → 빌려오지 않음

    def test_label_below_photo_maps_nearest_and_recommends_gallery(self):
        # 사진 아래 색상명: 각 컷은 가장 가까운 글자(바로 아래)로. 이전 'above' 규칙처럼 다음 사진에 밀려 붙지 않는다.
        rows, failures, error = self.run_crawl(ocr_by_width(MASK), colors=('그레이', '네이비'), detail=mask_strip(),
                                               gallery=((35, 45, 95), (185, 185, 185)))
        self.assertIsNone(error, failures)
        crops = [r for r in rows if r['크롭 좌표']]
        self.assertEqual(len(crops), 2)                       # 446px 컷은 주변 흰 배경으로만 500px까지 넓혀 저장
        for r in crops:
            l, t, rr, b = json.loads(r['크롭 좌표'])
            self.assertGreaterEqual(min(rr - l, b - t), 500)
        self.assertEqual([self.chosen(r) for r in crops], ['네이비', '그레이'])
        self.assertIn('네이비(NA)', crops[0]['OCR 근거'])
        gallery = [r for r in rows if not r['크롭 좌표']]
        self.assertEqual([self.chosen(r) for r in gallery], ['네이비', '그레이'])  # 색상명 붙은 컷을 기준으로 추천
        self.assertIn('색상명 붙은 컷', gallery[0]['OCR 근거'])

    def test_separate_from_other_redface_brands(self):
        module = load('redface_official_ocr')
        self.assertEqual(module.LABEL_POSITION, 'nearest')
        meta = json.loads((ROOT / 'brands' / 'redface_official_ocr.json').read_text(encoding='utf-8'))
        self.assertEqual((meta['id'], meta['script']), ('redface_official_ocr', 'redface_official_ocr.py'))

if __name__ == '__main__':
    unittest.main()
