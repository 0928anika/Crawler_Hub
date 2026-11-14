"""Immutable local candidates and revisioned WING filename exports."""
import hashlib
import io
import os
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote
from PIL import Image, ImageOps


def fs_path(path):
    """Use Windows extended paths for I/O without changing stored relative paths."""
    value=os.path.abspath(os.fspath(path))
    if os.name=='nt':
        slash=chr(92);prefix=slash*2+'?'+slash
        if not value.startswith(prefix):
            value=prefix+'UNC'+slash+value[2:] if value.startswith(slash*2) else prefix+value
    return Path(value)


def enabled(job):
    return job.brand.get('image_mode','url')=='local'


def inside(root,relative):
    root=Path(root).resolve();path=(root/relative).resolve()
    if root not in path.parents:raise ValueError('허용된 이미지 폴더 밖의 경로입니다.')
    return path


def asset_path(job,relative):
    path=inside(job.dir,relative)
    images=(job.dir/'images').resolve()
    if images not in path.parents or path.suffix.lower()!='.jpg':raise ValueError('작업 이미지 파일이 아닙니다.')
    return path


def preview_url(job,relative):
    return '/api/jobs/'+job.id+'/image?path='+quote(relative,safe='')


def save_candidate(root,source,task_id,crop=None,detail=False,metadata=''):
    root=Path(root).resolve()
    if isinstance(source,Image.Image):image=source.copy()
    else:
        with Image.open(io.BytesIO(source) if isinstance(source,(bytes,bytearray)) else fs_path(source)) as opened:
            if getattr(opened,'n_frames',1)>1:raise ValueError('움직이는 이미지는 저장할 수 없습니다.')
            image=ImageOps.exif_transpose(opened).copy()
    try:
        if crop is not None:
            if not isinstance(crop,(list,tuple)) or len(crop)!=4 or any(not isinstance(n,int) for n in crop):raise ValueError('crop은 정수 (left, top, right, bottom)이어야 합니다.')
            l,t,r,b=crop
            if not (0<=l<r<=image.width and 0<=t<b<=image.height):raise ValueError('크롭 영역이 원본 범위를 벗어났습니다.')
            original=image;image=original.crop(crop);original.close()
        width,height=image.size
        if min(width,height)<500 or width*height>60_000_000:raise ValueError('저장 이미지는 양변 500px 이상, 최대 6천만 픽셀이어야 합니다.')
        if not detail and max(width,height)/min(width,height)>1.8:raise ValueError('개별 상품 컷의 비율은 1.8 이하여야 합니다.')
        rgba=image.convert('RGBA');rgb=Image.new('RGB',rgba.size,'white');rgb.paste(rgba,mask=rgba.getchannel('A'));rgba.close()
        output=io.BytesIO();rgb.save(output,'JPEG',quality=95,optimize=True,**({'comment':str(metadata).encode('utf-8')} if metadata else {}));rgb.close();data=output.getvalue()
        if len(data)>10_000_000:raise ValueError('저장할 JPEG 파일이 10MB를 초과합니다.')
        relative=str(int(task_id))+'/'+hashlib.sha256(data).hexdigest()+'.jpg'
        target=inside(root,relative);fs_path(target.parent).mkdir(parents=True,exist_ok=True)
        if not fs_path(target).exists():
            # Keep the temporary leaf short: AppContainer redirects can add a
            # long prefix even when the visible job path looks short.
            temp=target.parent/('.w-'+uuid.uuid4().hex+'.tmp')
            try:
                with fs_path(temp).open('xb') as handle:
                    handle.write(data);handle.flush();os.fsync(handle.fileno())
                fs_path(temp).replace(fs_path(target))
            finally:fs_path(temp).unlink(missing_ok=True)
        return target
    finally:image.close()


def validate_saved(path,detail=False):
    path=fs_path(path)
    if path.stat().st_size>10_000_000:raise ValueError('저장 이미지가 10MB를 초과합니다.')
    with Image.open(path) as image:
        width,height=image.size
        if image.format!='JPEG' or min(width,height)<500 or width*height>60_000_000 or (not detail and max(width,height)/min(width,height)>1.8):raise ValueError('저장 이미지의 실제 JPEG 형식·크기·비율 조건 미달')
        image.verify()
    if hashlib.sha256(path.read_bytes()).hexdigest()!=path.stem:raise ValueError('저장 원본 이미지가 변경되었습니다. 다시 수집하세요.')


def token(value):
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f,]','_',str(value)).strip(' .')
    if not value or value in ('.','..'):raise ValueError('이미지 파일명을 만들 품번·색상 정보가 없습니다.')
    return value[:80]


def additional_count(job):
    value=job.brand.get('additional_image_count',9)
    if type(value) is not int or value not in (5,9):raise ValueError('additional_image_count는 5 또는 9여야 합니다.')
    return value


