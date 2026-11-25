import sys,tempfile,unittest,io,copy
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
from test_photo_exclusion import Tests as PhotoTests
from PIL import Image
import photo_exclusion as photos
import photo_thumbnails as thumbs

class Tests(unittest.TestCase):
 def image(self,size=(1800,2400)):
  output=io.BytesIO();Image.new('RGB',size,'red').save(output,'JPEG');return output.getvalue()
 def test_existing_photo_thumbnail_is_small_cached_and_keeps_original_rows(self):
  with tempfile.TemporaryDirectory() as folder:
   job=PhotoTests().job(folder,1);before=copy.deepcopy(job.items);photo=photos.gallery(job)['photos'][0]
   with patch.object(thumbs,'download',return_value=self.image()) as fetch:
    data=thumbs.thumbnail(job,photo['id']);again=thumbs.thumbnail(job,photo['id'])
   self.assertEqual(data,again);self.assertEqual(fetch.call_count,1)
   with Image.open(io.BytesIO(data)) as image:self.assertEqual(image.size,(320,320));self.assertEqual(image.format,'JPEG')
   self.assertLess(len(data),30_000);self.assertEqual(job.items,before)
   self.assertFalse((job.dir/'color_mapping.json').exists())
   self.assertTrue(photo['thumbnail_url'].startswith('/api/jobs/'));self.assertTrue(photo['url'].startswith('https://img/'))
 def test_parallel_requests_build_same_cached_file_once(self):
  with tempfile.TemporaryDirectory() as folder:
   job=PhotoTests().job(folder,1);pid=photos.gallery(job)['photos'][0]['id']
   with patch.object(thumbs,'download',return_value=self.image()) as fetch:
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(lambda _:thumbs.thumbnail(job,pid),range(6)))
   self.assertEqual(fetch.call_count,1);self.assertEqual(len(set(results)),1)
 def test_unknown_id_cannot_fetch_arbitrary_url(self):
  with tempfile.TemporaryDirectory() as folder:
   job=PhotoTests().job(folder,1)
   with patch.object(thumbs,'download') as fetch:
    for pid in ('https://localhost/secret','../../file.jpg','not-an-image'):
     with self.assertRaises(ValueError):thumbs.thumbnail(job,pid)
   fetch.assert_not_called()
 def test_duplicate_source_across_products_uses_same_preload_address(self):
  with tempfile.TemporaryDirectory() as folder:
   job=PhotoTests().job(folder,2);job.items[6]['unmapped']['image_url']=job.items[0]['unmapped']['image_url']
   gallery=photos.gallery(job);same=[p for p in gallery['photos'] if p['url']==job.items[0]['unmapped']['image_url']]
   self.assertEqual(len(same),2);self.assertEqual(same[0]['thumbnail_url'],same[1]['thumbnail_url'])
 def test_corrupt_source_does_not_leave_a_cached_image(self):
  with tempfile.TemporaryDirectory() as folder:
   job=PhotoTests().job(folder,1);pid=photos.gallery(job)['photos'][0]['id']
   with patch.object(thumbs,'download',return_value=b'not image'):
    with self.assertRaises(OSError):thumbs.thumbnail(job,pid)
   self.assertFalse(list(job.dir.glob('photo-thumbnails/**/*.jpg')))

if __name__=='__main__':unittest.main()
