"""Per-user manual color assignment; raw URL rows remain immutable."""
import hashlib
import html
import json
from copy import deepcopy
from schema import field_for, preview, output_headers, sanitized_html
from detail_store import excel_detail, registration_layout, verified_original_image_url
from storage import atomic_json
from color_palette import normalize_palette
import local_images
import am_requests
import photo_exclusion


DETAIL_OPTION_ID='__detail__'

def manual_detail(job):return job.brand.get('mapping_detail_mode')=='manual_ordered'

def reserved_detail(color):return color.get('id')==DETAIL_OPTION_ID or str(color.get('color_name') or color.get('label') or '').replace(' ','')=='상세페이지'

def key(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:24]



def is_manual(job):
    return job.brand.get('mapping_mode')=='manual'


def is_hybrid(job):
    return is_manual(job) and bool(job.brand.get('hybrid_mapping'))


def automatic_items(job):
    if not is_hybrid(job):return []
    items=successful_items(job)
    pending={str(item['code']).strip().upper() for item in items if item.get('unmapped')}
    return [item for item in items if not item.get('unmapped') and str(item['code']).strip().upper() not in pending]


def successful_items(job):
    completed={str(q['id']) for q in job.queue if q['status']=='SUCCESS'}
    return kept_items(job,[item for item in job.items if str(item.get('task_id')) in completed])


def source_groups(job):
    groups={}
    for item in successful_items(job):
        raw=item.get('unmapped')
        if not raw:continue
        code=str(item['code']).strip().upper()
        gid=key(code);group=groups.setdefault(gid,dict(id=gid,code=code,name=item['name'],colors=[],images=[]))
        for color in raw['colors']:
            if manual_detail(job) and reserved_detail(color):continue
            match=next((c for c in group['colors'] if same_color(c,color)),None)
            if match:
                if not match['color_code'] and color['color_code']:match['color_code']=color['color_code']
                if not match['color_name'] and color['color_name']:match['color_name']=color['color_name']
                match['label']=color_label(match)
                if 'size' in color:
                    match['size']=','.join(dict.fromkeys(s for text in (match.get('size',''),color['size']) for s in text.split(',') if s))
            else:group['colors'].append(dict(color))
        iid=key(raw['image_url'])
        if not any(image['id']==iid for image in group['images']):
            image=dict(id=iid,url=raw['image_url'],item_id=item['id'])
            if raw.get('preassigned_color_id') and not raw.get('asset_path'):
                suggested=next((c for c in raw['colors'] if c['id']==raw['preassigned_color_id']),None)
                match=next((c for c in group['colors'] if suggested and same_color(c,suggested)),None)
                image.update(preassigned_color_id=match['id'] if match else None,mapping_evidence=raw.get('mapping_evidence',''))
            if raw.get('review_required'):group['review_required']=True
            if raw.get('asset_path'):
                suggested=next((c for c in raw['colors'] if c['id']==raw.get('preassigned_color_id')),None)
                match=next((c for c in group['colors'] if suggested and same_color(c,suggested)),None)
                image.update(url=local_images.preview_url(job,raw['asset_path']),asset_path=raw['asset_path'],preassigned_color_id=match['id'] if match else None,mapping_evidence=raw.get('mapping_evidence',''))
                if raw.get('detail_asset_path'):image['detail_asset_path']=raw['detail_asset_path']
            group['images'].append(image)
        elif raw.get('preassigned_color_id') and raw.get('review_required'):
            image=next(i for i in group['images'] if i['id']==iid)
            suggested=next((c for c in raw['colors'] if c['id']==raw['preassigned_color_id']),None)
            match=next((c for c in group['colors'] if suggested and same_color(c,suggested)),None)
            ids=image.setdefault('preassigned_color_ids',[image['preassigned_color_id']] if image.get('preassigned_color_id') else [])
            if match and match['id'] not in ids:ids.append(match['id'])
            group['review_required']=True
    if is_hybrid(job):
        # Include existing official mappings only for product codes that need review.
        for item in successful_items(job):
            if item.get('unmapped'):continue
            group=groups.get(key(str(item['code']).strip().upper()))
            if group is None:continue
            raw=item.get('raw',{})
            def value(label):return next((str(v or '') for h,v in raw.items() if h.replace(' ','')==label),'')
            color=normalize_palette([dict(color_name=value('색상명') or item.get('color',''),color_code=value('색상코드'))])[0]
            match=next((c for c in group['colors'] if same_color(c,color)),None)
            if match is None:group['colors'].append(color);match=color
            for header,url_string in raw.items():
                if field_for(header) not in ('main_image','additional_images') or not url_string:continue
                for url in str(url_string).split(','):
                    url=url.strip()
                    if not url:continue
                    iid=key(url);photo=next((i for i in group['images'] if i['id']==iid),None)
                    if photo is None:
                        photo=dict(id=iid,url=url,item_id=item['id']);group['images'].append(photo)
                    photo.setdefault('preassigned_color_ids',[])
                    if match['id'] not in photo['preassigned_color_ids']:photo['preassigned_color_ids'].append(match['id'])
                    photo.update(preassigned_color_id=match['id'],mapping_evidence='공식 색상 자동 연결 결과')
            group['review_required']=True
        if job.brand.get('mapping_image_order')=='source':
            order={str(item['id']):index for index,item in enumerate(successful_items(job))}
            for group in groups.values():group['images'].sort(key=lambda i:order.get(str(i['item_id']),len(order)))
        else:
            for group in groups.values():group['images'].sort(key=lambda i:not bool(i.get('preassigned_color_id')))
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


