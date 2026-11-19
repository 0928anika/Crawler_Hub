"""휴대폰 원격 접속(선택 기능). 기본 실행은 그대로 PC 자신(127.0.0.1)에서만 열린다.

Crawler_Hub_Remote_Windows.bat(=server.py --remote)로 켰을 때만 같은 와이파이·Tailscale 등에서 접속을 받는다.
- PC 자신에서 오는 요청: 기존과 같이 통과(접속 주소와 Host가 모두 127.0.0.1/localhost일 때만).
- 다른 기기: PIN 로그인 후 받은 세션 쿠키(HttpOnly, SameSite=Strict)가 있어야 한다.
- PIN 연속 실패는 기기별 5회, 전체 20회에서 5분간 잠금.
- 원격 세션에서는 브랜드 관리(코드 등록)와 PC 폴더 열기를 막는다.
PIN과 세션(해시만)은 사용자 폴더의 remote.json에 저장한다.
"""
import hashlib
import json
import secrets
import socket
import threading
import time
from html import escape

LOCAL_ADDRESSES = ('127.0.0.1', '::1', '::ffff:127.0.0.1')
COOKIE = 'hub_session'
SESSION_DAYS = 30
LOCK_SECONDS = 300
PER_CLIENT_LIMIT = 5
GLOBAL_LIMIT = 20


class RemoteAccess:
    def __init__(self, home, reset=False):
        self.file = home / 'remote.json'
        self.lock = threading.Lock()
        self.fails = {}
        self.global_fails = []
        data = {}
        if self.file.is_file() and not reset:
            try:
                data = json.loads(self.file.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                data = {}
        self.pin = data.get('pin') if str(data.get('pin', '')).isdigit() and len(str(data.get('pin'))) == 6 else None
        self.sessions = {k: v for k, v in (data.get('sessions') or {}).items() if v > time.time()}
        if not self.pin:
            self.pin = ''.join(secrets.choice('0123456789') for _ in range(6))
            self.sessions = {}
        self.save()

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(dict(pin=self.pin, sessions=self.sessions)), encoding='utf-8')

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def session_ok(self, cookie_header):
        for part in (cookie_header or '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == COOKIE and value:
                with self.lock:
                    expires = self.sessions.get(self.digest(value))
                    return bool(expires and expires > time.time())
        return False

    def locked(self, client):
        now = time.time()
        with self.lock:
            self.global_fails = [t for t in self.global_fails if now - t < LOCK_SECONDS]
            mine = [t for t in self.fails.get(client, []) if now - t < LOCK_SECONDS]
            self.fails[client] = mine
            return len(mine) >= PER_CLIENT_LIMIT or len(self.global_fails) >= GLOBAL_LIMIT

    def login(self, client, pin):
        """맞으면 새 세션 토큰(쿠키 값), 틀리면 None. 잠금 중이면 ValueError."""
        if self.locked(client):
            raise ValueError('PIN을 여러 번 틀려 5분간 잠겼습니다. 잠시 후 다시 시도하세요.')
        if secrets.compare_digest(str(pin or '').strip(), self.pin):
            token = secrets.token_urlsafe(32)
            with self.lock:
                self.sessions[self.digest(token)] = time.time() + SESSION_DAYS * 86400
                self.fails.pop(client, None)
                self.save()
            return token
        with self.lock:
            now = time.time()
            self.fails.setdefault(client, []).append(now)
            self.global_fails.append(now)
        return None

    def logout(self, cookie_header):
        for part in (cookie_header or '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == COOKIE and value:
                with self.lock:
                    self.sessions.pop(self.digest(value), None)
                    self.save()

    @staticmethod
    def cookie(token):
        return f'{COOKIE}={token}; Max-Age={SESSION_DAYS * 86400}; Path=/; HttpOnly; SameSite=Strict'


def is_local_request(client_address, host_header, port):
    """PC 자신에서 온 요청인지: 실제 접속 주소와 Host 헤더가 모두 로컬일 때만(원격 기기가 Host를 꾸며도 통과 못 함)."""
    return client_address in LOCAL_ADDRESSES and host_header in (f'127.0.0.1:{port}', f'localhost:{port}')


def lan_addresses():
    """휴대폰에서 칠 주소 후보(IPv4). 사설망·Tailscale(100.x) 주소 위주."""
    found = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(('10.255.255.255', 1))  # 패킷은 보내지 않고 기본 경로의 내 주소만 확인
            found.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip.startswith('127.') or ip in out:
            continue
        out.append(ip)
    return out


def login_page(message=''):
    note = f'<p class="err">{escape(message)}</p>' if message else ''
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crawler Hub 로그인</title><link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#346aff">
<link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/icon-192.png">
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f8fa;font:15px/1.5 'Malgun Gothic',Arial,sans-serif;color:#17243b}}
form{{background:#fff;border:1px solid #e6eaf0;border-radius:14px;padding:28px 24px;width:min(86vw,340px);display:grid;gap:14px}}
h1{{font-size:20px;margin:0}}p{{margin:0;color:#7c8799;font-size:13px}}.err{{color:#c34c4c}}
input{{font-size:24px;letter-spacing:8px;text-align:center;padding:12px;border:1px solid #dfe5ed;border-radius:9px}}
button{{font-size:16px;padding:13px;border:0;border-radius:9px;background:#346aff;color:#fff;font-weight:700}}</style></head>
<body><form method="post" action="/login"><h1>Crawler Hub</h1><p>PC의 Crawler Hub 창에 표시된 6자리 PIN을 입력하세요.</p>{note}
<input name="pin" inputmode="numeric" pattern="[0-9]*" maxlength="6" autocomplete="one-time-code" autofocus required>
<button type="submit">접속</button></form></body></html>'''.encode('utf-8')


MANIFEST = json.dumps({
    'name': 'Crawler Hub', 'short_name': 'Crawler Hub', 'start_url': '/', 'scope': '/', 'display': 'standalone',
    'background_color': '#f7f8fa', 'theme_color': '#346aff', 'lang': 'ko',
    'icons': [{'src': '/icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
              {'src': '/icon-512.png', 'sizes': '512x512', 'type': 'image/png'}],
}, ensure_ascii=False).encode('utf-8')
