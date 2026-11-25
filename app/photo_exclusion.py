"""Reversible bulk review of already color-linked photo rows. Never edit raw results."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import shutil
import color_mapping as mapping
from storage import atomic_json
from photo_thumbnails import preview_url


def enabled(job):
    return job.brand.get('id')=='phantom_official_no_model' or bool(job.brand.get('photo_exclusion'))


def photo_key(group,image):
    return group['id']+':'+image['id']


def base_view(job,state=None):
    state=deepcopy(mapping.read_state(job) if state is None else state)
    state.pop('photo_review_confirmed',None)
    state.pop('photo_excluded',None)
    return mapping.snapshot(job,groups=mapping.source_groups(job),state=state)


def gallery(job):
    if not enabled(job):raise ValueError('사진 제외를 지원하지 않는 작업입니다.')
    state=mapping.read_state(job);view=base_view(job,state)
    draft=state.get('photo_review_draft')
    explicit=set(draft.get('excluded_ids',[]) if isinstance(draft,dict) else state.get('photo_excluded',[]));photos=[];previews={}
    for group in view['groups']:
        colors={c['id']:c for c in group['colors']}
        for image in group['images']:
            original=[cid for cid in image.get('preassigned_color_ids',[image.get('preassigned_color_id')]) if cid in colors]
            assigned=image.get('assigned_colors',[])
            ids=assigned or original
            pid=photo_key(group,image)
            excluded=pid in explicit or (not assigned and bool(original) and not state.get('photo_review_confirmed') and not isinstance(draft,dict)) or not ids
            thumbnail=previews.setdefault(image['url'],preview_url(job,pid))
            photos.append(dict(id=pid,url=image['url'],thumbnail_url=thumbnail,code=group['code'],name=group['name'],colors=' · '.join(colors[c]['label'] for c in ids),excluded=excluded,mapped=bool(ids)))
    return dict(revision=state.get('revision',0),photos=photos,products=len(view['groups']),finalized=bool(mapping.snapshot(job)['finalized'] and not isinstance(draft,dict)),draft=isinstance(draft,dict),excluded_tasks=len(view['excluded_tasks']))


def apply(job,group,assignments,state):
    """Used during normal result building, including older persisted jobs."""
    if not enabled(job) or not state.get('photo_review_confirmed'):return assignments
    excluded=set(state.get('photo_excluded',[]));valid={c['id'] for c in group['colors']}
    result={}
    for image in group['images']:
        if photo_key(group,image) in excluded:continue
        ids=assignments.get(image['id']) or [cid for cid in image.get('preassigned_color_ids',[image.get('preassigned_color_id')]) if cid in valid]
        if ids:result[image['id']]=list(ids)
    return result


def save(job,data):
    if not enabled(job):raise ValueError('사진 제외를 지원하지 않는 작업입니다.')
    if job.state in ('running','pausing','paused','stopping','saving'):raise ValueError('수집 완료 후 사진을 제외하세요.')
    excluded=data.get('excluded_ids')
    if not isinstance(excluded,list) or not all(isinstance(i,str) for i in excluded):raise ValueError('제외 사진 목록이 올바르지 않습니다.')
    with job.save_lock,job.data_lock:
        previous=mapping.read_state(job)
        if data.get('expected_revision')!=previous.get('revision',0):raise ValueError('다른 창에서 변경되었습니다. 사진 제외 화면을 다시 열어주세요.')
        view=base_view(job,previous)
        excluded_set=set(excluded)
        valid={photo_key(g,i) for g in view['groups'] for i in g['images']}
        if set(excluded)-valid:raise ValueError('현재 수집 결과에 없는 사진입니다. 화면을 다시 열어주세요.')
        if not valid:raise ValueError('검수할 수집 사진이 없습니다.')
        state=deepcopy(previous)
        if data.get('draft'):
            state.update(photo_review_draft=dict(excluded_ids=sorted(excluded_set)),revision=previous.get('revision',0)+1)
            backup=job.dir/'photo-review-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            backup.mkdir(parents=True,exist_ok=False)
            source=job.dir/'color_mapping.json'
            if source.is_file():shutil.copy2(source,backup/source.name)
            atomic_json(job.dir/'color_mapping.json',state)
            try:job.checkpoint()
            except Exception:
                atomic_json(job.dir/'color_mapping.json',previous);raise
            return dict(revision=state['revision'],draft=True,kept=len(valid)-len(excluded_set),excluded=len(excluded_set))
        state.pop('photo_review_draft',None)
        state.update(photo_excluded=sorted(set(excluded)),photo_review_confirmed=True,revision=previous.get('revision',0)+1,finalized=True,fingerprint=view['fingerprint'])
        new_view=mapping.snapshot(job,groups=mapping.source_groups(job),state=state)
        for group in new_view['groups']:
            if any(not i.get('assigned_colors') and photo_key(group,i) not in excluded_set for i in group['images']):raise ValueError(group['code']+': 공식 색상 연결이 없는 사진은 제외해주세요.')
        # Validate output before touching persisted state. Original rows stay immutable.
        rows=mapping.final_items(job,new_view)
        backup=job.dir/'photo-review-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup.mkdir(parents=True,exist_ok=False)
        for name in ('color_mapping.json','result.xlsx'):
            source=job.dir/name
            if source.is_file():shutil.copy2(source,backup/name)
        atomic_json(job.dir/'color_mapping.json',state)
        # The validated output belongs to this exact durable mapping revision.
        # Reuse it for workbook export, summary and review instead of rebuilding.
        stamp=mapping.view_stamp(job)
        if stamp is not None:
            job._mapping_view_cache=(stamp,new_view)
            job._mapping_result_cache=(stamp,rows)
        try:
            job.save();job.checkpoint()
        except Exception:
            atomic_json(job.dir/'color_mapping.json',previous)
            original=backup/'result.xlsx'
            if original.is_file():shutil.copy2(original,job.dir/'result.xlsx')
            raise
        return dict(revision=state['revision'],finalized=True,kept=len(valid)-len(set(excluded)),excluded=len(set(excluded)),rows=len(rows),backup=str(backup))
