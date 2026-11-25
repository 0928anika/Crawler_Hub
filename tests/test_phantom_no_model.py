import sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
from test_phantom import Tests as OfficialTests, fixture
from test_oberon_brands import load,run,ALL
from test_hybrid_mapping import HybridMappingTests
from schema import preview
import color_mapping as mapping

class Tests(unittest.TestCase):
 def test_preselected_gallery_can_remove_model_from_both_exports(self):
  module=load('phantom_official_no_model')
  routes=OfficialTests().routes()
  goods=fixture('product')
  goods['imageList']=['https://cdn.bucketstore.com/product.jpg','https://cdn.bucketstore.com/model.jpg','https://cdn.bucketstore.com/back.jpg']
  import json
  routes['https://bucketstore.com/goods/detail/211F3CB201_TQ']='1:'+json.dumps({'goodsDetail':goods,'actualMeasurementSizeList':fixture('measure')})
  rows,fail,error,net=run(module,'phantom_official_no_model','211F3CB201',ALL,routes)
  self.assertIsNone(error,fail);self.assertEqual(len(rows),3)
  self.assertTrue(all(r['_unmapped']['preassigned_color_id'] for r in rows))
  self.assertFalse(any('model.jpg' in r['상세페이지 HTML'] for r in rows))
  self.assertTrue(all('제품소재' in r['상세페이지 HTML'] and '가로' in r['상세페이지 HTML'] for r in rows))
  with tempfile.TemporaryDirectory() as folder:
   items=[]
   for index,row in enumerate(rows):
    raw={k:v for k,v in row.items() if k!='_unmapped'}
    item=preview(list(raw),list(raw.values()),ALL)
    item.update(id=str(index),task_id='0',raw=raw,unmapped=row['_unmapped']);items.append(item)
   job=HybridMappingTests().job(folder,items)
   job.brand.update(json.loads((ROOT/'brands/phantom_official_no_model.json').read_text('utf-8')))
   job.selected=list(ALL)
   view=mapping.snapshot(job);g=view['groups'][0];c=g['colors'][0]
   self.assertFalse(g['complete']);self.assertEqual(sum(bool(i['assigned_colors']) for i in g['images']),3)
   view=mapping.save_color(job,dict(group_id=g['id'],color_id=c['id'],image_ids=[i['id'] for i in g['images'] if 'model.jpg' not in i['url']],expected_revision=view['revision']))
   view=mapping.finalize(job,dict(expected_revision=view['revision']))
   result=mapping.final_items(job,view)[0]['raw']
   self.assertNotIn('model.jpg',result['대표이미지']+result['추가이미지']+result['상세페이지 HTML'])
   self.assertIn('back.jpg',result['상세페이지 HTML']);self.assertIn('제품소재',result['상세페이지 HTML'])
   self.assertEqual(result['상세페이지 HTML'].count('제품소재'),1)
 def test_color_tails_stay_separate_and_shared_photo_keeps_both_assignments(self):
  from test_hybrid_mapping import item,PALETTE
  import json
  with tempfile.TemporaryDirectory() as folder:
   items=[item(0,'STYLE','60','https://img/shared.jpg',True),item(1,'STYLE','AC','https://img/shared.jpg',True)]
   for row,color in zip(items,PALETTE):
    row['raw']['상세페이지 HTML']='<table><tr><td>'+color['color_code']+' notice</td></tr></table>'
    row['unmapped'].update(colors=[color],preassigned_color_id=color['id'],review_required=True,detail_mode='site_shared')
   job=HybridMappingTests().job(folder,items)
   job.brand.update(json.loads((ROOT/'brands/phantom_official_no_model.json').read_text('utf-8')))
   job.selected.append('detail_page')
   view=mapping.snapshot(job);g=view['groups'][0]
   self.assertEqual(len(g['images']),1);self.assertEqual(len(g['images'][0]['assigned_colors']),2)
   for color in g['colors']:
    view=mapping.save_color(job,dict(group_id=g['id'],color_id=color['id'],image_ids=[g['images'][0]['id']],expected_revision=view['revision']))
   view=mapping.finalize(job,dict(expected_revision=view['revision']))
   for row in mapping.final_items(job,view):
    markup=row['raw']['상세페이지 HTML'];code=row['raw']['색상코드']
    self.assertIn(code+' notice',markup)
    self.assertNotIn(('AC' if code=='60' else '60')+' notice',markup)
   self.assertIn('shared.jpg',mapping.detail_preview(job,g['id'])['html'])
 def test_no_gallery_fails_instead_of_unreviewable_detail(self):
  goods=fixture('product');goods['imageList']=[]
  rows,fail,error,_=run(load('phantom_official_no_model'),'phantom_official_no_model','211F3CB201',ALL,OfficialTests().routes(goods=goods))
  self.assertIsNotNone(error);self.assertFalse(rows)

if __name__=='__main__':unittest.main()
