"""Per-user manual color assignment; raw URL rows remain immutable."""
import hashlib
import html
import json
from schema import field_for, preview, output_headers
from detail_store import excel_detail
from storage import atomic_json
from color_palette import normalize_palette
import local_images


def key(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:24]



def is_manual(job):
    return job.brand.get('mapping_mode')=='manual'


def successful_items(job):
    completed={str(q['id']) for q in job.queue if q['status']=='SUCCESS'}
    return [item for item in job.items if str(item.get('task_id')) in completed]


def source_groups(job):
    groups={}
    for item in successful_items(job):
        raw=item.get('unmapped')
        if not raw:continue
        code=str(item['code']).strip().upper()
        gid=key(code);group=groups.setdefault(gid,dict(id=gid,code=code,name=item['name'],colors=[],images=[]))
        for color in raw['colors']:
            match=next((c for c in group['colors'] if same_color(c,color)),None)
            if match:
                if not match['color_code'] and color['color_code']:match['color_code']=color['color_code']
                if not match['color_name'] and color['color_name']:match['color_name']=color['color_name']
                match['label']=color_label(match)
            else:group['colors'].append(dict(color))
        iid=key(raw['image_url'])
        if not any(image['id']==iid for image in group['images']):
            image=dict(id=iid,url=raw['image_url'],item_id=item['id'])
            if raw.get('asset_path'):
                suggested=next((c for c in raw['colors'] if c['id']==raw.get('preassigned_color_id')),None)
                match=next((c for c in group['colors'] if suggested and same_color(c,suggested)),None)
                image.update(url=local_images.preview_url(job,raw['asset_path']),asset_path=raw['asset_path'],preassigned_color_id=match['id'] if match else None,mapping_evidence=raw.get('mapping_evidence',''))
                if raw.get('detail_asset_path'):image['detail_asset_path']=raw['detail_asset_path']
            group['images'].append(image)
    return list(groups.values())


def color_label(color):
    name= color.get('color_name','');code=color.get('color_code','')
    return name+' ('+code+')' if name and code else name or code


def same_color(a,b):
    ac=str(a.get('color_code','')).casefold();bc=str(b.get('color_code','')).casefold()
    an=str(a.get('color_name','')).casefold();bn=str(b.get('color_name','')).casefold()
    return (bool(ac and bc) and ac==bc) or (bool(an and bn) and an==bn and (not ac or not bc or ac==bc))


def read_state(job):
    path=job.dir/'color_mapping.json'
    if not path.exists():return dict(revision=0,groups={},finalized=False)
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError):raise ValueError('색상 매핑 저장 파일을 읽을 수 없습니다. 파일을 확인하세요.')


def snapshot(job):
    if not is_manual(job):raise ValueError('색상 자동 매핑 작업에는 수동 매핑이 필요하지 않습니다.')
    groups=source_groups(job);state=read_state(job)
    fingerprint=key(json.dumps(groups,ensure_ascii=False,sort_keys=True))
    total=done=0
    for group in groups:
        saved=state.get('groups',{}).get(group['id'],{})
        legacy_signature=key(json.dumps({'colors':[c['id'] for c in group['colors']],'images':[i['id'] for i in group['images']]},sort_keys=True))
        group['signature']=key(json.dumps([i['id'] for i in group['images']]))
        group['original_colors']=[dict(c) for c in group['colors']]
        for color in saved.get('added_colors',[]):
            if not any(same_color(c,color) for c in group['colors']):group['colors'].append(dict(color,manual=True))
        group['colors']=[c for c in group['colors'] if c['id'] not in saved.get('hidden_colors',[])]
        valid_colors={c['id'] for c in group['colors']};valid_images={i['id'] for i in group['images']}
        assignments={iid:cid for iid,cid in saved.get('assignments',{}).items() if iid in valid_images and cid in valid_colors}
        signature_matches=saved.get('image_signature')==group['signature'] or saved.get('signature')==legacy_signature
        confirmed=[cid for cid in saved.get('confirmed',[]) if cid in valid_colors] if signature_matches else []
        if group['id'] not in state.get('groups',{}):
            assignments={i['id']:i['preassigned_color_id'] for i in group['images'] if i.get('preassigned_color_id') in valid_colors}
            confirmed=sorted(set(assignments.values()))
        for image in group['images']:image['assigned_to']=assignments.get(image['id'])
        group['confirmed']=confirmed
        group['complete']=len(confirmed)==len(valid_colors)
        total+=len(valid_colors);done+=len(confirmed)
    finalized=bool(state.get('finalized') and state.get('fingerprint')==fingerprint and groups and all(g['complete'] for g in groups))
    excluded=[dict(code=q['code'],status=q['status'],error=q.get('error','')) for q in job.queue if q['status']!='SUCCESS']
    return dict(revision=state.get('revision',0),groups=groups,total_colors=total,saved_colors=done,excluded_tasks=excluded,image_mode=job.brand.get('image_mode','url'),
                finalized=finalized,fingerprint=fingerprint,unassigned=sum(not i['assigned_to'] for g in groups for i in g['images']))


