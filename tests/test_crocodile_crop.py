"""크로커다일 크롭(던필드몰) — 실제 상품 fixture(CDAABJK4101) + 합성 통이미지로 컷 분할·색상 후보·전달 형식 검사.

2026-11-27 실조회(CDAACJP3202=8686): 갤러리 5장은 카키뿐, 다크그레이는 상세 d_02(COLOR VIEW)·d_04에만 있음.
여기서는 네트워크 없이 같은 구조(갤러리 + 흰 여백으로 나뉜 상세 통이미지 + 사이즈표)를 흉내 낸다.
"""
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'brands')]
from PIL import Image, ImageDraw  # noqa: E402

import crocodile_official_crop as c  # noqa: E402

FIXTURE = (ROOT / 'tests/fixtures/crocodile/CDAABJK4101.html').read_text('utf-8')


def jpeg(image):
    buf = io.BytesIO()
    image.save(buf, 'JPEG', quality=90)
    return buf.getvalue()


def gallery_shot(seed):
    image = Image.new('RGB', (800, 949), (240, 240, 240))
    ImageDraw.Draw(image).rectangle((150, 120, 650, 880), fill=(seed * 40 % 255, 90, 60))
    return jpeg(image)


def detail_sheet():
    """흰 배경 1000x2000: 큰 상품컷 2장(가로 띠 여백으로 분리) + 사이 색상명 글자 띠."""
    image = Image.new('RGB', (1000, 2000), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((180, 150, 820, 850), fill=(110, 125, 100))
    for x in range(420, 580, 22):  # 글자처럼 듬성한 띠
        draw.rectangle((x, 930, x + 12, 960), fill=(30, 30, 30))
    draw.rectangle((180, 1100, 820, 1800), fill=(55, 65, 62))
    return jpeg(image)


def size_chart():
    image = Image.new('RGB', (1000, 600), (255, 255, 255))
    ImageDraw.Draw(image).rectangle((100, 100, 900, 500), outline=(0, 0, 0), width=3)
    return jpeg(image)


def fake_check(url, **kw):
    if url.endswith('size.jpg'):
        data = size_chart()
    elif '/product_images/' in url:
        data = detail_sheet()
    else:
        data = gallery_shot(int(hashlib.md5(url.encode()).hexdigest()[:6], 16))
    with Image.open(io.BytesIO(data)) as im:
        width, height = im.size
    return dict(status='accessible', url=url, final_url=url, data=data, reasons=[], warnings=[], width=width, height=height, format='JPEG')


class Ctx:
    def __init__(self, code, folder, selected=('size', 'price', 'regular_price', 'category', 'main_image', 'additional_images', 'detail_page')):
        self.product_code, self.selected = code, set(selected)
        self.output_dir, self.task_id = folder, 'task1'
        self.rows, self.failures, self.logs = [], [], []
        self.folder = Path(folder) / 'images'
        self.folder.mkdir(parents=True, exist_ok=True)

    def enabled(self, key): return key in self.selected
    def checkpoint(self): pass
    def stage(self, *_): pass
    def log(self, message, *_): self.logs.append(message)
    def fail_item(self, message): self.failures.append(message)

    def save_image(self, source, crop=None):
        image = source.crop(tuple(crop)) if crop else source
        w, h = image.size
        if min(w, h) < 500 or max(w, h) / min(w, h) > 1.8:
            raise ValueError('규격 미달 %dx%d' % (w, h))
        data = jpeg(image.convert('RGB'))
        path = self.folder / (hashlib.sha256(data).hexdigest()[:16] + '.jpg')
        path.write_bytes(data)
        return path

    def emit_local_image(self, record, colors, image_path, mapped_color=None, mapping_evidence='', shared_detail_html=''):
        if mapped_color is not None:
            assert mapped_color in colors and mapping_evidence
        self.rows.append(dict(record=record, colors=colors, path=Path(image_path), mapped=mapped_color, evidence=mapping_evidence, detail=shared_detail_html))


class CrocodileCropTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(c, k) for k in ('find_products', 'get_html', 'check_public_image', 'page_colors', 'ocr_tiles', 'identity')}
        c.find_products = lambda ctx, base: ['8400']
        c.get_html = lambda ctx, url, hosts, **kw: (FIXTURE, url)
        c.check_public_image = fake_check
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        for key, value in self.saved.items():
            setattr(c, key, value)
        self.tmp.cleanup()

    def run_crawl(self, code='CDAABJK4101', **kw):
        ctx = Ctx(code, self.tmp.name, **kw)
        c.crawl(ctx)
        return ctx

    def test_single_color_gallery_premapped_and_detail_cut_into_photos(self):
        ctx = self.run_crawl()
        gallery = [r for r in ctx.rows if '/web/product/' in r['record']['크롭 원본 이미지']]
        detail = [r for r in ctx.rows if '/product_images/' in r['record']['크롭 원본 이미지']]
        self.assertEqual(len(gallery), 5)
        self.assertTrue(all(r['mapped'] == {'color_name': '차콜', 'color_code': '98'} for r in gallery))
        # 상세 4장 모두 같은 합성 이미지라 같은 컷은 1번만(중복 저장 안 함): 위·아래 상품컷 2장
        self.assertEqual(len(detail), 2)
        for r in detail:
            self.assertTrue(r['record']['크롭 좌표'])
            with Image.open(r['path']) as im:
                self.assertGreaterEqual(min(im.size), 500)
        self.assertFalse(any(r['record']['크롭 원본 이미지'].endswith('size.jpg') for r in ctx.rows))
        self.assertTrue(any('사이즈표' in m for m in ctx.logs))
        record = ctx.rows[0]['record']
        self.assertEqual((record['product_code'], record['size'], record['price']), ('CDAABJK4101', '95,100,105,110', 230000))
        self.assertIn('size.jpg', ctx.rows[0]['detail'])  # 상세 HTML에는 사이즈표 유지
        self.assertFalse(ctx.failures)

    def test_multi_color_page_sends_every_cut_unmapped_with_full_palette(self):
        c.page_colors = lambda doc, text, notice: [('카키 47', {'sizes': ['95', '100'], 'prices': [189000], 'image': ''}),
                                                   ('다크그레이 97', {'sizes': ['95', '100'], 'prices': [189000], 'image': ''})]
        c.ocr_tiles = lambda ctx, tiles, work: {i: ('카키 47' if i == 0 else '') for i in range(len(tiles))}
        ctx = self.run_crawl()
        self.assertEqual(ctx.rows[0]['colors'], [{'color_name': '카키', 'color_code': '47'}, {'color_name': '다크그레이', 'color_code': '97'}])
        self.assertEqual(len(ctx.rows), 7)
        gallery = [r for r in ctx.rows if '/web/product/' in r['record']['크롭 원본 이미지']]
        self.assertTrue(all(r['mapped'] is None for r in gallery))  # 갤러리는 색상 근거 없음 → 사람이 매핑
        mapped = [r for r in ctx.rows if r['mapped']]
        self.assertEqual(len(mapped), 1)  # 글자 띠에 가장 가까운 컷 하나만
        self.assertEqual(mapped[0]['mapped'], {'color_name': '카키', 'color_code': '47'})
        self.assertIn('Windows OCR', mapped[0]['evidence'])
        self.assertEqual(ctx.rows[0]['record']['size'], '95,100')

    def test_requested_color_limits_palette(self):
        c.page_colors = lambda doc, text, notice: [('카키 47', {'sizes': [], 'prices': [], 'image': ''}),
                                                   ('다크그레이 97', {'sizes': [], 'prices': [], 'image': ''})]
        original = c.identity
        c.identity = lambda doc, text, base, info: (lambda r: (r[0], r[1], '97') if r else r)(original(doc, text, base, info))
        ctx = self.run_crawl('CDAABJK4101_97')
        self.assertEqual(ctx.rows[0]['colors'], [{'color_name': '다크그레이', 'color_code': '97'}])

    def test_color_view_side_by_side_small_cuts_widened_on_white_and_labelled_separately(self):
        """실제 d_02(COLOR VIEW 1000x838) 배치: 상품 333x350 두 장 나란히, 아래 한 줄에 '카키 47'·'다크그레이 97'."""
        image = Image.new('RGB', (1000, 838), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        for x in range(420, 580, 20):
            draw.rectangle((x, 110, x + 12, 145), fill=(20, 20, 20))
        draw.rectangle((119, 355, 452, 705), fill=(120, 130, 100))
        draw.rectangle((556, 355, 882, 705), fill=(50, 60, 60))
        for x in (250, 262, 276, 288, 680, 692, 704, 716, 736, 748):
            draw.rectangle((x, 762, x + 9, 782), fill=(30, 30, 30))
        ctx, tiles = Ctx('X', self.tmp.name), []
        cuts = c.cuts_from(ctx, 'd_02.jpg', image, set(), tiles, split=True)
        self.assertEqual(len(cuts), 2)
        self.assertEqual(sorted(key for _, _, key, _ in cuts), [0, 1])  # 컷마다 바로 아래 자기 색상명
        for path, box, _, _ in cuts:
            with Image.open(path) as im:
                self.assertEqual(im.size, (500, 500))
        self.assertLess(cuts[0][1][2], 556)  # 왼쪽 컷이 오른쪽 상품을 침범하지 않음
        self.assertGreater(cuts[1][1][0], 452)

    def test_images_required(self):
        with self.assertRaises(c.CrawlError):
            self.run_crawl(selected=('size',))

    def test_ocr_color_matching(self):
        palette = [{'color_name': '카키', 'color_code': '47'}, {'color_name': '다크그레이', 'color_code': '97'}, {'color_name': '그레이', 'color_code': '95'}]
        self.assertEqual(c.match_color('카키 47', palette)[0]['color_code'], '47')
        self.assertEqual(c.match_color('Khaki', palette)[0]['color_code'], '47')
        self.assertEqual(c.match_color('다크그레이 97', palette)[0]['color_code'], '97')
        self.assertEqual(c.match_color('Dark Grey', palette)[0]['color_code'], '97')
        self.assertIsNone(c.match_color('카키 다크그레이', palette)[0])
        self.assertEqual(c.english('라이트베이지'), 'LIGHT BEIGE')


if __name__ == '__main__':
    unittest.main()
