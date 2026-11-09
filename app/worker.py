"""Compatibility bridge: existing crawlers remain independently replaceable."""
import json
import runpy
import sys
import time
import urllib.request
import openpyxl
from pathlib import Path
from openpyxl.worksheet.worksheet import Worksheet

PREFIX = '@@PORTAL@@'
def emit(kind, **data):
    print(PREFIX + json.dumps(dict(kind=kind, **data), ensure_ascii=False, default=str), flush=True)

script, input_file, output_file, control = sys.argv[1:5]
options=json.loads(sys.argv[5]) if len(sys.argv)>5 else {}
def checkpoint():
    paused = False
    while Path(control).read_text(encoding='utf-8') == 'pause':
        if not paused:
            emit('paused')
            paused = True
        time.sleep(.15)
    if paused:
        emit('resumed')

old_open = urllib.request.OpenerDirector.open
def network(self, fullurl, data=None, timeout=30):
    checkpoint()
    emit('stage', message='사이트 응답 확인 중')
    return old_open(self, fullurl, data, timeout)
urllib.request.OpenerDirector.open = network
old_append = Worksheet.append
def append(self, values):
    checkpoint()
    result = old_append(self, values)
    if not (options.get('interface')=='portal_v1' or options.get('level')==2):
        emit('row', sheet=self.title, row=[c.value for c in self[self.max_row]], header=self.max_row == 1)
    return result
Worksheet.append = append
old_load=openpyxl.load_workbook
def filtered_input(filename,*args,**kwargs):
    if (options.get('interface')=='portal_v1' or options.get('level')==2) or 'index' not in options or not isinstance(filename,(str,Path)) or Path(filename).resolve()!=Path(input_file).resolve():
        return old_load(filename,*args,**kwargs)
    if 'input_row' in options:
        book=openpyxl.Workbook();old_append(book.active,options['input_headers']);old_append(book.active,options['input_row']);return book
    source=old_load(filename,read_only=True,data_only=True)
    try:
        sheet=source.active;book=openpyxl.Workbook();book.active.title=sheet.title
        old_append(book.active,next(sheet.iter_rows(min_row=1,max_row=1,values_only=True)))
        n=options['index']+2
        old_append(book.active,next(sheet.iter_rows(min_row=n,max_row=n,values_only=True)))
        return book
    finally:source.close()
openpyxl.load_workbook=filtered_input
sys.path.insert(0, str(Path(script).parent))
if (options.get('interface')=='portal_v1' or options.get('level')==2):
    from crawler_sdk import CrawlContext
    module=runpy.run_path(script,run_name='portal_crawler')
    if module.get('PORTAL_API_VERSION') not in (1,2) or not callable(module.get('crawl')):
        raise ValueError('새 크롤러는 PORTAL_API_VERSION=1와 crawl(ctx)가 필요합니다.')
    context=CrawlContext(input_file,output_file,options['index'],options['selected'],options['headers'],checkpoint,emit,
        dict(zip(options['input_headers'],options['input_row'])) if 'input_row' in options else None,mapping_mode=options.get('mapping_mode','automatic'),image_mode=options.get('image_mode','url'),asset_dir=options.get('asset_dir'))
    module['crawl'](context)
    context.finish()
else:
    sys.argv = [script, input_file, '-o', output_file]
    runpy.run_path(script, run_name='__main__')
