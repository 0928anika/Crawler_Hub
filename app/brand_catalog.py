"""Dependency-free brand definitions shared by the launcher, UI and CLI."""
import ast
import hashlib
import json
import re
from pathlib import Path, PureWindowsPath


def definition_path(folder, key):
    if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{1,39}', key):
        raise ValueError('브랜드 ID는 소문자 영문으로 시작하는 영문·숫자·밑줄 2~40자입니다.')
    if key == 'brands':
        raise ValueError('brands는 등록 목록에 예약된 ID입니다.')
    return Path(folder) / (key + '.json')


def script_path(folder, definition):
    name = definition.get('script')
    if not isinstance(name, str) or not name or '/' in name or '\\' in name or PureWindowsPath(name).drive:
        raise ValueError('브랜드 코드는 brands 폴더의 Python 파일명이어야 합니다.')
    folder = Path(folder).resolve()
    path = (folder / name).resolve()
    if path.parent != folder or path.suffix != '.py':
        raise ValueError('브랜드 코드는 brands 폴더의 Python 파일이어야 합니다.')
    return path


def definitions(folder):
    folder = Path(folder)
    legacy = folder / 'brands.json'
    files = sorted(p for p in folder.glob('*.json') if p.name != 'brands.json')
    if legacy.exists() and files:
        raise ValueError('brands.json과 개별 브랜드 JSON이 함께 있습니다. 한 가지 등록 방식만 사용하세요.')
    if legacy.exists():
        document = json.loads(legacy.read_text(encoding='utf-8-sig'))
        result = document.get('brands') if isinstance(document, dict) else None
        if not isinstance(result, list):
            raise ValueError('brands.json에는 brands 배열이 필요합니다.')
    else:
        result = []
        for path in files:
            item = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(item, dict) or path.name != definition_path(folder, item.get('id')).name:
                raise ValueError(path.name + ': 파일명은 브랜드 ID와 같아야 합니다.')
            result.append(item)
    ids, scripts = set(), set()
    for item in result:
        if not isinstance(item, dict):
            raise ValueError('브랜드 정의는 JSON 객체여야 합니다.')
        key = item.get('id')
        definition_path(folder, key)
        script = script_path(folder, item).name.casefold()
        if key in ids or script in scripts:
            raise ValueError('브랜드 ID 또는 Python 파일 경로가 중복되었습니다: ' + key)
        ids.add(key)
        scripts.add(script)
        headers = item.get('output_headers')
        if not isinstance(headers, list) or not headers or not all(isinstance(h, str) and h for h in headers):
            raise ValueError(key + ': output_headers 목록이 필요합니다.')
    return result


def registry_hash(folder, key=None):
    """Existing-brand tokens cover only that JSON; new-brand tokens cover the catalog."""
    folder = Path(folder)
    if (folder / 'brands.json').exists():
        paths = [folder / 'brands.json']
    elif key is not None:
        paths = [definition_path(folder, key)]
    else:
        paths = sorted(folder.glob('*.json'))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes() if path.exists() else b'')
        digest.update(b'\0')
    return digest.hexdigest()


def check_script(code, filename):
    tree = ast.parse(code, filename=filename)
    compile(tree, filename, 'exec')
    versions = [node.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'PORTAL_API_VERSION' for t in node.targets)]
    if versions:
        if len(versions) != 1 or not isinstance(versions[0], ast.Constant) or versions[0].value not in (1, 2):
            raise ValueError(filename + ': 지원하지 않는 PORTAL_API_VERSION입니다.')
        if not any(isinstance(node, ast.FunctionDef) and node.name == 'crawl' and len(node.args.args) == 1 for node in tree.body):
            raise ValueError(filename + ': def crawl(ctx)가 필요합니다.')


def verify_published(root):
    folder = Path(root) / 'brands'
    result = definitions(folder)
    for item in result:
        path = script_path(folder, item)
        if not path.is_file():
            raise ValueError(item['id'] + ': Python 파일이 없습니다.')
        data = path.read_bytes()
        if item.get('code_hash') and hashlib.sha256(data).hexdigest() != item['code_hash']:
            raise ValueError(item['id'] + ': 코드 해시가 다릅니다. 등록 도구로 JSON과 Python을 함께 갱신하세요.')
        check_script(data, path.name)
    return result
