"""Local AM request import. No translation or inferred Korean/code matching."""
import base64
import csv
import io
import re
import unicodedata
from zipfile import ZipFile, BadZipFile
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from color_palette import normalize_palette

PRODUCT_HEADERS={'품번','상품코드','제품코드','품목코드','스타일','스타일번호','스타일코드','모델명','모델번호','SKU','STYLE','STYLENO','STYLECODE','MODELNO','PRODUCTCODE','ITEMCODE'}
COLOR_HEADERS={'색상','색상명','컬러','컬러명','COLOR','COLOUR','COLORNAME','COLOURNAME','요청색상','AM색상','옵션','옵션명'}
CODE_HEADERS={'색상코드','컬러코드','COLORCODE','COLOURCODE','색상번호','COLORNO'}


def code_key(value):
    return unicodedata.normalize('NFKC',str(value or '')).strip().upper()


def header_key(value):
    return re.sub(r'[\s_\-()/.:]+','',code_key(value))


def cell_text(cell):
    value=cell.value
    if value is None:return ''
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        text=str(int(value)) if float(value).is_integer() else str(value)
        if re.fullmatch(r'0{2,}',cell.number_format or '') and float(value).is_integer():return text.zfill(len(cell.number_format))
        return text
    return str(value).strip()


def read_input(data):
    sheets=[];selected='';rows=[]
    if data.get('file'):
        try:raw=base64.b64decode(data['file'],validate=True)
        except (ValueError,TypeError) as exc:raise ValueError('AM 파일 데이터를 읽지 못했습니다.') from exc
        if len(raw)>10*1024*1024:raise ValueError('AM 파일은 10MB 이하로 선택하세요.')
        filename=str(data.get('file_name','')).lower()
        if filename.endswith(('.csv','.tsv')):
            try:text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
            except UnicodeDecodeError:text=raw.decode('cp949')
            rows=list(csv.reader(io.StringIO(text),delimiter='\t' if filename.endswith('.tsv') else ','))
        else:
            try:
                with ZipFile(io.BytesIO(raw)) as archive:
                    if sum(i.file_size for i in archive.infolist())>50*1024*1024:raise ValueError('AM 엑셀 압축 해제 크기가 너무 큽니다.')
                book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
                try:
                    sheets=book.sheetnames;selected=data.get('sheet') or sheets[0]
                    if selected not in sheets:raise ValueError('선택한 엑셀 시트가 없습니다.')
                    for index,row in enumerate(book[selected].iter_rows()):
                        if index>=20000:raise ValueError('AM 목록은 최대 20,000행까지 지원합니다.')
                        rows.append([cell_text(c) for c in row])
                finally:book.close()
            except (BadZipFile,KeyError) as exc:raise ValueError('xlsx 파일을 선택하세요. 구형 xls 파일은 xlsx로 저장 후 올려주세요.') from exc
    else:
        text=str(data.get('text','')).strip()
        if not text:raise ValueError('AM 엑셀을 선택하거나 요청 목록을 붙여넣으세요.')
        if len(text)>2_000_000:raise ValueError('AM 직접 입력이 너무 큽니다.')
        lines=[line.strip() for line in text.splitlines() if line.strip()]
        if '\t' in text:rows=list(csv.reader(io.StringIO(text),delimiter='\t'))
        elif any(header_key(v) in PRODUCT_HEADERS for v in re.split(r'[,;]',lines[0])):
            rows=list(csv.reader(io.StringIO(text),delimiter=';' if ';' in lines[0] else ','))
        else:
            rows=[['품번','색상']]
            for line in lines:
                match=re.fullmatch(r'\s*([A-Za-z0-9][A-Za-z0-9_-]*)\s+(.+)',line)
                rows.append([match[1],match[2]] if match else [line,''])
    if len(rows)>20000:raise ValueError('AM 목록은 최대 20,000행까지 지원합니다.')
    width=max((len(r) for r in rows),default=0)
    if width>256 or sum(len(r) for r in rows)>1_000_000:raise ValueError('AM 파일의 열 또는 셀 개수가 너무 많습니다.')
    return rows,sheets,selected


def split_values(value):
    return [re.sub(r'\s+',' ',part).strip() for part in re.split(r'[,，;；\n|·]|\s+/\s+',str(value or '')) if part.strip()]