def kept_items(job,items):
    deleted=read_state(job).get('deleted_groups',{})
    return [item for item in items if key(str(item.get('code','')).strip().upper()) not in deleted]


def view_stamp(job):
    # Real jobs increment revision on row edits. File replacement captures
    # mapping saves, option edits, resets, deletions and external reloads.
    if not hasattr(job,'revision'):return None
    path=job.dir/'color_mapping.json'
    try:
        stat=path.stat();mapping=(stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size)
    except FileNotFoundError:mapping=None
    return (job.revision,len(job.items),tuple((q['id'],q['status']) for q in job.queue),
            tuple(job.selected),mapping)


def snapshot(job, *, groups=None, state=None):
    if groups is not None or state is not None:return _snapshot(job,groups=groups,state=state)
    stamp=view_stamp(job);cached=getattr(job,'_mapping_view_cache',None)
    if stamp is not None and cached is not None and cached[0]==stamp:return deepcopy(cached[1])
    result=_snapshot(job)
    if stamp is not None:job._mapping_view_cache=(stamp,deepcopy(result))
    return result


def _snapshot(job, *, groups=None, state=None):
    if not is_manual(job):raise ValueError('색상 자동 매핑 작업에는 수동 매핑이 필요하지 않습니다.')
    groups=source_groups(job) if groups is None else groups
    state=read_state(job) if state is None else state
    fingerprint=key(json.dumps(groups,ensure_ascii=False,sort_keys=True))
    total=done=0
    for group in groups:
        saved=state.get('groups',{}).get(group['id'],{})
        legacy_signature=key(json.dumps({'colors':[c['id'] for c in group['colors']],'images':[i['id'] for i in group['images']]},sort_keys=True))
        group['signature']=key(json.dumps([i['id'] for i in group['images']]))
        group['original_colors']=[dict(c) for c in group['colors']]
        group['am_options']=deepcopy((state.get('am_requests') or {}).get('products',{}).get(am_requests.code_key(group['code']),[]))
        if manual_detail(job):group['am_options']=[c for c in group['am_options'] if not reserved_detail(c)]
        group['am_mode']=state.get('am_requests') is not None
        group['option_sources']=deepcopy(saved.get('option_sources',{}))
        for color in saved.get('added_colors',[]):
            if not any(c['id']==color['id'] for c in group['colors']):group['colors'].append(dict(color,manual=True))
        group['colors']=[c for c in group['colors'] if c['id'] not in saved.get('hidden_colors',[]) and not (manual_detail(job) and reserved_detail(c))]
        valid_colors={c['id'] for c in group['colors']};valid_images={i['id'] for i in group['images']}
        assignments={iid:[cid for cid in (value if isinstance(value,list) else [value]) if cid in valid_colors] for iid,value in saved.get('assignments',{}).items() if iid in valid_images}
        signature_matches=saved.get('image_signature')==group['signature'] or saved.get('signature')==legacy_signature
        confirmed=[cid for cid in saved.get('confirmed',[]) if cid in valid_colors] if signature_matches else []
        initial=group['id'] not in state.get('groups',{})
        if initial or (group.get('review_required') and not saved.get('all_photos')):
            for image in group['images']:
                ids=[cid for cid in image.get('preassigned_color_ids',[image.get('preassigned_color_id')]) if cid in valid_colors]
                if ids and image['id'] not in assignments:assignments[image['id']]=ids
            if initial:confirmed=[] if group.get('review_required') else sorted({cid for ids in assignments.values() for cid in ids})
        assignments=photo_exclusion.apply(job,group,assignments,state)
        for image in group['images']:
            image['assigned_colors']=assignments.get(image['id'],[])
            image['assigned_to']=next(iter(image['assigned_colors']),None)
        confirmed=[cid for cid in confirmed if any(cid in ids for ids in assignments.values())]
        if photo_exclusion.enabled(job) and state.get('photo_review_confirmed'):confirmed=sorted(valid_colors)
        group['confirmed']=confirmed
        group['complete']=len(confirmed)==len(valid_colors)
        if manual_detail(job):
            selected=list(dict.fromkeys(i for i in saved.get('detail_selected',[]) if i in valid_images))
            ready=bool(signature_matches and saved.get('detail_confirmed') and selected)
            group['detail']=dict(id=DETAIL_OPTION_ID,label='상세페이지',selected=selected,confirmed=ready,locked=True)
            group['complete']=group['complete'] and ready
            total+=1;done+=int(ready)
        total+=len(valid_colors);done+=len(confirmed)
    finalized=bool(state.get('finalized') and state.get('fingerprint')==fingerprint and (groups or state.get('deleted_groups')) and all(g['complete'] for g in groups))
    if is_hybrid(job) and not groups and automatic_items(job):finalized=True
    excluded=[dict(code=q['code'],status=q['status'],error=q.get('error','')) for q in job.queue if q['status']!='SUCCESS']
    return dict(revision=state.get('revision',0),groups=groups,total_colors=total,saved_colors=done,excluded_tasks=excluded,image_mode=job.brand.get('image_mode','url'),
                deleted_groups=list(state.get('deleted_groups',{}).values()),finalized=finalized,fingerprint=fingerprint,am_import=state.get('am_requests'),unassigned=sum(not i['assigned_to'] and i['id'] not in (g.get('detail') or {}).get('selected',[]) for g in groups for i in g['images']))


