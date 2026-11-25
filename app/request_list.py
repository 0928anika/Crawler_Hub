"""크롤링 결과 엑셀 → 3P Listing '요청 목록' 양식 변환.

결과 파일마다 시트·머리글 위치·항목명이 조금씩 다를 수 있어 머리글 이름(별칭)으로 열을 찾는다.
- Crawler Hub 결과(결과 시트: 품번/상품명/색상/색상명/사이즈/대표이미지/추가이미지/상세페이지 HTML/상품 링크)
- 단독 크롤러 결과(크롤링결과 시트: 노출상품명/옵션정보/사이즈별재고/대표이미지_URL/상세페이지_URL…)
- 이미 만든 요청 목록(4행 머리글) 등
사용자가 화면에서 열 연결을 직접 고칠 수 있도록 감지 결과(시트·머리글 행·열 연결)를 그대로 돌려준다.
요청 목록은 사이즈별 1행(전개형), 등록상품명 = 브랜드 + 상품명 + 품번(이미 들어 있으면 중복하지 않음).
"""
import base64
import io
import re
from collections import Counter
from copy import copy
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

TEMPLATE = Path(__file__).resolve().parent / 'templates' / 'request_list_template.xlsx'
MAX_FILE = 10 * 1024 * 1024
MAX_ROWS = 20000
HEADER_SCAN = 15
FIRST_DATA_ROW = 5

# 요청 목록 열 순서(양식 4행)
OUTPUT = ['품번', '등록상품명', '사이즈', '색상', '판매가', '정상가', '대표이미지_URL', '상세페이지(HTML)', '추가이미지_URL', '카테고리', 'URL']
# 원본에서 찾는 항목 → 앞쪽 별칭일수록 우선. 비교는 공백·_·()·-·/ 를 뺀 소문자.
FIELDS = [
    ('code', '품번', ['품번', '품번코드', '스타일코드', '스타일번호', '스타일', '상품코드', '모델코드', 'productcode', 'stylecode']),
    ('name', '상품명', ['등록상품명', '노출상품명', '상품명', 'productname']),
    ('brand', '브랜드', ['브랜드', '브랜드명', 'brand']),
    ('size', '사이즈', ['사이즈', 'size', '옵션사이즈', '사이즈별재고', '옵션정보']),
    ('color', '색상', ['색상명', '색상', '컬러', 'color', 'colorname']),
    ('price', '판매가', ['판매가', '할인가', '가격', 'price', 'saleprice']),
    ('regular', '정상가', ['정상가', '소비자가', '정가', 'regularprice']),
    ('main', '대표이미지', ['대표이미지url', '대표이미지', 'mainimage']),
    ('detail', '상세페이지(HTML)', ['상세페이지html', '상세페이지', 'detailpage', 'detailhtml']),
    ('extra', '추가이미지', ['추가이미지url', '추가이미지', '상세페이지url', 'additionalimages']),
    ('extra2', '추가이미지(2)', ['추가이미지url2', '추가이미지2', '상세페이지url2']),
    ('category', '카테고리', ['카테고리', 'category']),
    ('url', '상품 링크', ['url', '상품링크', '상품url', '공홈링크', '상세페이지링크', '링크', 'producturl']),
]
FIELD_KEYS = [key for key, _, _ in FIELDS]
LABELS = {key: label for key, label, _ in FIELDS}
SKIP_SHEETS = re.compile(r'로그|요약|이력|중복|log', re.I)
BLANK_PRICE = '공홈 조회'


def norm(value):
    return re.sub(r'[\s_()\-/·.]+', '', str(value or '')).lower()


def text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_book(data, name):
    if len(data) > MAX_FILE:
        raise ValueError(f'{name}: 엑셀 파일은 10MB 이하로 올려주세요.')
    try:
        return load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError(f'{name}: 엑셀(.xlsx) 파일을 열 수 없습니다. 보안(DRM)이 걸린 파일이면 엑셀에서 다른 이름으로 저장 후 올려주세요.') from exc


def match(headers):
    """머리글 목록 → {항목: 열 번호(0부터)}. 항목마다 우선순위가 가장 높은 별칭의 첫 열."""
    found = {}
    keys = [norm(h) for h in headers]
    used = set()
    for field, _, aliases in FIELDS:
        for alias in aliases:
            index = next((i for i, k in enumerate(keys) if k == alias and i not in used), None)
            if index is not None:
                found[field] = index
                used.add(index)
                break
    return found


def kind_of(sheet, headers, book_sheets):
    keys = {norm(h) for h in headers}
    if {'등록상품명', '대표이미지url', '추가이미지url'} <= keys:
        return '3P 요청 목록'
    if '작업처리로그' in book_sheets or ('상품링크' in keys and '상세페이지html' in keys):
        return 'Crawler Hub 결과'
    if '노출상품명' in keys or '상세페이지url' in keys or '옵션정보' in keys:
        return '단독 크롤러 결과'
    return '기타 양식'


