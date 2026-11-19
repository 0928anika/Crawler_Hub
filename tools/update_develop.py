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
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

REPO = '0928anika/Crawler_Hub'
BRANCH = 'develop'
UPDATER_VERSION = '2026-11-19-admin-only-v4'
DIRECTORIES = ('app', 'brands')
LAUNCHERS = ('Crawler_Hub_Windows.bat', 'Crawler_Hub_Mac.command')
UPDATER_FILES = ('Update.bat', 'tools/update_develop.py')
BACKUP_KEEP = 5
MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXPANDED = 400 * 1024 * 1024


def admin_dir(root):
    """Use the active admin folder; legacy root/tools locations fall back to admin."""
    root = root.resolve()
    configured = os.environ.get('CRAWLER_HUB_UPDATER_DIR')
    active = Path(configured).resolve() if configured else root / 'admin'
    if active == root or active.is_relative_to(root / 'tools'):
        active = root / 'admin'
    if not active.is_relative_to(root) or any(active.is_relative_to(root / d) for d in DIRECTORIES):
        raise ValueError('업데이트 관리 폴더는 설치 폴더 아래 app/brands 밖에 두세요.')
    for path in (active, *active.parents):
        if path == root:
            break
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('업데이트 관리 폴더 링크는 사용할 수 없습니다.')
    return active


def find_install_root(start):
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        if (folder / 'app/bootstrap.py').is_file() and (folder / 'brands').is_dir():
            return folder
    raise ValueError('상위 폴더에서 Crawler_Hub 설치 위치를 찾지 못했습니다. --root로 지정하세요.')


