"""색상 매핑에서 클릭한 순서 = 최종 대표(1번)·추가(2번~) 순서."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'tests'))
import test_hybrid_mapping as helper  # noqa: E402
import color_mapping as m  # noqa: E402
import local_images  # noqa: E402


class MappingClickOrderTests(unittest.TestCase):
    def job(self, folder):
        urls = ['https://img/a.jpg', 'https://img/b.jpg', 'https://img/c.jpg']
        return helper.HybridMappingTests().job(folder, [helper.item(n, 'STYLE', '60', url, True) for n, url in enumerate(urls)])

    def test_click_order_sets_main_then_additional_and_survives_reopen(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            job = self.job(folder)
            v = m.snapshot(job)
            g = v['groups'][0]
            ids = {i['url']: i['id'] for i in g['images']}
            red, blue = [c['id'] for c in g['colors']]
            clicked = [ids['https://img/c.jpg'], ids['https://img/a.jpg']]                 # 갤러리 순서와 반대로 클릭
            v = m.save_color(job, dict(group_id=g['id'], color_id=red, image_ids=clicked, expected_revision=v['revision']))
            self.assertEqual(v['groups'][0]['orders'][red], clicked)                       # 다시 열어도 클릭 순서 유지
            v = m.save_color(job, dict(group_id=g['id'], color_id=blue, image_ids=[ids['https://img/b.jpg']], expected_revision=v['revision']))
            v = m.finalize(job, dict(expected_revision=v['revision']))
            rows = {r['raw']['색상명'] or r['raw']['색상코드']: r['raw'] for r in m.final_items(job, v)}
            first = rows[g['colors'][0]['color_name'] or g['colors'][0]['color_code']]
            self.assertEqual(first['대표이미지'], 'https://img/c.jpg')
            self.assertEqual(first['추가이미지'].split(',')[0], 'https://img/a.jpg')

    def test_saved_state_without_order_keeps_gallery_order(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as folder:
            job = self.job(folder)
            v = m.snapshot(job)
            g = v['groups'][0]
            ids = [i['id'] for i in g['images']]
            red, blue = [c['id'] for c in g['colors']]
            state = dict(revision=1, groups={g['id']: dict(assignments={ids[2]: [red], ids[0]: [red], ids[1]: [blue]},
                                                            confirmed=[red, blue], image_signature=g['signature'])})
            (job.dir / 'color_mapping.json').write_text(json.dumps(state), encoding='utf-8')
            v = m.finalize(job, dict(expected_revision=1))
            rows = m.final_items(job, v)
            red_row = next(r['raw'] for r in rows if r['raw']['대표이미지'] in ('https://img/a.jpg', 'https://img/c.jpg'))
            self.assertEqual(red_row['대표이미지'], 'https://img/a.jpg')                    # 예전 저장분: 갤러리 순서

    def test_local_file_names_follow_click_order(self):
        view = dict(groups=[dict(id='g', code='STYLE', orders={'a': ['z', 'x']},
                                 colors=[dict(id='a', color_code='01', color_name='red')],
                                 images=[dict(id=i, assigned_colors=['a'], asset_path=f'images/{i}.jpg') for i in ('x', 'y', 'z')])])
        entries = local_images.plan(view, ['main_image', 'additional_images'], 5)[('g', 'a')]
        self.assertEqual([(e['image_id'], e['filename']) for e in entries],
                         [('z', 'STYLE_01.jpg'), ('x', 'STYLE_01_02.jpg'), ('y', 'STYLE_01_03.jpg')])


if __name__ == '__main__':
    unittest.main()