def initial_saved(group):
    return dict(assignments={i['id']:i['assigned_to'] for i in group['images'] if i.get('assigned_to')},confirmed=list(group['confirmed']),image_signature=group['signature'])


def save_color(job,data):
    if job.state in ('running','pausing','paused','stopping','saving'):raise ValueError('수집을 완료하거나 중지한 뒤 매핑하세요.')
    with job.data_lock:
        view=snapshot(job);state=read_state(job)
        if data.get('expected_revision')!=view['revision']:raise ValueError('다른 창에서 매핑이 변경되었습니다. 매핑 화면을 다시 열어주세요.')
        group=next((g for g in view['groups'] if g['id']==data.get('group_id')),None)
        if not group:raise ValueError('품번을 찾을 수 없습니다.')
        color=data.get('color_id')
        if color not in {c['id'] for c in group['colors']}:raise ValueError('잘못된 색상입니다.')
        selected=data.get('image_ids')
        if not isinstance(selected,list) or any(i not in {image['id'] for image in group['images']} for i in selected):raise ValueError('현재 품번의 사진만 선택할 수 있습니다.')
        saved=state.setdefault('groups',{}).setdefault(group['id'],initial_saved(group))
        assignments=saved['assignments'];confirmed=set(saved['confirmed'])
        # Moving a picture invalidates the previous color's confirmation.
        for iid in selected:
            previous=assignments.get(iid)
            if previous and previous!=color:confirmed.discard(previous)
        saved['assignments']={iid:cid for iid,cid in assignments.items() if cid!=color}
        saved['assignments'].update({iid:color for iid in selected})
        confirmed.add(color);saved['confirmed']=sorted(confirmed);saved['image_signature']=group['signature']
        state.update(revision=view['revision']+1,finalized=False,fingerprint=view['fingerprint'])
        atomic_json(job.dir/'color_mapping.json',state)
    job.checkpoint()
    return snapshot(job)


def edit_options(job,data):
    if job.state in ('running','pausing','paused','stopping','saving'):raise ValueError('수집을 마친 뒤 옵션을 수정하세요.')
    with job.data_lock:
        view=snapshot(job);state=read_state(job)
        if data.get('expected_revision')!=view['revision']:raise ValueError('다른 창에서 매핑이 변경되었습니다. 다시 열어주세요.')
        group=next((g for g in view['groups'] if g['id']==data.get('group_id')),None)
        if not group:raise ValueError('품번을 찾을 수 없습니다.')
        saved=state.setdefault('groups',{}).setdefault(group['id'],initial_saved(group))
        action=data.get('action');active=None
        if action=='add':
            name=data.get('color_name','');code=data.get('color_code','')
            if not isinstance(name,str) or not isinstance(code,str) or max(len(name),len(code))>80:raise ValueError('색상명·코드는 각각 80자 이하로 입력하세요.')
            color=normalize_palette([dict(color_name=name,color_code=code)])[0]
            if any(same_color(c,color) for c in group['colors']):raise ValueError('같은 색상 옵션이 이미 있습니다.')
            previous=next((c for c in group['original_colors']+saved.get('added_colors',[]) if same_color(c,color)),None)
            if previous:
                saved['hidden_colors']=[cid for cid in saved.get('hidden_colors',[]) if cid!=previous['id']];active=previous['id']
            else:saved.setdefault('added_colors',[]).append(color);active=color['id']
        elif action=='delete':
            if data.get('color_id') not in {c['id'] for c in group['colors']}:raise ValueError('삭제할 색상이 없습니다.')
            saved['hidden_colors']=list(set(saved.get('hidden_colors',[])+[data['color_id']]))
        elif action=='reset':
            saved['hidden_colors']=[];saved['added_colors']=[]
        else:raise ValueError('지원하지 않는 옵션 작업입니다.')
        state.update(revision=view['revision']+1,finalized=False,fingerprint=view['fingerprint'])
        atomic_json(job.dir/'color_mapping.json',state)
    job.checkpoint()
    result=snapshot(job);result['active_color']=active
    return result