def source_fingerprint(root):
    """Skip an unchanged commit only if installed managed files are unchanged too."""
    files = []
    for name in DIRECTORIES:
        files.extend(p for p in (root / name).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'))
    files.extend(root / name for name in LAUNCHERS if (root / name).is_file())
    admin = admin_dir(root)
    files.extend(admin / name for name in ('Update.bat', 'update_develop.py')
                 if (admin / name).is_file() or (name == 'Update.bat' and (admin / 'Update.bat.new').is_file()))
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        pending = admin / 'Update.bat.new' if path == admin / 'Update.bat' else path
        digest.update(hashlib.sha256((pending if pending.is_file() else path).read_bytes()).digest())
    return digest.hexdigest()


def already_current(root, sha):
    try:
        state = json.loads((admin_dir(root) / '.crawler-update-state.json').read_text(encoding='utf-8'))
        return (state.get('repository') == REPO and state.get('branch') == BRANCH
                and state.get('commit') == sha and state.get('fingerprint') == source_fingerprint(root))
    except (OSError, ValueError):
        return False


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
            if relative.parts[0] not in DIRECTORIES and relative.as_posix() not in LAUNCHERS + UPDATER_FILES:
                continue
            if relative.as_posix() in UPDATER_FILES:
                relative = Path('admin') / ('Update.bat' if relative.as_posix() == 'Update.bat' else 'update_develop.py')
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
    if (staging / 'admin/Update.bat').is_file() != (staging / 'admin/update_develop.py').is_file():
        raise ValueError('업데이트 BAT/Python 쌍 누락')
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
    if (staging / 'admin/update_develop.py').is_file():
        ast.parse((staging / 'admin/update_develop.py').read_bytes())
    for folder in DIRECTORIES:
        for path in (staging / folder).rglob('*.py'):
            ast.parse(path.read_bytes(), filename=str(path))
    return len(definitions)


def crawler_process(command, root):
    """Recognize our installation or immutable CrawlerHub release, not any server.py."""
    home = Path(os.environ.get('CRAWLER_HUB_HOME') or
                Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'CrawlerHub').resolve()
    for match in re.finditer(r'(?:^|\s)(?:"([^"\r\n]+\.py)"|([^\s"]+\.py))(?=\s|$)', command or '', re.I):
        value = match[1] or match[2]
        path = Path(value)
        if not path.is_absolute() or path.name.lower() not in ('server.py', 'bootstrap.py'):
            continue
        resolved = path.resolve()
        if resolved in ((root / 'app/server.py').resolve(), (root / 'app/bootstrap.py').resolve()):
            return True
        releases = home / 'releases'
        if (resolved.is_relative_to(releases) and resolved.parent.name == 'app'
                and (resolved.parent / 'brand_catalog.py').is_file()):
            return True
    return False


def ensure_closed(root=None):
    root = Path(root or '.').resolve()
    # Identify the app before blocking; another service may use these ports.
    for port in (8765, 8766):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:' + str(port) + '/', timeout=.5) as response:
                body = response.read(65536).decode('utf-8', errors='replace')
            if re.search(r'<title[^>]*>[^<]*Crawler (?:Hub|Portal)', body, re.I):
                raise ValueError('실행 중인 Crawler Hub를 종료한 뒤 업데이트하세요 (포트 ' + str(port) + ').')
        except (OSError, urllib.error.URLError):
            pass
    if os.name == 'nt':
        command = ("Get-CimInstance Win32_Process -ErrorAction Stop | "
                   "Where-Object { $_.Name -match '^(pythonw?|py)(\\.exe)?$' } | "
                   "Select-Object CommandLine | ConvertTo-Json -Compress")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 "$ErrorActionPreference='Stop';[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);" + command],
                                capture_output=True, timeout=20, encoding='utf-8', errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('실행 중인 앱 확인 실패. 앱 종료 후 다시 실행하세요.')
        rows = json.loads(result.stdout) if result.stdout.strip() else []
        if isinstance(rows, dict):
            rows = [rows]
        if any(crawler_process(row.get('CommandLine'), root) for row in rows or []):
            raise ValueError('실행 중인 Crawler Hub 콘솔을 종료한 뒤 업데이트하세요.')


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
    # OS releases this lock even after a crash. Keep the file to avoid unlink races.
    folder = admin_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / '.crawler-update.lock'
    if path.is_symlink():
        raise ValueError('업데이트 잠금 파일이 링크입니다.')
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('다른 업데이트가 실행 중입니다.') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def change_summary(root, staging):
    old, new = {}, {}
    for base, files in ((root, old), (staging, new)):
        for folder in DIRECTORIES:
            for path in (base / folder).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
                    files[path.relative_to(base).as_posix()] = hashlib.sha256(path.read_bytes()).digest()
    def brands(base):
        result = {}
        for path in (base / 'brands').glob('*.json'):
            try:
                meta = json.loads(path.read_text(encoding='utf-8-sig'))
            except (ValueError, UnicodeError):
                meta = {}
            filename = str(meta.get('script') or '')
            script = base / 'brands' / filename if re.fullmatch(r'[A-Za-z0-9_-]+\.py', filename) else None
            result[meta.get('id') or path.stem] = (path.read_bytes(), script.read_bytes() if script and script.is_file() else b'')
        return result
    old_brands, new_brands = brands(root), brands(staging)
    app_changed = sum(old.get(key) != new.get(key) for key in old.keys() | new.keys()
                      if key.startswith('app/'))
    return dict(app_changed=app_changed,
                brands_added=len(new_brands.keys() - old_brands.keys()),
                brands_removed=len(old_brands.keys() - new_brands.keys()),
                brands_changed=sum(old_brands[key] != new_brands[key]
                                   for key in old_brands.keys() & new_brands.keys()))



def prune_backups(root, keep=BACKUP_KEEP):
    admin = admin_dir(root)
    parent = admin / 'backups'
    if not parent.is_dir() or parent.is_symlink() or parent.resolve().parent != admin:
        return 0
    eligible = []
    for folder in parent.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.resolve().parent != parent.resolve():
            continue
        try:
            marker = json.loads((folder / 'backup.json').read_text(encoding='utf-8'))
            if marker.get('repository') == REPO and marker.get('complete') is True:
                eligible.append(folder)
        except (OSError, ValueError):
            continue
    removed = 0
    for folder in sorted(eligible, key=lambda p: p.name, reverse=True)[max(1, keep):]:
        # Only direct, recognized complete backups inside this installation.
        if folder.resolve().parent != parent.resolve():
            raise ValueError('백업 정리 경로 불일치')
        shutil.rmtree(folder);removed += 1
    return removed