def plan(view,selected=None,additional_count=9):
    result={};used={}
    selected=['main_image','additional_images'] if selected is None else selected
    limit=(1+additional_count if 'additional_images' in selected else 1) if 'main_image' in selected else (additional_count if 'additional_images' in selected else 0)
    for group in view['groups']:
        for color in group['colors']:
            images=[];seen=set()
            for image in group['images']:
                if color['id'] not in image.get('assigned_colors',[image.get('assigned_to')]):continue
                digest=Path(image['asset_path']).stem
                if digest in seen:continue
                seen.add(digest);images.append(image)
            images=images[:limit]
            base=token(group['code'])+'_'+token(color['color_code'] or color['color_name'])
            entries=[]
            for n,image in enumerate(images,1):
                name=base+('' if n==1 else '_'+str(n).zfill(2))+'.jpg'
                if name.casefold() in used:raise ValueError('이미지 파일명 충돌: '+name+' · 품번/색상코드를 구분해주세요.')
                used[name.casefold()]=True
                entries.append(dict(filename=name,asset_path=image['asset_path'],image_id=image['id']))
            result[(group['id'],color['id'])]=entries
    return result


def prepare_export(job,view,revision):
    roles={}
    for entries in plan(view,job.selected,additional_count(job)).values():
        if not entries:continue
        if 'main_image' in job.selected:roles['대표이미지/'+entries[0]['filename']]=entries[0]
        if 'additional_images' in job.selected:
            extras=(entries[1:] or entries[:1]) if 'main_image' in job.selected else entries
            for entry in extras:roles['추가이미지/'+entry['filename']]=entry
    for group in view['groups']:
        if not any(i.get('assigned_to') for i in group['images']):continue
        entry=detail_entry(job,group['code'])
        if entry:
            name='상세이미지/'+entry['filename']
            if name in roles:raise ValueError('상세이미지 파일명 충돌: '+entry['filename'])
            roles[name]=dict(entry,detail=True)
    folder=job.dir/'images'/'exports'/('r'+str(revision)+'-'+uuid.uuid4().hex[:8])
    staging=folder.with_name(folder.name+'.tmp');fs_path(staging).mkdir(parents=True)
    try:
        for role in ('대표이미지','추가이미지'):fs_path(staging/role).mkdir()
        if any(name.startswith('상세이미지/') for name in roles):fs_path(staging/'상세이미지').mkdir()
        for relative,entry in roles.items():
            source=asset_path(job,entry['asset_path'])
            if not fs_path(source).is_file():raise ValueError('저장한 원본 이미지가 없습니다: '+source.name)
            validate_saved(source,detail=entry.get('detail',False))
            shutil.copy2(fs_path(source),fs_path(staging/relative))
        fs_path(staging).rename(fs_path(folder))
    except Exception:
        shutil.rmtree(fs_path(staging),ignore_errors=True);raise
    return dict(folder=folder.relative_to(job.dir).as_posix(),files=list(roles),layout='roles-v1')


def decorate(job,item,main,extras,paths,additional_paths=None):
    from schema import slots
    item['image_mode']='local';item['image_files']=dict(main=main,additional=extras)
    item['main_images']=[preview_url(job,paths[main])] if main else []
    item['additional_images']=[preview_url(job,(additional_paths or paths)[name]) for name in extras]
    item['images']=item['main_images']+item['additional_images'];item['additional_slots']=slots(item['additional_images'])
    for slot,name in zip(item['additional_slots'],extras):slot['filename']=name
    item['unique_additional']=len(set(extras));item['duplicate_count']=len(extras)-len(set(extras))


def decorate_candidate(job,item,metadata):
    relative=metadata['asset_path'];asset_path(job,relative)
    filename=Path(relative).name
    decorate(job,item,filename if 'main_image' in job.selected else '',[filename] if 'main_image' not in job.selected and 'additional_images' in job.selected else [],{filename:relative})
    if metadata.get('detail_asset_path'):decorate_detail(job,item,metadata['detail_asset_path'],Path(metadata['detail_asset_path']).name)


def detail_entry(job,code):
    if 'detail_page' not in job.selected:return None
    from color_mapping import successful_items
    for item in successful_items(job):
        if str(item.get('code','')).strip().upper()!=code:continue
        relative=item.get('unmapped',{}).get('detail_asset_path')
        if relative:return dict(filename=token(code)+'.jpg',asset_path=relative)
    return None


def decorate_detail(job,item,relative,filename):
    asset_path(job,relative)
    item['html']='<img src="'+preview_url(job,relative)+'" style="max-width:100%;height:auto">'
    item['detail_image_file']=filename


def export_folder(job):
    from color_mapping import snapshot,read_state
    if not enabled(job) or not snapshot(job)['finalized']:raise ValueError('색상 매핑을 완료하여 파일명 결과를 먼저 생성하세요.')
    exported=read_state(job).get('file_export') or {}
    folder=inside(job.dir,exported.get('folder',''))
    root=(job.dir/'images'/'exports').resolve()
    if root not in folder.parents or not fs_path(folder).is_dir():raise ValueError('등록용 이미지 폴더를 찾을 수 없습니다. 매핑 결과를 다시 저장하세요.')
    return folder,exported.get('files',[])


def role_folder(job,role):
    if role not in ('대표이미지','추가이미지','상세이미지'):raise ValueError('알 수 없는 이미지 폴더입니다.')
    folder,_=export_folder(job)
    path=folder/role
    return path if fs_path(path).is_dir() else folder  # Earlier flat exports remain accessible.