def initial_saved(group):
    return dict(assignments={i['id']:list(i['assigned_colors']) for i in group['images'] if i.get('assigned_colors')},confirmed=list(group['confirmed']),image_signature=group['signature'],all_photos=True)


def save_color(job,data):
    if job.state in ('running','pausing','paused','stopping','saving'):raise ValueError('수집을 완료하거나 중지한 뒤 매핑하세요.')
    with job.data_lock:
        groups=source_groups(job);state=read_state(job)
        view=snapshot(job,groups=deepcopy(groups),state=state)
        if data.get('expected_revision')!=view['revision']:raise ValueError('다른 창에서 매핑이 변경되었습니다. 매핑 화면을 다시 열어주세요.')
        group=next((g for g in view['groups'] if g['id']==data.get('group_id')),None)
        if not group:raise ValueError('품번을 찾을 수 없습니다.')
        color=data.get('color_id')
        if color==DETAIL_OPTION_ID:
            if not manual_detail(job):raise ValueError('상세페이지 수동 매핑 작업이 아닙니다.')
            selected=data.get('image_ids');valid={i['id'] for i in group['images']}
            if not isinstance(selected,list) or not selected or any(i not in valid for i in selected):raise ValueError('상세페이지에 넣을 현재 품번의 사진을 최소 1장 선택하세요.')
            saved=state.setdefault('groups',{}).setdefault(group['id'],initial_saved(group))
            saved.update(detail_selected=list(dict.fromkeys(selected)),detail_confirmed=True,image_signature=group['signature'])
            state.update(revision=view['revision']+1,finalized=False,fingerprint=view['fingerprint'])
            atomic_json(job.dir/'color_mapping.json',state)
            return snapshot(job,groups=groups,state=state)
        reference=next((c for c in group['original_colors'] if c['id']==color),None) if group.get('am_mode') else None
        if color not in {c['id'] for c in group['colors']} and not reference:raise ValueError('잘못된 색상입니다.')
        selected=data.get('image_ids')
        valid_images={image['id'] for image in group['images']}
        if not isinstance(selected,list) or any(i not in valid_images for i in selected):raise ValueError('현재 품번의 사진만 선택할 수 있습니다.')
        if not selected:raise ValueError('사진을 최소 1장 선택한 뒤 저장하세요. 사용하지 않는 색상은 옵션을 삭제하세요.')
        saved=state.setdefault('groups',{}).setdefault(group['id'],initial_saved(group))
        if group.get('review_required') and not saved.get('all_photos'):
            saved.update(initial_saved(group))
        if reference:
            saved['hidden_colors']=[cid for cid in saved.get('hidden_colors',[]) if cid!=color]
        saved['all_photos']=True
        assignments=saved['assignments'];confirmed=set(saved['confirmed'])
        # Each color owns its selection independently; legacy scalar values migrate here.
        assignments={iid:[cid for cid in (value if isinstance(value,list) else [value]) if cid!=color] for iid,value in assignments.items()}
        for iid in selected:assignments.setdefault(iid,[]).append(color)
        saved['assignments']={iid:ids for iid,ids in assignments.items() if ids}
        confirmed.add(color);saved['confirmed']=sorted(confirmed);saved['image_signature']=group['signature']
        state.update(revision=view['revision']+1,finalized=False,fingerprint=view['fingerprint'])
        atomic_json(job.dir/'color_mapping.json',state)
        # Mapping has its own durable file; progress/queue did not change.
        return snapshot(job,groups=groups,state=state)


