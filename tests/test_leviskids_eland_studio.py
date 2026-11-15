import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import leviskids_eland_studio as c
import color_mapping as m
import test_hybrid_mapping as helper
class Context:
 product_code='V224SH308P'
 def __init__(self):self.rows=[]
 def enabled(self,k):return k in ('main_image','additional_images','detail_page')
 def checkpoint(self):pass
 def stage(self,*args):pass
 def log(self,*args):pass
 def fail_item(self,*args):pass
 def emit_unmapped(self,*args,**kwargs):self.rows.append((args,kwargs))
class LevisElandTests(unittest.TestCase):
 def test_empty_model_number_uses_exact_title_code(self):
  d=json.loads((ROOT/'tests/fixtures/leviskids_eland_studio/2611636774.json').read_text('utf-8'))
  main=d['itemMain'];link=c.SITE+'/i/item?itemNo='+str(main['itemNo'])+'&lowerVendNo='+main['lowerVendNo']
  soup=c.BeautifulSoup('<script>const itemDetailJson = '+json.dumps(dict(itemDetail=d))+'</script>','html.parser')
  with patch.object(c,'page',return_value=soup):
   self.assertIsNotNone(c.product(Context(),link,'V211SW122P'))
   self.assertIsNone(c.product(Context(),link,'V211SW122'))
 def test_body_includes_individual_cuts_but_not_banner_labels_or_long_composite(self):
  urls=['front.jpg','close.jpg','long.jpg','shipping.jpg','V224SH308P_MB_L1.jpg','V224SH308P_MB_F1.jpg','size.jpg']
  d=dict(itemDesc=dict(itemDescdocumentCon=''.join('<img src="https://image.istyle24.com/'+u+'">' for u in urls)))
  def info(ctx,url,cache,detail=False):return ('JPEG',1000,3000 if 'long' in url else 1300,url,False)
  with patch.object(c,'image_info',side_effect=info):result=c.body_photos(Context(),d,{})
  self.assertEqual([u.rsplit('/',1)[-1] for u,_ in result],['front.jpg','close.jpg','V224SH308P_MB_F1.jpg'])
 def test_only_primary_background_gates_product(self):
  main=dict(itemImage=[dict(imageDcode='010',imagePath='front.jpg'),dict(imageDcode='030',imagePath='complex.jpg')])
  def info(ctx,url,cache):return ('JPEG',1000,1300,url,'front' in url)
  with patch.object(c,'image_info',side_effect=info):
   self.assertTrue(c.primary_studio(Context(),main,{}));self.assertEqual(len(c.gallery(Context(),main,{})),2)
 def test_selected_photos_only_and_fixed_chart_per_color(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
   items=[helper.item(i,'STYLE','60','https://img/photo'+str(i)+'.jpg',True) for i in range(9)]
   tail='<img src="'+c.SIZE_IMAGE+'">'
   for item in items:
    item['raw']['상세페이지 HTML']=tail;item['unmapped']['shared_detail_html']=tail;item['unmapped']['detail_mode']='site_shared'
   job=helper.HybridMappingTests().job(folder,items)
   job.brand.update(mapping_detail_mode='selected_images_with_tail',mapping_image_order='source',output_headers=helper.HEADERS+['상세페이지 HTML'])
   job.selected.append('detail_page')
   view=m.snapshot(job);g=view['groups'][0]
   selections=[g['images'][:7],[g['images'][0],g['images'][7]]]
   for color,images in zip(g['colors'],selections):
    view=m.save_color(job,dict(group_id=g['id'],color_id=color['id'],image_ids=[i['id'] for i in images],expected_revision=view['revision']))
   view=m.finalize(job,dict(expected_revision=view['revision']));rows=m.final_items(job,view)
   for row,images in zip(rows,selections):
    markup=row['raw']['상세페이지 HTML'];self.assertEqual(markup.count(c.SIZE_IMAGE),1)
    for image in g['images']:self.assertEqual(image['url'] in markup,image['id'] in [i['id'] for i in images],str((row['raw']['색상코드'],image['url'],markup)))
   # Changing the saved selection must remove the previously selected cuts.
   view=m.save_color(job,dict(group_id=g['id'],color_id=g['colors'][0]['id'],image_ids=[g['images'][8]['id']],expected_revision=view['revision']))
   view=m.finalize(job,dict(expected_revision=view['revision']));markup=m.final_items(job,view)[0]['raw']['상세페이지 HTML']
   self.assertIn('photo8.jpg',markup);self.assertNotIn('photo0.jpg',markup)
if __name__=='__main__':unittest.main()




