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
from test_oberon_brands import ALL, ROOT, CrawlContext, load, plain_html  # noqa: E402
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
    d.rectangle((700, 200, 900, 225), fill=(20, 20, 20))           # FRONT VIEW
    d.rectangle((300, 300, 1300, 1300), fill=IVORY)                # 큰 컷(아이보리)
    d.rectangle((750, 1450, 850, 1475), fill=(20, 20, 20))         # COLOR
    for cx in (400, 800, 1200):                                    # BLACK / GRAY / IVORY 색상명 한 줄
        d.rectangle((cx - 50, 1550, cx + 50, 1570), fill=(20, 20, 20))
    for cx, c in ((400, BLACK), (800, GRAY), (1200, IVORY)):       # 스와치 300x450 (저장 규격 미달)
        d.rectangle((cx - 150, 1620, cx + 150, 2070), fill=c)
    d.rectangle((700, 2200, 900, 2225), fill=(20, 20, 20))         # DETAIL VIEW
    d.rectangle((300, 2300, 1300, 3100), fill=IVORY)               # 디테일 컷(아이보리 + 검은 지퍼)
    d.rectangle((790, 2300, 810, 3100), fill=BLACK)
    d.rectangle((300, 3300, 1300, 3850), fill=IVORY)               # 모델 착용 컷: 상의 아이보리 + 하의 검정(비율 차이 작음)
    d.rectangle((300, 3850, 1300, 4300), fill=BLACK)
    return jpeg(img)


class RedfaceOcrRecommend(unittest.TestCase):
    def run_crawl(self, ocr, selected=ALL):
        module = load('redface_official_ocr')
        module.ocr_tiles = ocr
        stock = {}
        for n, color in enumerate(('블랙', '그레이', '아이보리')):
            for m, size in enumerate(('090', '095')):
                stock['P' + str(n) + str(m)] = {'option_name_original': ['색상', '사이즈'], 'option_value_orginal': [color, size], 'option_price': 249000}
        page = ('<html><head><meta property="og:image" content="https://theredface.com/web/product/big/main.jpg">'
                '<meta property="product:price:amount" content="249000"></head><body>'
                "<script>var product_name = '루미 구스 헤비 다운 우먼 재킷 REWFJKWBF560'; var option_stock_data = '"
                + json.dumps(stock, ensure_ascii=False).replace('"', '\\"') + "';</script>"
                '<div class="xans-product-addimage"><img src="//theredface.com/web/product/extra/small/ex1.jpg"></div>'
                '<ul option_title="색상"><li option_value="블랙" link_image=""></li><li option_value="그레이" link_image=""></li>'
                '<li option_value="아이보리" link_image=""></li></ul>'
                '<div id="prdDetail"><img ec-data-src="https://theredface.diskn.com/Online/26FW/page_1/REWFJKWBF560.jpg"></div></body></html>')
        routes = {
            'https://theredface.com/product/search.html?keyword=REWFJKWBF560':
                '<ul class="prdList"><li><a href="/product/rewfjkwbf560/4413/category/1/display/1/">루미 재킷 REWFJKWBF560</a></li></ul>',
            'https://theredface.com/product/rewfjkwbf560/4413/category/1/display/1/': page,
            'https://theredface.com/web/product/big/main.jpg': product_shot(BLACK),
            'https://theredface.com/web/product/extra/big/ex1.jpg': product_shot(IVORY),
            'https://theredface.diskn.com/Online/26FW/page_1/REWFJKWBF560.jpg': strip(),
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
        seen = []

        def fake_ocr(ctx, tiles, work):
            seen.append(len(tiles))
            return {0: 'BLACK', 1: 'GRAY', 2: 'IVORY', 3: '루미 구스 헤비', 4: 'FRONT VIEW', 5: 'DETAIL VIEW'}
        rows, failures, error = self.run_crawl(fake_ocr)
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
        def fake_ocr(ctx, tiles, work):
            return {0: 'BLACK', 1: '', 2: ''}               # 그레이·아이보리 글자를 못 읽음
        rows, failures, error = self.run_crawl(fake_ocr)
        self.assertIsNone(error, failures)
        picks = [self.chosen(r) for r in rows]
        self.assertEqual(picks[0], '블랙')
        self.assertEqual(picks[1:], [None] * 4)             # 1위가 글자 없는 스와치 → 아이보리로 추측하지 않음

    def test_no_ocr_means_manual(self):
        rows, failures, error = self.run_crawl(lambda ctx, tiles, work: {})
        self.assertIsNone(error, failures)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(self.chosen(r) is None for r in rows))

    def test_original_brand_untouched(self):
        module = load('redface_official')
        self.assertEqual(module.REVISION, '2026-11-18-redface-4')
        self.assertFalse(hasattr(module, 'recommend'))


if __name__ == '__main__':
    unittest.main()
