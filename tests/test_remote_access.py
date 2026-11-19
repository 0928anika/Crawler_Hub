"""휴대폰 원격 접속(--remote) 검사. 휴대폰 요청은 Host 헤더를 PC 주소가 아닌 값으로 보내 흉내 낸다."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
import server  # noqa: E402
import remote_access  # noqa: E402


class RemoteAccessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.remote = remote_access.RemoteAccess(Path(self.folder.name))
        self.patches = [patch.object(server, 'REMOTE', self.remote),
                        patch.object(server, 'brands', side_effect=lambda: []),
                        patch.object(server, 'save_settings', side_effect=lambda data: dict(ok=True))]
        for p in self.patches:
            p.start()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.http.server_port

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(2)
        for p in reversed(self.patches):
            p.stop()
        self.folder.cleanup()

    def call(self, path, method='GET', body=None, headers=None, phone=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        head = {'Host': ('192.168.0.10:%d' if phone else '127.0.0.1:%d') % self.port}
        head.update(headers or {})
        try:
            connection.request(method, path, body=body, headers=head)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def login(self, pin):
        return self.call('/login', 'POST', urlencode({'pin': pin}).encode(),
                         {'Content-Type': 'application/x-www-form-urlencoded'})

    def cookie(self):
        status, headers, _ = self.login(self.remote.pin)
        self.assertEqual(status, 303)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';')[0]

    def test_pc_itself_still_opens_without_pin(self):
        status, _, body = self.call('/', phone=False)
        self.assertEqual(status, 200)
        self.assertIn(server.TOKEN.encode(), body)

    def test_phone_needs_pin_and_never_sees_token_before_login(self):
        status, headers, body = self.call('/')
        self.assertEqual((status, headers.get('Location')), (303, '/login'))
        self.assertNotIn(server.TOKEN.encode(), body)
        status, _, _ = self.call('/api/brands')
        self.assertEqual(status, 403)
        status, _, body = self.call('/login')
        self.assertEqual(status, 200)
        self.assertIn('PIN'.encode(), body)
        status, _, _ = self.call('/manifest.webmanifest')
        self.assertEqual(status, 200)
        status, headers, _ = self.call('/icon-192.png')
        self.assertEqual((status, headers['Content-Type']), (200, 'image/png'))

    def test_correct_pin_gives_session_and_full_page(self):
        jar = self.cookie()
        status, _, body = self.call('/', headers={'Cookie': jar})
        self.assertEqual(status, 200)
        self.assertIn(server.TOKEN.encode(), body)
        self.assertIn(b'manifest.webmanifest', body)
        status, _, body = self.call('/api/brands', headers={'Cookie': jar})
        self.assertEqual(status, 200)
        # 세션은 재시작 후에도 유지(파일에는 토큰 해시만 저장)
        again = remote_access.RemoteAccess(Path(self.folder.name))
        self.assertTrue(again.session_ok(jar))
        saved = (Path(self.folder.name) / 'remote.json').read_text(encoding='utf-8')
        self.assertNotIn(jar.split('=', 1)[1], saved)

    def test_post_needs_session_and_token(self):
        jar = self.cookie()
        body = json.dumps({'selected': []}).encode()
        status, _, _ = self.call('/api/settings', 'POST', body, {'Content-Type': 'application/json', 'X-Portal-Token': server.TOKEN})
        self.assertEqual(status, 403)                         # 쿠키 없음
        status, _, _ = self.call('/api/settings', 'POST', body, {'Content-Type': 'application/json', 'Cookie': jar})
        self.assertEqual(status, 403)                         # 토큰 없음
        status, _, _ = self.call('/api/settings', 'POST', body, {'Content-Type': 'application/json', 'Cookie': jar, 'X-Portal-Token': server.TOKEN})
        self.assertEqual(status, 200)

    def test_phone_cannot_manage_brand_code_or_open_pc_folders(self):
        jar = self.cookie()
        status, _, _ = self.call('/api/admin', headers={'Cookie': jar})
        self.assertEqual(status, 403)
        head = {'Content-Type': 'application/json', 'Cookie': jar, 'X-Portal-Token': server.TOKEN}
        status, _, body = self.call('/api/admin/save', 'POST', b'{}', head)
        self.assertEqual(status, 403)
        self.assertIn('휴대폰'.encode(), body)
        status, _, _ = self.call('/api/jobs/' + 'a' * 32 + '/open-folder', 'POST', b'{}', head)
        self.assertEqual(status, 403)

    def test_wrong_pin_locks_after_five_tries(self):
        wrong = '000000' if self.remote.pin != '000000' else '111111'
        for _ in range(5):
            status, _, _ = self.login(wrong)
            self.assertEqual(status, 401)
        status, _, body = self.login(self.remote.pin)          # 잠긴 동안에는 맞는 PIN도 거절
        self.assertEqual(status, 429)
        self.assertIn('잠겼'.encode(), body)

    def test_spoofed_host_from_other_device_is_not_local(self):
        self.assertTrue(remote_access.is_local_request('127.0.0.1', '127.0.0.1:8765', 8765))
        self.assertFalse(remote_access.is_local_request('192.168.0.23', '127.0.0.1:8765', 8765))
        self.assertFalse(remote_access.is_local_request('127.0.0.1', 'evil.example:8765', 8765))

    def test_reset_pin_logs_out_phones(self):
        jar = self.cookie()
        fresh = remote_access.RemoteAccess(Path(self.folder.name), reset=True)
        self.assertFalse(fresh.session_ok(jar))
        self.assertRegex(fresh.pin, r'^\d{6}$')


class DefaultModeTests(unittest.TestCase):
    def test_without_remote_flag_other_hosts_are_refused(self):
        with patch.object(server, 'REMOTE', None):
            http_server = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', http_server.server_port, timeout=5)
                connection.request('GET', '/', headers={'Host': 'phone.lan:%d' % http_server.server_port})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
                connection = http.client.HTTPConnection('127.0.0.1', http_server.server_port, timeout=5)
                connection.request('POST', '/login', body=b'pin=123456', headers={'Host': 'phone.lan:%d' % http_server.server_port})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
            finally:
                http_server.shutdown()
                http_server.server_close()
                thread.join(2)


if __name__ == '__main__':
    unittest.main()