def edit_options(job,data):
    if job.state in ('running','pausing','paused','stopping','saving'):raise ValueError('수집을 마친 뒤 옵션을 수정하세요.')
    with job.data_lock:
        view=snapshot(job);state=read_state(job)
        if data.get('expected_revision')!=view['revision']:raise ValueError('다른 창에서 매핑이 변경되었습니다. 다시 열어주세요.')
        group=next((g for g in view['groups'] if g['id']==data.get('group_id')),None)
        if not group:raise ValueError('품번을 찾을 수 없습니다.')
        saved=state.setdefault('groups',{}).setdefault(group['id'],initial_saved(group))
        action=data.get('action');active=None;transfers={}
        if manual_detail(job) and data.get('color_id')==DETAIL_OPTION_ID:raise ValueError('상세페이지 옵션은 삭제하거나 변경할 수 없습니다.')
        if action=='delete-product':
            state.setdefault('deleted_groups',{})[group['id']]=dict(id=group['id'],code=group['code'],name=group['name'])
        elif action=='add':
            name=data.get('color_name','');code=data.get('color_code','')
            if not isinstance(name,str) or not isinstance(code,str) or max(len(name),len(code))>80:raise ValueError('색상명·코드는 각각 80자 이하로 입력하세요.')
            color=normalize_palette([dict(color_name=name,color_code=code)])[0]
            if manual_detail(job) and reserved_detail(color):raise ValueError('상세페이지 옵션은 항상 맨 앞에 고정되어 있습니다.')
            if any(same_color(c,color) for c in group['colors']):raise ValueError('같은 색상 옵션이 이미 있습니다.')
            previous=next((c for c in group['original_colors']+saved.get('added_colors',[]) if same_color(c,color)),None)
            if previous:
                saved['hidden_colors']=[cid for cid in saved.get('hidden_colors',[]) if cid!=previous['id']];active=previous['id']
            else:saved.setdefault('added_colors',[]).append(color);active=color['id']
        elif action=='delete':
            if data.get('color_id') not in {c['id'] for c in group['colors']}:raise ValueError('삭제할 색상이 없습니다.')
            saved['hidden_colors']=list(set(saved.get('hidden_colors',[])+[data['color_id']]))
        elif action=='delete-unsaved':
            pending=[c['id'] for c in group['colors'] if c['id'] not in group['confirmed']]
            if not pending:raise ValueError('삭제할 미저장 옵션이 없습니다.')
            saved['hidden_colors']=sorted(set(saved.get('hidden_colors',[])+pending))
        elif action=='configure':
            transfers=configure_options(saved,group,data.get('entries'))
        elif action=='reset':
            if group.get('am_mode'):configure_options(saved,group,[dict(source='am',id=c['id']) for c in group['am_options']])
            else:saved['hidden_colors']=[];saved['added_colors']=[];saved['option_sources']={}
        else:raise ValueError('지원하지 않는 옵션 작업입니다.')
        state.update(revision=view['revision']+1,finalized=False,fingerprint=view['fingerprint'])
        atomic_json(job.dir/'color_mapping.json',state)
    job.checkpoint()
    result=snapshot(job);result['active_color']=active
    if action=='configure':result['option_transfers']=transfers
    return result


