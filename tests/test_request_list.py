"""결과 엑셀(양식이 조금씩 다른 여러 유형) → 3P Listing '요청 목록' 변환 검사."""
import io
import sys
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import request_list as rl  # noqa: E402

HUB_HEADERS = ['품번', '상품명', '색상', '상품 링크', '색상명', '색상코드', '사이즈', '판매가', '정상가', '카테고리', '대표이미지', '추가이미지', '상세페이지 HTML', '원본 상품명']
STANDALONE_HEADERS = ['대표이미지_미리보기', 'URL', '품번', '색상', '검색어', '브랜드', '카테고리', '품절상태', '대표이미지(HTML)', '노출상품명', '판매가', '정상가',
                      '옵션정보', '상세페이지(HTML)', '대표이미지_URL', '상세페이지_URL', '상세페이지_URL(2)', '기타이미지', '상세페이지_링크', '이미지점검', '사이즈별재고']


def book(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def hub_file():
    return book({'결과': [HUB_HEADERS,
                         ['ZAD4JP403', 'PUFFER DOWN', 'IVORY (IV)', 'https://z.kr/1', 'IVORY', 'IV', 'S,M,L', 199500, 950000, 'OUTER',
                          'https://cdn/a.jpg', 'https://cdn/b.jpg,https://cdn/c.jpg', '<p>상세</p>', 'PUFFER DOWN [IVORY]'],
                         ['ZAD4JP403', 'PUFFER DOWN', 'BLACK (BK)', 'https://z.kr/1', 'BLACK', 'BK', '090,095', 199500, None, 'OUTER',
                          'https://cdn/k.jpg', '', '', '']],
                 '작업처리로그': [['입력 행', '품번', '상태'], [2, 'ZAD4JP403', 'SUCCESS']]})


def standalone_file():
    row = [None, 'https://shop/1', 'AWJU67301', '블랙', 'AWJU67301', '올포유', '여성 > 아우터', '판매중', '<img>', '여성 패딩 점퍼', '59,000', '129,000',
           '색상: 블랙 / 사이즈: 090, 095 / 품절사이즈: 100', '<div>상세</div>', 'https://img/main.jpg', 'https://img/d1.jpg?RS=1,https://img/d2.jpg', 'https://img/d10.jpg',
           '', 'https://shop/1', 'OK', '090:3, 095:있음, 100:0']
    return book({'크롤링결과': [STANDALONE_HEADERS, row], '요약': [['품번', '행수'], ['AWJU67301', 1]]})


def convert(entries, **kw):
    files = []
    for name, data, brand in entries:
        info = rl.inspect(data, name)
        files.append(dict(name=name, data=data, sheet=info['sheet'], header_row=info['header_row'], mapping=info['mapping'], brand=brand or info['brand']))
    content, summary = rl.convert(files, **kw)
    ws = load_workbook(io.BytesIO(content))['요청 목록']
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=5)]
    return ws, rows, summary