def shared_detail(job,group):
    """The same collected site detail for every color, across all product links."""
    collected=[];legacy=[];seen=set();modern=False
    for item in successful_items(job):
        if str(item.get('code','')).strip().upper()!=group['code']:continue
        info=item.get('unmapped') or {}
        if info.get('detail_mode')=='site_shared':
            modern=True
            markup=next((v for h,v in item['raw'].items() if field_for(h)=='detail_page' and v),'')
            if markup and key(markup) not in seen:collected.append(markup);seen.add(key(markup))
        else:
            markup=info.get('shared_detail_html','')
            if markup and key(markup) not in seen:legacy.append(markup);seen.add(key(markup))
    if modern:return ''.join(collected+legacy)
    # Old jobs did not collect the full site detail: keep all available colors'
    # gallery pictures and their original common notices/size tables.
    return ''.join('<img src="'+html.escape(i['url'],quote=True)+'">' for i in group['images'])+''.join(legacy)


def result_headers(job):
    headers=list(job.brand['output_headers'])
    for item in job.items:
        for header in item['raw']:
            if header not in headers:headers.append(header)
    return output_headers(headers,job.selected)


def first_product_link(job,code):
    for item in successful_items(job):
        if str(item.get('code','')).strip().upper()!=code:continue
        link=item.get('normalized',{}).get('product_url') or item.get('source_url')
        if not link:link=next((v for h,v in item['raw'].items() if field_for(h)=='product_url' and v),'')
        if link:return link
    return ''


