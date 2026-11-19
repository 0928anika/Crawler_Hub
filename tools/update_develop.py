"""Anonymous public develop updater. Only managed source paths are replaced."""
import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

REPO = '0928anika/Crawler_Hub'
BRANCH = 'develop'
DIRECTORIES = ('app', 'brands', 'tools')
LAUNCHERS = ('Crawler_Hub_Windows.bat', 'Crawler_Hub_Mac.command',
             'Crawler_Hub_Develop_Windows.bat')
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXPANDED = 400 * 1024 * 1024


def download(url, destination, limit=MAX_ARCHIVE):
    """No browser session, Git credentials, token, or company credentials."""
    allowed = ('api.github.com', 'github.com', 'codeload.github.com')
    from urllib.parse import urlsplit
    if urlsplit(url).hostname not in allowed or urlsplit(url).scheme != 'https':
        raise ValueError('허용되지 않은 다운로드 주소')
    request = urllib.request.Request(url, headers={'User-Agent': 'CrawlerHub-Public-Updater',
                                                  'Accept': 'application/vnd.github+json'})
    deadline = time.monotonic() + 180
    with urllib.request.urlopen(request, timeout=30) as response:
        final = urlsplit(response.geturl())
        if final.scheme != 'https' or final.hostname not in allowed:
            raise ValueError('GitHub 외부 다운로드 이동 거부')
        if int(response.headers.get('Content-Length', '0')) > limit:
            raise ValueError('다운로드 용량 한도 초과')
        size = 0
        with Path(destination).open('wb') as output:
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError('다운로드 전체 시간 한도 초과')
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ValueError('다운로드 용량 한도 초과')
                output.write(chunk)


def latest_commit(folder):
    path = folder / 'ref.json'
    download('https://api.github.com/repos/' + REPO + '/git/ref/heads/' + BRANCH,
             path, 1024 * 1024)
    data = json.loads(path.read_text(encoding='utf-8'))
    sha = str((data.get('object') or {}).get('sha') or '')
    if data.get('ref') != 'refs/heads/' + BRANCH or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('공식 develop 커밋 확인 실패')
    return sha


def unpack(archive, staging):
    """Extract source only; reject traversal, symlinks and oversized archives."""
    total = 0
    top = None
    names = set()
    with zipfile.ZipFile(archive) as package:
        if len(package.infolist()) > 25000:
            raise ValueError('ZIP 파일 수 한도 초과')
        for info in package.infolist():
            path = PurePosixPath(info.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in info.filename
                    or ':' in info.filename or not path.parts):
                raise ValueError('안전하지 않은 ZIP 경로')
            if any(part.rstrip(' .') != part or re.fullmatch(
                    r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
                   for part in path.parts):
                raise ValueError('Windows 예약 ZIP 경로')
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('ZIP 심볼릭 링크 거부')
            top = top or path.parts[0]
            if path.parts[0] != top:
                raise ValueError('ZIP 최상위 폴더 불일치')
            total += info.file_size
            if total > MAX_EXPANDED:
                raise ValueError('ZIP 압축 해제 용량 초과')
            if info.is_dir() or len(path.parts) < 2:
                continue
            relative = Path(*path.parts[1:])
            if relative.parts[0] not in DIRECTORIES and relative.as_posix() not in LAUNCHERS:
                continue
            # Match Windows case-insensitive paths on every test platform.
            key = relative.as_posix().casefold()
            if key in names:
                raise ValueError('ZIP 중복 경로')
            names.add(key)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)


def validate(staging):
    for name in ('bootstrap.py', 'server.py', 'schema.py', 'brand_catalog.py'):
        if not (staging / 'app' / name).is_file():
            raise ValueError('앱 필수 파일 누락: ' + name)
    definitions = list((staging / 'brands').glob('*.json'))
    if not definitions:
        raise ValueError('브랜드 정의 없음')
    ids = set()
    for path in definitions:
        meta = json.loads(path.read_text(encoding='utf-8-sig'))
        brand_id, script = meta.get('id'), meta.get('script')
        if not isinstance(brand_id, str) or brand_id != path.stem or brand_id in ids:
            raise ValueError('브랜드 ID 불일치: ' + path.name)
        ids.add(brand_id)
        if not isinstance(script, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.py', script):
            raise ValueError('브랜드 스크립트 경로 오류: ' + path.name)
        body = (staging / 'brands' / script).read_bytes()
        if meta.get('code_hash') and hashlib.sha256(body).hexdigest() != meta['code_hash']:
            raise ValueError('브랜드 코드 해시 불일치: ' + brand_id)
    # Parse/compile only: do not import or execute downloaded Python code.
    for folder in DIRECTORIES:
        for path in (staging / folder).rglob('*.py'):
            ast.parse(path.read_bytes(), filename=str(path))
    return len(definitions)


def ensure_closed():
    for port in (8765, 8766):
        with socket.socket() as connection:
            connection.settimeout(.3)
            if connection.connect_ex(('127.0.0.1', port)) == 0:
                raise ValueError('실행 중인 앱을 종료한 뒤 다시 업데이트하세요 (포트 ' + str(port) + ').')
    if os.name == 'nt':
        # Also covers servers on custom ports. Do not kill jobs or other apps.
        command = ("Get-CimInstance Win32_Process -ErrorAction Stop | "
                   "Where-Object { $_.Name -match '^(pythonw?|py)(\\.exe)?$' -and "
                   "$_.CommandLine -match '[\\\\/](server|bootstrap)\\.py' } | "
                   "Select-Object -ExpandProperty ProcessId")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 "$ErrorActionPreference='Stop';" + command],
                                capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('실행 중인 앱 확인 실패. 앱 종료 후 다시 실행하세요.')
        if result.stdout.strip():
            raise ValueError('실행 중인 Python 앱(server.py/bootstrap.py)을 종료한 뒤 업데이트하세요.')


