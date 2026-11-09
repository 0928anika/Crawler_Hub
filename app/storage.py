import base64
import io
import json
import os
import re
import tempfile
import threading
from collections import Counter
from pathlib import Path
from zipfile import ZipFile
from openpyxl import load_workbook
from registry import ROOT
from schema import selection, DEFAULT_SELECTION, output_headers

VERSION='0.10.0'
SETTINGS_LOCK=threading.RLock()

from local_paths import validate_local

from local_paths import user_home
HOME=user_home()
HOME.mkdir(parents=True,exist_ok=True)

def atomic_json(path,data):
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,default=str)
        f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def settings():
    path=HOME/'settings.json'
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return dict(selected=DEFAULT_SELECTION,recent_brands=[])

def save_settings(data):
    with SETTINGS_LOCK:
        return _save_settings(data)

def _save_settings(data):
    current=settings()
    current['selected']=selection(data.get('selected',current.get('selected')))
    if data.get('last_brand'):
        current['recent_brands']=list(dict.fromkeys([data['last_brand']]+current.get('recent_brands',[])))[:5]
    atomic_json(HOME/'settings.json',current)
    return current

def parse_input(data,brand):
    if data.get('file'):
        raw=base64.b64decode(data['file'],validate=True)
        if len(raw)>10*1024*1024:raise ValueError('엑셀 파일은 10MB 이하로 올려주세요.')
        with ZipFile(io.BytesIO(raw)) as archive:
            if sum(x.file_size for x in archive.infolist())>50*1024*1024:raise ValueError('압축 해제 용량이 너무 큽니다.')
        wb=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
        try:
            iterator=wb.active.iter_rows(values_only=True)
            headers=[str(v or '').strip() for v in next(iterator,[])]
            rows=[]
            for n,r in enumerate(iterator):
                if n>=5000:raise ValueError('엑셀은 최대 5,000행까지 검사할 수 있습니다.')
                if any(v not in ('',None) for v in r):rows.append(list(r))
        finally:wb.close()
    else:
        headers=['품번']
        rows=[[s.strip()] for s in str(data.get('codes','')).splitlines() if s.strip()]
    if not rows or len(rows)>3000:raise ValueError('품번을 1~3,000개 입력해주세요.')
    # Legacy header alias is accepted as the default crawler CLI still reads 품번.
    if '품번' not in headers and '품번+색상' in headers:headers[headers.index('품번+색상')]='품번'
    if '품번' not in headers:raise ValueError('첫 행에 품번 열이 필요합니다.')
    idx=headers.index('품번')
    headers=[headers[idx]]+headers[:idx]+headers[idx+1:]
    clean=[]
    for r in rows:
        r=list(r)+[None]*max(0,len(headers)-len(r))
        code=str(r[idx] or '').strip()
        clean.append([code]+r[:idx]+r[idx+1:])
    return headers,clean

def validate_input(data,brand):
    headers,rows=parse_input(data,brand)
    guide=brand['guide']
    missing=[]
    for col in guide.get('required_columns',['품번']):
        aliases=guide.get('aliases',{}).get(col,[col])
        if not any(x in headers for x in aliases):missing.append(col)
    errors=[];warnings=[]
    for i,row in enumerate(rows):
        code=row[0]
        if not code:errors.append(dict(row=i+2,code=code,message='품번이 비어 있습니다.'));continue
        norm=code.upper()
        if not data.get('demo',False) and guide.get('pattern') and not re.fullmatch(guide['pattern'],norm):
            warnings.append(dict(row=i+2,code=code,message='기존 크롤러의 입력 형식과 다를 수 있습니다.'))
        if not data.get('demo',False):
            for col in guide.get('required_columns',[]):
                match=next((h for h in guide.get('aliases',{}).get(col,[col]) if h in headers),None)
                if match and row[headers.index(match)] in ('',None):errors.append(dict(row=i+2,code=code,message=col+' 값이 비어 있습니다.'))
    if data.get('demo',False):missing=[]
    selected=selection(data.get('selected'))
    unsupported=[k for k in selected if not brand['fields'][k]['supported']]
    selected=[k for k in selected if brand['fields'][k]['supported']]
    counts=Counter(r[0] for r in rows)
    result=dict(total=len(rows),duplicates=sum(v-1 for v in counts.values()),missing_columns=missing,
        errors=errors[:100],warnings=warnings[:100],invalid_count=len(errors),warning_count=len(warnings),
        rows=[dict(id=str(i),code=r[0],source_row=i+2) for i,r in enumerate(rows)],
        selected=selected,unsupported=unsupported,output_headers=output_headers(brand['output_headers'],selected),
        directory=str(HOME/'jobs'),valid=not missing and not errors,notice=guide['validation_note'])
    return result,headers,rows
