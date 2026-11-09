"""Edits the synchronized source, never the running local release or job data."""
import ast
import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from storage import HOME
from datetime import datetime
from pathlib import Path
from registry import ROOT,brands,brand_root
from schema import FIELDS,field_for
from local_paths import sync_roots
from brand_catalog import definition_path, definitions, registry_hash, script_path as catalog_script_path

SOURCE=Path(os.environ.get('CRAWLER_HUB_SOURCE_ROOT') or ROOT).resolve()
ADMIN_LOCK=threading.RLock()

TEMPLATE=(ROOT/'brand_template.py').read_text(encoding='utf-8')

@contextmanager
def edit_lock():
    folder=HOME/'admin-locks';folder.mkdir(exist_ok=True)
    file=folder/(hashlib.sha256(str(SOURCE).casefold().encode()).hexdigest()+'.lock')
    handle=file.open('a+b')
    if file.stat().st_size==0:handle.write(b'0');handle.flush()
    handle.seek(0)
    acquired=False
    try:
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            acquired=True
        except OSError:raise ValueError('이 PC의 다른 관리자 저장이 진행 중입니다.')
        yield
    finally:
        if acquired:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()

def source_status():
    synced=any(SOURCE==p or p in SOURCE.parents for p in sync_roots()) or any('onedrive' in p.lower() or 'sharepoint' in p.lower() for p in SOURCE.parts)
    return dict(source_root=str(SOURCE),synced=synced,writable=os.access(SOURCE,os.W_OK),
        message='이 PC의 소스 폴더에 저장합니다. 다른 PC에 전달하려면 변경 파일을 커밋하고 GitHub에 올린 뒤 해당 버전을 받으세요.')

def metadata_template():
    return json.loads((ROOT/'brand_definition.json').read_text(encoding='utf-8'))


def listing():
    return dict(source_status(),brands=brands(SOURCE),template=TEMPLATE,metadata_template=metadata_template(),
        registry_hash=registry_hash(brand_root(SOURCE)))

def script_path(b):
    return catalog_script_path(brand_root(SOURCE),b)

def read_brand(key):
    b=next((x for x in brands(SOURCE) if x['id']==key),None)
    if not b:raise ValueError('브랜드를 찾을 수 없습니다.')
    code=script_path(b).read_text(encoding='utf-8')
    return dict(metadata=b,code=code,hash=hashlib.sha256(script_path(b).read_bytes()).hexdigest(),
        registry_hash=registry_hash(brand_root(SOURCE),key))

