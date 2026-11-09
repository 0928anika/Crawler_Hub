import atexit
import copy
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
# Never open or migrate the user's real runtime, settings or job history.
RUNTIME = tempfile.TemporaryDirectory(prefix='crawler-hub-tests-')
atexit.register(RUNTIME.cleanup)
os.environ['CRAWLER_HUB_HOME'] = str(Path(RUNTIME.name) / 'runtime')
import admin
from brand_catalog import definitions, definition_path, registry_hash, verify_published

spec = importlib.util.spec_from_file_location('manage_brands', ROOT / 'tools/manage_brands.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class BrandManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crawler-brands-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = self.root / 'brands'
        self.folder.mkdir()
        (self.root / 'app').mkdir()
        home = self.root / 'runtime'
        home.mkdir()
        self.enterContext(patch.object(admin, 'SOURCE', self.root / 'app'))
        self.enterContext(patch.object(admin, 'HOME', home))
        self.enterContext(patch.dict(os.environ, CRAWLER_HUB_SOURCE_ROOT=str(self.root / 'app')))
        self.template = json.loads((ROOT / 'brands/hangten_official.json').read_text(encoding='utf-8'))
        self.code = 'PORTAL_API_VERSION = 1\n\ndef crawl(ctx):\n    pass\n'
        self.write_brand('a_brand')
        self.write_brand('b_brand')

    def write_brand(self, key, code=None):
        code = self.code if code is None else code
        value = copy.deepcopy(self.template)
        value.update(id=key, script=key + '.py', code_hash=hashlib.sha256(code.encode()).hexdigest())
        (self.folder / value['script']).write_bytes(code.encode())
        definition_path(self.folder, key).write_bytes(admin.json_bytes(value))
        return value

    def payload(self, key='a_brand'):
        current = admin.read_brand(key)
        return dict(metadata=current['metadata'], code=current['code'], expected_hash=current['hash'],
                    registry_hash=current['registry_hash'])

    def test_split_catalog_runs_all_brands(self):
        self.assertEqual(len(verify_published(self.root)), 2)
        self.assertTrue(all(b['available'] for b in admin.listing()['brands']))

    def test_same_brand_name_with_different_ids_remains_separate(self):
        items = definitions(self.folder)
        self.assertEqual(items[0]['name'], items[1]['name'])
        self.assertEqual({b['id'] for b in items}, {'a_brand', 'b_brand'})
        self.assertEqual(len(admin.listing()['brands']), 2)

    def test_migration_preserves_metadata_and_python_bytes(self):
        items = definitions(self.folder)
        before = {b['script']: (self.folder / b['script']).read_bytes() for b in items}
        for path in self.folder.glob('*.json'):
            path.unlink()
        (self.folder / 'brands.json').write_text(json.dumps(dict(brands=items)), encoding='utf-8')
        self.assertEqual(manage.migrate(self.root), 2)
        self.assertEqual(definitions(self.folder), items)
        self.assertFalse((self.folder / 'brands.json').exists())
        self.assertEqual(before, {name: (self.folder / name).read_bytes() for name in before})
        self.assertEqual(manage.migrate(self.root), 0)

    def test_legacy_still_readable_but_requires_migration_before_edit(self):
        items = definitions(self.folder)
        for path in self.folder.glob('*.json'):
            path.unlink()
        (self.folder / 'brands.json').write_text(json.dumps(dict(brands=items)), encoding='utf-8')
        self.assertEqual(len(admin.listing()['brands']), 2)
        with self.assertRaisesRegex(ValueError, 'migrate'):
            admin.save_brand(self.payload())

    def test_mixed_layout_rejected(self):
        (self.folder / 'brands.json').write_text('{"brands": []}', encoding='utf-8')
        with self.assertRaises(ValueError):
            definitions(self.folder)

    def test_mismatched_filename_rejected(self):
        definition_path(self.folder, 'a_brand').rename(self.folder / 'wrong_name.json')
        with self.assertRaises(ValueError):
            definitions(self.folder)

    def test_duplicate_script_rejected(self):
        path = definition_path(self.folder, 'b_brand')
        data = json.loads(path.read_bytes())
        data['script'] = 'a_brand.py'
        path.write_bytes(admin.json_bytes(data))
        with self.assertRaises(ValueError):
            definitions(self.folder)

    def test_script_traversal_rejected(self):
        for name in ('../bad.py', '..\\bad.py', 'C:bad.py', '/bad.py'):
            with self.subTest(name=name):
                from brand_catalog import script_path
                with self.assertRaises(ValueError):
                    script_path(self.folder, dict(script=name))

    def test_hash_mismatch_blocks_launch(self):
        (self.folder / 'a_brand.py').write_bytes(b'# changed\n')
        with self.assertRaisesRegex(ValueError, '해시'):
            verify_published(self.root)

    def test_syntax_error_blocks_launch_even_with_correct_hash(self):
        self.write_brand('a_brand', 'def broken(:\n')
        with self.assertRaises(SyntaxError):
            verify_published(self.root)

    def test_local_non_synced_folder_can_save_and_refresh_hash(self):
        self.assertFalse(admin.source_status()['synced'])
        data = self.payload()
        data['code'] += '# updated\n'
        result = admin.save_brand(data)
        self.assertEqual(hashlib.sha256((self.folder / 'a_brand.py').read_bytes()).hexdigest(), result['hash'])
        self.assertEqual(len(verify_published(self.root)), 2)

    def test_other_brand_edit_does_not_block_save(self):
        data = self.payload()
        other = admin.read_brand('b_brand')
        admin.save_notes(dict(id='b_brand', notes='other edit', registry_hash=other['registry_hash']))
        admin.save_brand(data)

    def test_same_brand_stale_metadata_blocked(self):
        data = self.payload()
        admin.save_notes(dict(id='a_brand', notes='changed', registry_hash=data['registry_hash']))
        with self.assertRaisesRegex(ValueError, '브랜드 정보'):
            admin.save_brand(data)

    def test_same_brand_stale_code_blocked(self):
        data = self.payload()
        (self.folder / 'a_brand.py').write_bytes(b'# external change')
        with self.assertRaisesRegex(ValueError, '다른 수정'):
            admin.save_brand(data)

    def test_notes_touch_only_selected_json(self):
        other = definition_path(self.folder, 'b_brand').read_bytes()
        code = (self.folder / 'a_brand.py').read_bytes()
        current = admin.read_brand('a_brand')
        result = admin.save_notes(dict(id='a_brand', notes='new note', registry_hash=current['registry_hash']))
        self.assertEqual(other, definition_path(self.folder, 'b_brand').read_bytes())
        self.assertEqual(code, (self.folder / 'a_brand.py').read_bytes())
        self.assertEqual(result['registry_hash'], registry_hash(self.folder, 'a_brand'))

    def test_json_write_failure_restores_python(self):
        data = self.payload()
        data['code'] += '# pending\n'
        before = (self.folder / 'a_brand.py').read_bytes()
        real_write = admin.atomic_bytes
        def fail_json(path, content):
            if path.suffix == '.json':
                raise OSError('simulated storage failure')
            real_write(path, content)
        with patch.object(admin, 'atomic_bytes', side_effect=fail_json):
            with self.assertRaises(OSError):
                admin.save_brand(data)
        self.assertEqual(before, (self.folder / 'a_brand.py').read_bytes())
        verify_published(self.root)

    def test_new_brand_saves_pair(self):
        meta = copy.deepcopy(self.template)
        meta.update(id='new_brand', script='new_brand.py')
        admin.save_brand(dict(metadata=meta, code=self.code, expected_hash='', registry_hash=registry_hash(self.folder)))
        self.assertEqual(len(verify_published(self.root)), 3)

    def test_import_requires_explicit_replace(self):
        with self.assertRaisesRegex(ValueError, 'replace'):
            manage.import_brand(self.root, definition_path(self.folder, 'a_brand'))

    def test_cli_import_matches_ui_validation_and_updates_hash(self):
        source = self.root / 'incoming'
        source.mkdir()
        meta = copy.deepcopy(self.template)
        meta.update(id='imported_brand', script='imported_brand.py')
        path = source / 'imported_brand.json'
        path.write_bytes(admin.json_bytes(meta))
        (source / meta['script']).write_bytes(self.code.encode())
        manage.import_brand(self.root, path)
        self.assertEqual(len(verify_published(self.root)), 3)

    def test_http_admin_lists_and_saves_pair_then_notes(self):
        import server
        hub_http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=hub_http.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection('127.0.0.1', hub_http.server_port, timeout=5)
        def request(method, path, data=None):
            body = json.dumps(data).encode() if data is not None else None
            connection.request(method, path, body, {'X-Portal-Token': server.TOKEN, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        try:
            status, catalog = request('GET', '/api/brands')
            self.assertEqual(status, 200)
            self.assertEqual(len(catalog['brands']), 2)
            status, current = request('GET', '/api/admin/brands/a_brand')
            self.assertEqual(status, 200)
            status, result = request('POST', '/api/admin/save', dict(metadata=current['metadata'],
                code=current['code'] + '# via HTTP\n', expected_hash=current['hash'], registry_hash=current['registry_hash']))
            self.assertEqual(status, 200, result)
            self.assertTrue(result['ok'])
            _, current = request('GET', '/api/admin/brands/a_brand')
            status, result = request('POST', '/api/admin/notes', dict(id='a_brand', notes='HTTP note', registry_hash=current['registry_hash']))
            self.assertEqual(status, 200, result)
            self.assertEqual(result['notes'], 'HTTP note')
            self.assertEqual(len(verify_published(self.root)), 2)
        finally:
            connection.close()
            hub_http.shutdown()
            hub_http.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
