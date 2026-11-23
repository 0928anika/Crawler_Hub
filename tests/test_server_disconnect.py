import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server

class Tests(unittest.TestCase):
    def handler(self):
        h = server.Handler.__new__(server.Handler)
        h.send_response = Mock()
        h.send_header = Mock()
        h.end_headers = Mock()
        h.wfile = Mock()
        return h
    def test_disconnect_during_headers_and_body(self):
        for method in ('end_headers', 'wfile'):
            for error in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                h = self.handler()
                target = h.end_headers if method == 'end_headers' else h.wfile.write
                target.side_effect = error('connection closed')
                h.send({'ok': True})
                self.assertTrue(h.close_connection)
                h.send_response.assert_called_once_with(200)
    def test_get_disconnect_does_not_send_second_error_response(self):
        h = self.handler()
        h.authorized = Mock(return_value=True)
        h.get = Mock(side_effect=ConnectionAbortedError())
        h.send = Mock()
        h.do_GET()
        h.send.assert_not_called()
        self.assertTrue(h.close_connection)
    def test_real_application_error_is_still_reported(self):
        h = self.handler()
        h.authorized = Mock(return_value=True)
        h.get = Mock(side_effect=ValueError('actual error'))
        h.send = Mock()
        h.do_GET()
        h.send.assert_called_once_with({'error': 'actual error'}, status=400)

if __name__ == '__main__': unittest.main()