class RequestListTests(unittest.TestCase):
    def test_hub_result_expands_sizes_and_builds_registered_name(self):
        info = rl.inspect(hub_file(), 'hub.xlsx')
        self.assertEqual(info['kind'], 'Crawler Hub 결과')
        self.assertEqual((info['sheet'], info['header_row']), ('결과', 1))
        self.assertEqual(info['mapping']['color'], '색상명')                      # 'IVORY (IV)'보다 색상명 우선
        self.assertEqual(info['mapping']['url'], '상품 링크')
        ws, rows, summary = convert([('hub.xlsx', hub_file(), '젠사이')])
        self.assertEqual([c.value for c in ws[4]], rl.OUTPUT)                      # 양식 머리글 그대로
        self.assertIn('필수 정보', ws['A1'].value)
        self.assertEqual([r[2] for r in rows], ['S', 'M', 'L', '090', '095'])       # 사이즈별 1행, 앞자리 0 보존
        first = rows[0]
        self.assertEqual(first[:6], ['ZAD4JP403', '젠사이 PUFFER DOWN ZAD4JP403', 'S', 'IVORY(IV)', 199500, 950000])
        self.assertEqual(first[6:], ['https://cdn/a.jpg', '<p>상세</p>', 'https://cdn/b.jpg,https://cdn/c.jpg', 'OUTER', 'https://z.kr/1'])
        self.assertEqual(rows[3][5], '공홈 조회')                                   # 빈 정상가
        self.assertEqual(summary['rows'], 5)
        self.assertEqual(ws.auto_filter.ref, 'A4:K9')
        self.assertEqual(ws['A5'].fill.fgColor.theme, ws['A6'].fill.fgColor.theme)  # 양식 데이터 행 서식 복사

    def test_standalone_crawler_layout(self):
        info = rl.inspect(standalone_file(), 'old.xlsx')
        self.assertEqual(info['kind'], '단독 크롤러 결과')
        self.assertEqual(info['brand'], '올포유')
        self.assertEqual(info['mapping']['main'], '대표이미지_URL')
        self.assertEqual(info['mapping']['extra'], '상세페이지_URL')
        self.assertEqual(info['mapping']['extra2'], '상세페이지_URL(2)')
        self.assertEqual(info['mapping']['size'], '사이즈별재고')
        self.assertEqual(info['mapping']['url'], 'URL')
        ws, rows, _ = convert([('old.xlsx', standalone_file(), '')])
        self.assertEqual([r[2] for r in rows], ['090', '095', '100'])
        self.assertEqual(rows[0][1], '올포유 여성 패딩 점퍼 AWJU67301')
        self.assertEqual(rows[0][4:6], [59000, 129000])
        self.assertEqual(rows[0][8], 'https://img/d1.jpg?RS=1,https://img/d2.jpg,https://img/d10.jpg')

    def test_option_info_column_when_no_size_column(self):
        self.assertEqual(rl.sizes_of('색상: 블랙 / 사이즈: 090, 095 / 품절사이즈: 100', '옵션정보'), ['090', '095', '100'])
        self.assertEqual(rl.sizes_of('FREE', '사이즈'), ['FREE'])
        self.assertEqual(rl.sizes_of('S/M\nL', '사이즈'), ['S', 'M', 'L'])

    def test_shifted_and_reordered_headers_are_found(self):
        data = book({'Sheet1': [['3P 등록 취합본'], [], ['비고', '컬러', '상품코드', 'SIZE', '상품명', '가격', '대표이미지', 'URL'],
                                ['메모', 'NAVY', 'AB1234', 'M,L', '니트', '35,000원', 'https://i/1.jpg', 'https://s/1']]})
        info = rl.inspect(data, 'custom.xlsx')
        self.assertEqual((info['header_row'], info['kind']), (3, '기타 양식'))
        self.assertEqual(info['mapping'], {'code': '상품코드', 'name': '상품명', 'size': 'SIZE', 'color': '컬러', 'price': '가격', 'main': '대표이미지', 'url': 'URL'})
        _, rows, _ = convert([('custom.xlsx', data, '브랜드A')])
        self.assertEqual([r[:5] for r in rows], [['AB1234', '브랜드A 니트 AB1234', 'M', 'NAVY', 35000], ['AB1234', '브랜드A 니트 AB1234', 'L', 'NAVY', 35000]])

    def test_existing_request_list_keeps_registered_name(self):
        data = book({'요청 목록': [['안내'], ['안내'], ['필수'], rl.OUTPUT,
                                ['PCW51GJP109', '탈렌트키즈 보네샐리점퍼 PCW51GJP109', '110', '분홍색', '19900', '99900', 'https://a', '<div/>', 'https://b', '유아동', 'https://u']]})
        info = rl.inspect(data, 'req.xlsx')
        self.assertEqual((info['kind'], info['header_row']), ('3P 요청 목록', 4))
        _, rows, _ = convert([('req.xlsx', data, '다른브랜드')])
        self.assertEqual(rows[0][1], '탈렌트키즈 보네샐리점퍼 PCW51GJP109')

    def test_name_does_not_repeat_brand_or_code(self):
        self.assertEqual(rl.registered_name('올포유', '올포유 패딩 AWJU67301', 'AWJU67301'), '올포유 패딩 AWJU67301')
        self.assertEqual(rl.registered_name('', '패딩', 'AW1'), '패딩 AW1')

    def test_duplicates_across_files_and_blank_price_option(self):
        ws, rows, summary = convert([('a.xlsx', hub_file(), 'Z'), ('b.xlsx', hub_file(), 'Z')], fill_blank_price=False)
        self.assertEqual(len(rows), 5)
        self.assertEqual(summary['files'][1].get('중복 제외'), 5)
        self.assertIn(rows[3][5], ('', None))

    def test_errors_are_clear(self):
        with self.assertRaisesRegex(ValueError, '품번 열'):
            rl.inspect(book({'S': [['이름', '가격'], ['a', 1]]}), 'x.xlsx')
        with self.assertRaisesRegex(ValueError, '열 수 없습니다'):
            rl.inspect(b'not excel', 'bad.xlsx')
        info = rl.inspect(hub_file(), 'hub.xlsx')
        with self.assertRaisesRegex(ValueError, '찾을 수 없습니다'):
            rl.convert([dict(name='hub.xlsx', data=hub_file(), sheet='결과', header_row=1, mapping=dict(info['mapping'], size='없는열'), brand='')])

    def test_real_uploaded_hub_result_if_present(self):
        path = Path('/mnt/user-data/uploads/5ca6a012-crawler-result.xlsx')
        if not path.exists():
            self.skipTest('사용자 업로드 파일 없음')
        _, rows, _ = convert([('crawler-result.xlsx', path.read_bytes(), '젠사이')])
        self.assertEqual([r[2] for r in rows], ['S', 'M', 'L'])
        self.assertEqual(rows[0][1], '젠사이 PUFFER DOWN ZAD4JP403')

    def test_cells_are_not_text_formatted_so_formulas_work(self):
        ws, rows, _ = convert([('hub.xlsx', hub_file(), '젠사이')])
        for row in ws.iter_rows(min_row=5, max_row=6):
            self.assertTrue(all(c.number_format != '@' for c in row))           # 텍스트 서식이면 수식이 문자로 보임
        self.assertEqual(ws['C8'].value, '090')                                 # 앞자리 0은 문자열로 유지
        self.assertIsInstance(ws['E5'].value, int)

    def test_color_label_name_with_code(self):
        self.assertEqual(rl.color_label('IVORY', 'IV'), 'IVORY(IV)')
        self.assertEqual(rl.color_label('챠콜 그레이(05)', '05'), '챠콜 그레이(05)')          # 이미 붙어 있으면 그대로
        self.assertEqual(rl.color_label('블랙', None), '블랙')
        self.assertEqual(rl.color_label('', 'BK'), 'BK')
        self.assertEqual(rl.color_label('BLACK', 'black'), 'BLACK')                    # 코드=이름이면 한 번만
        self.assertEqual(rl.color_label(None, None), '')

    def test_download_filenames(self):
        from datetime import datetime
        at = datetime(2026, 11, 27, 17, 25, 9)
        today = '20261127_172509'
        self.assertEqual(rl.result_filename('올리비아로렌', at), f'올리비아로렌_RAW RESULT_{today}.xlsx')
        self.assertEqual(rl.result_filename('', at), f'RAW RESULT_{today}.xlsx')
        self.assertEqual(rl.filename(['올리비아로렌'], at), f'3P_Listing_표준양식_올리비아로렌_{today}.xlsx')
        self.assertEqual(rl.filename(['젠사이', '올포유', '젠사이'], at), f'3P_Listing_표준양식_젠사이 외 1_{today}.xlsx')
        self.assertEqual(rl.filename([], at), f'3P_Listing_표준양식_{today}.xlsx')
        self.assertEqual(rl.result_filename('a/b:c*', at), f'a b c_RAW RESULT_{today}.xlsx')
        self.assertRegex(rl.result_filename('젠사이'), r'^젠사이_RAW RESULT_\d{8}_\d{6}\.xlsx$')
        _, summary = rl.convert([dict(name='hub.xlsx', data=hub_file(), sheet='결과', header_row=1,
                                      mapping=rl.inspect(hub_file(), 'hub.xlsx')['mapping'], brand='젠사이')])
        self.assertEqual(summary['brands'], ['젠사이'])

    def test_content_disposition_keeps_korean_name(self):
        import server
        from urllib.parse import unquote
        header = server.disposition('올리비아로렌_RAW RESULT_20261126.xlsx')
        self.assertTrue(header.startswith('attachment; filename="'))
        header.encode('latin-1')                                                   # HTTP 헤더에 그대로 실림
        self.assertEqual(unquote(header.split("UTF-8''")[1]), '올리비아로렌_RAW RESULT_20261126.xlsx')


if __name__ == '__main__':
    unittest.main()
