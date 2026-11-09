from pathlib import Path
import json
import os
import hashlib
from schema import FIELDS, field_for
from brand_catalog import definitions, script_path

ROOT=Path(__file__).resolve().parent

def brand_label(brand):
    source=brand.get('crawl_source') or '소스 미지정'
    return str(brand.get('name',''))+' ('+str(source)+')'

def brand_root(source_root=None):
    return Path(source_root or os.environ.get('CRAWLER_HUB_SOURCE_ROOT') or ROOT).resolve().parent/'brands'

def brands(source_root=None):
    folder=brand_root(source_root)
    result=[]
    for definition in definitions(folder):
        b=dict(definition);path=script_path(folder,b)
        b['available']=path.is_file();b['column_fields']={h:field_for(h) for h in b['output_headers']}
        if b['available'] and b.get('code_hash'):
            b['available']=hashlib.sha256(path.read_bytes()).hexdigest()==b['code_hash']
        supported=set(b['column_fields'].values())
        b.setdefault('fields',{key:dict(label=label,supported=key in supported,status='conditional',reason='상품별 누락 가능') for key,label in FIELDS.items()})
        b.setdefault('extensions',[]);b.setdefault('execution_level',1);b.setdefault('interface','portal_v1');b.setdefault('notes','')
        b.setdefault('crawl_source','')
        b.setdefault('mapping_mode','automatic')
        b.setdefault('image_mode','url')
        b['display_name']=brand_label(b)
        result.append(b)
    return result

def get_brand(key):
    for b in brands():
        if b['id']==key:return b
    raise ValueError('등록된 브랜드가 없습니다. 관리자에서 브랜드를 먼저 등록하세요.')
