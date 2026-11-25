"""Small disk-cached previews for immutable collected photos; exports keep originals."""
import hashlib
import io
import threading
import uuid
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener
from PIL import Image, ImageOps
import local_images

_LOCKS=[threading.Lock() for _ in range(64)]
_DOWNLOADS=threading.BoundedSemaphore(4)
_INDEX_LOCK=threading.Lock()
MAX_BYTES=64_000_000
EDGE=320


def preview_url(job,image_id):
    return '/api/jobs/'+str(getattr(job,'id',job.dir.name))+'/photo-thumbnail?image='+quote(image_id,safe='')


def sources(job):
    import color_mapping as mapping
    stamp=(mapping.view_stamp(job),len(job.items))
    with _INDEX_LOCK:
        cached=getattr(job,'_photo_thumbnail_sources',None)
        if cached and cached[0]==stamp:return cached[1]
        index={g['id']+':'+i['id']:i for g in mapping.source_groups(job) for i in g['images']}
        job._photo_thumbnail_sources=(stamp,index)
        return index


def download(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('공개 원본 이미지 주소가 아닙니다.')
    error=None
    for _ in range(3):
        try:
            req=Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36','Accept':'image/*'})
            with build_opener().open(req,timeout=12) as response:
                if 'text/html' in response.headers.get('Content-Type','').lower():raise ValueError('원본 서버가 이미지 대신 HTML을 반환했습니다.')
                data=response.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:raise ValueError('미리보기 원본이 64MB를 초과합니다.')
            return data
        except (OSError,ValueError) as exc:
            error=exc
            if isinstance(exc,ValueError) or getattr(exc,'code',None) in (401,403,404,410):break
    raise ValueError('미리보기 원본 조회 실패: '+str(error))


def thumbnail(job,image_id):
    import photo_exclusion
    if not photo_exclusion.enabled(job):raise ValueError('사진 제외 작업이 아닙니다.')
    image=sources(job).get(image_id)
    if not image:raise ValueError('현재 수집 결과에 없는 사진입니다.')
    digest=hashlib.sha256(str(image.get('asset_path') or image['url']).encode()).hexdigest()
    target=job.dir/'photo-thumbnails'/'v1'/(digest+'.jpg')
    file=local_images.fs_path(target)
    with _LOCKS[int(digest[:8],16)%len(_LOCKS)]:
        if file.is_file():return file.read_bytes()
        with _DOWNLOADS:
            source=local_images.fs_path(local_images.asset_path(job,image['asset_path'])).read_bytes() if image.get('asset_path') else download(image['url'])
            with Image.open(io.BytesIO(source)) as opened:
                if opened.width*opened.height>60_000_000:raise ValueError('미리보기 원본이 6천만 픽셀을 초과합니다.')
                opened.thumbnail((EDGE,EDGE),Image.Resampling.LANCZOS)
                oriented=ImageOps.exif_transpose(opened)
                rgba=oriented.convert('RGBA')
                canvas=Image.new('RGB',(EDGE,EDGE),'white')
                canvas.paste(rgba,((EDGE-rgba.width)//2,(EDGE-rgba.height)//2),rgba.getchannel('A'))
                out=io.BytesIO();canvas.save(out,'JPEG',quality=82,optimize=True)
                rgba.close();oriented.close();canvas.close();data=out.getvalue()
            file.parent.mkdir(parents=True,exist_ok=True)
            temp=file.parent/('.thumb-'+uuid.uuid4().hex+'.tmp')
            try:temp.write_bytes(data);temp.replace(file)
            finally:temp.unlink(missing_ok=True)
            return data