def final_items(job,view):
    by_id={item['id']:item for item in job.items};result=[]
    headers=result_headers(job)
    count=local_images.additional_count(job)
    local_plan=local_images.plan(view,job.selected,count) if local_images.enabled(job) else None
    state=read_state(job)
    for group in view['groups']:
        first_link=first_product_link(job,group['code'])
        detail=local_images.detail_entry(job,group['code']) if local_images.enabled(job) else None
        compact,overflow=(detail['filename'],False) if detail else excel_detail(shared_detail(job,group)) if 'detail_page' in job.selected else ('',False)
        if overflow:raise ValueError(group['code']+': 공통 상세 HTML이 셀 한도를 초과합니다.')
        for color in group['colors']:
            images=[i for i in group['images'] if i.get('assigned_to')==color['id']]
            if not images:continue  # Explicitly saved empty colors are omitted.
            base=by_id[images[0]['item_id']];record={}
            for header,value in base['raw'].items():
                field=field_for(header)
                if field not in ('color','main_image','additional_images','detail_page'):record[header]=value
            record.update({'품번':group['code'],'상품명':group['name'],'색상':color['color_name'] or color['color_code'],
                           '색상명':color['color_name'],'색상코드':color['color_code']})
            for header in headers:
                if field_for(header) is not None:continue
                values=[]
                for image in images:
                    value=by_id[image['item_id']]['raw'].get(header)
                    if value is not None and value!='' and value not in values:values.append(value)
                record[header]=values[0] if len(values)==1 else '\n'.join(str(v) for v in values) if values else None
            entries=local_plan[(group['id'],color['id'])] if local_plan is not None else None
            urls=[e['filename'] for e in entries] if entries is not None else [i['url'] for i in images]
            extras=urls[:count] if entries is not None and 'main_image' not in job.selected else urls[1:count+1] or urls[:1]
            while len(extras)<5:extras.append(extras[-1])
            values=[]
            for header in headers:
                field=field_for(header)
                value=record.get(header,record.get(next((h for h in record if field_for(h)==field),'')))
                if field=='main_image':value=urls[0]
                elif field=='product_url':value=first_link
                elif field=='additional_images':value=','.join(extras)
                elif field=='detail_page':value=compact
                values.append(value)
            item=preview(headers,values,job.selected)
            if entries is not None:
                folder=state.get('file_export',{}).get('folder') if state.get('finalized') else None
                role_layout=folder and state.get('file_export',{}).get('layout')=='roles-v1'
                paths={e['filename']:folder+('/대표이미지/' if role_layout else '/')+e['filename'] if folder else e['asset_path'] for e in entries}
                extra_paths={e['filename']:folder+('/추가이미지/' if role_layout else '/')+e['filename'] if folder else e['asset_path'] for e in entries}
                local_images.decorate(job,item,urls[0] if 'main_image' in job.selected else '',extras if 'additional_images' in job.selected else [],paths,extra_paths)
            if detail:
                exported=state.get('file_export',{}).get('folder') if state.get('finalized') else None
                relative=exported+'/상세이미지/'+detail['filename'] if exported else detail['asset_path']
                local_images.decorate_detail(job,item,relative,detail['filename'])
            item.update(id=str(len(result)),index=len(result),task_id=base.get('task_id'),raw=dict(zip(headers,values)))
            result.append(item)
    return result


def finalize(job,data):
    if job.state in ('running','pausing','paused','stopping','saving'):raise ValueError('수집 중에는 매핑을 완료할 수 없습니다.')
    with job.save_lock,job.data_lock:
        view=snapshot(job)
        if data.get('expected_revision')!=view['revision']:raise ValueError('매핑이 변경되었습니다. 다시 확인하세요.')
        if not view['groups']:raise ValueError('매핑할 수집 성공 품번이 없습니다. 실패·미완료 품번은 결과에서 제외됩니다.')
        if not all(g['complete'] for g in view['groups']):raise ValueError('수집에 성공한 품번의 색상 선택을 모두 저장하세요. 사진 0장을 저장한 색상은 결과에서 제외됩니다.')
        final_items(job,view)  # Validate every row before changing final status.
        previous=read_state(job);state=dict(previous,finalized=True,fingerprint=view['fingerprint'],revision=view['revision']+1)
        if local_images.enabled(job):state['file_export']=local_images.prepare_export(job,view,state['revision'])
        atomic_json(job.dir/'color_mapping.json',state)
        try:job.save();job.checkpoint()
        except Exception:
            atomic_json(job.dir/'color_mapping.json',previous);raise
    return snapshot(job)


def visible_items(job):
    if not is_manual(job):return job.items
    view=snapshot(job)
    return final_items(job,view) if view['finalized'] else job.items


def export_sheet(job):
    view=snapshot(job)
    if view['finalized']:
        rows=final_items(job,view);headers=result_headers(job)
        return [headers]+[[item['raw'].get(h) for h in headers] for item in rows]
    headers=['품번','상품명','색상 후보','상품 링크','이미지파일' if local_images.enabled(job) else '이미지URL','매핑상태']
    optional=[h for h in result_headers(job) if field_for(h) in ('size','price','category','detail_page') or field_for(h) is None]
    headers+=optional;rows=[headers]
    for item in job.items:
        info=item.get('unmapped')
        if info:rows.append([item['code'],item['name'],','.join(c['label'] for c in info['colors']),item.get('normalized',{}).get('product_url') or item.get('source_url',''),info['asset_path'].rsplit('/',1)[-1] if info.get('asset_path') else info['image_url'],'색상 매핑 대기']+[item['raw'].get(h) for h in optional])
    return rows
