"""Oberon Python 업데이터(tools/update_from_github.py) 오프라인 검사."""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import update_from_github as updater  # noqa: E402


def archive(entries):
    def fetch(url, dest):
        assert url == 'https://codeload.github.com/0928anika/Crawler_Hub/zip/refs/heads/Oberon', url
        with zipfile.ZipFile(dest, 'w') as package:
            for name, data in entries.items():
                package.writestr('Crawler_Hub-Oberon/' + name, data)
    return fetch


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'app').mkdir()
        (self.root / 'app/bootstrap.py').write_text('old')
        (self.root / 'brands').mkdir()
        (self.root / 'brands/gone.py').write_text('x')
        (self.root / 'mine.txt').write_text('keep')
        (self.root / updater.SELF_BAT).write_text('old bat')

    def tearDown(self):
        self.temp.cleanup()

    def test_overwrites_changed_keeps_local_and_defers_running_bat(self):
        fetch = archive({'app/bootstrap.py': 'new', 'brands/a.py': 'a', updater.SELF_BAT: 'new bat',
                         'tools/removed_files.txt': '# note\nbrands/gone.py\n../evil.py\n', '.gitattributes': 'x'})
        changed, moved = updater.update(self.root, 'Oberon', fetch=fetch)
        self.assertEqual((changed, moved), (4, 1))
        self.assertEqual((self.root / 'app/bootstrap.py').read_text(), 'new')
        self.assertEqual((self.root / 'mine.txt').read_text(), 'keep')
        self.assertEqual((self.root / updater.SELF_BAT).read_text(), 'old bat')
        self.assertEqual((self.root / (updater.SELF_BAT + '.new')).read_text(), 'new bat')
        self.assertFalse((self.root / 'brands/gone.py').exists())
        self.assertEqual(len(list((self.root / '_removed_backup').rglob('gone.py'))), 1)
        self.assertFalse((self.root / '.gitattributes').exists())

    def test_rejects_unsafe_or_foreign_archives(self):
        with self.assertRaises(ValueError):
            updater.update(self.root, 'Oberon', fetch=archive({'../evil.py': 'x', 'app/bootstrap.py': 'n'}))
        with self.assertRaises(ValueError):
            updater.update(self.root, 'Oberon', fetch=archive({'readme.txt': 'x'}))
        self.assertEqual((self.root / 'app/bootstrap.py').read_text(), 'old')

    def test_refuses_non_hub_folder(self):
        with tempfile.TemporaryDirectory() as other, self.assertRaises(ValueError):
            updater.update(other, 'Oberon', fetch=archive({'app/bootstrap.py': 'n'}))


if __name__ == '__main__':
    unittest.main()