def check_destination(root):
    if (root / '.git').exists():
        raise ValueError('Git 개발 폴더에서는 덮어쓰지 않습니다. git pull로 업데이트하세요.')
    if not (root / 'app' / 'bootstrap.py').is_file():
        raise ValueError('앱 설치 폴더를 확인하세요. BAT를 Crawler_Hub 폴더에서 실행하세요.')
    for folder in DIRECTORIES:
        target = root / folder
        if target.is_symlink() or (target.exists() and target.resolve().parent != root):
            raise ValueError('설치 폴더의 링크/외부 경로는 교체할 수 없습니다.')
        if hasattr(target, 'is_junction') and target.is_junction():
            raise ValueError('설치 폴더 junction은 교체할 수 없습니다.')
        if (target / 'jobs').exists() or (target / 'settings.json').exists():
            raise ValueError(folder + ' 안에 사용자 데이터가 있습니다. 자동 교체를 중단합니다.')
        configured = os.environ.get('CRAWLER_HUB_HOME')
        if configured and Path(configured).resolve().is_relative_to(target.resolve()):
            raise ValueError('사용자 데이터 경로가 소스 폴더 안에 있어 업데이트할 수 없습니다.')


@contextmanager
def update_lock(root):
    path = root / '.crawler-update.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('다른 업데이트가 실행 중입니다. 이전 실행이 종료됐는지 확인하세요.') from None
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def install(root, staging, sha):
    check_destination(root)
    ensure_closed()
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '_' + sha[:12]
    backup = root / '.crawler-update-backups' / name
    backup.mkdir(parents=True)
    targets = [Path(p) for p in DIRECTORIES if (staging / p).is_dir()]
    targets += [Path(p) for p in LAUNCHERS if (staging / p).is_file()]
    moved, installed = [], []
    try:
        for relative in targets:
            target, previous = root / relative, backup / relative
            if target.exists():
                previous.parent.mkdir(parents=True, exist_ok=True)
                target.rename(previous)
                moved.append(relative)
            (staging / relative).rename(target)
            installed.append(relative)
        state = root / '.crawler-update-state.json'
        temporary = root / '.crawler-update-state.tmp'
        temporary.write_text(json.dumps(dict(repository=REPO, branch=BRANCH, commit=sha,
                                            backup=str(backup)), indent=2), encoding='utf-8')
        temporary.replace(state)
    except Exception:
        # Roll back source only. User home, jobs, settings and venv are never touched.
        for relative in reversed(installed):
            target = root / relative
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        for relative in reversed(moved):
            (backup / relative).rename(root / relative)
        raise
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description='공개 GitHub develop 앱/브랜드 업데이트 (로그인 불필요)')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check-only', action='store_true', help='다운로드와 검증만 수행, 설치 파일 변경 없음')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if sys.version_info < (3, 10):
            raise ValueError('Python 3.10 이상이 필요합니다.')
        if not args.check_only:
            check_destination(root)
            ensure_closed()
        with update_lock(root), tempfile.TemporaryDirectory(prefix='.crawler-update-', dir=root) as temp:
            work = Path(temp)
            print('공개 develop 버전 확인 중...', flush=True)
            sha = latest_commit(work)
            print('다운로드: ' + sha, flush=True)
            archive = work / 'source.zip'
            download('https://codeload.github.com/' + REPO + '/zip/' + sha, archive)
            staging = work / 'source'
            staging.mkdir()
            unpack(archive, staging)
            count = validate(staging)
            print('앱 문법 및 브랜드 ' + str(count) + '개 등록/해시 검증 완료.', flush=True)
            if args.check_only:
                print('검사 완료. 설치 파일은 변경하지 않았습니다.')
                return 0
            backup = install(root, staging, sha)
            print('업데이트 완료: ' + sha[:12] + '\n백업: ' + str(backup))
            print('기존 작업·설정은 유지했습니다. 앱을 다시 실행하고 새 수집 작업을 만드세요.')
        return 0
    except Exception as exc:
        print('업데이트 실패: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
