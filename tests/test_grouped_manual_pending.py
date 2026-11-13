import sys,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import color_mapping as mapping
from color_palette import normalize_palette
from schema import preview
HEADERS=['품번','상품명','색상','상품 링크','사이즈','대표이미지','추가이미지']
class GroupedPendingTests(unittest.TestCase):
 def test_one_model_before_mapping_and_one_row_per_color_after(self):
  palette=normalize_palette([dict(color_name='BLACK',color_code=''),dict(color_name='NAVY',color_code='')])
  items=[]
  for i in range(2):
   url='https://example.com/photo'+str(i)+'.jpg'
   values=['RMACO4805','니트 비니','BLACK,NAVY','https://www.fashionplus.co.kr/goods/detail/'+str(i+1),'095,100',url,url]
   row=preview(HEADERS,values,['size','main_image','additional_images']);row.update(id=str(i),task_id='0',raw=dict(zip(HEADERS,values)),unmapped=dict(colors=palette,image_url=url,shared_detail_html=''));items.append(row)
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   job=SimpleNamespace(brand=dict(mapping_mode='manual',image_mode='url',group_manual_pending=True,output_headers=HEADERS),items=items,selected=['size','main_image','additional_images'],queue=[dict(id='0',status='SUCCESS')],dir=Path(folder),state='completed',data_lock=threading.RLock(),save_lock=threading.RLock(),checkpoint=lambda:None,save=lambda:None)
   self.assertEqual(len(mapping.visible_items(job)),1)
   self.assertEqual(len(mapping.export_sheet(job)),2)
   view=mapping.snapshot(job);self.assertEqual(len(view['groups'][0]['images']),2)
   group=view['groups'][0]
   for i,color in enumerate(group['colors']):
    view=mapping.save_color(job,dict(expected_revision=view['revision'],group_id=group['id'],color_id=color['id'],image_ids=[group['images'][i]['id']]))
   mapping.finalize(job,dict(expected_revision=view['revision']))
   rows=mapping.visible_items(job);self.assertEqual(len(rows),2)
   self.assertTrue(all(row['raw']['사이즈']=='095,100' for row in rows))
   self.assertEqual(len(job.items),2)
if __name__=='__main__':unittest.main()
