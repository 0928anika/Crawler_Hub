"""Migrate/check brands without dependencies; import using the installed hub runtime."""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from brand_catalog import definitions, definition_path, registry_hash, script_path, verify_published


def migrate(root):
    folder = root / 'brands'
    legacy = folder / 'brands.json'
    if not legacy.exists():
        verify_published(root)
        return 0
    items = verify_published(root)
    staged, installed = [], []
    backup = legacy.with_name('brands.json.migration-' + uuid.uuid4().hex + '.bak')
    try:
        for item in items:
            destination = definition_path(folder, item['id'])
            temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(item, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            staged.append((temporary, destination))
        os.replace(legacy, backup)
        for temporary, destination in staged:
            os.replace(temporary, destination)
            installed.append(destination)
        verify_published(root)
    except Exception:
        for destination in installed:
            destination.unlink(missing_ok=True)
        if backup.exists():
            os.replace(backup, legacy)
        raise
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    backup.unlink()
    return len(items)


def import_brand(root, metadata, script=None, replace=False):
    """Use exactly the same validation, conflict checks and backups as the UI."""
    os.environ['CRAWLER_HUB_SOURCE_ROOT'] = str(root / 'app')
    import admin
    meta = json.loads(metadata.read_text(encoding='utf-8-sig'))
    if not isinstance(meta, dict):
        raise ValueError('브랜드 정의는 JSON 객체여야 합니다.')
    definition_path(root / 'brands', meta.get('id'))
    folder = root / 'brands'
    current = next((b for b in definitions(folder) if b['id'] == meta.get('id')), None)
    if current and not replace:
        raise ValueError('기존 브랜드입니다. 교체하려면 --replace를 지정하세요.')
    source = script or script_path(metadata.parent, meta)
    if source.suffix != '.py' or not source.is_file():
        raise ValueError('가져올 Python 파일을 확인하세요.')
    existing = admin.read_brand(current['id']) if current else None
    return admin.save_brand(dict(metadata=meta, code=source.read_bytes().decode('utf-8-sig'),
        expected_hash=existing['hash'] if existing else '',
        registry_hash=existing['registry_hash'] if existing else registry_hash(folder)))


def main(argv=None):
    parser = argparse.ArgumentParser(description='브랜드별 JSON 전환, 등록 및 배포 전 검사')
    parser.add_argument('--root', type=Path, default=ROOT, help='허브 최상위 폴더')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate', help='brands.json을 브랜드별 JSON으로 분리')
    commands.add_parser('check', help='브랜드 등록 정보·파일·해시와 Python 문법 검사')
    importer = commands.add_parser('import', help='JSON과 Python을 검증 후 함께 등록')
    importer.add_argument('metadata', type=Path)
    importer.add_argument('--script', type=Path)
    importer.add_argument('--replace', action='store_true')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == 'migrate':
            print('분리한 브랜드:', migrate(root))
        elif args.command == 'check':
            items = verify_published(root)
            for path in (root / 'app').rglob('*.py'):
                compile(path.read_bytes(), str(path), 'exec')
            print('등록 정보·해시·문법 검사 통과:', len(items), '개 브랜드')
            print('실제 사이트 수집과 최종 엑셀 확인은 별도로 진행하세요.')
        else:
            result = import_brand(root, args.metadata.resolve(), args.script.resolve() if args.script else None, args.replace)
            print('브랜드 저장 완료:', result['path'])
            print('백업:', result['backup'])
        return 0
    except (ValueError, SyntaxError, OSError, ImportError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
