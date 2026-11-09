"""Small shared contract, also frozen into each manual crawler worker."""
import hashlib


def normalize_palette(values):
    if not isinstance(values,list):raise ValueError('색상 후보 목록이 필요합니다.')
    result=[];seen=set()
    for value in values:
        if not isinstance(value,dict):raise ValueError('색상 후보는 dict여야 합니다.')
        name=str(value.get('color_name') or '').strip();code=str(value.get('color_code') or '').strip()
        if not name and not code:raise ValueError('실제 색상명 또는 색상코드가 필요합니다.')
        cid=hashlib.sha256((name+'\0'+code).encode('utf-8')).hexdigest()[:24]
        if cid in seen:continue
        seen.add(cid)
        result.append(dict(id=cid,color_name=name,color_code=code,label=name+' ('+code+')' if name and code else name or code))
    if not result:raise ValueError('실제 색상 후보가 없습니다.')
    return result
