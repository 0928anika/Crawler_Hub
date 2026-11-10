import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import color_mapping as mapping
from color_palette import normalize_palette
from crawler_sdk import CrawlContext
from schema import preview

HEADERS=['품번','상품명','색상','상품 링크','색상명','색상코드','대표이미지','추가이미지']
PALETTE=normalize_palette([dict(color_code='60',color_name='Charcoal'),dict(color_code='AC',color_name='Gray')])


def item(index,code,color_code,url,unmapped=False):
    name='Charcoal' if color_code=='60' else 'Gray'
    values=[code,'Pullover',name,'https://whoau.com/product/pullover/5600/',name,color_code,url,','.join([url]*5)]
    result=preview(HEADERS,values,['main_image','additional_images'])
    result.update(id=str(index),task_id='0',raw=dict(zip(HEADERS,values)))
    if unmapped:result['unmapped']=dict(colors=PALETTE,image_url=url,shared_detail_html='')
    return result


class HybridMappingTests(unittest.TestCase):
    def job(self,folder,items):
        return SimpleNamespace(brand=dict(mapping_mode='manual',hybrid_mapping=True,image_mode='url',output_headers=HEADERS),
            items=items,selected=['main_image','additional_images'],queue=[dict(id='0',status='SUCCESS',code='WHKAG4T35U')],dir=Path(folder),state='completed',
            data_lock=threading.RLock(),save_lock=threading.RLock(),checkpoint=lambda:None,save=lambda:None)

    def test_only_unknown_photos_are_visible_and_auto_rows_survive_finalization(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            job=self.job(folder,[item(0,'WHKAG4T35U','60','https://img/auto-black.jpg'),item(1,'WHKAG4T35U','AC','https://img/auto-gray.jpg'),item(2,'WHKAG4T35U','60','https://img/model.jpg',True)])
            view=mapping.snapshot(job)
            self.assertEqual([i['url'] for i in view['groups'][0]['images']],['https://img/model.jpg'])
            pending=mapping.export_sheet(job)
            self.assertEqual(len(pending),4)
            self.assertEqual(pending[1][-1],'공식 색상 자동 매핑 완료')
            group=view['groups'][0]
            for color in group['colors']:
                view=mapping.save_color(job,dict(expected_revision=view['revision'],group_id=group['id'],color_id=color['id'],image_ids=[group['images'][0]['id']] if color['color_code']=='60' else []))
            view=mapping.finalize(job,dict(expected_revision=view['revision']))
            rows=mapping.final_items(job,view)
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['raw']['대표이미지'],'https://img/auto-black.jpg')
            self.assertIn('https://img/model.jpg',rows[0]['raw']['추가이미지'])
            self.assertEqual(len(rows[0]['raw']['추가이미지'].split(',')),5)
            self.assertEqual(rows[1]['raw']['대표이미지'],'https://img/auto-gray.jpg')
            self.assertEqual(len(mapping.export_sheet(job)),3)

    def test_all_auto_job_is_complete_without_mapping_groups(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            job=self.job(folder,[item(0,'WHKAG4T35U','60','https://img/auto.jpg')])
            view=mapping.snapshot(job)
            self.assertEqual(view['groups'],[])
            self.assertTrue(view['finalized'])
            self.assertEqual(len(mapping.export_sheet(job)),2)

    def test_failed_auto_results_are_not_exported(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            job=self.job(folder,[item(0,'WHKAG4T35U','60','https://img/auto.jpg')])
            job.queue[0]['status']='FAILED'
            self.assertFalse(mapping.snapshot(job)['finalized'])
            self.assertEqual(mapping.export_sheet(job)[1:],[])

    def test_sdk_allows_auto_only_with_explicit_hybrid_opt_in(self):
        for allow in (False,True):
            with self.subTest(allow=allow),tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
                ctx=CrawlContext('unused',Path(folder)/'result.xlsx',0,['main_image'],HEADERS,lambda:None,lambda *a,**k:None,input_item={'품번':'WHKAG4T35U'},mapping_mode='manual')
                ctx.allow_automatic_results=allow
                ctx.emit_result(dict(product_code='WHKAG4T35U',product_name='Pullover',color='Gray',product_url='https://whoau.com/product/pullover/5600/',main_image='https://img/auto.jpg'))
                if allow:ctx.finish()
                else:
                    with self.assertRaises(RuntimeError):ctx.finish()

    def test_legacy_manual_job_still_exports_only_raw_candidates(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            job=self.job(folder,[item(0,'WHKAG4T35U','60','https://img/model.jpg',True)])
            job.brand.pop('hybrid_mapping')
            self.assertFalse(mapping.snapshot(job)['finalized'])
            self.assertEqual(mapping.export_sheet(job)[0][4],'이미지URL')


if __name__=='__main__':unittest.main()