def selected_detail(job,group):
    selected=(group.get('detail') or {}).get('selected',[])
    images={i['id']:i for i in group['images']}
    return registration_layout(''.join('<img src="'+html.escape(verified_original_image_url(images[i]['url']) if job.brand.get('id')=='fashionplus_integrated' else images[i]['url'],quote=True)+'" alt="상품 상세">' for i in selected if i in images))


def shared_detail(job,group,items=None):
    """The same collected site detail for every color, across all product links."""
    collected=[];legacy=[];seen=set();modern=False
    for item in successful_items(job) if items is None else items:
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


def detail_preview(job,group_id):
    view=snapshot(job)
    group=next((g for g in view['groups'] if g['id']==group_id),None)
    if group is None:raise ValueError('매핑할 품번을 찾을 수 없습니다.')
    markup='';notice=''
    if manual_detail(job):return dict(code=group['code'],name=group['name'],html=selected_detail(job,group),notice='선택한 순서대로 구성한 상세페이지입니다.' if group['detail']['selected'] else '상세페이지 옵션에서 사진을 선택하세요.',product_url=first_product_link(job,group['code']))
    if job.brand.get('mapping_detail_mode')=='selected_images_with_tail' and job.brand.get('mapping_tail_scope')=='color' and 'detail_page' in job.selected:
        rows=final_items(job,dict(view,groups=[group]))
        markup=''.join(next((str(v or '') for h,v in row['raw'].items() if field_for(h)=='detail_page'),'') for row in rows)
        return dict(code=group['code'],name=group['name'],html=markup,notice='현재 저장된 색상별 선택 사진과 사이즈표·고시입니다. 모델컷 선택을 해제하고 저장하면 상세에서도 제외됩니다.',product_url=first_product_link(job,group['code']))
    if 'detail_page' not in job.selected:notice='이 작업에서는 상세페이지 수집을 선택하지 않았습니다.'
    else:
        entry=local_images.detail_entry(job,group['code']) if local_images.enabled(job) else None
        if entry:markup='<img src="'+html.escape(local_images.preview_url(job,entry['asset_path']),quote=True)+'" alt="수집된 상세이미지">'
        else:
            for item in successful_items(job):
                if str(item.get('code','')).strip().upper()!=group['code']:continue
                raw=next((v for h,v in item.get('raw',{}).items() if field_for(h)=='detail_page' and v),'')
                raw=raw or (item.get('unmapped') or {}).get('shared_detail_html','')
                if raw:markup=sanitized_html(str(raw));break
        if not markup:notice='수집된 상세페이지가 없습니다. 상품 원본에서 확인할 수 있습니다.'
    return dict(code=group['code'],name=group['name'],html=markup,notice=notice,product_url=first_product_link(job,group['code']))


def result_headers(job):
    headers=list(job.brand['output_headers'])
    for item in job.items:
        for header in item['raw']:
            if header not in headers:headers.append(header)
    headers=output_headers(headers,job.selected)
    prefix=[h for h in job.brand.get('export_prefix_headers',[]) if h in headers]
    return prefix+[h for h in headers if h not in prefix]


def first_product_link(job,code,items=None):
    for item in successful_items(job) if items is None else items:
        if str(item.get('code','')).strip().upper()!=code:continue
        link=item.get('normalized',{}).get('product_url') or item.get('source_url')
        if not link:link=next((v for h,v in item['raw'].items() if field_for(h)=='product_url' and v),'')
        if link:return link
    return ''