def validate_definition(data):
    meta=dict(data['metadata']);code=data['code']
    if not isinstance(code,str) or len(code.encode())>1024*1024:raise ValueError('코드는 1MB 이하의 텍스트여야 합니다.')
    key=meta.get('id','')
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}',key):raise ValueError('브랜드 ID는 소문자 영문으로 시작하는 영문·숫자·밑줄 2~40자입니다.')
    definition_path(brand_root(SOURCE),key)
    if key in ('schema','crawler_sdk','worker','registry','tasks','storage','admin','bootstrap','local_paths','local_images','color_mapping','color_palette','detail_store','brand_catalog'):raise ValueError('Crawler Hub 공통 모듈 이름은 브랜드 ID로 사용할 수 없습니다.')
    if not str(meta.get('name','')).strip():raise ValueError('브랜드명을 입력하세요.')
    # Validate supplied paths, then retain the registered destination on edits.
    # Generated definitions commonly contain custom/<id>.py even for a legacy
    # brand whose source lives in a different folder.
    if meta.get('script'):script_path(meta)
    current=next((b for b in brands(SOURCE) if b['id']==key),None)
    meta.setdefault('crawl_source',(current or {}).get('crawl_source',''))
    if not isinstance(meta['crawl_source'],str) or not meta['crawl_source'].strip() or len(meta['crawl_source'])>100:
        raise ValueError('크롤링 소스를 1~100자로 입력하세요. 예: 공식 홈페이지, 네이버 NC_YS')
    meta['crawl_source']=meta['crawl_source'].strip()
    meta.setdefault('mapping_mode',(current or {}).get('mapping_mode','automatic'))
    if meta['mapping_mode'] not in ('automatic','manual'):raise ValueError('mapping_mode는 automatic 또는 manual이어야 합니다.')
    meta.pop('display_name',None)
    meta.setdefault('notes',(current or {}).get('notes',''))
    meta.setdefault('image_mode',(current or {}).get('image_mode','url'))
    if meta['image_mode'] not in ('url','local'):raise ValueError('image_mode는 url 또는 local이어야 합니다.')
    if meta['image_mode']=='local' and meta.get('mapping_mode')!='manual':raise ValueError('이미지 저장 유형은 mapping_mode=manual로 등록하세요. 사전 매핑도 이 화면에서 확인·수정합니다.')
    if 'additional_image_count' in meta and (type(meta['additional_image_count']) is not int or meta['additional_image_count'] not in (5,9)):raise ValueError('additional_image_count는 5 또는 9여야 합니다.')
    validate_notes(meta['notes'])
    meta['script']=current['script'] if current else key+'.py'
    script_path(meta)
    tree=ast.parse(code)
    level=meta.get('execution_level',1)
    if level!=1:raise ValueError('새 크롤러의 execution_level은 1로 지정하세요.')
    version=any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PORTAL_API_VERSION' for t in n.targets) and isinstance(n.value,ast.Constant) and n.value.value==1 for n in tree.body)
    if any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PORTAL_API_VERSION' for t in n.targets) for n in tree.body) and not version:raise ValueError('새 크롤러 코드는 PORTAL_API_VERSION = 1로 작성하세요.')
    crawl=any(isinstance(n,ast.FunctionDef) and n.name=='crawl' and len(n.args.args)==1 for n in tree.body)
    meta['execution_level']=1
    meta['interface']='portal_v1' if version and crawl else 'legacy_cli'
    if version and not crawl:raise ValueError('PORTAL_API_VERSION=1에는 def crawl(ctx)가 필요합니다.')
    headers=meta.get('output_headers')
    if not isinstance(headers,list) or not headers or len(headers)>60 or not all(isinstance(h,str) and h for h in headers):raise ValueError('output_headers에 결과 열 목록이 필요합니다.')
    if len(headers)!=len(set(headers)):raise ValueError('출력 헤더를 중복해서 지정할 수 없습니다.')
    if not any(field_for(h)=='product_code' for h in headers):raise ValueError('출력 헤더에 품번 열이 필요합니다.')
    guide=meta.get('guide',{})
    for name in ('example','format','notes','method'):
        if not isinstance(guide.get(name),str):raise ValueError('입력 가이드에 '+name+'이 필요합니다.')
    if guide.get('pattern'):re.compile(guide['pattern'])
    if not isinstance(guide.get('required_columns',['품번']),list) or not all(isinstance(c,str) and c for c in guide.get('required_columns',['품번'])):
        raise ValueError('required_columns는 필수 입력 열 이름 목록이어야 합니다.')
    guide.setdefault('examples',[guide['example']]);guide.setdefault('required_columns',['품번'])
    guide.setdefault('invalid_examples',['공백']);guide.setdefault('validation_note','형식 검사이며 상품 존재 확인이 아닙니다.')
    meta['guide']=guide;meta.setdefault('category','기타');meta.setdefault('extensions',[])
    supported={field_for(h) for h in headers}
    meta['fields']={k:dict(label=v,supported=k in supported,status='conditional' if k in supported else 'unsupported',reason='출력 컬럼 지원 · 상품별 누락 가능' if k in supported else '독립 결과 컬럼 없음') for k,v in FIELDS.items()}
    for key,cap in meta.get('capabilities',{}).items():
        if key not in FIELDS or not isinstance(cap,dict) or not isinstance(cap.get('supported'),bool):raise ValueError('capabilities 형식이 올바르지 않습니다.')
        meta['fields'][key].update(supported=cap['supported'],status=cap.get('status','supported' if cap['supported'] else 'unsupported'),reason=cap.get('reason','지원 근거를 입력하세요.'))
    meta['column_fields']={h:field_for(h) for h in headers};meta.pop('available',None)
    return meta,code


