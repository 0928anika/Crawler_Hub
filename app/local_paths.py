"""Dependency-free local storage guard, also used before runtime installation."""
import os
import re
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent


def open_local_path(path):
    """Called only after the HTTP handler has validated the local job path."""
    if sys.platform=='darwin':
        subprocess.Popen(['open',str(path)])
    elif sys.platform=='win32':
        os.startfile(str(path))
    else:
        raise ValueError('폴더/파일 열기는 Windows와 macOS에서 지원합니다.')

def sync_roots():
    roots=[Path(v).resolve() for k,v in os.environ.items() if k.lower().startswith('onedrive') and v]
    if os.name=='nt':
        import winreg
        # SyncRootManager includes business SharePoint roots whose folder names are arbitrary.
        for branch in (r'Software\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager', r'Software\Microsoft\OneDrive\Accounts'):
            def visit(key,depth=0):
                if depth>5:return
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as h:
                        for i in range(winreg.QueryInfoKey(h)[1]):
                            name,value,_=winreg.EnumValue(h,i)
                            if isinstance(value,str) and (name.lower() in ('userfolder','mountpoint','path') or 'usersyncroots' in key.lower()):
                                if re.match(r'^[A-Za-z]:[\\/]',value):roots.append(Path(value).resolve())
                        for i in range(winreg.QueryInfoKey(h)[0]):visit(key+'\\'+winreg.EnumKey(h,i),depth+1)
                except OSError:pass
            visit(branch)
    return roots

def validate_local(path):
    path=Path(path).resolve()
    for root in sync_roots():
        if path==root or root in path.parents:raise ValueError('동기화 폴더에는 결과를 저장할 수 없습니다.')
    if any('onedrive' in p.lower() or 'sharepoint' in p.lower() for p in path.parts):
        raise ValueError('동기화 폴더에는 결과를 저장할 수 없습니다.')
    if path==ROOT or ROOT in path.parents:raise ValueError('공유 소스 폴더에는 결과를 저장할 수 없습니다.')
    if str(path).startswith('\\\\'):raise ValueError('로컬 디스크 경로만 허용합니다.')
    return path



def user_home():
    import shutil
    configured=os.environ.get('CRAWLER_HUB_HOME')
    base=Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))
    home=validate_local(Path(configured) if configured else base/'CrawlerHub')
    if not configured:
        previous=base/('Crawler'+'Portal')
        marker=home/'migration.done'
        if previous.is_dir() and not marker.exists():
            home.mkdir(parents=True,exist_ok=True)
            for folder in ('jobs',):
                if (previous/folder).is_dir() and not (home/folder).exists():shutil.copytree(previous/folder,home/folder)
            if (previous/'settings.json').is_file() and not (home/'settings.json').exists():shutil.copy2(previous/'settings.json',home/'settings.json')
            marker.write_text('Local history preserved',encoding='utf-8')
    return home
