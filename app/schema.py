"""UI normalization and Level 1 export selection; source values remain untouched."""
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from detail_store import excel_detail

FIELDS = {
    'product_code':'품번', 'product_name':'상품명', 'color':'색상', 'product_url':'상품 링크',
    'size':'사이즈', 'price':'판매가', 'regular_price':'정상가', 'category':'카테고리',
    'main_image':'대표이미지', 'additional_images':'추가이미지', 'detail_page':'상세페이지',
}
REQUIRED = ['product_code','product_name','color','product_url']
OPTIONAL = [k for k in FIELDS if k not in REQUIRED]
DEFAULT_SELECTION = ['main_image','additional_images','detail_page']

def field_for(header):
    h = re.sub(r'\s+', '', str(header)).lower()
    if h in ('product_url','상품링크','상품url','공홈링크','공식링크','링크'):return 'product_url'
    if h in ('상세페이지','상세이미지','상세이미지파일명'):return 'detail_page'
    if 'html' in h: return 'detail_page'
    if '대표이미지' in h: return 'main_image'
    if '추가이미지' in h or '기타이미지' in h: return 'additional_images'
    if h in ('품번','품번+색상','입력품번'): return 'product_code'
    if h == '상품명': return 'product_name'
    if h in ('색상','색상명','색상코드'): return 'color'
    if h == '사이즈': return 'size'
    if h in ('가격','판매가','price','sale_price'): return 'price'
    if h in ('정상가','소비자가','정가','regular_price'): return 'regular_price'
    if h == '카테고리': return 'category'
    return None

def selection(values=None):
    values = DEFAULT_SELECTION if values is None else values
    if not isinstance(values, list) or any(k not in OPTIONAL for k in values):
        raise ValueError('올바른 선택 수집 항목을 지정해주세요.')
    return list(dict.fromkeys(values))

def export_indexes(headers, selected):
    keep = set(REQUIRED + selected)
    standard=[i for i,h in enumerate(headers) if field_for(h) in keep]
    return standard+[i for i,h in enumerate(headers) if field_for(h) is None]

def output_headers(headers, selected):
    result = [headers[i] for i in export_indexes(headers, selected) if field_for(headers[i]) is not None]
    for key in REQUIRED:
        if not any(field_for(h) == key for h in result):
            result.append(FIELDS[key])
    return result+[h for h in headers if field_for(h) is None]

def urls(value):
    # Intentionally preserve order and repeated slots, including repeated URLs in one cell.
    return re.findall(r'https?://[^\s<>"\x27,;\[\]]+|/sample/[0-9]+/[0-9]+', str(value or ''))

def slots(values):
    seen, result = {}, []
    for i,url in enumerate(values,1):
        item = dict(slot=i, url=url, duplicate=url in seen)
        if url in seen: item['duplicate_of'] = seen[url]
        else: seen[url] = i
        result.append(item)
    return result

def sanitized_html(markup):
    soup = BeautifulSoup(markup or '', 'html.parser')
    allowed = {'div','p','br','span','b','strong','table','caption','thead','tbody','tfoot','tr','th','td','img','ul','ol','li','h1','h2','h3','h4','h5','h6','pre','code'}
    for tag in list(soup.find_all(True)):
        if tag.name in ('script','style','iframe','object','embed','form','svg','link','meta','base'):
            tag.decompose(); continue
        if not tag.name: continue
        if tag.name not in allowed:
            tag.unwrap(); continue
        src = tag.get('src','') if tag.name == 'img' else ''
        spans={key:str(tag.get(key,'')) for key in ('colspan','rowspan') if tag.name in ('th','td') and str(tag.get(key,'')).isdigit() and 0<int(tag.get(key))<=100}
        tag.attrs = {}
        tag.attrs.update(spans)
        if src and (urlparse(src).scheme in ('http','https') or src.startswith('/sample/')):
            tag['src'] = src
            tag['referrerpolicy'] = 'no-referrer'
    return str(soup)

def preview(headers, row, selected=None, allow_empty_product_name=False):
    selected = selection(selected)
    allowed = set(REQUIRED + selected)
    raw = dict(zip(headers,row))
    normalized, fields, main, extra, markup = {}, {}, [], [], ''
    source = ''
    for key,value in raw.items():
        f = field_for(key)
        if str(key) in ('공홈링크','공홈 링크','공식 링크','상품 링크','링크','상품URL'):
            source = str(value or '')
        if f is not None and f not in allowed: continue
        fields[str(key)] = '' if value is None else str(value)
        if f and value not in ('',None): normalized.setdefault(f,str(value))
        if f == 'product_url':source=str(value or '')
        if f == 'main_image': main.extend(urls(value))
        elif f == 'additional_images': extra.extend(urls(value))
        elif f == 'detail_page': markup = str(value or '')
    missing = [FIELDS[k] for k in REQUIRED if not normalized.get(k) and not (k=='product_name' and allow_empty_product_name)]
    compact,overflow=excel_detail(markup)
    return dict(code=normalized.get('product_code',''), name=normalized.get('product_name',''),
        color=normalized.get('color',''), normalized=normalized, fields=fields,
        extra_fields={str(k):v for k,v in raw.items() if field_for(k) is None},
        images=main+extra, main_images=main, additional_images=extra, additional_slots=slots(extra),
        unique_additional=len(set(extra)), duplicate_count=len(extra)-len(set(extra)),
        html=sanitized_html(compact), detail_overflow=overflow, missing=missing, review_status='warning' if missing else 'normal',
        source_url=source if urlparse(source).scheme in ('http','https') else '')


def review_result(item, selected):
    """Review the final deliverable, never intermediate crawler WARNING logs."""
    messages=[]
    if item.get('missing'):messages.append('필수 항목 미수집: '+', '.join(item['missing']))
    if 'main_image' in selected and not item.get('main_images'):messages.append('대표이미지 미수집')
    if 'additional_images' in selected and len(item.get('additional_images',[]))<5:messages.append('추가이미지 5슬롯 미완성')
    for key in ('size','price','category'):
        if key in selected and not item.get('normalized',{}).get(key):messages.append(FIELDS[key]+' 미수집')
    if 'detail_page' in selected:
        if item.get('detail_overflow'):messages.append('상세 HTML 정리 후에도 Excel 셀 한도(32,767자) 초과 · 등록용 상세 생성 실패')
        elif not item.get('html'):messages.append('상세페이지 미수집')
    state='error' if item.get('detail_overflow') else 'warning' if messages else 'normal'
    return state,messages