def validate_notes(notes):
    if not isinstance(notes,str) or len(notes)>20000:
        raise ValueError('브랜드 메모는 20,000자 이하의 텍스트여야 합니다.')


def save_notes(data):
    notes=data.get('notes','');validate_notes(notes)
    with ADMIN_LOCK,edit_lock():
        folder=editable_folder()
        current=next((b for b in definitions(folder) if b['id']==data.get('id')),None)
        if not current:raise ValueError('기존 브랜드를 먼저 선택하세요. 새 브랜드는 소스 저장 후 메모를 저장할 수 있습니다.')
        if data.get('registry_hash','')!=registry_hash(folder,current['id']):
            raise ValueError('브랜드 정보가 변경되었습니다. 브랜드를 다시 선택한 뒤 메모를 저장하세요.')
        config=definition_path(folder,current['id']);previous=config.read_bytes()
        backup=backup_folder();(backup/config.name).write_bytes(previous)
        atomic_bytes(config,json_bytes(dict(current,notes=notes)))
        token=registry_hash(folder,current['id'])
    return dict(ok=True,notes=notes,registry_hash=token)


def editable_folder():
    folder=brand_root(SOURCE)
    if not os.access(SOURCE,os.W_OK):raise ValueError('소스 폴더에 쓰기 권한이 없습니다.')
    if (folder/'brands.json').exists():raise ValueError('먼저 python tools/manage_brands.py migrate 명령으로 브랜드 JSON을 분리하세요.')
    folder.mkdir(parents=True,exist_ok=True)
    return folder


def backup_folder():
    folder=HOME/'admin-backups'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True,exist_ok=True)
    return folder


def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def atomic_bytes(path,value):
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_bytes(value)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)

def save_brand(data):
    with ADMIN_LOCK,edit_lock():
        folder=editable_folder()
        meta,code=validate_definition(data)
        meta['code_hash']=hashlib.sha256(code.encode('utf-8')).hexdigest()
        current=next((b for b in definitions(folder) if b['id']==meta['id']),None)
        if current and current['script']!=meta['script']:raise ValueError('기존 브랜드의 파일 경로는 바꿀 수 없습니다.')
        path=script_path(meta);existing=path.read_bytes() if path.exists() else b''
        if not current and path.exists():raise ValueError('등록되지 않은 동일 이름의 Python 파일이 있습니다. 파일을 확인한 뒤 등록하세요.')
        if any(b['id']!=meta['id'] and b['script'].casefold()==meta['script'].casefold() for b in definitions(folder)):
            raise ValueError('다른 브랜드가 사용하는 Python 파일입니다.')
        expected=hashlib.sha256(existing).hexdigest() if existing else ''
        if data.get('expected_hash','')!=expected:raise ValueError('다른 수정이 감지되었습니다. 다시 불러온 뒤 저장하세요.')
        config=definition_path(folder,meta['id']);old_config=config.read_bytes() if config.exists() else b''
        if data.get('registry_hash','')!=registry_hash(folder,meta['id'] if current else None):
            raise ValueError('브랜드 정보가 변경되었습니다. 다시 불러오세요.')
        if path.exists() and path.read_bytes()!=existing:raise ValueError('저장 직전 파일 변경이 감지되었습니다.')
        backup=backup_folder()
        if existing:(backup/'crawler.py').write_bytes(existing)
        if old_config:(backup/config.name).write_bytes(old_config)
        path.parent.mkdir(parents=True,exist_ok=True)
        atomic_bytes(path,code.encode('utf-8'))
        try:
            atomic_bytes(config,json_bytes(meta))
        except Exception:
            if existing:atomic_bytes(path,existing)
            else:path.unlink(missing_ok=True)
            raise
    return dict(ok=True,source_root=str(SOURCE),path=str(path),hash=hashlib.sha256(code.encode()).hexdigest(),backup=str(backup),restart_required=True)
