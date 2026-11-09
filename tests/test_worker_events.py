"""Exercise the actual worker pipe with deliberately fragmented stdout writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class WorkerEventsTest(unittest.TestCase):
    def test_parallel_events_remain_one_json_per_line(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            script=folder/'parallel.py'
            script.write_text('''PORTAL_API_VERSION = 1
def crawl(ctx):
    import sys, threading, time
    original=sys.stdout
    class Fragmented:
        def write(self, text):
            for offset in range(0,len(text),9):
                original.write(text[offset:offset+9])
                time.sleep(.00001)
        def flush(self): original.flush()
    sys.stdout=Fragmented()
    def send(index):
        for step in range(10): ctx.stage(str(index)+':'+str(step))
    threads=[threading.Thread(target=send,args=(i,)) for i in range(12)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    sys.stdout=original
    ctx.emit_result(dict(product_code='TEST',product_name='Test',color='Black',product_url='https://example.com/product'))
''',encoding='utf-8')
            control=folder/'control.txt';control.write_text('run',encoding='utf-8')
            options=dict(index=0,selected=[],headers=['품번','상품명','색상','상품 링크'],interface='portal_v1',input_headers=['품번'],input_row=['TEST'])
            run=subprocess.run([sys.executable,'-B',str(ROOT/'app/worker.py'),str(script),str(folder/'input.xlsx'),str(folder/'result.xlsx'),str(control),json.dumps(options)],
                cwd=folder,env=dict(os.environ,PYTHONIOENCODING='utf-8'),capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            events=[json.loads(line[len('@@PORTAL@@'):]) for line in run.stdout.splitlines() if line.startswith('@@PORTAL@@')]
            self.assertEqual(sum(e['kind']=='stage' for e in events),120)
            self.assertEqual(sum(e['kind']=='row' and not e.get('header') for e in events),1)


if __name__=='__main__': unittest.main()
