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
        message='동기화 소스에 저장합니다. 다른 PC에는 동기화 완료 후 다음 실행부터 적용됩니다.' if synced else '현재 소스는 등록된 동기화 폴더로 확인되지 않습니다. SharePoint 동기화 폴더의 실행 파일로 시작해주세요.')

def metadata_template():
    return json.loads((ROOT/'brand_definition.json').read_text(encoding='utf-8'))


def listing():
    manifest=brand_root(SOURCE)/'brands.json'
    return dict(source_status(),brands=brands(SOURCE),template=TEMPLATE,metadata_template=metadata_template(),
        registry_hash=hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else '')

def script_path(b):
    spec=Path(b['script'])
    if spec.is_absolute() or spec.drive or '..' in spec.parts:raise ValueError('크롤러 경로는 brands 폴더의 파일명여야 합니다.')
    path=(brand_root(SOURCE)/b['script']).resolve()
    if path.parent!=brand_root(SOURCE).resolve() or path.suffix!='.py':raise ValueError('허용된 크롤러 파일이 아닙니다.')
    return path

def read_brand(key):
    b=next((x for x in brands(SOURCE) if x['id']==key),None)
    if not b:raise ValueError('브랜드를 찾을 수 없습니다.')
    code=script_path(b).read_text(encoding='utf-8')
    manifest=brand_root(SOURCE)/'brands.json'
    return dict(metadata=b,code=code,hash=hashlib.sha256(script_path(b).read_bytes()).hexdigest(),
        registry_hash=hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else '')

def validate_definition(data):
    meta=dict(data['metadata']);code=data['code']
    if not isinstance(code,str) or len(code.encode())>1024*1024:raise ValueError('코드는 1MB 이하의 텍스트여야 합니다.')
    key=meta.get('id','')
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}',key):raise ValueError('브랜드 ID는 소문자 영문으로 시작하는 영문·숫자·밑줄 2~40자입니다.')
    if key in ('schema','crawler_sdk','worker','registry','tasks','storage','admin','bootstrap','local_paths','local_images','color_mapping','color_palette','detail_store'):raise ValueError('Crawler Hub 공통 모듈 이름은 브랜드 ID로 사용할 수 없습니다.')
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
    if not source_status()['synced']:raise ValueError('SharePoint/OneDrive 동기화 소스에서 실행해야 공용 메모를 저장할 수 있습니다.')
    with ADMIN_LOCK,edit_lock():
        current=next((b for b in brands(SOURCE) if b['id']==data.get('id')),None)
        if not current:raise ValueError('기존 브랜드를 먼저 선택하세요. 새 브랜드는 공유 소스 저장 후 메모를 저장할 수 있습니다.')
        config=brand_root(SOURCE)/'brands.json';previous=config.read_bytes() if config.exists() else b''
        digest=hashlib.sha256(previous).hexdigest() if previous else ''
        if data.get('registry_hash','')!=digest:raise ValueError('브랜드 정보가 변경되었습니다. 브랜드를 다시 선택한 뒤 메모를 저장하세요.')
        backup=HOME/'admin-backups'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        backup.mkdir(parents=True,exist_ok=True)
        if previous:(backup/'brands.json').write_bytes(previous)
        definitions=json.loads(previous).get('brands',[]) if previous else []
        updated=dict(current,notes=notes);updated.pop('available',None);updated.pop('display_name',None)
        definitions=[b for b in definitions if b['id']!=current['id']]+[updated]
        content=json.dumps(dict(brands=definitions),ensure_ascii=False,indent=2)
        temporary=config.with_name(config.name+'.'+uuid.uuid4().hex+'.tmp')
        temporary.write_bytes(content.encode('utf-8'));os.replace(temporary,config)
    return dict(ok=True,notes=notes,registry_hash=hashlib.sha256(content.encode('utf-8')).hexdigest())

def save_brand(data):
    meta,code=validate_definition(data)
    if not source_status()['synced']:raise ValueError('SharePoint/OneDrive 동기화 소스에서 실행해야 공용 소스를 저장할 수 있습니다.')
    meta['code_hash']=hashlib.sha256(code.encode()).hexdigest()
    with ADMIN_LOCK,edit_lock():
        current=next((b for b in brands(SOURCE) if b['id']==meta['id']),None)
        if current and current['script']!=meta['script']:raise ValueError('기존 브랜드의 파일 경로는 바꿀 수 없습니다.')
        path=script_path(meta);existing=path.read_bytes() if path.exists() else b''
        expected=hashlib.sha256(existing).hexdigest() if existing else ''
        if data.get('expected_hash','')!=expected:raise ValueError('다른 수정이 감지되었습니다. 다시 불러온 뒤 저장하세요.')
        config=brand_root(SOURCE)/'brands.json';old_config=config.read_bytes() if config.exists() else b''
        if data.get('registry_hash','')!=(hashlib.sha256(old_config).hexdigest() if old_config else ''):
            raise ValueError('브랜드 목록이 변경되었습니다. 다시 불러오세요.')
        if path.exists() and path.read_bytes()!=existing:raise ValueError('저장 직전 파일 변경이 감지되었습니다.')
        backup=HOME/'admin-backups'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        backup.mkdir(parents=True,exist_ok=True)
        if existing:(backup/'crawler.py').write_bytes(existing)
        if old_config:(backup/'brands.json').write_bytes(old_config)
        definitions=json.loads(old_config).get('brands',[]) if old_config else []
        definitions=[b for b in definitions if b['id']!=meta['id']]+[meta]
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(code.encode('utf-8'));os.replace(temp,path)
        try:
            temp_config=config.with_name(config.name+'.'+uuid.uuid4().hex+'.tmp')
            temp_config.write_text(json.dumps(dict(brands=definitions),ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temp_config,config)
        except Exception:
            if existing:path.write_bytes(existing)
            else:path.unlink(missing_ok=True)
            raise
    return dict(ok=True,source_root=str(SOURCE),path=str(path),hash=hashlib.sha256(code.encode()).hexdigest(),backup=str(backup),restart_required=True)
