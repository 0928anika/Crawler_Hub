import hashlib
import os
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
import io
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
            self.assertTrue((stage/'admin/update_develop.py').is_file());self.assertFalse((stage/'tools').exists())

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
            (stage/'admin').mkdir();(stage/'admin/update_develop.py').write_bytes(b'NEW_UPDATER = True')
            (stage/'admin/Update.bat').write_bytes(b'@echo new updater')
            (root/'brands/removed.py').write_bytes(b'old crawler')
            with patch.object(u,'ensure_closed'):
                backup=u.install(root,stage,'a'*40)
            self.assertIn('new',(root/'app/server.py').read_text())
            self.assertEqual((root/'jobs/task/result.xlsx').read_bytes(),b'old result')
            self.assertEqual((root/'settings.json').read_bytes(),b'personal settings')
            self.assertEqual((root/'venv/marker').read_bytes(),b'runtime')
            self.assertEqual((root/'tools/update_develop.py').read_bytes(),b'keep manual updater')
            self.assertEqual((root/'admin/update_develop.py').read_bytes(),b'NEW_UPDATER = True')
            self.assertEqual((root/'admin/Update.bat.new').read_bytes(),b'@echo new updater')
            self.assertFalse((root/'Update.bat.new').exists())
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
            output=io.StringIO()
            with patch.object(u,'download',side_effect=get) as download,patch.object(u,'ensure_closed',side_effect=AssertionError('no replacement needed')),redirect_stdout(output):
                self.assertEqual(u.main(['--root',str(root)]),0)
                self.assertEqual(download.call_count,1)
            self.assertEqual(output.getvalue().splitlines(),['최신 버전 확인 중...','최신 버전입니다.'])
            self.assertTrue(u.already_current(root,'e'*40))
            (root/'brands/demo.py').write_bytes(b'changed locally')
            self.assertFalse(u.already_current(root,'e'*40))

    def test_updater_pair_is_included_in_fingerprint_but_caches_are_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root=source(Path(temp));before=u.source_fingerprint(root)
            (root/'admin').mkdir();(root/'admin/update_develop.py').write_bytes(b'manual')
            (root/'admin/Update.bat').write_bytes(b'manual bat')
            (root/'app/__pycache__').mkdir();(root/'app/__pycache__/x.pyc').write_bytes(b'cache')
            self.assertNotEqual(u.source_fingerprint(root),before)
            before=u.source_fingerprint(root)
            (root/'app/__pycache__/x.pyc').write_bytes(b'changed cache')
            self.assertEqual(u.source_fingerprint(root),before)
            self.assertTrue((root/'brands/demo.json').exists())
            self.assertFalse((root/'admin/.crawler-update-state.json').exists())

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
            with u.update_lock(root):pass
            # The reusable file is harmless; only the OS lock indicates activity.

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
            output=io.StringIO()
            with patch.object(u,'download',side_effect=download),patch.object(u,'ensure_closed'),patch.dict(u.os.environ,CRAWLER_HUB_HOME=str(home)),redirect_stdout(output):
                self.assertEqual(u.main(['--root',str(root)]),0)
            self.assertIn('파일 검사 완료',output.getvalue())
            self.assertIn('앱 변경 4개 / 브랜드 추가 0개 / 브랜드 수정 0개 / 브랜드 제거 0개',output.getvalue())
            self.assertIn('백업:',output.getvalue())
            self.assertEqual((home/'settings.json').read_bytes(),b'company settings')
            self.assertEqual((home/'jobs/result.xlsx').read_bytes(),b'company results')
            self.assertFalse((root/'jobs').exists());self.assertFalse((root/'settings.json').exists())
            self.assertIn('new',(root/'app/server.py').read_text())

    def test_failure_log_is_one_clear_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root=source(Path(temp));output=io.StringIO()
            with patch.object(u,'latest_commit',side_effect=ValueError('연결 실패')),redirect_stdout(io.StringIO()),redirect_stderr(output):
                self.assertEqual(u.main(['--root',str(root)]),1)
            self.assertEqual(output.getvalue().strip(),'업데이트 실패: 연결 실패')

    def test_nested_updater_pair_and_pending_bat_are_updated_together(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'installed','old');stage=source(p/'new')
            active=root/'개발자 도구';active.mkdir()
            (active/'Update.bat').write_bytes(b'old bat')
            (active/'update_develop.py').write_bytes(b'old python')
            (stage/'admin').mkdir();(stage/'admin/update_develop.py').write_bytes(b'VERSION=3')
            (stage/'admin/Update.bat').write_bytes(b'@echo new')
            with patch.object(u,'ensure_closed'),patch.dict(os.environ,CRAWLER_HUB_UPDATER_DIR=str(active)):
                backup=u.install(root,stage,'f'*40)
            self.assertEqual((active/'Update.bat').read_bytes(),b'old bat')
            self.assertEqual((active/'Update.bat.new').read_bytes(),b'@echo new')
            self.assertEqual((active/'update_develop.py').read_bytes(),b'VERSION=3')
            self.assertEqual((backup/'개발자 도구/Update.bat').read_bytes(),b'old bat')
            self.assertEqual((backup/'개발자 도구/update_develop.py').read_bytes(),b'old python')

    def test_partial_updater_pair_fails_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            stage=source(Path(temp));(stage/'admin').mkdir();(stage/'admin/Update.bat').write_text('@echo hi')
            with self.assertRaisesRegex(ValueError,'쌍'):u.validate(stage)

    def test_self_update_failure_rolls_back_app_and_updater(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'installed','old');stage=source(p/'new')
            (root/'tools').mkdir();(root/'tools/update_develop.py').write_bytes(b'old python')
            (root/'Update.bat').write_bytes(b'old bat')
            (stage/'admin').mkdir();(stage/'admin/update_develop.py').write_bytes(b'NEW=True')
            (stage/'admin/Update.bat').write_bytes(b'new bat')
            rename=Path.rename
            def fail(path,target):
                if path==stage/'admin/Update.bat.new':raise OSError('simulated updater lock')
                return rename(path,target)
            with patch.object(u,'ensure_closed'),patch.object(Path,'rename',fail):
                with self.assertRaisesRegex(OSError,'simulated'):u.install(root,stage,'b'*40)
            self.assertIn('old',(root/'app/server.py').read_text())
            self.assertEqual((root/'tools/update_develop.py').read_bytes(),b'old python')
            self.assertEqual((root/'Update.bat').read_bytes(),b'old bat')
            self.assertFalse((root/'Update.bat.new').exists())

    def test_pending_bat_fingerprint_matches_after_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=source(Path(temp));(root/'admin').mkdir();(root/'admin/Update.bat').write_bytes(b'old')
            (root/'admin/Update.bat.new').write_bytes(b'new')
            fingerprint=u.source_fingerprint(root)
            (root/'admin/Update.bat.new').replace(root/'admin/Update.bat')
            self.assertEqual(u.source_fingerprint(root),fingerprint)

    def test_keep_five_complete_backups_preserves_unknown_and_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);parent=root/'admin/backups';parent.mkdir(parents=True)
            for n in range(8):
                folder=parent/str(n);folder.mkdir()
                (folder/'backup.json').write_text(json.dumps(dict(repository=u.REPO,complete=True)))
                (folder/'app').mkdir();(folder/'app/old.txt').write_text('old')
            (parent/'manual-backup').mkdir();(parent/'failed-backup').mkdir()
            (parent/'failed-backup/backup.json').write_text('{}')
            self.assertEqual(u.prune_backups(root),3)
            self.assertEqual(sorted(p.name for p in parent.iterdir()),['3','4','5','6','7','failed-backup','manual-backup'])

    def test_process_detection_ignores_unrelated_server_but_detects_our_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root=source(Path(temp)/'install with spaces');home=Path(temp)/'home'
            release=home/'releases/abc/app';release.mkdir(parents=True)
            (release/'brand_catalog.py').write_text('')
            with patch.dict(os.environ,CRAWLER_HUB_HOME=str(home)):
                self.assertFalse(u.crawler_process('python "'+str(Path(temp)/'other/server.py')+'"',root))
                self.assertTrue(u.crawler_process('python "'+str(root/'app/bootstrap.py')+'"',root))
                self.assertTrue(u.crawler_process('python "'+str(release/'server.py')+'" --port 8888',root))

    def test_crashed_process_releases_lock_automatically(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            code="import importlib.util, pathlib, os; s=importlib.util.spec_from_file_location('u',"+repr(str(ROOT/'tools/update_develop.py'))+"); u=importlib.util.module_from_spec(s); s.loader.exec_module(u); lock=u.update_lock(pathlib.Path("+repr(str(root))+")); lock.__enter__(); os._exit(0)"
            subprocess.run([sys.executable,'-c',code],check=True)
            with u.update_lock(root):pass

    @unittest.skipUnless(os.name=='nt','Windows BAT integration')
    def test_real_bat_success_failure_check_and_pending_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=source(Path(temp)/'install with spaces');(root/'admin').mkdir()
            bat=root/'admin/Update.bat';bat.write_bytes((ROOT/'Update.bat').read_bytes())
            script=root/'admin/update_develop.py'
            script.write_text('import sys; print("FAKE_UPDATE"); raise SystemExit(0)')
            (root/'Crawler_Hub_Windows.bat').write_text('@echo off\n echo launched>"%~dp0launched.txt"\nexit /b 0\n')
            env=dict(os.environ,LOCALAPPDATA=str(Path(temp)/'empty'),PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'])
            def run(args=''):
                return subprocess.run(['cmd.exe','/d','/c','call Update.bat '+args],cwd=root/'admin',env=env,
                    stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
            self.assertEqual(run('--check-only').returncode,0);self.assertFalse((root/'launched.txt').exists())
            self.assertEqual(run('--no-launch').returncode,0);self.assertFalse((root/'launched.txt').exists())
            result=run();self.assertEqual(result.returncode,0,result.stdout);self.assertTrue((root/'launched.txt').exists())
            (root/'launched.txt').unlink();script.write_text('raise SystemExit(7)')
            result=run();self.assertEqual(result.returncode,7,result.stdout);self.assertFalse((root/'launched.txt').exists())
            script.write_text('raise SystemExit(0)')
            pending=bat.with_name('Update.bat.new');pending.write_bytes(bat.read_bytes()+b'\r\nrem updated version\r\n')
            result=run('--no-launch');self.assertEqual(result.returncode,0,result.stdout)
            self.assertFalse(pending.exists());self.assertIn(b'rem updated version',bat.read_bytes())

    def test_summary_counts_brand_ids_once_and_tolerates_broken_old_json(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'old');stage=source(p/'new')
            (stage/'brands/demo.py').write_text('changed')
            (stage/'brands/demo.json').write_text(json.dumps(dict(id='demo',script='demo.py',changed=True)))
            (stage/'brands/added.py').write_text('new')
            (stage/'brands/added.json').write_text(json.dumps(dict(id='added',script='added.py')))
            (root/'brands/removed.json').write_text('{broken json')
            (stage/'app/server.py').write_text('changed')
            self.assertEqual(u.change_summary(root,stage),dict(app_changed=1,brands_added=1,brands_changed=1,brands_removed=1))

    def test_updater_inside_tools_does_not_copy_same_file(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'old');stage=source(p/'new')
            (root/'tools').mkdir();(stage/'admin').mkdir()
            (stage/'admin/update_develop.py').write_bytes(b'NEW=True')
            (stage/'admin/Update.bat').write_bytes(b'@echo new')
            with patch.object(u,'ensure_closed'),patch.dict(os.environ,CRAWLER_HUB_UPDATER_DIR=str(root/'tools')):
                u.install(root,stage,'a'*40)
            self.assertEqual((root/'admin/update_develop.py').read_bytes(),b'NEW=True');self.assertFalse((root/'tools/update_develop.py').exists())
            self.assertTrue((root/'admin/Update.bat.new').exists());self.assertFalse((root/'Update.bat.new').exists())

    def test_only_admin_holds_management_files_and_single_pending_bat(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);root=source(p/'installed','old');stage=source(p/'new')
            admin=root/'admin';admin.mkdir()
            (admin/'Update.bat').write_bytes(b'old bat')
            (admin/'update_develop.py').write_bytes(b'old updater')
            (stage/'admin').mkdir()
            (stage/'admin/Update.bat').write_bytes(b'new bat')
            (stage/'admin/update_develop.py').write_bytes(b'new updater')
            with patch.object(u,'ensure_closed'),patch.dict(os.environ,CRAWLER_HUB_UPDATER_DIR=str(admin)):
                with u.update_lock(root):backup=u.install(root,stage,'a'*40)
                self.assertTrue(u.already_current(root,'a'*40))
                (admin/'Update.bat.new').replace(admin/'Update.bat')
                self.assertTrue(u.already_current(root,'a'*40))
            self.assertEqual({n.name for n in root.iterdir()},{'app','brands','admin'})
            self.assertEqual(backup.parent,admin/'backups')
            self.assertTrue((admin/'.crawler-update.lock').is_file())
            self.assertTrue((admin/'.crawler-update-state.json').is_file())
            self.assertFalse(list(root.glob('*.new')))


if __name__=='__main__':unittest.main()
