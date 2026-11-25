"""Local HTTP interface (PC only by default; optional PIN-protected phone access with --remote)."""
import base64
import json
import os
import re
import secrets
import sys
sys.dont_write_bytecode=True
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from registry import ROOT,brands,get_brand,brand_root
from schema import preview,FIELDS,REQUIRED,OPTIONAL,review_result
from storage import HOME,VERSION,validate_local,settings,save_settings,validate_input
from tasks import Job,ACTIVE,safe_cell,history
from execution_limits import MAX_JOBS,resource_profile,blocked_reason
import admin
import color_mapping
import request_list
import local_images
import zipfile
import uuid
from local_paths import open_local_path
from urllib.parse import parse_qsl
import remote_access

TOKEN=secrets.token_urlsafe(32)
REMOTE=None  # remote_access.RemoteAccess when started with --remote
PUBLIC_FILES={'/icon-192.png':'image/png','/icon-512.png':'image/png'}
JOBS={}
LOCK=threading.RLock()

def active_jobs():
    result=[]
    for job in JOBS.values():
        if job.state not in ACTIVE:continue
        resources=job_resources(job)
        result.append(dict(id=job.id,name=job.name,brand_id=job.brand['id'],brand=job.brand['name'],state=job.state,resources=resources))
    return result


def job_resources(job):
    saved=job.brand.get('execution_resources')
    engine=getattr(job,'engine_root',None);script=getattr(job,'engine_script',None)
    if engine and script:
        path=(Path(engine)/script).resolve()
        if Path(engine).resolve() in path.parents and path.is_file():return resource_profile(path)
    if saved is not None:return saved
    return resource_profile(brand_root()/job.brand.get('script',''))


def concurrency_snapshot(catalog=None):
    with LOCK:
        active=active_jobs()
        return dict(limit=MAX_JOBS,active=[{k:v for k,v in j.items() if k!='resources'} for j in active],
            availability={b['id']:dict(allowed=not (reason:=blocked_reason(b['execution_resources'],active)),reason=reason) for b in (catalog if catalog is not None else brands())})


def check_start(resources,exclude_id=None):
    reason=blocked_reason(resources,active_jobs(),exclude_id)
    if reason:raise ValueError(reason)


def launch_job(job):
    job.thread=threading.Thread(target=job.run,daemon=True)
    try:job.thread.start()
    except Exception:
        job.state='error';job.checkpoint();raise


def job_for(jid):
    if not re.fullmatch('[a-f0-9]{32}',jid):raise ValueError('잘못된 작업 ID입니다.')
    with LOCK:
        if jid not in JOBS:
            path=(HOME/'jobs'/jid).resolve()
            if path.parent!=(HOME/'jobs').resolve():raise ValueError('허용되지 않은 작업 경로입니다.')
            if not (path/'progress.json').is_file():raise ValueError('작업을 찾을 수 없습니다.')
            JOBS[jid]=Job.load(path)
        return JOBS[jid]

def request_source(job):
    """작업 이력의 결과 엑셀을 요청 목록 변환용으로 검사한다(브랜드명은 작업 브랜드로 미리 채움)."""
    if job.state in ACTIVE:raise ValueError('수집이 끝난 뒤 변환하세요.')
    if color_mapping.is_manual(job) and not color_mapping.snapshot(job)['finalized']:raise ValueError('색상 매핑을 완료한 뒤 요청 목록으로 변환하세요.')
    job.save()
    info=request_list.inspect((job.dir/'result.xlsx').read_bytes(),job.name+'.xlsx')
    info.update(job=job.id,brand=info['brand'] or job.brand.get('name',''))
    return info

def page_rows(rows,query):
    offset=max(0,int(query.get('offset',['0'])[0]));limit=min(300,max(1,int(query.get('limit',['100'])[0])))
    return dict(total=len(rows),rows=rows[offset:offset+limit],offset=offset,limit=limit)

