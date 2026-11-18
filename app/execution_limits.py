"""Admission rules for isolated crawler jobs; no crawler code is executed here."""
import ast
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

MAX_JOBS=4
ACTIVE_STATES=('running','pausing','paused','stopping','saving')
PLATFORMS={
    'elandmall.co.kr':'elandmall.co.kr','elandmall.com':'elandmall.co.kr','elandrs.com':'elandmall.co.kr',
    'fashionplus.co.kr':'fashionplus.co.kr',
    'naver.com':'naver-shopping','pstatic.net':'naver-shopping',
    'goodwearmall.com':'goodwearmall.com','style24.com':'style24.com',
    'lotteon.com':'lotteon.com','halfclub.com':'halfclub.com','boribori.co.kr':'boribori.co.kr',
}
IGNORED={'coupang.com','example.com','example.org','example.net','w3.org','schema.org','localhost'}


def site_key(host):
    host=host.lower().rstrip('.')
    if '.' not in host or host in IGNORED or re.fullmatch(r'[\d.]+',host):return None
    for suffix in IGNORED:
        if host==suffix or host.endswith('.'+suffix):return None
    for suffix,key in PLATFORMS.items():
        if host==suffix or host.endswith('.'+suffix):return key
    # Cafe24 shops are independent sites, not one shared cafe24.com shop.
    if host.endswith('.cafe24.com'):return host.removeprefix('www.')
    parts=host.split('.')
    suffix='.'.join(parts[-2:])
    return '.'.join(parts[-3:] if suffix in ('co.kr','or.kr','go.kr','ne.kr','co.jp','co.uk','com.au') else parts[-2:])


@lru_cache(maxsize=256)
def _profile(filename,modified,size):
    try:
        source=Path(filename).read_text('utf-8-sig');tree=ast.parse(source)
    except (OSError,ValueError,SyntaxError):return dict(sites=[],browser=True,known=False)
    hosts=set();browser=False
    for node in ast.walk(tree):
        if isinstance(node,(ast.Import,ast.ImportFrom)):
            modules=[node.module or ''] if isinstance(node,ast.ImportFrom) else [n.name for n in node.names]
            if any(re.match(r'playwright|selenium|pyppeteer|DrissionPage',m,re.I) for m in modules):browser=True
        if isinstance(node,ast.Constant) and isinstance(node.value,str):
            hosts.update(re.findall(r'https?://([a-zA-Z0-9.-]+)',node.value))
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and re.search(r'(?:HOSTS?|DOMAINS?)$',t.id) for t in node.targets):
            for value in ast.walk(node.value):
                if isinstance(value,ast.Constant) and isinstance(value.value,str) and re.fullmatch(r'[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',value.value):hosts.add(value.value)
    if re.search(r'\.(?:chromium|firefox|webkit|launch_persistent_context)\b|webdriver\.|(?:chrome|msedge)\.exe',source):browser=True
    sites=sorted({key for h in hosts if (key:=site_key(h))})
    return dict(sites=sites,browser=browser,known=bool(sites))


def resource_profile(path):
    try:
        path=Path(path).resolve();stat=path.stat()
        profile=_profile(str(path),stat.st_mtime_ns,stat.st_size)
        return dict(profile,sites=list(profile['sites']))
    except OSError:return dict(sites=[],browser=True,known=False)


def blocked_reason(resources,active,exclude_id=None):
    """active entries contain id/name/resources. Paused jobs retain reservations."""
    others=[job for job in active if job['id']!=exclude_id]
    if len(others)>=MAX_JOBS:return '최대 4개 작업이 실행 중입니다. 한 작업이 끝난 뒤 시작하세요.'
    for job in others:
        used=job['resources'];name=job.get('name') or '다른 작업'
        if not resources.get('known') or not used.get('known'):
            return '사이트·크롬 사용 정보를 확인할 수 없어 단독 실행이 필요합니다: '+name
        if set(resources['sites'])&set(used['sites']):return '같은 사이트 사용 중: '+name
        if resources['browser'] and used['browser']:return '크롬·브라우저 사용 중: '+name
    return ''
