"""실행 중·일시정지·종료 후 대상 품번 수정(추가·수정·제외·복원) 검사. 샘플 실행(demo)이라 네트워크 없음."""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = tempfile.TemporaryDirectory()
os.environ.setdefault('CRAWLER_HUB_HOME', str(Path(RUNTIME.name) / 'runtime'))
sys.path.insert(0, str(ROOT / 'app'))
import tasks  # noqa: E402

BRAND = dict(id='target_test', name='대상테스트', crawl_source='공식 홈페이지', script='missing_target_test.py',
             mapping_mode='automatic', image_mode='url', output_headers=['품번', '상품명', '색상', '상품 링크'],
             guide=dict(pattern='^[A-Z]{2}[0-9]{2}$', required_columns=['품번']))


class JobTargetTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.enterContext(patch.object(tasks, 'HOME', Path(self.home.name)))
        (Path(self.home.name) / 'jobs').mkdir()

    def tearDown(self):
        self.home.cleanup()

    def job(self, codes, headers=('품번',)):
        rows = [[c] + [None] * (len(headers) - 1) if isinstance(c, str) else list(c) for c in codes]
        return tasks.Job(dict(BRAND), list(headers), rows, True, [], '대상 테스트')

    def statuses(self, job):
        return [(q['code'], q['status']) for q in job.queue]

    def run_until_paused(self, job):
        job.control.write_text('pause', encoding='utf-8')
        thread = threading.Thread(target=job.run, daemon=True)
        thread.start()
        for _ in range(100):
            if job.state == 'paused':
                break
            time.sleep(.05)
        self.assertEqual(job.state, 'paused')
        return thread

    def test_edit_skip_add_while_paused_then_resume_in_input_order(self):
        job = self.job(['AA01', 'AA02', 'AA03'])
        thread = self.run_until_paused(job)
        result = job.edit_targets(dict(edit=[dict(id='1', code='BB02')], skip=['2'], add='CC04\nAA01\n\nbad code'))
        self.assertEqual((result['added'], result['edited'], result['skipped']), (2, 1, 1))
        self.assertEqual(result['duplicates'], ['AA01'])
        self.assertTrue(any('badcode' in w for w in result['warnings']))            # 형식 다름은 경고만
        self.assertEqual(job.rows[1][0], 'BB02')
        self.assertEqual(job.summary()['total'], 4)                                  # 제외 1개 빼고
        self.assertEqual(job.summary()['skipped'], 1)
        job.control.write_text('run', encoding='utf-8')
        thread.join(30)
        self.assertEqual(self.statuses(job), [('AA01', 'SUCCESS'), ('BB02', 'SUCCESS'), ('AA03', 'SKIPPED'),
                                              ('CC04', 'SUCCESS'), ('badcode', 'SUCCESS')])
        self.assertEqual([i['raw']['품번'] for i in job.items], ['AA01', 'BB02', 'CC04', 'badcode'])
        self.assertEqual(job.state, 'completed')

    def test_running_and_finished_rows_are_locked(self):
        job = self.job(['AA01', 'AA02'])
        job.queue[0]['status'] = 'RUNNING'
        job.queue[1]['status'] = 'SUCCESS'
        with self.assertRaisesRegex(ValueError, '수집 중인'):
            job.edit_targets(dict(edit=[dict(id='0', code='ZZ99')]))
        with self.assertRaisesRegex(ValueError, '완료된'):
            job.edit_targets(dict(skip=['1']))
        with self.assertRaisesRegex(ValueError, '바뀐 내용'):
            job.edit_targets(dict(add='AA02'))                                       # 중복만 → 변경 없음
        with self.assertRaisesRegex(ValueError, '이미 대상 목록'):
            job2 = self.job(['AA01', 'AA02'])
            job2.edit_targets(dict(edit=[dict(id='1', code='aa01')]))

    def test_restore_and_edit_failed_row_after_completion_then_continue(self):
        job = self.job(['AA01', 'AA02', 'AA03'])
        job.run()
        self.assertEqual(job.state, 'completed')
        for i in (1, 2):
            job.queue[i].update(status='FAILED', error='결과 없음')                  # 부분 결과가 있는 실패 품번
        job.edit_targets(dict(edit=[dict(id='2', code='AC03')]))
        self.assertEqual([i['raw']['품번'] for i in job.items], ['AA01', 'AA02'])   # 고친 실패 품번의 부분 결과는 지움
        job.edit_targets(dict(skip=['1']))
        self.assertEqual([i['raw']['품번'] for i in job.items], ['AA01'])           # 제외한 품번의 결과도 지움
        job.edit_targets(dict(restore=['1']))
        self.assertTrue(job.summary()['can_continue'])
        job.prepare_rerun('continue')
        job.run()
        self.assertEqual(self.statuses(job), [('AA01', 'SUCCESS'), ('AA02', 'SUCCESS'), ('AC03', 'SUCCESS')])
        self.assertEqual(sorted(i['raw']['품번'] for i in job.items), ['AA01', 'AA02', 'AC03'])

    def test_reload_keeps_skipped_edited_and_added_rows(self):
        job = self.job(['AA01', 'AA02'])
        job.run()
        job.queue[1].update(status='FAILED')
        job.checkpoint()
        job.edit_targets(dict(skip=['1'], add='CC03'))
        loaded = tasks.Job.load(job.dir)
        self.assertEqual(self.statuses(loaded), [('AA01', 'SUCCESS'), ('AA02', 'SKIPPED'), ('CC03', 'WAITING')])
        self.assertEqual(loaded.rows[2][0], 'CC03')
        self.assertEqual([i['raw']['품번'] for i in loaded.items], ['AA01'])
        self.assertTrue(loaded.summary()['can_continue'])

    def test_multi_column_rows_need_tab_separated_values(self):
        job = self.job([('AA01', 'https://a/1')], headers=('품번', '상품URL'))
        job.brand['guide'] = dict(BRAND['guide'], required_columns=['품번', '상품URL'])
        with self.assertRaisesRegex(ValueError, '탭으로 구분'):
            job.edit_targets(dict(add='AA02'))
        job.edit_targets(dict(add='AA02\thttps://a/2'))
        self.assertEqual(job.rows[1], ['AA02', 'https://a/2'])
        self.assertEqual(job.queue[1]['source_row'], 3)

    def test_legacy_job_without_input_cannot_be_edited(self):
        job = self.job(['AA01'])
        job.headers = []
        with self.assertRaisesRegex(ValueError, '이전 버전'):
            job.edit_targets(dict(add='AA02'))
        self.assertFalse(job.summary()['editable_targets'])


if __name__ == '__main__':
    unittest.main()
