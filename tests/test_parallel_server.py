import http.client
import json
import sys
import threading
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import server

class FakeJob:
    released=None
    def __init__(self,brand,headers,rows,demo,selected,name):
        self.id=uuid.uuid4().hex;self.brand=brand;self.state='running';self.name=name or brand['name'];self.checkpoints=0;self.control=SimpleNamespace(write_text=lambda *a,**kw:None)
    def run(self):self.released.wait(10);self.state='completed'
    def summary(self):return dict(id=self.id,name=self.name,state=self.state,brand=self.brand['name'])
    def checkpoint(self):self.checkpoints+=1
    def prepare_rerun(self,action):self.state='running'

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.catalog={}
        for i in range(7):
            b=dict(id='brand'+str(i),name='브랜드'+str(i),available=True,mapping_mode='automatic',execution_resources=dict(sites=['site'+str(i)],browser=False,known=True))
            self.catalog[b['id']]=b
        self.released=threading.Event();FakeJob.released=self.released
        self.jobs={}
        self.patches=[patch.object(server,'JOBS',self.jobs),patch.object(server,'Job',FakeJob),patch.object(server,'get_brand',side_effect=lambda key:self.catalog[key]),patch.object(server,'brands',side_effect=lambda:list(self.catalog.values())),patch.object(server,'validate_input',side_effect=lambda data,brand:(dict(valid=True,selected=[]),['품번'],[['TEST']])),patch.object(server,'save_settings')]
        for p in self.patches:p.start()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.released.set()
        for job in self.jobs.values():
            if getattr(job,'thread',None):job.thread.join(2)
        self.http.shutdown();self.http.server_close();self.thread.join(2)
        for p in reversed(self.patches):p.stop()
    def call(self,path,data=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=5)
        try:
            connection.request('POST' if data is not None else 'GET',path,body=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-Portal-Token':server.TOKEN})
            response=connection.getresponse();return response.status,json.loads(response.read())
        finally:connection.close()
    def start(self,key):return self.call('/api/jobs',dict(brand=key))

    def test_atomic_four_job_limit_under_simultaneous_requests(self):
        with ThreadPoolExecutor(max_workers=7) as pool:results=list(pool.map(self.start,self.catalog))
        self.assertEqual(sum(status==200 for status,_ in results),4)
        self.assertEqual(sum(status==400 for status,_ in results),3)
        status,view=self.call('/api/concurrency')
        self.assertEqual(len(view['active']),4);self.assertEqual(view['limit'],4)
        self.assertTrue(all(not item['allowed'] for item in view['availability'].values()))

    def test_same_site_and_browser_block_while_other_sites_run(self):
        self.catalog['brand0']['execution_resources']['browser']=True
        self.catalog['brand1']['execution_resources']['browser']=True
        self.catalog['brand2']['execution_resources']['sites']=['site0']
        self.assertEqual(self.start('brand0')[0],200)
        self.assertIn('브라우저',self.start('brand1')[1]['error'])
        self.assertIn('같은 사이트',self.start('brand2')[1]['error'])
        self.assertEqual(self.start('brand3')[0],200)
        view=self.call('/api/concurrency')[1]
        self.assertFalse(view['availability']['brand1']['allowed'])
        self.assertTrue(view['availability']['brand4']['allowed'])

    def test_paused_and_stopping_retain_reservations_completion_releases(self):
        status,result=self.start('brand0');job=self.jobs[result['id']]
        for state in ['paused','pausing','stopping','saving']:
            job.state=state
            self.assertIn('같은 사이트',self.start('brand0')[1]['error'])
        job.state='completed'
        self.assertEqual(self.start('brand0')[0],200)

    def test_retry_uses_frozen_resource_reservation(self):
        self.catalog['brand0']['execution_resources']['browser']=True
        self.assertEqual(self.start('brand0')[0],200)
        old=FakeJob(dict(self.catalog['brand1'],execution_resources=dict(sites=['old-site'],browser=True,known=True)),[],[],False,[],None)
        old.state='completed';self.jobs[old.id]=old
        status,result=self.call('/api/jobs/'+old.id+'/retry-failed',{})
        self.assertEqual(status,400);self.assertIn('브라우저',result['error']);self.assertEqual(old.state,'completed')

    def test_two_requests_for_same_site_cannot_race_past_guard(self):
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.start('brand0'),range(2)))
        self.assertEqual(sorted(status for status,_ in results),[200,400])
        self.assertEqual(len(self.jobs),1)

    def test_saved_job_is_classified_from_frozen_script_before_live_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            engine=Path(folder);(engine/'brands').mkdir()
            (engine/'brands'/'old.py').write_text("SITE='https://old-site.co.kr'\nfrom playwright.sync_api import sync_playwright\n",encoding='utf-8')
            job=SimpleNamespace(brand=self.catalog['brand0'],engine_root=engine,engine_script='brands/old.py')
            resources=server.job_resources(job)
            self.assertEqual(resources['sites'],['old-site.co.kr']);self.assertTrue(resources['browser'])

if __name__=='__main__':unittest.main()
