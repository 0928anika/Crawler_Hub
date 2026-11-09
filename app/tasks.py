import copy
import hashlib
import json
import os
import subprocess
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook, load_workbook
from registry import ROOT,brand_root,brand_label
from schema import preview, selection, export_indexes, output_headers, field_for, review_result
from storage import HOME, VERSION, atomic_json
from detail_store import excel_detail
import color_mapping
import local_images

ACTIVE=('running','pausing','paused','stopping','saving')
def safe_cell(value):
    return "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value

class Job:
    def __init__(self,brand,headers,rows,demo,selected=None,name=None):
        self.id=uuid.uuid4().hex
        self.brand,self.headers,self.rows,self.demo=brand,headers,rows,demo
        self.selected=selection(selected)
        self.name=str(name or (brand_label(brand)+' 수집'))[:150]
        self.dir=HOME/'jobs'/self.id
        self.dir.mkdir(parents=True)
        self.control=self.dir/'control.txt';self.control.write_text('run',encoding='utf-8')
        self.state='running';self.done=self.failed=0;self.current='';self.stage='작업 준비'
        self.sheets={};self.items=[];self.row_events=[];self.revision=0;self.logs=[];self.process=None;self.item_error=False
        self.stop=threading.Event();self.data_lock=threading.RLock();self.save_lock=threading.RLock()
        self.started=time.time();self.ended=None;self.created_at=datetime.now().astimezone().isoformat()
        self.saved_at=None;self.save_error='';self.last_export=0;self.last_saved_rows=0
        self.current_task=None;self.item_failures=[];self.task_warnings=[]
        self.queue=[dict(id=str(i),code=str(r[0] or ''),source_row=i+2,status='WAITING',duration=0,error='',result_ids=[]) for i,r in enumerate(rows)]
        path=brand_root()/brand['script']
        self.crawler_hash=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ''
        self.engine_root=None;self.engine_script=None;self.engine_manifest={}
        self.write_input()
        if not demo:self.freeze_engine()
        self.log('샘플 실행 · 실제 사이트에 접속하지 않습니다.' if demo else ('Level 1 · 선택 항목을 실제 수집에 적용합니다.' if brand.get('interface')=='portal_v1' else '기존 샘플 CLI · 새 Level 1 코드로 교체할 수 있습니다.'))
        self.checkpoint()
    def log(self,text,level='INFO'):
        with self.data_lock:
            entry=dict(time=datetime.now().astimezone().isoformat(timespec='seconds'),text=str(text)[:4000],level=level,task_id=self.current_task)
            self.logs.append(entry)
            with (self.dir/'logs.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
    def summary(self):
        with self.data_lock:
            elapsed=(self.ended or time.time())-self.started
            success=sum(x['status']=='SUCCESS' for x in self.queue)
            waiting=sum(x['status']=='WAITING' for x in self.queue)
            mapping=color_mapping.snapshot(self) if color_mapping.is_manual(self) else None
            mapped_count=sum(len({image['assigned_to'] for image in group['images'] if image.get('assigned_to')}) for group in mapping['groups']) if mapping and mapping['finalized'] else len(self.items)
            return dict(id=self.id,name=self.name,brand=brand_label(self.brand),brand_name=self.brand['name'],crawl_source=self.brand.get('crawl_source',''),brand_id=self.brand['id'],state=self.state,
                total=len(self.rows),done=self.done,failed=self.failed,success=success,waiting=waiting,
                current=self.current,stage=self.stage,directory=str(self.dir),demo=self.demo,selected=self.selected,
                created_at=self.created_at,elapsed=round(elapsed,1),average=round(elapsed/self.done,1) if self.done else None,
                eta=round(elapsed/self.done*waiting) if self.done>=3 and self.state in ACTIVE else None,
                pending=sum(q['status'] in ('WAITING','RUNNING','CANCELLED') for q in self.queue),can_retry=any(q['status']=='FAILED' for q in self.queue),can_continue=any(q['status'] in ('WAITING','RUNNING','CANCELLED') for q in self.queue) and bool(self.headers),count=mapped_count,raw_count=len(self.items),saved_at=self.saved_at,save_error=self.save_error,version=VERSION,
                mapping_mode=self.brand.get('mapping_mode','automatic'),image_mode=self.brand.get('image_mode','url'),mapping_finalized=bool(mapping and mapping['finalized']),
                detail_image_ready=bool(mapping and mapping['finalized'] and any(f.startswith('상세이미지/') for f in color_mapping.read_state(self).get('file_export',{}).get('files',[]))),
                crawler_hash=self.crawler_hash,revision=self.revision,queue_revision=self.revision+sum(x['status']!='WAITING' for x in self.queue)+self.done)
    def snapshot(self):
        return dict(self.summary(),items=copy.deepcopy(self.items),logs=copy.deepcopy(self.logs[-150:]))
    def snapshot_queue(self):
        with self.data_lock:return copy.deepcopy(self.queue)
    def write_input(self):
        book=Workbook();book.active.title='입력';book.active.append(self.headers)
        for row in self.rows:book.active.append([safe_cell(v) for v in row])
        book.save(self.dir/'input.xlsx')
    def freeze_engine(self):
        source=(brand_root()/self.brand['script']).resolve()
        if not source.is_file():raise ValueError('기존 작업의 크롤러 파일이 없습니다.')
        if self.crawler_hash and hashlib.sha256(source.read_bytes()).hexdigest()!=self.crawler_hash:
            raise ValueError('기존 작업과 크롤러 버전이 다릅니다. 원래 릴리스에서 이어 실행해주세요.')
        target=self.dir/'engine';target.mkdir(exist_ok=True)
        rel=Path(self.brand['script'])
        if rel.is_absolute():rel=Path(source.name)
        folder=target/'brands'/rel.parent;folder.mkdir(parents=True,exist_ok=True)
        for path in source.parent.glob('*.py'):
            if '__pycache__' in path.parts:continue
            dest=folder/path.relative_to(source.parent);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
        for name in ('worker.py','crawler_sdk.py','crawler_rules.py','regular_price.py','schema.py','detail_store.py','color_palette.py','local_images.py'):
            shutil.copy2(ROOT/name,target/name)
        self.engine_root=target;self.engine_script='brands/'+rel.as_posix()
        self.engine_manifest={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*.py')}
    def verify_engine(self):
        if self.demo:return
        if not self.engine_root:self.freeze_engine()
        if self.engine_root.resolve()!=(self.dir/'engine').resolve():raise ValueError('작업 실행 사본 경로가 올바르지 않습니다.')
        for name,digest in self.engine_manifest.items():
            p=(self.engine_root/name).resolve()
            if self.engine_root.resolve() not in p.parents or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
                raise ValueError('작업의 크롤러 실행 사본이 변경되거나 손상되었습니다: '+name)
    def recount(self):
        self.done=sum(q['status'] in ('SUCCESS','FAILED') for q in self.queue)
        self.failed=sum(q['status']=='FAILED' for q in self.queue)
    def rebuild(self):
        self.sheets={};self.items=[]
        for q in self.queue:q['result_ids']=[]
        heads={}
        for event in self.row_events:self.apply_row(event,heads,True)
    def reset_task(self,tid):
        with self.data_lock:
            self.append_event(dict(kind='task_reset',task_id=tid))
            if any(e.get('task_id')==tid for e in self.row_events):
                self.row_events=[e for e in self.row_events if e.get('task_id')!=tid]
                self.rebuild()
            self.revision+=1
    def prepare_rerun(self,mode):
        if self.state in ACTIVE:raise ValueError('진행 중인 작업은 재실행할 수 없습니다.')
        statuses={'FAILED'} if mode=='retry-failed' else {'WAITING','RUNNING','CANCELLED'}
        targets=[q for q in self.queue if q['status'] in statuses]
        if not targets:raise ValueError('재실행할 품번이 없습니다.')
        if not self.headers or not all(str(self.rows[int(q['id'])][0] or '').strip() for q in targets):
            raise ValueError('이전 버전 작업에 입력 데이터가 없어 이어 실행할 수 없습니다. 입력 엑셀로 새 작업을 만드세요.')
        self.verify_engine();self.write_input()
        for q in targets:q.update(status='WAITING',error='')
        self.stop.clear();self.control.write_text('run',encoding='utf-8')
        self.current_task=None
        self.state='running';self.started=time.time();self.ended=None;self.save_error=''
        self.recount();self.checkpoint()
        self.log(('실패 품번 재실행' if mode=='retry-failed' else '남은 품번 이어하기')+f' · {len(targets)}개 · 완료 품번은 제외')
    def checkpoint(self):
        with self.data_lock:
            data=dict(self.summary(),headers=self.headers,rows=self.rows,queue=self.queue,brand_metadata=self.brand,
                started=self.started,ended=self.ended,engine_root=str(self.engine_root) if self.engine_root else None,engine_script=self.engine_script,engine_manifest=self.engine_manifest)
            atomic_json(self.dir/'progress.json',data)
    def append_event(self,event):
        path=self.dir/'results.jsonl'
        if path.exists() and path.stat().st_size:
            with path.open('rb') as f:f.seek(-1,2);last=f.read(1)
            if last!=b'\n':
                with path.open('ab') as f:f.write(b'\n')
        with (self.dir/'results.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(event,ensure_ascii=False,default=str)+'\n');f.flush();os.fsync(f.fileno())
    def save(self):
        with self.save_lock:
            with self.data_lock:
                sheets=copy.deepcopy(self.sheets)
                events=copy.deepcopy(self.row_events)
                count=len(self.items)
            if events:
                sheets={}
                sheet_headers={};sheet_records={}
                for e in sorted(enumerate(events),key=lambda pair:(int(pair[1]['task_id']) if pair[1].get('task_id') is not None else 10**9,pair[0])):
                    event=e[1];title=event['sheet']
                    heads=event['row'] if event['header'] else event.get('headers',[])
                    union=sheet_headers.setdefault(title,[])
                    for header in heads:
                        if header not in union:union.append(header)
                    if not event['header']:sheet_records.setdefault(title,[]).append(dict(zip(heads,event['row'])))
                for title,heads in sheet_headers.items():
                    sheets[title]=[heads]+[[record.get(h) for h in heads] for record in sheet_records.get(title,[])]
            if color_mapping.is_manual(self):sheets={'결과':color_mapping.export_sheet(self)}
            try:
                for original in (False,):
                    book=Workbook();book.remove(book.active)
                    for title,rows in sheets.items():
                        if not rows:continue
                        is_log=any(x in title for x in ('로그','중복'))
                        ws=book.create_sheet(title[:31])
                        if original or is_log or color_mapping.is_manual(self):
                            output=rows
                        else:
                            indexes=export_indexes(rows[0],self.selected)
                            heads=output_headers(rows[0],self.selected)
                            output=[heads]+[[dict(zip(rows[0],r)).get(h) for h in heads] for r in rows[1:]]
                        for index,row in enumerate(output):
                            ws.append([safe_cell(excel_detail(v)[0] if index and i<len(output[0]) and field_for(output[0][i])=='detail_page' else v) for i,v in enumerate(row)])
                        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
                    if '작업처리로그' not in book.sheetnames:
                        ws=book.create_sheet('작업처리로그');ws.append(['입력 행','품번','상태','처리 시간(초)','오류/주의'])
                        for q in self.queue:
                            notices=[('매핑 결과 제외 · 수집 실패/미완료' if q['status']!='SUCCESS' else '수동 색상 매핑 '+('완료' if color_mapping.snapshot(self)['finalized'] else '대기'))] if color_mapping.is_manual(self) else list(dict.fromkeys(message for item in self.items if item.get('task_id')==q['id'] for message in review_result(item,self.selected)[1]))
                            ws.append([q.get('source_row'),safe_cell(q['code']),q['status'],q.get('duration'),safe_cell(' / '.join(filter(None,[q.get('error','')]+notices)))])
                    if len(book.sheetnames)==1 and book.sheetnames[0]=='작업처리로그':book.create_sheet('결과',0).append(output_headers(self.brand.get('output_headers',[]),self.selected))
                    target=self.dir/('original.xlsx' if original else 'result.xlsx')
                    temp=self.dir/('export-'+uuid.uuid4().hex+'.xlsx')
                    book.save(temp);os.replace(temp,target)
                with self.data_lock:
                    self.saved_at=datetime.now().astimezone().isoformat(timespec='seconds');self.save_error=''
                    self.last_export=time.time();self.last_saved_rows=count
                    self.checkpoint()
                return self.dir/'result.xlsx'
            except Exception as e:
                self.save_error='결과 엑셀 저장 실패: '+str(e)
                self.log(self.save_error,'ERROR');self.checkpoint();raise
    def gate(self):
        while self.control.read_text(encoding='utf-8')=='pause' and not self.stop.is_set():
            self.state='paused';time.sleep(.1)
        if not self.stop.is_set():self.state='running'
        return not self.stop.is_set()
    def apply_row(self,event,local_headers,replay=False):
        title,row=event['sheet'],event['row']
        if event['header']:
            local_headers[title]=row;self.sheets.setdefault(title,[row]);return
        heads=local_headers.get(title,event.get('headers',[]))
        self.sheets.setdefault(title,[heads]).append(row)
        is_log=any(x in title for x in ('로그','중복'))
        if not is_log:
            item=preview(heads,row,self.selected)
            item.update(index=len(self.items),id=str(len(self.items)),task_id=event.get('task_id',self.current_task))
            item['raw']=dict(zip(heads,row))
            if event.get('unmapped'):item['unmapped']=event['unmapped']
            if event.get('unmapped',{}).get('asset_path'):local_images.decorate_candidate(self,item,event['unmapped'])
            item['detail_files']=event.get('detail_files',[])
            item['review_status'],item['warnings']=review_result(item,self.selected)
            self.items.append(item)
            tid=item['task_id']
            if tid is not None and int(tid)<len(self.queue):
                ids=self.queue[int(tid)]['result_ids']
                if item['id'] not in ids:ids.append(item['id'])
            self.stage='수집 결과 수신'
        status_idx=next((i for i,h in enumerate(heads) if h=='상태'),None)
        bad=('실패' in str(row[status_idx])) if status_idx is not None and status_idx<len(row) else '실패' in title
        if bad:
            self.item_error=True
            if not replay:self.log(' / '.join(str(v or '') for v in row),'WARNING')
    def accept(self,event,local_headers):
        with self.data_lock:
            if event['kind']=='row':
                event=dict(event,task_id=self.current_task,headers=local_headers.get(event['sheet'],[]))
                if not event['header'] and not any(x in event['sheet'] for x in ('로그','중복')):
                    event['detail_files']=[]
                    for column,header in enumerate(event['headers']):
                        if field_for(header)=='detail_page' and column<len(event['row']) and event['row'][column]:
                            _,overflow=excel_detail(event['row'][column])
                            if overflow:
                                message='상세 HTML 정리 후에도 Excel 셀 한도 초과 · 등록용 상세 생성 실패'
                                self.log(message,'ERROR');self.item_failures.append(message)
                    event['warnings']=list(dict.fromkeys(self.task_warnings))
                    if self.current_task is not None:self.queue[int(self.current_task)]['warnings']=event['warnings']
                if not event['header'] and any(x in event['sheet'] for x in ('로그','중복')) and self.current_task is not None:
                    event['row']=list(event['row'])
                    for i,h in enumerate(event['headers']):
                        if str(h).replace(' ','')=='입력행' and i<len(event['row']):event['row'][i]=int(self.current_task)+2
                self.append_event(event)
                self.row_events.append(event);self.revision+=1
                self.apply_row(event,local_headers)
            elif event['kind']=='item_failure':
                self.item_failures.append(str(event['message']))
            elif event['kind']=='log':
                self.log(event['message'],event.get('level','INFO'))
                if event.get('level')=='WARNING':
                    self.task_warnings.append(str(event['message']))
                    if self.current_task is not None:self.queue[int(self.current_task)]['warnings']=list(dict.fromkeys(self.task_warnings))
            elif event['kind']=='paused':self.state='paused';self.checkpoint()
            elif event['kind']=='resumed':self.state='running'
            elif event['kind']=='stage':self.stage=event['message']
        # Journal is durable for every row; Excel generation is batched.
        if event['kind']=='row' and (len(self.items)-self.last_saved_rows>=25 or time.time()-self.last_export>=10):self.save()
    def run(self):
        terminal_state='error'
        try:
            for i,row in enumerate(self.rows):
                if self.queue[i]['status']!='WAITING':continue
                if not self.gate():break
                self.reset_task(str(i))
                q=self.queue[i];self.current_task=str(i);self.current=str(row[0] or '')
                q['status']='RUNNING';start=time.time();self.item_error=False;self.item_failures=[];self.task_warnings=[];q['warnings']=[]
                self.stage='품번 검색 및 상품 확인';self.checkpoint()
                self.log(f'[{i+1}/{len(self.rows)}] {self.current}')
                before=len(self.items)
                try:
                    code=0
                    if self.demo:self.run_demo(i,row)
                    else:code=self.run_real(i,row)
                    if self.stop.is_set():q['status']='CANCELLED';q['error']='사용자가 중지했습니다.'
                    elif code or self.item_failures or not any(any(v not in ('',None) for k,v in item['raw'].items() if field_for(k) not in (None,'product_code') or k in ('SKU','GTIN')) for item in self.items[before:]):
                        q['status']='FAILED';q['error']=' / '.join(self.item_failures) or ('결과 없음' if not code else f'크롤러 종료 코드 {code}')
                    elif self.item_error:
                        q['status']='SUCCESS';q['error']='일부 후보·색상에서 오류가 발생했습니다. 로그를 확인하세요.'
                    else:q['status']='SUCCESS'
                except Exception as e:
                    q['status']='FAILED';q['error']=str(e);self.log(str(e),'ERROR');self.kill()
                q['duration']=round(time.time()-start,1)
                self.append_event(dict(kind='task_commit',task_id=str(i),status=q['status'],error=q['error'],duration=q['duration'],warnings=q.get('warnings',[])))
                if q['status']=='FAILED':self.log(q['error'],'ERROR')
                elif q['status']=='SUCCESS':self.log(self.current+' 처리 완료','SUCCESS')
                self.recount()
                self.checkpoint()
            terminal_state='stopped' if self.stop.is_set() else 'completed'
            self.stage='중지됨 · 수신 완료분 보존' if self.stop.is_set() else '처리 완료'
        except Exception as e:
            self.state='error';self.log(str(e),'ERROR')
        finally:
            self.ended=time.time()
            for q in self.queue:
                if q['status'] in ('WAITING','RUNNING'):q['status']='CANCELLED';q['error']='작업 종료로 처리되지 않았습니다.'
            self.state='saving'
            try:
                self.save()
                self.state=terminal_state
                self.checkpoint()
            except Exception:
                self.state='error'
                self.checkpoint()
    def run_demo(self,i,row):
        if color_mapping.is_manual(self):return self.run_manual_demo(i,row)
        heads=self.brand.get('output_headers',['품번','상품명','색상','대표이미지','추가이미지','상세페이지 HTML'])
        self.accept(dict(kind='row',sheet='결과',row=heads,header=True),{})
        for stage in ('상품 확인 중','이미지 슬롯 수집 중','상세페이지 확인 중'):
            for _ in range(5):
                if not self.gate():return
                self.stage=stage;time.sleep(.1)
        from schema import field_for
        main=f'/sample/{i%3}/0'
        extra=[f'/sample/{i%3}/{n}' for n in (1,2,3,3,3)]
        markup=f'<h2>{self.brand["name"]} 샘플 상세페이지</h2><p>UI 확인용 가상 상품 · 실사이트 수집 아님</p><img src="/sample/{i%3}/9">'
        values=dict(product_code=row[0],product_name=self.brand['name']+' 샘플 상품',color=['BLACK','IVORY','BLUE'][i%3],size='M',price=59000,category='샘플 카테고리',main_image=main,additional_images='\n'.join(extra),detail_page=markup)
        result=[values.get(field_for(h),{'SKU':'SAMPLE_M','GTIN':'0000000000000'}.get(h,'')) for h in heads]
        self.accept(dict(kind='row',sheet='결과',header=False,row=result),{'결과':heads})
    def run_manual_demo(self,i,row):
        from color_palette import normalize_palette
        colors=normalize_palette([dict(color_name='IVORY',color_code='39'),dict(color_name='BLACK',color_code='19')] if i%2==0 else [dict(color_name='IVORY',color_code='39')])
        heads=self.brand['output_headers'];self.accept(dict(kind='row',sheet='결과',row=heads,header=True),{})
        shared='<h3>상품정보고시</h3><table><tr><th>구분</th><td>매핑 UI 검증용 가상 상품</td></tr></table>'
        for slot in range(6):
            if not self.gate():return
            url=f'/sample/{1 if slot%2==0 else 0}/{slot}'
            values=dict(product_code=row[0],product_name='색상 매핑 샘플 상품',color=','.join(c['label'] for c in colors),price=59900,category='샘플',main_image=url,additional_images=url if 'main_image' not in self.selected else None,detail_page=shared)
            result=[values.get(field_for(h)) if field_for(h) in ('product_code','product_name','color') or field_for(h) in self.selected else None for h in heads]
            self.accept(dict(kind='row',sheet='결과',header=False,row=result,unmapped=dict(colors=colors,image_url=url,shared_detail_html=shared if 'detail_page' in self.selected else '')),{'결과':heads})
    def run_real(self,i,row):
        output_dir=self.dir/'.work'/'outputs';output_dir.mkdir(parents=True,exist_ok=True)
        inp,out=self.dir/'input.xlsx',output_dir/f'raw-{i}.xlsx'
        engine=self.engine_root or ROOT
        script=engine/self.engine_script if self.engine_script else brand_root()/self.brand['script']
        options=dict(index=i,selected=self.selected,headers=self.brand.get('output_headers',[]),level=self.brand.get('execution_level',1),interface=self.brand.get('interface','legacy_cli'),input_headers=self.headers,input_row=row,mapping_mode=self.brand.get('mapping_mode','automatic'),image_mode=self.brand.get('image_mode','url'),asset_dir=str(self.dir/'images'/'original'))
        env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1')
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        p=subprocess.Popen([sys.executable,'-B',str(engine/'worker.py'),str(script),str(inp),str(out),str(self.control),json.dumps(options,ensure_ascii=False,default=str)],
            cwd=self.dir,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace',creationflags=flags)
        self.process=p
        if self.stop.is_set():self.kill()
        # Includes stalled network/browser operations; user can stop sooner at any point.
        watch_stop=threading.Event()
        def watchdog():
            elapsed=0
            while not watch_stop.wait(1):
                if p.poll() is not None:return
                if self.state not in ('paused','pausing'):elapsed+=1
                if elapsed>=600:
                    self.log('입력 행의 실제 실행 시간이 10분을 초과했습니다.','ERROR')
                    self.kill();return
        threading.Thread(target=watchdog,daemon=True).start()
        local={}
        try:
            for line in p.stdout:
                if line.startswith('@@PORTAL@@'):self.accept(json.loads(line[len('@@PORTAL@@'):]),local)
                elif line.strip():self.log(line.strip())
            return p.wait()
        finally:
            watch_stop.set();p.stdout.close()
            if p.poll() is None:self.kill();p.wait(timeout=10)
            self.process=None
    def kill(self):
        p=self.process
        if p and p.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            else:p.terminate()
    @classmethod
    def load(cls,path):
        data=json.loads((path/'progress.json').read_text(encoding='utf-8'))
        self=cls.__new__(cls)
        self.id=path.name;self.dir=path;self.control=path/'control.txt'
        self.brand=data.get('brand_metadata') or dict(id=data.get('brand_id','legacy'),name=data.get('brand','이전 작업'),script='')
        self.headers=data.get('headers',[]);self.rows=data.get('rows',[[None]]*data.get('total',0));self.demo=data.get('demo',False)
        self.selected=data.get('selected',selection());self.name=data.get('name',self.brand['name']+' 수집')
        self.state=data.get('state','completed');self.done=data.get('done',0);self.failed=data.get('failed',0)
        self.current=data.get('current','');self.stage=data.get('stage','이전 결과')
        self.started=data.get('started',path.stat().st_mtime);self.ended=data.get('ended') or path.stat().st_mtime
        self.created_at=data.get('created_at',datetime.fromtimestamp(self.started).astimezone().isoformat())
        self.saved_at=data.get('saved_at');self.save_error=data.get('save_error','');self.crawler_hash=data.get('crawler_hash','')
        self.sheets={};self.items=[];self.row_events=[];self.revision=0;self.logs=[];self.process=None;self.item_error=False;self.current_task=None;self.item_failures=[];self.task_warnings=[]
        self.stop=threading.Event();self.data_lock=threading.RLock();self.save_lock=threading.RLock();self.last_export=0;self.last_saved_rows=0
        self.engine_root=(path/'engine') if (path/'engine').is_dir() else (Path(data['engine_root']) if data.get('engine_root') else None)
        self.engine_script=data.get('engine_script');self.engine_manifest=data.get('engine_manifest',{})
        self.queue=data.get('queue',[dict(id=str(i),code=str(r[0] or ''),status='CANCELLED',duration=0,error='이전 버전 작업',result_ids=[]) for i,r in enumerate(self.rows)])
        journal=path/'results.jsonl'
        if journal.is_file():
            commits={}
            for line in journal.read_text(encoding='utf-8').splitlines():
                try:
                    event=json.loads(line)
                    kind=event.get('kind','row');tid=event.get('task_id')
                    if kind=='task_reset':
                        self.row_events=[e for e in self.row_events if e.get('task_id')!=tid]
                        commits.pop(tid,None)
                        if tid is not None:self.queue[int(tid)]['status']='CANCELLED'
                    elif kind=='task_commit':commits[tid]=event
                    elif kind=='row':self.row_events.append(event)
                    self.revision+=1
                except (ValueError,KeyError,TypeError,IndexError):continue
            self.rebuild()
            for tid,event in commits.items():
                if tid is not None and int(tid)<len(self.queue):
                    self.queue[int(tid)].update({k:event[k] for k in ('status','error','duration')})
                    self.queue[int(tid)]['warnings']=event.get('warnings',[])
            self.recount()
        else:
            file=path/'original.xlsx' if (path/'original.xlsx').is_file() else path/'result.xlsx'
            if file.is_file():
                wb=load_workbook(file,read_only=True,data_only=True)
                for ws in wb:
                    rows=list(ws.values)
                    if not rows:continue
                    local={ws.title:list(rows[0])};self.sheets[ws.title]=[list(rows[0])]
                    for row in rows[1:]:self.apply_row(dict(sheet=ws.title,row=list(row),header=False),local,True)
                wb.close()
        logpath=path/'logs.jsonl'
        if logpath.exists():
            for line in logpath.read_text(encoding='utf-8').splitlines():
                try:self.logs.append(json.loads(line))
                except ValueError:pass
        else:self.logs=data.get('logs',[])
        if self.state in ACTIVE:
            self.state='interrupted';self.stage='프로그램 종료로 중단됨 · 완료분 복구'
            for q in self.queue:
                if q['status'] in ('RUNNING','WAITING'):q['status']='CANCELLED';q['error']='이전 실행이 종료되었습니다.'
        return self

def history():
    records=[]
    for path in sorted((HOME/'jobs').glob('*/progress.json'),key=lambda x:x.stat().st_mtime,reverse=True):
        try:
            d=json.loads(path.read_text(encoding='utf-8'))
            if d.get('brand_metadata'):d['brand']=brand_label(d['brand_metadata'])
            if d.get('state') in ACTIVE:d['state']='interrupted'
            queue=d.get('queue',[])
            d['pending']=sum(q['status'] in ('WAITING','RUNNING','CANCELLED') for q in queue)
            d['can_continue']=bool(d['pending'] and d.get('headers'))
            d['can_retry']=any(q['status']=='FAILED' for q in queue)
            records.append({k:d.get(k) for k in ('id','name','brand','brand_id','state','total','done','failed','success','count','created_at','elapsed','selected','directory','demo','saved_at','save_error','pending','can_continue','can_retry','mapping_mode','image_mode','mapping_finalized')})
        except (OSError,ValueError):continue
    return records