def install(root, staging, sha):
    check_destination(root)
    ensure_closed(root)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '_' + sha[:12]
    active = admin_dir(root)
    backup = active / 'backups' / name
    parent = active / 'backups'
    if parent.is_symlink() or parent.resolve().parent != active:
        raise ValueError('백업 폴더 링크/외부 경로 거부')
    backup.mkdir(parents=True)
    targets = [Path(p) for p in DIRECTORIES if (staging / p).is_dir()]
    targets += [Path(p) for p in LAUNCHERS if (staging / p).is_file()]
    # Python has loaded its source already; BAT is deferred until its next run.
    relative = active.relative_to(root)
    for source_name, target_name in [('update_develop.py', 'update_develop.py'), ('Update.bat', 'Update.bat.new')]:
        source = staging / 'admin' / source_name
        if source.is_file():
            destination = relative / target_name
            staged = staging / destination
            staged.parent.mkdir(parents=True, exist_ok=True)
            if source != staged:
                source.rename(staged)
            targets.append(destination)
    if (active / 'Update.bat').is_file():
        previous = backup / relative / 'Update.bat'
        previous.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(active / 'Update.bat', previous)
    for relative in targets:
        target = root / relative
        if target.is_symlink() or target.parent.resolve() != (root / relative.parent).absolute():
            raise ValueError('교체 대상 링크/외부 경로 거부: ' + str(relative))
    moved, installed = [], []
    state = active / '.crawler-update-state.json'
    previous_state = state.read_bytes() if state.is_file() else None
    try:
        for relative in targets:
            target, previous = root / relative, backup / relative
            if target.exists():
                previous.parent.mkdir(parents=True, exist_ok=True)
                target.rename(previous)
                moved.append(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            (staging / relative).rename(target)
            installed.append(relative)
        temporary = active / '.crawler-update-state.tmp'
        temporary.write_text(json.dumps(dict(repository=REPO, branch=BRANCH, commit=sha,
                                            backup=str(backup), fingerprint=source_fingerprint(root)),
                                        indent=2), encoding='utf-8')
        temporary.replace(state)
        (backup / 'backup.json').write_text(json.dumps(dict(repository=REPO, complete=True,
            commit=sha)), encoding='utf-8')
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
        (backup / 'backup.json').unlink(missing_ok=True)
        if previous_state is None:
            state.unlink(missing_ok=True)
        else:
            state.write_bytes(previous_state)
        raise
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description='공개 GitHub develop 앱/브랜드 업데이트 (로그인 불필요)')
    parser.add_argument('--root', type=Path, help='생략하면 보조 Python 파일의 상위에서 앱 폴더 자동 탐색')
    parser.add_argument('--no-launch', action='store_true', help='BAT 업데이트 후 앱 자동 실행 생략')
    parser.add_argument('--check-only', action='store_true', help='다운로드와 검증만 수행, 설치 파일 변경 없음')
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve() if args.root else find_install_root(Path(__file__).resolve().parent)
        if sys.version_info < (3, 10):
            raise ValueError('Python 3.10 이상이 필요합니다.')
        if not args.check_only:
            check_destination(root)
            updater_folder = Path(os.environ.get('CRAWLER_HUB_UPDATER_DIR') or __file__).resolve()
            if any(updater_folder.is_relative_to(root / name) for name in DIRECTORIES):
                raise ValueError('업데이트 프로그램은 교체 대상인 app/brands 밖의 폴더에 두세요.')
        with update_lock(root), tempfile.TemporaryDirectory(prefix='.crawler-update-', dir=admin_dir(root)) as temp:
            work = Path(temp)
            print('최신 버전 확인 중...', flush=True)
            sha = latest_commit(work)
            if not args.check_only and already_current(root, sha):
                print('최신 버전입니다.', flush=True)
                return 0
            print('다운로드 중...', flush=True)
            archive = work / 'source.zip'
            download('https://codeload.github.com/' + REPO + '/zip/' + sha, archive)
            staging = work / 'source'
            staging.mkdir()
            unpack(archive, staging)
            validate(staging)
            if args.check_only:
                print('파일 검사 완료. 업데이트하지 않았습니다.', flush=True)
                return 0
            print('파일 검사 완료', flush=True)
            summary = change_summary(root, staging)
            backup = install(root, staging, sha)
            print('업데이트 완료 · 앱 변경 {app_changed}개 / 브랜드 추가 {brands_added}개 / 브랜드 수정 {brands_changed}개 / 브랜드 제거 {brands_removed}개'.format(**summary), flush=True)
            print('백업: ' + str(backup), flush=True)
            try:
                removed = prune_backups(root)
                print('완료된 백업은 최근 ' + str(BACKUP_KEEP) + '개 보관합니다. 정리 ' + str(removed) + '개', flush=True)
            except (OSError, ValueError) as exc:
                print('업데이트는 완료됐지만 이전 백업 정리는 건너뛰었습니다: ' + str(exc), flush=True)
        return 0
    except Exception as exc:
        print('업데이트 실패: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