def final_items(job,view):
    by_id={item['id']:item for item in job.items};result=deepcopy(automatic_items(job))
    headers=result_headers(job)
    count=local_images.additional_count(job)
    local_plan=local_images.plan(view,job.selected,count) if local_images.enabled(job) else None
    state=read_state(job)
    grouped={}
    for item in successful_items(job):grouped.setdefault(str(item.get('code','')).strip().upper(),[]).append(item)
    for group in view['groups']:
        group_items=grouped.get(group['code'],[])
        first_link=first_product_link(job,group['code'],group_items)
        detail=local_images.detail_entry(job,group['code']) if local_images.enabled(job) else None
        compact,overflow=(detail['filename'],False) if detail else excel_detail(selected_detail(job,group) if manual_detail(job) else shared_detail(job,group,group_items)) if 'detail_page' in job.selected else ('',False)
        if overflow:raise ValueError(group['code']+': 공통 상세 HTML이 셀 한도를 초과합니다.')
        for color in group['colors']:
            images=[i for i in group['images'] if color['id'] in i.get('assigned_colors',[i.get('assigned_to')])]
            if not images:continue  # Explicitly saved empty colors are omitted.
            if job.brand.get('mapping_detail_mode')=='selected_images_with_tail' and 'detail_page' in job.selected:
                selected_urls=list(dict.fromkeys(i['url'] for i in images))
                tail_items=group_items
                if job.brand.get('mapping_tail_scope')=='color':
                    tail_items=[item for item in group_items if any(same_color(c,color) for c in (item.get('unmapped') or {}).get('colors',[]))]
                    if not tail_items:tail_items=[by_id[i['item_id']] for i in images]
                markup='<div style="width:100%;max-width:780px;margin:0 auto;text-align:center">'+''.join('<img src="'+html.escape(url,quote=True)+'" alt="상품 사진" style="display:block;width:100%;height:auto;margin:0 auto">' for url in selected_urls)+shared_detail(job,group,tail_items)+'</div>'
                compact,overflow=excel_detail(markup)
                if overflow:raise ValueError(group['code']+': 선택 사진 상세 HTML이 셀 한도를 초과합니다.')

            base=by_id[images[0]['item_id']];record={}
            for header,value in base['raw'].items():
                field=field_for(header)
                if field not in ('color','main_image','additional_images','detail_page'):record[header]=value
            record.update({'품번':group['code'],'상품명':group['name'],'색상':(color['color_name']+color['color_code'] if job.brand.get('color_label_format')=='name_code' else color['color_name'] or color['color_code']),
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
            if entries is None and job.brand.get('export_all_additional_images'):extras=(urls[1:] if 'main_image' in job.selected else urls) or urls[:1]
            extras=extras[:9]
            while len(extras)<5:extras.append(extras[-1])
            values=[]
            for header in headers:
                field=field_for(header)
                value=record.get(header,record.get(next((h for h in record if field_for(h)==field),'')))
                if field=='main_image':value=urls[0]
                elif field=='product_url':value=first_link
                elif field=='additional_images':value=','.join(extras)
                elif field=='detail_page':value=compact
                elif field=='size' and 'size' in job.selected and 'size' in color:value=color['size']
                values.append(value)
            item=preview(headers,values,job.selected,allow_empty_product_name=bool(getattr(job,'brand',{}).get('allow_empty_product_name',False)))
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
    return merge_hybrid(job,result,headers) if is_hybrid(job) else result


def merge_hybrid(job,items,headers):
    """Keep official rows and add manually chosen shots to the same color once."""
    result=[];positions={}
    def identity(item):
        raw=item['raw']
        code=next((v for h,v in raw.items() if field_for(h)=='product_code'),'')
        color=next((v for h,v in raw.items() if h.replace(' ','')=='색상코드'),'')
        if not color:color=next((v for h,v in raw.items() if field_for(h)=='color'),'')
        return str(code).strip().upper(),str(color).strip().casefold()
    def photos(item):
        raw=item['raw'];urls=[]
        for role in ('main_image','additional_images'):
            for h,v in raw.items():
                if field_for(h)!=role or not v:continue
                for url in str(v).split(','):
                    if url and url not in urls:urls.append(url)
        return urls
    for item in items:
        signature=identity(item)
        if signature not in positions:
            positions[signature]=len(result);result.append(item);continue
        index=positions[signature];base=result[index];urls=photos(base)
        urls.extend(u for u in photos(item) if u not in urls)
        raw=dict(base['raw'])
        if urls:
            extras=urls[1:6] or urls[:1]
            while len(extras)<5:extras.append(extras[-1])
            for h in headers:
                if field_for(h)=='main_image':raw[h]=urls[0]
                elif field_for(h)=='additional_images':raw[h]=','.join(extras)
        values=[raw.get(h) for h in headers]
        merged=preview(headers,values,job.selected,allow_empty_product_name=bool(job.brand.get('allow_empty_product_name',False)))
        merged.update(task_id=base.get('task_id'),raw=raw);result[index]=merged
    for index,item in enumerate(result):item.update(id=str(index),index=index)
    return result


def finalize(job,data):
    if job.state in ('running','pausing','paused','stopping','saving'):raise ValueError('수집 중에는 매핑을 완료할 수 없습니다.')
    with job.save_lock,job.data_lock:
        view=snapshot(job)
        if data.get('expected_revision')!=view['revision']:raise ValueError('매핑이 변경되었습니다. 다시 확인하세요.')
        if not view['groups'] and not view.get('deleted_groups'):raise ValueError('매핑할 수집 성공 품번이 없습니다. 실패·미완료 품번은 결과에서 제외됩니다.')
        if not all(g['complete'] for g in view['groups']):raise ValueError('상세페이지와 색상 선택을 모두 저장하세요. 사진을 최소 1장 선택하고 사용하지 않는 색상은 삭제하세요.' if manual_detail(job) else '수집에 성공한 품번의 색상 선택을 모두 저장하세요. 모든 색상에 사진을 최소 1장 선택하세요. 사용하지 않는 색상은 옵션을 삭제하세요.')
        final_items(job,view)  # Validate every row before changing final status.
        previous=read_state(job);state=dict(previous,finalized=True,fingerprint=view['fingerprint'],revision=view['revision']+1)
        if local_images.enabled(job):state['file_export']=local_images.prepare_export(job,view,state['revision'])
        atomic_json(job.dir/'color_mapping.json',state)
        try:job.save();job.checkpoint()
        except Exception:
            atomic_json(job.dir/'color_mapping.json',previous);raise
    return snapshot(job)


def visible_items(job):
    stamp=view_stamp(job);cached=getattr(job,'_mapping_result_cache',None)
    if stamp is not None and cached is not None and cached[0]==stamp:return deepcopy(cached[1])
    result=_visible_items(job)
    if stamp is not None:job._mapping_result_cache=(stamp,deepcopy(result))
    return result


def _visible_items(job):
    if not is_manual(job):return job.items
    view=snapshot(job)
    if view['finalized']:return final_items(job,view)
    if job.brand.get('group_manual_pending'):
        rows=grouped_pending_items(job)
        if is_hybrid(job):
            rows=automatic_items(job)+rows
            rows.sort(key=lambda row:int(row['id']))
        return rows
    return kept_items(job,job.items)


def grouped_pending_items(job):
    """Optional one-row preview/export per model; keep original photo rows for mapping."""
    headers=result_headers(job);by_id={item['id']:item for item in job.items};result=[]
    for group in source_groups(job):
        if not group['images']:continue
        base=by_id[group['images'][0]['item_id']];raw=dict(base['raw'])
        urls=[image['url'] for image in group['images']]
        for header in headers:
            field=field_for(header)
            if field=='color':raw[header]=','.join(c['label'] for c in group['colors'])
            elif field=='main_image':raw[header]=urls[0]
            elif field=='additional_images':raw[header]=','.join(urls[1:10])
            elif field=='product_url':raw[header]=first_product_link(job,group['code'])
        values=[raw.get(h) for h in headers]
        item=preview(headers,values,job.selected,allow_empty_product_name=bool(job.brand.get('allow_empty_product_name',False)))
        item.update(id=base['id'],index=len(result),task_id=base.get('task_id'),raw=dict(zip(headers,values)))
        result.append(item)
    return result


def export_sheet(job):
    view=snapshot(job)
    if view['finalized']:
        rows=final_items(job,view);headers=result_headers(job)
        return [headers]+[[item['raw'].get(h) for h in headers] for item in rows]
    if job.brand.get('group_manual_pending'):
        headers=result_headers(job)
        return [headers]+[[item['raw'].get(h) for h in headers] for item in visible_items(job)]
    if is_hybrid(job):
        headers=result_headers(job)+['매핑상태']
        return [headers]+[[item['raw'].get(h) for h in headers[:-1]]+['색상 매핑 대기' if item.get('unmapped') else '공식 색상 자동 매핑 완료'] for item in successful_items(job)]
    headers=['품번','상품명','색상 후보','상품 링크','이미지파일' if local_images.enabled(job) else '이미지URL','매핑상태']
    optional=[h for h in result_headers(job) if field_for(h) in ('size','price','regular_price','category','detail_page') or field_for(h) is None]
    headers+=optional;rows=[headers]
    for item in kept_items(job,job.items):
        info=item.get('unmapped')
        if info:rows.append([item['code'],item['name'],','.join(c['label'] for c in info['colors']),item.get('normalized',{}).get('product_url') or item.get('source_url',''),info['asset_path'].rsplit('/',1)[-1] if info.get('asset_path') else info['image_url'],'색상 매핑 대기']+[item['raw'].get(h) for h in optional])
    return rows


def configure_options(saved,group,entries):
    """Build only the chosen working palette; source palettes stay immutable."""
    if not isinstance(entries,list) or len(entries)>300:raise ValueError('품번별 작업 옵션은 최대 300개입니다.')
    catalog={'crawl':{c['id']:c for c in group['original_colors']},'am':{c['id']:c for c in group.get('am_options',[])},'current':{c['id']:c for c in group['colors']}}
    chosen={};sources={};transfers={};inherit={}
    for entry in entries:
        if not isinstance(entry,dict):raise ValueError('작업 옵션 형식이 올바르지 않습니다.')
        source=entry.get('source');original=catalog.get(source,{}).get(entry.get('id'))
        if not original:raise ValueError('현재 품번의 AM/수집 옵션만 선택할 수 있습니다.')
        if group.get('detail') and reserved_detail(original):continue
        ids={original['id']};color={k:original.get(k,'') for k in ('color_name','color_code')}
        if entry.get('linked_crawl_id'):raise ValueError('옵션 연결은 지원하지 않습니다.')
        if 'size' in original:color['size']=original['size']
        if max(len(color['color_name']),len(color['color_code']))>80:raise ValueError('옵션 이름/코드는 80자 이하로 입력하세요.')
        normalized=normalize_palette([color])[0];cid=normalized['id'];chosen[cid]=normalized
        inherit.setdefault(cid,set()).update(ids|{cid})
        sources[cid]=dict(source=source,origin_id=original['id'],linked_crawl_id=None)
    raw_ids=set(catalog['crawl']);added={c['id']:c for c in saved.get('added_colors',[])}
    for cid,color in chosen.items():
        if cid not in raw_ids:added[cid]=color
    saved['added_colors']=list(added.values());saved['hidden_colors']=sorted((raw_ids|set(added))-set(chosen));saved['option_sources']=sources
    assignments={iid:list(value) if isinstance(value,list) else [value] for iid,value in saved.get('assignments',{}).items()}
    confirmed=set(saved.get('confirmed',[]));active_confirmed=set(group['confirmed'])
    for cid,old_ids in inherit.items():
        for old in old_ids:transfers.setdefault(old,[]).append(cid)
        for iid,values in assignments.items():
            if old_ids.intersection(values) and cid not in values:values.append(cid)
        if old_ids.intersection(active_confirmed):confirmed.add(cid)
    saved.update(assignments=assignments,confirmed=sorted(confirmed),image_signature=group['signature'],all_photos=True)
    return transfers


def preview_am(job,data):
    with job.data_lock:known=[g['code'] for g in source_groups(job)]
    parsed=am_requests.parse_requests(data,known)
    if manual_detail(job):
        for code,options in parsed.get('products',{}).items():parsed['products'][code]=[c for c in options if not reserved_detail(c)]
        parsed['products']={code:options for code,options in parsed.get('products',{}).items() if options}
        parsed['option_count']=sum(len(v) for v in parsed.get('products',{}).values())
    return parsed


def import_am(job,data):
    if job.state in ('running','pausing','paused','stopping','saving'):raise ValueError('수집을 마친 뒤 AM 요청을 불러오세요.')
    parsed=preview_am(job,data)
    if parsed['needs_columns']:raise ValueError('품번과 색상 열을 선택하고 다시 미리보기하세요.')
    if not parsed['products']:raise ValueError('인식된 AM 요청 옵션이 없습니다. 열과 입력 내용을 확인하세요.')
    with job.data_lock:
        view=snapshot(job);state=read_state(job)
        if data.get('expected_revision')!=view['revision']:raise ValueError('다른 창에서 매핑이 변경되었습니다. 다시 불러오세요.')
        state['am_requests']=parsed;applied=[];kept=[]
        for group in view['groups']:
            options=parsed['products'].get(am_requests.code_key(group['code']),[])
            group['am_options']=options
            saved=state.setdefault('groups',{}).setdefault(group['id'],initial_saved(group))
            configure_options(saved,group,[dict(source='am',id=c['id']) for c in options]);applied.append(group['code'])
        state.update(revision=view['revision']+1,finalized=False,fingerprint=view['fingerprint'])
        atomic_json(job.dir/'color_mapping.json',state)
    job.checkpoint();result=snapshot(job);result['am_applied']=applied;result['am_preserved']=kept
    return result
