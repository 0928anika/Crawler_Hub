"""Crawler Hub 업데이트(Python 표준 라이브러리만): GitHub 공개 저장소 브랜치 ZIP을 받아 덮어씀.

update_from_github.ps1과 같은 동작이지만 PowerShell 스크립트 실행을 쓰지 않음.
회사 PC처럼 PowerShell 스크립트(-File)가 정책으로 막힌 곳에서도 돌아가도록 develop 업데이터와 같은
방식(Python urllib, Windows 시스템 프록시 설정 사용, git·로그인 불필요)으로 다운로드한다.

- 이 PC에만 있는 파일(작업 결과·설정 등)은 지우지 않음.
- 실행 중인 업데이트 BAT(Crawler_Hub_Update_Windows.bat)는 .new로 저장 → BAT가 다음 실행 때 교체.
- tools/removed_files.txt에 적힌, 저장소에서 지워진 파일은 _removed_backup/날짜 폴더로 옮김(삭제 안 함).
"""
import argparse
import hashlib
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

REPO = '0928anika/Crawler_Hub'
ALLOWED = ('codeload.github.com', 'github.com', 'api.github.com')
SELF_BAT = 'Crawler_Hub_Update_Windows.bat'
MAX_ARCHIVE = 150 * 1024 * 1024
MAX_EXPANDED = 600 * 1024 * 1024


def download(url, destination):
    request = urllib.request.Request(url, headers={'User-Agent': 'CrawlerHub-Updater'})
    deadline = time.monotonic() + 300
    with urllib.request.urlopen(request, timeout=60) as response:
        final = urlsplit(response.geturl())
        if final.scheme != 'https' or final.hostname not in ALLOWED:
            raise ValueError('GitHub 밖으로 이동한 다운로드 거부: ' + response.geturl())
        size = 0
        with open(destination, 'wb') as output:
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError('다운로드 시간 초과(5분)')
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise ValueError('다운로드 용량 한도 초과')
                output.write(chunk)
    return size


def safe_members(package):
    """(ZIP 항목, 최상위 폴더 아래 상대경로). 경로 탈출·링크·과대 압축 거부."""
    total, top, out = 0, None, []
    for info in package.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or '..' in path.parts or '\\' in info.filename or ':' in info.filename or not path.parts:
            raise ValueError('안전하지 않은 ZIP 경로: ' + info.filename)
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError('ZIP 심볼릭 링크 거부: ' + info.filename)
        top = top or path.parts[0]
        if path.parts[0] != top:
            raise ValueError('ZIP 최상위 폴더가 여러 개입니다.')
        total += info.file_size
        if total > MAX_EXPANDED:
            raise ValueError('ZIP 압축 해제 용량 초과')
        if info.is_dir() or len(path.parts) < 2:
            continue
        relative = PurePosixPath(*path.parts[1:])
        if relative.parts[0].startswith('.git'):
            continue
        out.append((info, relative))
    return out


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def update(target, branch, repo=REPO, fetch=download):
    target = Path(target).resolve()
    if not (target / 'app' / 'bootstrap.py').is_file():
        raise ValueError('Crawler Hub 폴더가 아닙니다(app\\bootstrap.py 없음): ' + str(target))
    with tempfile.TemporaryDirectory(prefix='crawler_hub_update_') as work:
        archive = Path(work) / 'hub.zip'
        fetch('https://codeload.github.com/' + repo + '/zip/refs/heads/' + branch, archive)
        with zipfile.ZipFile(archive) as package:
            members = safe_members(package)
            names = {m[1].as_posix() for m in members}
            if 'app/bootstrap.py' not in names:
                raise ValueError('받은 ZIP에 Crawler Hub 파일이 없습니다.')
            changed = 0
            for info, relative in members:
                data = package.read(info)
                dest = target.joinpath(*relative.parts)
                if dest.is_file() and sha256(dest.read_bytes()) == sha256(data):
                    continue
                if relative.as_posix() == SELF_BAT:
                    dest = dest.with_name(SELF_BAT + '.new')
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + '.updating')
                tmp.write_bytes(data)
                tmp.replace(dest)
                changed += 1
            removed_list = 'tools/removed_files.txt'
            removed = package.read(next(i for i, r in members if r.as_posix() == removed_list)).decode('utf-8-sig') \
                if removed_list in names else ''
    moved = 0
    backup = target / '_removed_backup' / datetime.now().strftime('%Y%m%d_%H%M%S')
    for line in removed.splitlines():
        rel = line.strip()
        if not rel or rel.startswith('#') or '..' in rel or ':' in rel:
            continue
        rel = PurePosixPath(rel.replace('\\', '/'))
        if rel.as_posix() in names:
            continue
        src = target.joinpath(*rel.parts)
        if src.is_file():
            dest = backup.joinpath(*rel.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            moved += 1
    return changed, moved


def main(argv=None):
    parser = argparse.ArgumentParser(description='GitHub 공개 브랜치 ZIP으로 Crawler Hub 업데이트(로그인 불필요)')
    parser.add_argument('--target', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--branch', default='Oberon')
    parser.add_argument('--repo', default=REPO)
    args = parser.parse_args(argv)
    try:
        print('최신 버전 내려받는 중... GitHub ' + args.branch + ' 브랜치', flush=True)
        changed, moved = update(args.target, args.branch, args.repo)
    except urllib.error.HTTPError as exc:
        print(f'업데이트 실패: GitHub 응답 HTTP {exc.code} ({exc.url})')
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, 'reason', exc)
        print('업데이트 실패: GitHub 연결 안 됨 - ' + str(reason))
        print('  회사망이 github.com / codeload.github.com 을 막았거나 프록시 설정 문제일 수 있습니다.')
        return 1
    except (ValueError, zipfile.BadZipFile) as exc:
        print('업데이트 실패: ' + str(exc))
        return 1
    print(f'바뀐 파일 {changed}개 반영, 지워진 파일 {moved}개 백업 폴더로 이동 (브랜치: {args.branch})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
