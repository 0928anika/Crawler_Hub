import http.client,json,sys,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'app'))
import server
class ResultIdTests(unittest.TestCase):
 def setUp(self):
  self.rows=[dict(id='0',task_id='12',code='ANPM27A17',raw={'품번':'ANPM27A17'}),dict(id='4',task_id='13',code='ANPM27A18',raw={'품번':'ANPM27A18'})]
  originals=[dict(id=str(i),task_id='12' if i<4 else '13',code='ANPM27A17' if i<4 else 'ANPM27A18') for i in range(7)]
  self.job=SimpleNamespace(items=originals,brand=dict(mapping_mode='manual',group_manual_pending=True),selected=[],data_lock=threading.RLock(),queue=[dict(id='12',status='SUCCESS'),dict(id='13',status='SUCCESS')])
 def test_sparse_id_is_not_array_offset(self):
  self.assertIs(server.find_result_item(self.job,self.rows,'4',True),self.rows[1])
 def test_hidden_photo_id_resolves_to_correct_model(self):
  self.assertIs(server.find_result_item(self.job,self.rows,'1',True),self.rows[0]);self.assertIs(server.find_result_item(self.job,self.rows,'5',True),self.rows[1])
 def test_unknown_or_finalized_hidden_id_not_substituted(self):
  self.assertIsNone(server.find_result_item(self.job,self.rows,'999',True));self.assertIsNone(server.find_result_item(self.job,self.rows,'5',False))
 def test_failed_collected_row_can_be_opened_even_if_hidden_from_mapping(self):
  self.job.queue[0]['status']='FAILED'
  self.assertIs(server.find_result_item(self.job,[],'1',True),self.job.items[1])
  self.assertIsNone(server.find_result_item(self.job,[],'5',True))
 def test_http_detail_and_raw_for_grouped_ids(self):
  with patch.object(server,'job_for',return_value=self.job),patch.object(server.color_mapping,'visible_items',return_value=self.rows),patch.object(server.color_mapping,'snapshot',return_value=dict(finalized=False)):
   hub=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=hub.serve_forever,daemon=True);thread.start()
   try:
    conn=http.client.HTTPConnection('127.0.0.1',hub.server_port,timeout=5)
    for suffix,status,code in [('0',200,'ANPM27A17'),('1',200,'ANPM27A17'),('4',200,'ANPM27A18'),('5',200,'ANPM27A18'),('4/raw',200,'ANPM27A18'),('999',404,None)]:
     conn.request('GET','/api/jobs/'+('a'*32)+'/items/'+suffix);r=conn.getresponse();body=json.loads(r.read());self.assertEqual(r.status,status,body)
     if code:self.assertEqual(body.get('code',body.get('품번')),code)
    conn.close()
   finally:hub.shutdown();hub.server_close();thread.join(timeout=5)
if __name__=='__main__':unittest.main()