def parse_requests(data,known_codes=()):
    rows,sheets,sheet=read_input(data);width=max((len(r) for r in rows),default=0)
    if not width:raise ValueError('AM 목록에 데이터가 없습니다.')
    known={code_key(c) for c in known_codes};suggested={};header=-1;best=0
    for index,row in enumerate(rows[:1]):
        keys=[header_key(v) for v in row]
        product=next((i for i,k in enumerate(keys) if k in PRODUCT_HEADERS),None)
        color=next((i for i,k in enumerate(keys) if k in COLOR_HEADERS),None)
        color_code=next((i for i,k in enumerate(keys) if k in CODE_HEADERS),None)
        score=(3 if product is not None else 0)+(2 if color is not None else 0)+(2 if color_code is not None else 0)
        if score>best and product is not None and (color is not None or color_code is not None):
            best=score;header=index;suggested=dict(product=product,color=color,code=color_code)
    if not suggested:
        scores=[sum(code_key(row[i]) in known for row in rows[:200] if i<len(row) and row[i]) for i in range(width)]
        if scores and max(scores)>0:suggested['product']=scores.index(max(scores))
        elif width==2:suggested['product']=0
        if width==2 and 'product' in suggested:suggested['color']=1-suggested['product']
    overrides=data.get('columns') or {}
    header=-1 if any(code_key(v) in known for v in rows[0]) else 0
    columns={key:suggested.get(key) for key in ['product','color','code']}
    for key in columns:
        if key in overrides:columns[key]=None if overrides[key] in (None,'',-1,'-1') else int(overrides[key])
        if columns[key] is not None and not 0<=columns[key]<width:raise ValueError('선택한 열이 파일 범위를 벗어났습니다.')
    used=[i for i in columns.values() if i is not None]
    if len(used)!=len(set(used)):raise ValueError('품번·색상명·색상코드는 서로 다른 열로 선택하세요.')
    options=[]
    for index in range(width):
        title=rows[header][index] if header>=0 and index<len(rows[header]) else ''
        sample=next((str(r[index])[:45] for r in rows[header+1:header+6] if index<len(r) and r[index]),'')
        options.append(dict(index=index,label=get_column_letter(index+1)+' · '+(str(title) or '제목 없음')+((' · '+sample) if sample else '')))
    result=dict(columns=dict(columns,header_row=header),column_options=options,sheets=sheets,sheet=sheet,products={},warnings=[],row_count=max(0,len(rows)-header-1),option_count=0,needs_columns=columns['product'] is None or (columns['color'] is None and columns['code'] is None),source_name=str(data.get('file_name') or '직접 입력'))
    if result['needs_columns']:return result
    products={};seen={};warnings=[];valid_rows=0;mode='auto'
    if mode not in ('auto','name','code'):raise ValueError('올바른 색상 값 형식을 선택하세요.')
    for number,row in enumerate(rows[header+1:],header+2):
        if not any(str(v or '').strip() for v in row):continue
        def value(key):
            index=columns[key];return str(row[index] or '').strip() if index is not None and index<len(row) else ''
        code=code_key(value('product'));names=split_values(value('color'));codes=split_values(value('code'))
        if not code or len(code)>80 or not(names or codes):warnings.append(f'{number}행: 품번 또는 색상 누락');continue
        if names and codes and len(names)!=len(codes):warnings.append(f'{number}행: 색상명/코드 개수가 달라 연결하지 않음');continue
        colors=[]
        for i in range(max(len(names),len(codes))):
            name=names[i] if names else '';color_code=codes[i] if codes else ''
            if not color_code and name and (mode=='code' or (mode=='auto' and re.fullmatch(r'(?:[A-Z0-9]{1,3}|\d{2,3})',name) and name not in ('RED','TAN','NAVY','BLUE','ALL'))):color_code=name;name=''
            if max(len(name),len(color_code))>80:warnings.append(f'{number}행: 옵션 값은 80자 이하로 입력');continue
            colors.append(dict(color_name=name,color_code=color_code))
        if not colors:continue
        valid_rows+=1;target=products.setdefault(code,[]);keys=seen.setdefault(code,set())
        for color in colors:
            key=(color['color_name'].casefold(),color['color_code'].upper())
            if key not in keys:target.append(color);keys.add(key)
    result['products']={code:normalize_palette(colors) for code,colors in products.items()}
    result.update(warnings=warnings[:50],warning_count=len(warnings),valid_rows=valid_rows,option_count=sum(len(v) for v in result['products'].values()))
    result['matched_codes']=[c for c in products if c in known];result['unmatched_codes']=[c for c in products if c not in known]
    return result