def inspect(data, name):
    """가장 그럴듯한 시트·머리글 행을 찾는다."""
    book = read_book(data, name)
    try:
        best = None
        for ws in book.worksheets:
            if SKIP_SHEETS.search(ws.title):
                continue
            for number, row in enumerate(ws.iter_rows(max_row=HEADER_SCAN, values_only=True), 1):
                headers = [text(v) for v in row]
                found = match(headers)
                if 'code' not in found:
                    continue
                score = len(found)
                if best is None or score > best['score']:
                    best = dict(score=score, sheet=ws.title, header_row=number, headers=headers, mapping=found)
        if best is None or best['score'] < 3:
            raise ValueError(f'{name}: 품번 열이 있는 결과 시트를 찾지 못했습니다. 머리글에 "품번"이 있는지 확인하세요.')
        ws = book[best['sheet']]
        rows = [r for r in ws.iter_rows(min_row=best['header_row'] + 1, values_only=True) if any(v not in (None, '') for v in r)]
        if len(rows) > MAX_ROWS:
            raise ValueError(f'{name}: 행이 너무 많습니다(최대 {MAX_ROWS:,}행).')
        headers = best['headers']
        while headers and not headers[-1]:
            headers.pop()
        mapping = {field: headers[index] for field, index in best['mapping'].items() if index < len(headers)}
        brand = ''
        if 'brand' in best['mapping']:
            values = Counter(text(r[best['mapping']['brand']]) for r in rows if best['mapping']['brand'] < len(r) and text(r[best['mapping']['brand']]))
            brand = values.most_common(1)[0][0] if values else ''
        kind = kind_of(best['sheet'], headers, book.sheetnames)
        warnings = []
        for field in ('name', 'size', 'color', 'main'):
            if field not in mapping:
                warnings.append(f'{LABELS[field]} 열을 찾지 못했습니다. 아래에서 직접 고르세요.')
        if kind == 'Crawler Hub 결과' and len(rows) and '대표이미지' in mapping.values():
            sample = text(rows[0][headers.index(mapping['main'])]) if mapping.get('main') in headers else ''
            if sample and not sample.startswith('http'):
                warnings.append('대표이미지가 파일명입니다(이미지 저장형). 요청 목록에는 파일명이 그대로 들어갑니다.')
        return dict(name=name, kind=kind, sheet=best['sheet'], header_row=best['header_row'], headers=[h for h in headers],
                    mapping=mapping, brand=brand, rows=len(rows), warnings=warnings)
    finally:
        book.close()


def sizes_of(value, header):
    raw = text(value)
    if not raw:
        return []
    key = norm(header)
    if key == '옵션정보':
        found = []
        for label in ('사이즈', '품절사이즈'):
            match_ = re.search(r'(?:^|/)\s*' + label + r'\s*:\s*([^/]+)', raw)
            if match_:
                found += [s.strip() for s in match_[1].split(',') if s.strip()]
        return list(dict.fromkeys(found))
    if key == '사이즈별재고' or re.fullmatch(r'\s*[^,:]+:[^,]*(,\s*[^,:]+:[^,]*)*\s*', raw):
        return list(dict.fromkeys(part.split(':', 1)[0].strip() for part in raw.split(',') if part.strip() and ':' in part))
    return list(dict.fromkeys(s.strip() for s in re.split(r'[,\n/]', raw) if s.strip()))


def price_of(value):
    raw = text(value)
    digits = re.sub(r'[^\d]', '', raw)
    return digits if digits else ''


def urls_of(*values):
    parts = []
    for value in values:
        parts += [u.strip() for u in re.split(r'[,\n]', text(value)) if u.strip()]
    return ','.join(parts)


def registered_name(brand, name, code):
    name = text(name)
    brand = text(brand)
    words = [name]
    if brand and not re.match(re.escape(brand), name, re.I):
        words.insert(0, brand)
    if code and not re.search(r'(?<![A-Za-z0-9])' + re.escape(code) + r'(?![A-Za-z0-9])', name, re.I):
        words.append(code)
    return re.sub(r'\s+', ' ', ' '.join(w for w in words if w)).strip()


