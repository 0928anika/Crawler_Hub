from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import local_paths


class PlatformTests(unittest.TestCase):
    def test_mac_uses_open_as_argument_list(self):
        path = ROOT/'example folder'/'result.xlsx'
        with patch.object(local_paths.sys,'platform','darwin'),patch.object(local_paths.subprocess,'Popen') as opened:
            local_paths.open_local_path(path)
        opened.assert_called_once_with(['open',str(path)])

    @unittest.skipUnless(sys.platform=='win32','Windows file opener')
    def test_windows_keeps_native_file_opening(self):
        path = ROOT/'example folder'
        with patch.object(local_paths.sys,'platform','win32'),patch.object(local_paths.os,'startfile') as opened:
            local_paths.open_local_path(path)
        opened.assert_called_once_with(str(path))

    def test_other_platforms_report_supported_operating_systems(self):
        with patch.object(local_paths.sys,'platform','linux'):
            with self.assertRaisesRegex(ValueError,'macOS'):
                local_paths.open_local_path(ROOT)

    def test_mac_launcher_is_lf_without_bom_and_forwards_arguments(self):
        content = (ROOT/'Crawler_Hub_Mac.command').read_bytes()
        self.assertTrue(content.startswith(b'#!/bin/bash\n'))
        self.assertNotIn(b'\r',content)
        self.assertIn(b'bootstrap.py "$@"',content)
        self.assertIn(b'sys.version_info >= (3, 10)',content)
        self.assertTrue((ROOT/'Crawler_Hub_Windows.bat').is_file())
        self.assertFalse((ROOT/'Crawler_Hub.bat').exists())


if __name__=='__main__':
    unittest.main()
