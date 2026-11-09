import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
import venv
import shutil

from local_paths import validate_local

ROOT = Path(__file__).resolve().parent
from local_paths import user_home
HOME=user_home()
ENV = HOME / 'venv'
PY = ENV / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
req = ROOT / 'requirements.txt'
stamp = HOME / 'requirements.sha256'
digest = hashlib.sha256(req.read_bytes()).hexdigest()
if not PY.exists():
    venv.create(ENV, with_pip=True)
if not stamp.exists() or stamp.read_text() != digest:
    subprocess.run([str(PY), '-m', 'pip', 'install', '-r', str(req)], check=True)
    stamp.write_text(digest)
if '--install-browser' in sys.argv:
    subprocess.run([str(PY), '-m', 'playwright', 'install', 'chromium'], check=True)
else:
    def verify_published(folder):
        manifest=folder/'brands'/'brands.json'
        if not manifest.exists():return
        for brand in json.loads(manifest.read_text(encoding='utf-8')).get('brands',[]):
            file=(folder/'brands'/brand['script']).resolve()
            if file.parent!=(folder/'brands').resolve():raise RuntimeError('공용 소스의 크롤러 경로가 올바르지 않습니다.')
            if brand.get('code_hash') and (not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=brand['code_hash']):
                raise RuntimeError('브랜드 코드 동기화가 완료되지 않았습니다. 동기화 완료 후 다시 실행하세요.')
    verify_published(ROOT.parent)
    # Launch an immutable local snapshot; OneDrive updates cannot change a running job.
    code_hash = hashlib.sha256()
    source_files=list(ROOT.rglob('*'))+list((ROOT.parent/'brands').glob('*'))
    for path in sorted(source_files):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py','.js','.css','.html','.txt','.json'):
            code_hash.update(str(path.relative_to(ROOT.parent)).encode())
            code_hash.update(path.read_bytes())
    release = HOME / 'releases' / code_hash.hexdigest()[:16]
    if not release.exists():
        staging = HOME / 'releases' / (release.name + '-staging-' + str(os.getpid()))
        staging.mkdir(parents=True,exist_ok=True)
        shutil.copytree(ROOT, staging/'app', dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT.parent/'brands', staging/'brands',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
        verify_published(staging)
        try:
            staging.rename(release)
        except FileExistsError:
            pass  # Another launcher completed the same immutable release first.
    env=dict(os.environ,CRAWLER_HUB_SOURCE_ROOT=str(ROOT))
    subprocess.run([str(PY), '-B', str(release / 'app' / 'server.py')]+sys.argv[1:],check=True,env=env)