def find_result_item(job, items, item_id, grouped_pending=False):
    """Result IDs belong to source rows, not offsets in a grouped view."""
    item = next((row for row in items if str(row['id']) == str(item_id)), None)
    if item is None and grouped_pending:
        source = next((row for row in job.items if str(row['id']) == str(item_id)), None)
        if source is not None:
            item = next((row for row in items
                         if row.get('task_id') == source.get('task_id')
                         and row.get('code') == source.get('code')), None)
    if item is None:
        source = next((row for row in job.items if str(row['id']) == str(item_id)), None)
        if source is not None and any(str(q['id']) == str(source.get('task_id')) and q['status'] == 'FAILED' for q in job.queue):
            # Failed collection may still have a real archived result to inspect.
            item = source
    return item


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def send(self,body,mime='application/json',status=200,attachment=None):
        if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False,default=str).encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        if attachment:self.send_header('Content-Disposition','attachment; filename="'+attachment+'"')
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass
    def is_local(self):
        return remote_access.is_local_request(self.client_address[0],self.headers.get('Host',''),self.server.server_port)
    def authorized(self):
        if self.is_local():return True
        return REMOTE is not None and REMOTE.session_ok(self.headers.get('Cookie'))
    def redirect(self,location,cookie=None):
        self.send_response(303);self.send_header('Location',location);self.send_header('Cache-Control','no-store');self.send_header('Content-Length','0')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers()
    def public_get(self,path):
        """로그인 전에도 보여줄 것: 로그인 화면, 홈 화면 추가용 manifest·아이콘. 처리했으면 True."""
        if REMOTE is None or self.is_local():
            if path=='/manifest.webmanifest':self.send(remote_access.MANIFEST,'application/manifest+json');return True
            if path in PUBLIC_FILES:self.send((ROOT/'static'/path[1:]).read_bytes(),PUBLIC_FILES[path]);return True
            return False
        if path=='/login':self.send(remote_access.login_page(),'text/html; charset=utf-8');return True
        if path=='/manifest.webmanifest':self.send(remote_access.MANIFEST,'application/manifest+json');return True
        if path in PUBLIC_FILES:self.send((ROOT/'static'/path[1:]).read_bytes(),PUBLIC_FILES[path]);return True
        return False
    def remote_blocked(self,path):
        """원격(휴대폰) 세션에서는 브랜드 코드 관리와 PC 폴더 열기를 막는다."""
        if self.is_local():return False
        if path.startswith('/api/admin'):return True
        parts=path.strip('/').split('/')
        return len(parts)==4 and parts[:2]==['api','jobs'] and parts[3].startswith('open-')
    def send_file(self,file,mime,attachment):
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(file.stat().st_size));self.send_header('Cache-Control','no-store');self.send_header('Content-Disposition','attachment; filename="'+attachment+'"');self.end_headers()
        try:
            with file.open('rb') as source:
                while chunk:=source.read(65536):self.wfile.write(chunk)
        except (BrokenPipeError,ConnectionResetError):pass
    def do_GET(self):
        path=urlparse(self.path).path
        if self.public_get(path):return
        if not self.authorized():
            if REMOTE is not None and path=='/':return self.redirect('/login')
            return self.send({'error':'접근 불가'},status=403)
        if self.remote_blocked(path):return self.send({'error':'휴대폰 접속에서는 사용할 수 없는 기능입니다. PC에서 진행하세요.'},status=403)
        try:self.get()
        except (ValueError,KeyError,IndexError) as e:self.send({'error':str(e)},status=400)
        except Exception as e:self.send({'error':'요청 처리 실패: '+str(e)},status=500)
    def get(self):
        url=urlparse(self.path);path=url.path;query=parse_qs(url.query)
        if path=='/':return self.send((ROOT/'static/index.html').read_bytes().replace(b'__TOKEN__',TOKEN.encode()),'text/html; charset=utf-8')
        if path=='/api/brands':
            catalog=brands()
            return self.send(dict(brands=catalog,concurrency=concurrency_snapshot(catalog),directory=str(HOME/'jobs'),version=VERSION,fields=FIELDS,required=REQUIRED,optional=OPTIONAL))
        if path=='/api/concurrency':return self.send(concurrency_snapshot())
        if path=='/api/active':
            job=next((j for j in JOBS.values() if j.state in ACTIVE),None)
            return self.send(job.summary() if job else None)
        if path=='/api/admin':return self.send(admin.listing())
        if path.startswith('/api/admin/brands/'):return self.send(admin.read_brand(path.rsplit('/',1)[-1]))
        if path.startswith('/api/brands/'):
            parts=path.split('/');b=get_brand(parts[3]);return self.send(b['guide'] if parts[-1]=='input-guide' else b)
        if path=='/api/settings':
            phone=dict(phone_urls=getattr(REMOTE,'urls',[]),phone_pin=REMOTE.pin) if REMOTE is not None and self.is_local() else {}
            return self.send(dict(settings(),directory=str(HOME/'jobs'),runtime=str(Path(sys.executable).resolve()),version=VERSION,local_only=REMOTE is None,remote=REMOTE is not None,level=1,**phone))
        if path=='/api/history':
            rows=history();seen={x['id'] for x in rows}
            rows=[JOBS[x['id']].summary() if x['id'] in JOBS else x for x in rows]
            return self.send([j.summary() for k,j in JOBS.items() if k not in seen]+rows)
        if path.startswith('/api/jobs/'):
            parts=path.strip('/').split('/');job=job_for(parts[2]);action=parts[3] if len(parts)>3 else ''
            if action=='image':
                file=local_images.fs_path(local_images.asset_path(job,query.get('path',[''])[0]))
                if not file.is_file():return self.send({'error':'이미지 파일이 없습니다'},status=404)
                return self.send(file.read_bytes(),'image/jpeg')
            if action=='images-zip':
                with job.save_lock,job.data_lock:
                    folder,names=local_images.export_folder(job);job.save()
                    archive=job.dir/('images-export-'+uuid.uuid4().hex+'.zip')
                    try:
                        with zipfile.ZipFile(local_images.fs_path(archive),'w',zipfile.ZIP_STORED) as bundle:
                            bundle.write(local_images.fs_path(job.dir/'result.xlsx'),'result.xlsx')
                            for name in names:
                                file=local_images.inside(folder,name)
                                bundle.write(local_images.fs_path(file),'images/'+file.relative_to(folder).as_posix())
                        return self.send_file(local_images.fs_path(archive),'application/zip','crawler-images-and-result.zip')
                    finally:local_images.fs_path(archive).unlink(missing_ok=True)
            if action in ('download','export','original'):
                job.save();file=job.dir/'result.xlsx'
                return self.send(file.read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',attachment='crawler-result.xlsx')
            if action=='mapping':
                with job.data_lock:
                    if len(parts)==5 and parts[4]=='detail':return self.send(color_mapping.detail_preview(job,query.get('group_id',[''])[0]))
                    return self.send(color_mapping.snapshot(job))
            if action=='queue':
                rows=job.snapshot_queue();search=query.get('search',[''])[0].lower();status=query.get('status',[''])[0]
                return self.send(page_rows([r for r in rows if search in r['code'].lower() and (not status or r['status']==status)],query))
            if action=='items':
                with job.data_lock:
                    items=color_mapping.visible_items(job)
                    manual_pending=color_mapping.is_manual(job) and not color_mapping.snapshot(job)['finalized']
                    if len(parts)>4:
                        if not re.fullmatch(r'\d+',parts[4]):raise ValueError('올바른 결과 ID가 필요합니다.')
                        item=find_result_item(job,items,parts[4],manual_pending and job.brand.get('group_manual_pending',False))
                        if item is None:return self.send({'error':'결과를 찾을 수 없습니다. 목록을 새로고침해주세요.'},status=404)
                        if len(parts)>5 and parts[5]=='html':return self.send({'error':'찾을 수 없습니다'},status=404)
                        if len(parts)>5 and parts[5]=='raw':return self.send(item['raw'])
                        task=next((x for x in job.queue if x['id']==item['task_id']),None)
                        result={k:v for k,v in item.items() if k!='raw'}
                        result['review_status'],result['warnings']=review_result(item,job.selected)
                        if manual_pending:
                            result['review_status']='mapping';result['warnings']=[]
                        if manual_pending and job.brand.get('mapping_detail_mode')=='selected_images_with_tail':
                            result['detail_notice']='상세페이지는 색상 매핑에서 선택한 상품 사진과 공식 홈페이지의 사이즈표로 완성됩니다. 선택하지 않은 사진은 최종 상세페이지에 포함되지 않습니다. 현재는 사이즈표만 미리 표시됩니다.'
                        result.pop('detail_files',None)
                        if task and task['status']=='FAILED':result['review_status']='error'
                        return self.send(result)
                    search=query.get('search',[''])[0].lower();status=query.get('status',[''])[0];rows=[]
                    tasks={x['id']:x for x in job.queue}
                    for item in items:
                        task=tasks.get(item['task_id']);rs='error' if task and task['status']=='FAILED' else review_result(item,job.selected)[0]
                        if rs!='error' and manual_pending:rs='mapping'
                        if search not in (item['code']+' '+item['name']).lower() or status and rs!=status:continue
                        rows.append({k:item.get(k) for k in ('id','task_id','code','name','color','missing')}|dict(status=rs,image_count=len(item['images'])))
                return self.send(page_rows(rows,query))
            if action=='targets':return self.send(dict(rows=job.target_rows(),job=job.summary(),headers=job.headers))
            if action=='request-source':return self.send(request_source(job))
            if action=='logs':
                with job.data_lock:rows=list(job.logs)
                term=query.get('search',[''])[0].lower();level=query.get('level',[''])[0]
                return self.send(page_rows([r for r in rows if term in r['text'].lower() and (not level or r.get('level')==level)],query))
            if action=='log-file':
                file=job.dir/'logs.jsonl';return self.send(file.read_bytes() if file.exists() else b'','text/plain; charset=utf-8',attachment='crawler-logs.jsonl')
            if not action:return self.send(job.summary())
        if re.fullmatch(r'/sample/[0-9]+/[0-9]+',path):
            _,_,n,slot=path.split('/');color=['#253448','#e5d9c7','#8fa8d0'][int(n)%3]
            svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="660"><rect width="600" height="660" fill="#f3f5f8"/><path d="M195 140L110 220 160 290 200 260 200 500 400 500 400 260 440 290 490 220 405 140 350 120Q300 190 250 120Z" fill="{color}"/><text x="300" y="570" text-anchor="middle" font-family="Arial" fill="#64748b" font-size="20">Crawler Hub / SAMPLE {slot}</text></svg>'
            return self.send(svg.encode(),'image/svg+xml')
        if path in ('/app.js','/am-mapping.js','/mapping-filters.js','/style.css','/image-groups.css'):
            return self.send((ROOT/'static'/path[1:]).read_bytes(),'text/javascript; charset=utf-8' if path.endswith('js') else 'text/css; charset=utf-8')
        return self.send({'error':'찾을 수 없습니다'},status=404)
    def remote_login(self):
        length=int(self.headers.get('Content-Length','0') or 0)
        if not 0<length<=1024:return self.send(remote_access.login_page('PIN을 입력하세요.'),'text/html; charset=utf-8',status=400)
        pin=dict(parse_qsl(self.rfile.read(length).decode('utf-8','replace'))).get('pin','')
        try:token=REMOTE.login(self.client_address[0],pin)
        except ValueError as e:return self.send(remote_access.login_page(str(e)),'text/html; charset=utf-8',status=429)
        if not token:return self.send(remote_access.login_page('PIN이 맞지 않습니다.'),'text/html; charset=utf-8',status=401)
        return self.redirect('/',remote_access.RemoteAccess.cookie(token))
    def do_POST(self):
        if REMOTE is not None and not self.is_local() and self.path=='/login':return self.remote_login()
        if not self.authorized() or self.headers.get('X-Portal-Token')!=TOKEN:return self.send({'error':'접근 불가'},status=403)
        if self.remote_blocked(urlparse(self.path).path):return self.send({'error':'휴대폰 접속에서는 사용할 수 없는 기능입니다. PC에서 진행하세요.'},status=403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=16*1024*1024:raise ValueError('올바른 크기의 입력을 보내주세요.')
            data=json.loads(self.rfile.read(length))
            if self.path in ('/api/validate','/api/jobs'):data['demo']=False
            if self.path=='/api/admin/validate':
                meta,_=admin.validate_definition(data);return self.send(dict(ok=True,metadata=meta))
            if self.path=='/api/admin/save':return self.send(admin.save_brand(data))
            if self.path=='/api/admin/notes':return self.send(admin.save_notes(data))
            if self.path=='/api/settings':return self.send(save_settings(data))
            if self.path=='/api/deduplicate':
                from storage import deduplicate_input
                return self.send(deduplicate_input(data,get_brand(data['brand'])))
            if self.path=='/api/validate':
                result,_,_=validate_input(data,get_brand(data['brand']));return self.send(result)
            if self.path=='/api/request-list/inspect':
                files=data.get('files') or []
                if not 1<=len(files)<=20:raise ValueError('엑셀 파일을 1~20개 올려주세요.')
                return self.send(dict(files=[request_list.inspect(request_list.decode(f),str(f.get('name') or '파일')) for f in files]))
            if self.path=='/api/request-list/build':
                entries=[]
                for f in data.get('files') or []:
                    if f.get('job'):
                        job=job_for(str(f['job']));source=request_source(job)
                        entry=dict(f,name=source['name'],data=(job.dir/'result.xlsx').read_bytes())
                    else:entry=dict(f,name=str(f.get('name') or '파일'),data=request_list.decode(f))
                    entries.append(entry)
                if not entries:raise ValueError('변환할 파일을 먼저 올려주세요.')
                content,summary=request_list.convert(entries,fill_blank_price=bool(data.get('fill_blank_price',True)))
                return self.send(dict(summary=summary,name=request_list.filename(),file=base64.b64encode(content).decode('ascii')))
            if self.path=='/api/jobs':
                with LOCK:
                    brand=get_brand(data['brand']);check_start(brand['execution_resources'])
                    result,headers,rows=validate_input(data,brand)
                    if brand.get('mapping_mode')=='manual' and not any(k in result['selected'] for k in ('main_image','additional_images')):raise ValueError('수동 색상 매핑에는 대표 또는 추가이미지 수집을 선택하세요.')
                    if not result['valid']:raise ValueError('입력 검증 실패: '+', '.join(result['missing_columns'])+' / '+str(result['invalid_count'])+'개 입력 오류')
                    if not brand['available']:raise ValueError('브랜드 크롤러 파일이 없습니다.')
                    job=Job(brand,headers,rows,False,result['selected'],data.get('name'));JOBS[job.id]=job
                    save_settings(dict(selected=data.get('selected',result['selected']),last_brand=brand['id']))
                    launch_job(job)
                return self.send({'id':job.id})
            parts=self.path.strip('/').split('/')
            if len(parts)==5 and parts[:2]==['api','jobs'] and parts[3]=='mapping':
                job=job_for(parts[2])
                if parts[4]=='color':return self.send(color_mapping.save_color(job,data))
                if parts[4]=='finish':return self.send(color_mapping.finalize(job,data))
                if parts[4]=='options':return self.send(color_mapping.edit_options(job,data))
                if parts[4]=='am-preview':return self.send(color_mapping.preview_am(job,data))
                if parts[4]=='am-import':return self.send(color_mapping.import_am(job,data))
                raise ValueError('지원하지 않는 매핑 작업입니다.')
            if len(parts)!=4 or parts[:2]!=['api','jobs']:raise ValueError('지원하지 않는 요청입니다.')
            job=job_for(parts[2]);action=parts[3]
            if action=='targets':
                result=job.edit_targets(data)
                return self.send(dict(result,job=job.summary(),rows=job.target_rows()))
            if action in ('continue','retry-failed'):
                with LOCK:
                    check_start(job_resources(job),exclude_id=job.id)
                    job.prepare_rerun(action)
                    launch_job(job)
            elif action=='pause' and job.state=='running':
                job.control.write_text('pause',encoding='utf-8');job.state='pausing';job.checkpoint()
            elif action=='resume' and job.state in ('paused','pausing'):
                with LOCK:
                    check_start(job_resources(job),exclude_id=job.id)
                    job.control.write_text('run',encoding='utf-8');job.state='running';job.checkpoint()
            elif action=='stop' and job.state in ('running','paused','pausing'):
                job.state='stopping';job.stop.set();job.control.write_text('run',encoding='utf-8');job.kill()
            elif action=='save':job.save()
            elif action in ('open-folder','open-result','open-images','open-main-images','open-additional-images','open-detail-images'):
                path=job.dir.resolve()
                if path.parent!=(HOME/'jobs').resolve():raise ValueError('허용된 작업 폴더가 아닙니다.')
                if action=='open-result':job.save();path=path/'result.xlsx'
                if action=='open-images':path,_=local_images.export_folder(job)
                if action=='open-main-images':path=local_images.role_folder(job,'대표이미지')
                if action=='open-additional-images':path=local_images.role_folder(job,'추가이미지')
                if action=='open-detail-images':path=local_images.role_folder(job,'상세이미지')
                open_local_path(path)
            else:raise ValueError('현재 상태에서 사용할 수 없는 작업입니다.')
            return self.send({'ok':True,'saved_at':job.saved_at})
        except Exception as e:return self.send({'error':str(e)},status=400)

def main():
    import argparse
    global REMOTE
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--remote',action='store_true',help='같은 와이파이·Tailscale의 휴대폰에서 PIN으로 접속 허용')
    parser.add_argument('--reset-pin',action='store_true',help='휴대폰 PIN을 새로 만들고 기존 휴대폰 로그인을 모두 해제');args=parser.parse_args()
    if args.remote:REMOTE=remote_access.RemoteAccess(HOME,reset=args.reset_pin)
    server=ThreadingHTTPServer(('0.0.0.0' if REMOTE else '127.0.0.1',args.port),Handler)
    url=f'http://127.0.0.1:{server.server_port}';print('Crawler Hub '+VERSION+': '+url+('  (PC 전용 주소 — 휴대폰에서는 아래 주소)' if REMOTE else ''),flush=True)
    if REMOTE:
        REMOTE.urls=[f'http://{ip}:{server.server_port}' for ip in remote_access.lan_addresses()]
        print('',flush=True)
        print('='*60,flush=True)
        print(' [휴대폰 접속] 휴대폰 크롬 주소창에 아래 주소를 입력하세요',flush=True)
        print(' (127.0.0.1은 PC 자신이라 휴대폰에서는 열리지 않습니다)',flush=True)
        for phone_url in REMOTE.urls or ['(PC의 IP 주소를 찾지 못했습니다 — 와이파이 연결 확인)']:print('    '+phone_url,flush=True)
        print(' PIN: '+REMOTE.pin,flush=True)
        print(' PC와 휴대폰이 같은 와이파이여야 하고, 이 창을 닫으면 끊깁니다.',flush=True)
        print('='*60,flush=True)
        print('',flush=True)
    if not args.no_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        for job in JOBS.values():
            if job.state in ACTIVE:job.stop.set();job.kill()
        for job in JOBS.values():
            thread=getattr(job,'thread',None)
            if thread and thread.is_alive():thread.join(timeout=5)
        server.server_close()

if __name__=='__main__':main()
