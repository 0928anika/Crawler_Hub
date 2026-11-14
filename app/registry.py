from pathlib import Path
import json
import os
import hashlib
import copy
import threading
from schema import FIELDS, field_for
from brand_catalog import definitions, script_path

ROOT=Path(__file__).resolve().parent

def brand_label(brand):
    source=brand.get('crawl_source') or '소스 미지정'
    return str(brand.get('name',''))+' ('+str(source)+')'

def brand_root(source_root=None):
    return Path(source_root or os.environ.get('CRAWLER_HUB_SOURCE_ROOT') or ROOT).resolve().parent/'brands'

def _load_brands(source_root=None):
    folder=brand_root(source_root)
    result=[]
    for definition in definitions(folder):
        b=dict(definition);path=script_path(folder,b)
        b['available']=path.is_file();b['column_fields']={h:field_for(h) for h in b['output_headers']}
        if b['available'] and b.get('code_hash'):
            b['available']=hashlib.sha256(path.read_bytes()).hexdigest()==b['code_hash']
        supported=set(b['column_fields'].values())
        fields=b.setdefault('fields',{})
        for key,label in FIELDS.items():
            fields.setdefault(key,dict(label=label,supported=key in supported,status='conditional' if key in supported else 'unsupported',reason='상품별 누락 가능' if key in supported else '독립 결과 컬럼 없음'))
            fields[key]['label']=label
        b.setdefault('extensions',[]);b.setdefault('execution_level',1);b.setdefault('interface','portal_v1');b.setdefault('notes','')
        b.setdefault('crawl_source','')
        b.setdefault('mapping_mode','automatic')
        b.setdefault('image_mode','url')
        b['display_name']=brand_label(b)
        result.append(b)
    return result

_CATALOG_CACHE = {}
_CATALOG_LOCK = threading.RLock()


def brands(source_root=None):
    folder=brand_root(source_root)
    # Refresh automatically for imports, edits, additions and deletions. Avoid
    # rereading and hashing every crawler for each wizard validation request.
    paths=sorted(p for p in folder.iterdir() if p.suffix in ('.json','.py') and p.is_file())
    signature=tuple((p.name,p.stat().st_mtime_ns,p.stat().st_size) for p in paths)
    key=str(folder)
    with _CATALOG_LOCK:
        cached=_CATALOG_CACHE.get(key)
        if cached is None or cached[0]!=signature:
            cached=(signature,_load_brands(source_root))
            _CATALOG_CACHE[key]=cached
            if len(_CATALOG_CACHE)>8:_CATALOG_CACHE.pop(next(iter(_CATALOG_CACHE)))
        return copy.deepcopy(cached[1])


def get_brand(key):
    folder=brand_root()
    with _CATALOG_LOCK:
        cached=_CATALOG_CACHE.get(str(folder))
        if cached is not None:
            item=next((b for b in cached[1] if b['id']==key),None)
            if item is not None:
                stamps={name:(modified,size) for name,modified,size in cached[0]}
                meta_name='brands.json' if 'brands.json' in stamps else key+'.json'
                unchanged=True
                for name in (meta_name,item['script']):
                    try:
                        st=(folder/name).stat()
                        unchanged=unchanged and stamps.get(name)==(st.st_mtime_ns,st.st_size)
                    except OSError:unchanged=False
                if unchanged:return copy.deepcopy(item)
    for b in brands():
        if b['id']==key:return b
    raise ValueError('등록된 브랜드가 없습니다. 관리자에서 브랜드를 먼저 등록하세요.')
