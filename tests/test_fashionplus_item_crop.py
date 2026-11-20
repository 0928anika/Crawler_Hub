"""패션플러스 크롭필요건(fashionplus_item_crop): 품목번호 직접 조회 + 통짜 이미지 분할 + OCR 사전 매핑 fixture 검사."""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app')]
from crawler_sdk import CrawlContext  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

LINK = 'https://www.fashionplus.co.kr/goods/detail/416083574'
IMG = 'https://img.fashionplus.co.kr/mall/assets/product_img/36407/'


def load():
    spec = importlib.util.spec_from_file_location('fashionplus_item_crop', ROOT / 'brands' / 'fashionplus_item_crop.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jpeg(image):
    buf = io.BytesIO()
    image.save(buf, 'JPEG', quality=90)
    return buf.getvalue()


def strip():
    """747px 통짜: 단독컷 3장(약 350px 폭, 흰 여백) → DETAIL 글자 → 큰 디테일컷 → 단체컷 + 아래 색상명 3단어 → INFO 글자줄."""
    img = Image.new('RGB', (747, 5200), 'white')
    d = ImageDraw.Draw(img)
    for y, c in ((100, (210, 190, 160)), (750, (200, 200, 205)), (1400, (40, 50, 80))):
        d.rectangle((200, y, 547, y + 507), fill=c)
    d.rectangle((330, 2050, 420, 2080), fill=(20, 20, 20))            # DETAIL
    d.rectangle((60, 2200, 690, 3100), fill=(205, 185, 155))           # 디테일컷 630x900
    d.rectangle((60, 3400, 690, 3750), fill=(120, 120, 120))           # 단체컷(가로로 긴)
    for cx in (180, 370, 560):
        d.rectangle((cx - 50, 3820, cx + 50, 3840), fill=(30, 30, 30))  # 베이지(BE) 그레이(GR) 네이비(NA)
    for k in range(6):
        d.rectangle((250, 4100 + k * 40, 500, 4112 + k * 40), fill=(60, 60, 60))  # INFO 글자줄
    return jpeg(img)


PAGE = ('<html><head><meta property="og:url" content="' + LINK + '"></head><body>'
        '<div class="m_prodetail-head"><div class="mm_breadcrumb"><a href="/brand-shop/1">레드페이스 ></a>'
        '<a href="/goods/category/1">여성</a><a href="/goods/category/2">잡화</a></div></div>'
        '<div class="m__product-info"><p class="text_product">브리즈 마스크 REEGMKXBE1016_IC</p></div>'
        '<dl class="discount-detail__row"><dt>판매가</dt><dd>21,750원</dd></dl>'
        '<dl class="discount-detail__row"><dt>희망소비자가</dt><dd>29,000원</dd></dl>'
        '<section><h4>상품정보 제공고시</h4><table><tr><th>상품번호</th><td>REEGMKXBE1016_IC</td></tr></table></section>'
        '<div class="m_prodetail-head-thumbnail"><a class="btn_thumbnail"><i data-preload="{ \'_src\': \'' + IMG + 'plg36407_REEGMKXBE1016.jpg?RS=800x1072&AR=0\' }"></i></a></div>'
        '</body></html>')
OPTIONS = [{'_name': '브리즈 마스크 REEGMKXBE1016_IC', 'options': [
    {'_name': '그레이 free', '_price': 21750, '_stock': 48, '_id': 1},
    {'_name': '네이비 free', '_price': 21750, '_stock': 50, '_id': 2},
    {'_name': '베이지 free', '_price': 21750, '_stock': 49, '_id': 3}]}]
BODY = ('<div class="m_prodetail-desc"><div class="mm_pinchzoom-wrapper">'
        '<img src="http://img.fashionplus.co.kr/mall/assets/brand_info/brandImagebar.jpg">'
        '<img data-preload="{ \'_src\': \'' + IMG + 'plgk36407_REEGMKXBE1016.jpg\' }">'
        '<img src="' + IMG + 'plgr36407_REEGMKXBE1016.jpg">'
        '<img src="https://img.fashionplus.co.kr/mall/event/220413/moda_banner.jpg">'
        '<p>브리즈 마스크</p></div></div>')


class FashionplusCrop(unittest.TestCase):
    def run_crop(self, selected, ocr=None, options=None):
        module = load()
        if ocr is not None:
            module.ocr_tiles = ocr
        main = Image.new('RGB', (1000, 1000), (215, 195, 165))
        routes = {LINK: PAGE.encode(), LINK + '/fetch-option-data': json.dumps(options or OPTIONS, ensure_ascii=False).encode(),
                  LINK + '/description-iframe': BODY.encode(), IMG + 'plg36407_REEGMKXBE1016.jpg': jpeg(main),
                  IMG + 'plgk36407_REEGMKXBE1016.jpg': strip()}
        calls = []

        def fetch(ctx, url, limit=0):
            calls.append(url)
            if url not in routes:
                raise module.MissingResource('HTTP 404: ' + url)
            return routes[url]
        module.fetch = fetch
        meta = json.loads((ROOT / 'brands' / 'fashionplus_item_crop.json').read_text(encoding='utf-8'))
        events, error = [], None
        with tempfile.TemporaryDirectory() as folder:
            ctx = CrawlContext('unused', Path(folder) / 'result.xlsx', 0, selected, meta['output_headers'], lambda: None,
                               lambda kind, **data: events.append((kind, data)), input_item={'품번': '416083574'},
                               mapping_mode=meta['mapping_mode'], image_mode=meta['image_mode'])
            try:
                module.crawl(ctx)
                ctx.finish()
            except Exception as exc:
                error = exc
        rows = [d for k, d in events if k == 'row' and not d.get('header')]
        headers = [d['row'] for k, d in events if k == 'row' and d.get('header')]
        records = [dict(dict(zip(headers[-1], d['row'])), _unmapped=d.get('unmapped')) for d in rows] if headers else []
        failures = [d['message'] for k, d in events if k == 'item_failure']
        return records, failures, error, calls

    def test_cuts_widen_into_real_background_and_exclude_group_and_text(self):
        rows, failures, error, calls = self.run_crop(['main_image', 'additional_images', 'size', 'price', 'regular_price', 'category', 'detail_page'])
        self.assertIsNone(error, failures)
        crops = [json.loads(r['크롭 좌표']) for r in rows if r['크롭 좌표']]
        self.assertEqual(len(rows), 6)            # 갤러리 대표 1 + 단독컷 3 + 디테일 1 + 단체컷 1(실제 흰 배경으로 넓혀 후보, 글자 제외)
        self.assertEqual(len(crops), 5)
        self.assertTrue(all(not r.get('흰 여백 보충') for r in rows))
        for l, t, r, b in crops:
            self.assertGreaterEqual(min(r - l, b - t), 500)
            self.assertLessEqual(max(r - l, b - t) / min(r - l, b - t), 1.8)
            self.assertLessEqual(b, 3820) if t < 3820 else None   # 색상명 글자(3820~) 미포함
        r = rows[0]
        self.assertEqual((r['품번'], r['상품명'], r['사이즈'], r['판매가'], r['정상가'], r['카테고리']),
                         ('REEGMKXBE1016_IC', '브리즈 마스크', 'free', 21750, 29000, '여성 > 잡화'))
        self.assertEqual(r['원본 상품명'], '브리즈 마스크 REEGMKXBE1016_IC')
        self.assertEqual(sorted(c['color_name'] for c in r['_unmapped']['colors']), ['그레이', '네이비', '베이지'])
        self.assertNotIn('brandImagebar', r['상세페이지 HTML'])
        self.assertNotIn('moda_banner', r['상세페이지 HTML'])
        self.assertTrue(all(not x['_unmapped'].get('preassigned_color_id') for x in rows))   # OCR 없이는 사전 매핑 없음
        self.assertFalse(any('RS=' in u for u in calls if 'product_img' in u))             # 리사이즈 아닌 원본 요청

    def test_ocr_label_below_single_cut_maps_only_that_cut(self):
        module = load()
        img = Image.new('RGB', (900, 2000), 'white')
        d = ImageDraw.Draw(img)
        d.rectangle((150, 100, 750, 800), fill=(40, 50, 80))
        d.rectangle((380, 860, 520, 885), fill=(20, 20, 20))       # 사진 바로 아래 '네이비(NA)'
        d.rectangle((150, 1000, 750, 1700), fill=(210, 190, 160))  # 다음 사진: 아래 글자 없음
        photos, labels = module.segment(img)
        self.assertEqual(photos[0][1][0], 0)
        self.assertIsNone(photos[1][1])
        self.assertIsNone(photos[0][2])
        palette = [{'color_name': '그레이', 'color_code': ''}, {'color_name': '네이비', 'color_code': ''}]
        self.assertEqual(module.match_color('네이비(NA)', palette)[0]['color_name'], '네이비')

    def test_flat_shoes_get_white_padding_only_when_real_background_is_not_enough(self):
        # 288624429(공용 인디고워킹화 RESGLCX241041_IC) 실측 배치: 796px 폭, 신발 442x294, 바로 아래 색상명, 위는 그림
        module = load()
        img = Image.new('RGB', (796, 2900), 'white')
        d = ImageDraw.Draw(img)
        d.rectangle((0, 376, 795, 883), fill=(225, 30, 40))                 # 위쪽 그림·띠
        for top, color, label in ((1007, (235, 180, 190), (342, 1331, 450, 1365)),
                                  (1521, (60, 70, 100), (317, 1854, 478, 1889)),
                                  (2073, (215, 205, 185), (321, 2406, 473, 2442))):
            d.rectangle((173, top, 615, top + 294), fill=color)              # 핑크·네이비·베이지 신발
            d.rectangle(label, fill=(30, 30, 30))                            # 핑크(P)·네이비(NA)·베이지(BE)
        photos, labels = module.segment(img)
        shoes = [p for p in photos if p[0][1] > 885]
        self.assertIsNone([p for p in photos if p[0][1] < 885][0][1])        # 위쪽 그림은 아래 '핑크' 글자를 받지 않음
        self.assertEqual(len(shoes), 3)
        for (l, t, r, b), key, pad in shoes:
            extra = pad[1] if pad else (0, 0, 0, 0)
            self.assertGreaterEqual(min(r - l + extra[0] + extra[2], b - t + extra[1] + extra[3]), 500)
            self.assertIsNotNone(key)                                        # 아래 색상명 연결 유지
        pink, navy, beige = shoes
        self.assertIsNotNone(pink[2])                                        # 실제 흰 배경 448px뿐 → 흰 여백 보충
        self.assertIsNotNone(navy[2])
        self.assertIsNone(beige[2])                                          # 517px 실제 배경으로 충분 → 보충 없음
        self.assertEqual(pink[2][0], (255, 255, 255))
        self.assertLessEqual(max(pink[2][1]), 200)
        self.assertGreater(pink[0][1], 883)                                  # 위쪽 그림은 포함하지 않음
        self.assertLess(pink[0][3], 1331)                                    # 아래 색상명 글자도 포함하지 않음
        self.assertLess(navy[0][3], 1854)
        self.assertGreater(navy[0][1], 1365)

    def test_padding_never_added_on_non_white_background(self):
        module = load()
        gray = Image.new('RGB', (796, 1600), (200, 200, 200))                # 회색 배경: 보충 불가
        d = ImageDraw.Draw(gray)
        d.rectangle((0, 300, 795, 500), fill=(20, 20, 20))
        d.rectangle((173, 560, 615, 854), fill=(60, 70, 100))
        d.rectangle((300, 880, 480, 910), fill=(20, 20, 20))
        photos, _ = module.segment(gray)
        self.assertFalse(any(p[2] for p in photos))
        self.assertIsNone(module.solid_background(gray))

    def test_image_fields_required_and_no_requests(self):
        rows, failures, error, calls = self.run_crop(['price'])
        self.assertIsNotNone(error)
        self.assertEqual(calls, [])


    def test_size_only_options_fail_instead_of_fake_color(self):
        sizes_only = [{'_name': '사이즈', 'options': [{'_name': 'free', '_price': 21750, '_stock': 3, '_id': 9}]}]
        rows, failures, error, calls = self.run_crop(['main_image'], options=sizes_only)
        self.assertIsNotNone(error)
        self.assertTrue(any('색상 후보' in f for f in failures))
        self.assertEqual(rows, [])


if __name__ == '__main__':
    unittest.main()
