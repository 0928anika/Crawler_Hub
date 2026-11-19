import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('update_develop', ROOT/'tools/update_develop.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


def source(folder, value='new'):
    (folder/'app').mkdir(parents=True)
    for name in ('bootstrap.py', 'server.py', 'schema.py', 'brand_catalog.py'):
        (folder/'app'/name).write_text('VERSION = '+repr(value), encoding='utf-8')
    (folder/'brands').mkdir()
    body = b'PORTAL_API_VERSION=1\ndef crawl(ctx):pass\n'
    (folder/'brands/demo.py').write_bytes(body)
    (folder/'brands/demo.json').write_text(json.dumps(dict(id='demo',script='demo.py',
        code_hash=hashlib.sha256(body).hexdigest())), encoding='utf-8')
    return folder


class Tests(unittest.TestCase):
    def test_archive_only_installs_managed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);archive=p/'source.zip';stage=p/'stage';stage.mkdir()
            with zipfile.ZipFile(archive,'w') as z:
                for name in ['app/server.py','brands/demo.py','tools/update_develop.py',
                             'jobs/task.json','settings.json','.env','tests/test_a.py']:
                    z.writestr('Crawler_Hub-commit/'+name,'data')
            u.unpack(archive,stage)
            self.assertTrue((stage/'app/server.py').exists())
            self.assertFalse((stage/'jobs').exists());self.assertFalse((stage/'settings.json').exists())
            self.assertFalse((stage/'.env').exists());self.assertFalse((stage/'tests').exists())
            self.assertFalse((stage/'tools').exists())

    def test_unsafe_archive_paths_rejected(self):
        for name in ['repo/app/../../settings.json','/repo/app/a.py',
                     'repo/app/CON.py','repo/app/dir./a.py','repo/app/..\\..\\settings.json']:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                p=Path(temp);stage=p/'stage';stage.mkdir()
                with zipfile.ZipFile(p/'a.zip','w') as z:z.writestr(name,'bad')
                with self.assertRaises(ValueError):u.unpack(p/'a.zip',stage)

    def test_invalid_hash_and_syntax_never_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            p=source(Path(temp));self.assertEqual(u.validate(p),1)
            (p/'brands/demo.py').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'해시'):u.validate(p)
            meta=json.loads((p/'brands/demo.json').read_text());meta.pop('code_hash')
            (p/'brands/demo.json').write_text(json.dumps(meta))
            (p/'brands/demo.py').write_bytes(b'def invalid(')
            with self.assertRaises(SyntaxError):u.validate(p)

    def test_update_replaces_sources_preserves_data_and_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'installed','old');stage=source(p/'new')
            (root/'jobs/task').mkdir(parents=True);(root/'jobs/task/result.xlsx').write_bytes(b'old result')
            (root/'settings.json').write_bytes(b'personal settings')
            (root/'venv').mkdir();(root/'venv/marker').write_bytes(b'runtime')
            (root/'tools').mkdir();(root/'tools/update_develop.py').write_bytes(b'keep manual updater')
            (stage/'tools').mkdir();(stage/'tools/update_develop.py').write_bytes(b'must not replace')
            (root/'brands/removed.py').write_bytes(b'old crawler')
            with patch.object(u,'ensure_closed'):
                backup=u.install(root,stage,'a'*40)
            self.assertIn('new',(root/'app/server.py').read_text())
            self.assertEqual((root/'jobs/task/result.xlsx').read_bytes(),b'old result')
            self.assertEqual((root/'settings.json').read_bytes(),b'personal settings')
            self.assertEqual((root/'venv/marker').read_bytes(),b'runtime')
            self.assertEqual((root/'tools/update_develop.py').read_bytes(),b'keep manual updater')
            self.assertFalse((root/'brands/removed.py').exists())
            self.assertTrue((backup/'brands/removed.py').exists())
            self.assertIn('old',(backup/'app/server.py').read_text())

    def test_interrupted_install_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'installed','old');stage=source(p/'new')
            rename=Path.rename
            def fail(path,target):
                if path==stage/'brands':raise OSError('simulated locked file')
                return rename(path,target)
            with patch.object(u,'ensure_closed'),patch.object(Path,'rename',fail):
                with self.assertRaisesRegex(OSError,'simulated'):u.install(root,stage,'b'*40)
            self.assertIn('old',(root/'app/server.py').read_text())

    def test_arbitrary_nested_developer_folder_finds_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=source(Path(temp))
            developer=root/'any name'/'nested';developer.mkdir(parents=True)
            self.assertEqual(u.find_install_root(developer),root)

    def test_same_commit_skips_zip_but_local_content_change_forces_update(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'installed','old');stage=source(p/'new')
            with patch.object(u,'ensure_closed'):u.install(root,stage,'e'*40)
            def get(url,destination,limit=u.MAX_ARCHIVE):
                self.assertIn('api.github.com',url,'ZIP must not be requested')
                destination.write_text(json.dumps(dict(ref='refs/heads/develop',object=dict(sha='e'*40))))
            with patch.object(u,'download',side_effect=get) as download,patch.object(u,'ensure_closed',side_effect=AssertionError('no replacement needed')):
                self.assertEqual(u.main(['--root',str(root)]),0)
                self.assertEqual(download.call_count,1)
            self.assertTrue(u.already_current(root,'e'*40))
            (root/'brands/demo.py').write_bytes(b'changed locally')
            self.assertFalse(u.already_current(root,'e'*40))

    def test_tools_and_manual_updater_do_not_affect_runtime_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp:
            root=source(Path(temp));before=u.source_fingerprint(root)
            (root/'tools').mkdir();(root/'tools/update_develop.py').write_bytes(b'manual')
            (root/'Crawler_Hub_Update_Windows.bat').write_bytes(b'manual bat')
            (root/'app/__pycache__').mkdir();(root/'app/__pycache__/x.pyc').write_bytes(b'cache')
            self.assertEqual(u.source_fingerprint(root),before)
            self.assertTrue((root/'brands/demo.json').exists())
            self.assertFalse((root/'.crawler-update-state.json').exists())

    def test_git_checkout_and_nested_user_data_are_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=source(Path(temp));(root/'.git').write_text('gitdir: x')
            with self.assertRaisesRegex(ValueError,'Git'):u.check_destination(root)
            (root/'.git').unlink();(root/'app/settings.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'사용자 데이터'):u.check_destination(root)

    def test_concurrent_updates_are_locked(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with u.update_lock(root):
                with self.assertRaises(ValueError):
                    with u.update_lock(root):pass
            self.assertFalse((root/'.crawler-update.lock').exists())

    def test_anonymous_ref_is_pinned_and_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            def get(url,dest,limit):
                self.assertIn('/git/ref/heads/develop',url)
                dest.write_text(json.dumps(dict(ref='refs/heads/develop',object=dict(sha='a'*40))))
            with patch.object(u,'download',side_effect=get):
                self.assertEqual(u.latest_commit(Path(temp)),'a'*40)

    def test_running_app_blocks_before_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'installed','old');stage=source(p/'new')
            with patch.object(u,'ensure_closed',side_effect=ValueError('running app')):
                with self.assertRaisesRegex(ValueError,'running'):u.install(root,stage,'c'*40)
            self.assertIn('old',(root/'app/server.py').read_text())

    def test_complete_update_download_validate_install_and_preserve_user_home(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'installed','old');upstream=source(p/'upstream')
            home=p/'user-home';(home/'jobs').mkdir(parents=True)
            (home/'settings.json').write_bytes(b'company settings')
            (home/'jobs/result.xlsx').write_bytes(b'company results')
            archive=p/'upstream.zip'
            with zipfile.ZipFile(archive,'w') as z:
                for path in upstream.rglob('*'):
                    if path.is_file():z.write(path,'repo-commit/'+path.relative_to(upstream).as_posix())
                z.writestr('repo-commit/jobs/overwrite.txt','must not install')
                z.writestr('repo-commit/settings.json','must not install')
            def download(url,destination,limit=u.MAX_ARCHIVE):
                if 'api.github.com' in url:
                    destination.write_text(json.dumps(dict(ref='refs/heads/develop',object=dict(sha='d'*40))))
                else:
                    self.assertTrue(url.endswith('/'+'d'*40))
                    destination.write_bytes(archive.read_bytes())
            with patch.object(u,'download',side_effect=download),patch.object(u,'ensure_closed'),patch.dict(u.os.environ,CRAWLER_HUB_HOME=str(home)):
                self.assertEqual(u.main(['--root',str(root)]),0)
            self.assertEqual((home/'settings.json').read_bytes(),b'company settings')
            self.assertEqual((home/'jobs/result.xlsx').read_bytes(),b'company results')
            self.assertFalse((root/'jobs').exists());self.assertFalse((root/'settings.json').exists())
            self.assertIn('new',(root/'app/server.py').read_text())


if __name__=='__main__':unittest.main()