def convert(files, fill_blank_price=True):
    """files: [{name, data(bytes), sheet, header_row, mapping{field: 머리글}, brand}] → (xlsx bytes, 요약)."""
    rows, seen, summary, brands = [], set(), [], []
    for entry in files:
        name = entry['name']
        book = read_book(entry['data'], name)
        try:
            if entry.get('sheet') not in book.sheetnames:
                raise ValueError(f'{name}: 시트 "{entry.get("sheet")}"를 찾을 수 없습니다.')
            ws = book[entry['sheet']]
            header_row = int(entry.get('header_row') or 1)
            headers = [text(v) for v in next(ws.iter_rows(min_row=header_row, max_row=header_row, values_only=True), [])]
            mapping = {k: v for k, v in (entry.get('mapping') or {}).items() if k in FIELD_KEYS and v}
            missing = [h for h in mapping.values() if h not in headers]
            if missing:
                raise ValueError(f'{name}: 머리글 "{missing[0]}"을(를) 찾을 수 없습니다. 다시 확인해 주세요.')
            if 'code' not in mapping:
                raise ValueError(f'{name}: 품번 열을 골라 주세요.')
            col = {k: headers.index(v) for k, v in mapping.items()}
            stats = Counter()
            direct = norm(mapping.get('name')) == '등록상품명'
            for raw in ws.iter_rows(min_row=header_row + 1, values_only=True):
                if not any(v not in (None, '') for v in raw):
                    continue
                get = lambda field: raw[col[field]] if field in col and col[field] < len(raw) else None
                code = text(get('code'))
                if not code:
                    stats['품번 없음'] += 1
                    continue
                brand = text(get('brand')) or text(entry.get('brand'))
                if brand and brand not in brands:
                    brands.append(brand)
                name_value = text(get('name'))
                title = name_value if direct else registered_name(brand, name_value, code)
                color = text(get('color'))
                sizes = sizes_of(get('size'), mapping.get('size')) or ['']
                price = price_of(get('price'))
                regular = price_of(get('regular'))
                if not price:
                    stats['판매가 빈칸'] += 1
                if fill_blank_price:
                    price = price or BLANK_PRICE
                    regular = regular or BLANK_PRICE
                main = text(get('main'))
                if not main:
                    stats['대표이미지 빈칸'] += 1
                if sizes == ['']:
                    stats['사이즈 없음'] += 1
                for size in sizes:
                    key = (code.upper(), color, size)
                    if key in seen:
                        stats['중복 제외'] += 1
                        continue
                    seen.add(key)
                    rows.append([code, title, size, color, price, regular, main, text(get('detail')),
                                 urls_of(get('extra'), get('extra2')), text(get('category')), text(get('url'))])
                    stats['요청 행'] += 1
            summary.append(dict(name=name, **{k: v for k, v in stats.items()}))
        finally:
            book.close()
    if not rows:
        raise ValueError('요청 목록으로 옮길 행이 없습니다. 품번 열과 시트를 확인하세요.')
    return write(rows), dict(files=summary, rows=len(rows), brands=brands)


def write(rows):
    book = load_workbook(TEMPLATE)
    ws = book['요청 목록']
    styles = [ws.cell(FIRST_DATA_ROW, c) for c in range(1, len(OUTPUT) + 1)]
    for offset, values in enumerate(rows):
        r = FIRST_DATA_ROW + offset
        for c, value in enumerate(values, 1):
            cell = ws.cell(r, c)
            cell.value = '\'' + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value
            source = styles[c - 1]
            cell.font, cell.fill, cell.border = copy(source.font), copy(source.fill), copy(source.border)
            cell.alignment, cell.number_format = copy(source.alignment), '@'
    ws.auto_filter.ref = f'A4:K{FIRST_DATA_ROW + len(rows) - 1}'
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def decode(entry):
    try:
        data = base64.b64decode(entry.get('file') or '', validate=True)
    except ValueError as exc:
        raise ValueError(f"{entry.get('name', '파일')}: 파일을 읽을 수 없습니다.") from exc
    return data


def safe_name(value):
    """파일명에 못 쓰는 글자 제거."""
    return re.sub(r'\s+', ' ', re.sub(r'[\\/:*?"<>|\x00-\x1f]', ' ', str(value or ''))).strip(' .')[:60]


def brand_title(brands):
    names = list(dict.fromkeys(n for n in (safe_name(b) for b in brands) if n))
    if not names:
        return ''
    return names[0] + (f' 외 {len(names) - 1}' if len(names) > 1 else '')


def filename(brands=()):
    """브랜드_요청목록 변환_YYYYMMDD.xlsx (브랜드 여러 개면 '첫 브랜드 외 N')."""
    title = brand_title(brands)
    return (title + '_' if title else '') + '요청목록 변환_' + datetime.now().strftime('%Y%m%d') + '.xlsx'


def result_filename(brand):
    """작업 결과 엑셀: 브랜드명_RAW RESULT_YYYYMMDD.xlsx"""
    title = safe_name(brand)
    return (title + '_' if title else '') + 'RAW RESULT_' + datetime.now().strftime('%Y%m%d') + '.xlsx'

