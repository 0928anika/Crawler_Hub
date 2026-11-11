from pathlib import Path
from unittest.mock import patch
from bs4 import BeautifulSoup
import importlib.util
import unittest
ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/moimoln'
spec=importlib.util.spec_from_file_location('moimoln_test',ROOT/'brands/moimoln_official.py')
c=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

FIELDS=['size','price','regular_price','category','main_image','additional_images','detail_page']
class Context:
 def __init__(self,code,fields):
  self.product_code=code;self.fields=set(fields);self.rows=[];self.failures=[];self.logs=[];self.output_dir=Path('.')
 def enabled(self,k):return k in self.fields
 def checkpoint(self):return None
 def stage(self,m):self.logs.append('STAGE '+m)
 def log(self,m,level='INFO'):self.logs.append(level+' '+m)
 def fail_item(self,m):self.failures.append(m)
 def emit_result(self,r):self.rows.append(dict(r))

DOCS={'BE':BeautifulSoup((FIX/'BE.html').read_bytes(),'html.parser'),'IV':BeautifulSoup((FIX/'IV.html').read_bytes(),'html.parser')}
def local_product(ctx,url,base):
 if base!='M264DMB07P':raise c.LookupFailure('missing full code')
 color=c.parse_qs(c.urlsplit(url).query).get('colorCd',['BE'])[0]
 return DOCS[color],'https://www.style24.com/goods/detail/form?goodsCd='+base+'&colorCd='+color,color

def forbidden(*a,**kw):raise AssertionError('unselected operation')

def offline():
 optional=['sizes','sale_price','regular_price','category','gallery','detail']
 with patch.object(c,'load_product',local_product),patch.multiple(c,**{k:forbidden for k in optional}):
  for code,count in [('M264DMB07P',2),('M264DMB07P_BE',1),('M264DMB07P-BE',1),('m264dmb07p_iv',1)]:
   ctx=Context(code,[]);c.crawl(ctx);assert len(ctx.rows)==count and not ctx.failures
   assert all(r['product_name']=='몽글후드덕다운점퍼' and r['product_url'].startswith('https://www.style24.com/goods/detail/form?') for r in ctx.rows)
   assert all(set(r)=={'product_code','product_name','color','color_name','color_code','product_url'} for r in ctx.rows)
 with patch.object(c,'load_product',local_product):
  for fields in [[],['price'],['regular_price'],['price','regular_price']]:
   ctx=Context('M264DMB07P',fields);c.crawl(ctx)
   assert all(('price' in r)==('price' in fields) and ('regular_price' in r)==('regular_price' in fields) for r in ctx.rows)
   for r in ctx.rows:
    if 'price' in fields:assert r['price']==143100
    if 'regular_price' in fields:assert r['regular_price']==159000
 for value in [None,'0','not a price']:
  docs={k:BeautifulSoup(str(d),'html.parser') for k,d in DOCS.items()}
  for doc in docs.values():
   node=doc.select_one('.org_price em')
   if value is None:node.decompose()
   else:node.string=value
  def product(ctx,url,base):
   d,u,col=local_product(ctx,url,base);return docs[col],u,col
  with patch.object(c,'load_product',product):
   ctx=Context('M264DMB07P',['regular_price']);c.crawl(ctx);assert all(r['regular_price'] is None for r in ctx.rows) and not ctx.failures
 with patch.object(c,'money',side_effect=OSError('normal price source unavailable')):
  assert c.regular_price(Context('',[]),DOCS['BE']) is None
 d=BeautifulSoup(str(DOCS['BE']),'html.parser');d.select_one('.descript_box .name').string='몽글후드덕다운점퍼 [26 겨울]'
 assert c.product_name(d,'M264DMB07P')=='몽글후드덕다운점퍼'
 d.select_one('.opt_size input')['disabled']='disabled';assert c.sizes(d,'BE').startswith('090,')
 requested=[]
 def info(ctx,url,cache,gallery=False):requested.append(url);return ('JPEG',1080,1404,False,url,'')
 with patch.object(c,'image_info',info):
  html=c.detail(Context('',[]),DOCS['BE'],'https://www.style24.com','M264DMB07P','BE',{})
  out=BeautifulSoup(html,'html.parser')
  source=[(c.text(t.th),c.text(t.td)) for t in DOCS['BE'].select('.required_box .area_infotbl')[0].select('table tr')]
  target=[(c.text(t.th),c.text(t.td)) for t in out.select('table')[-1].select('tr')]
  assert len(source)==14 and source==target
  assert all('M264DMB07P_BE_' in x['src'] for x in out.select('img'))
  assert not any('/Upload/editor/' in u or '_J' in u or '_F' in u or '_G' in u or '/contents/' in u for u in requested)
  main,extra=c.gallery(Context('',[]),DOCS['BE'],'https://www.style24.com','M264DMB07P','BE',{})
  assert main and len(extra.split(','))==5 and main not in extra.split(',')
  assert 'moigift' not in html and '배너' not in html
  missing=BeautifulSoup(str(DOCS['BE']),'html.parser');missing.select_one('.pd-core__size').decompose();missing.select_one('.required_box .area_infotbl').decompose()
  assert '상품정보고시' not in c.detail(Context('',[]),missing,'https://www.style24.com','M264DMB07P','BE',{})
 def fail_iv(ctx,url,base):
  if 'colorCd=IV' in url:raise c.CrawlError('IV network failure')
  return local_product(ctx,url,base)
 with patch.object(c,'load_product',fail_iv):
  ctx=Context('M264DMB07P',[])
  try:c.crawl(ctx)
  except c.CrawlError:assert len(ctx.rows)==1 and ctx.failures
  else:raise AssertionError('partial failure swallowed')
 print('OFFLINE PASS: suffixes/all colors, disabled loaders, name cleanup, price combinations, missing/zero/malformed/error regular price, 14 notice rows, excluded sections requested 0 times, absent tables, partial failure')



import io
from PIL import Image,ImageDraw
def encoded(size=(800,800),fmt='JPEG',collage=False):
 im=Image.new('RGB',size,(80,100,120))
 if collage:ImageDraw.Draw(im).rectangle((0,350,800,450),fill='white')
 out=io.BytesIO();im.save(out,format=fmt);return out.getvalue()

url='https://image.style24.com/Upload/ProductImage/test/M264DMB07P_BE_01.jpg'
def run(data,cuts=('01',)):
 calls=[]
 def fake_fetch(ctx,address,limit):
  calls.append(address)
  assert limit==10000000
  if len(data)>limit:raise c.CrawlError('response too large')
  return data,address,'image/test'
 doc=BeautifulSoup('<div class="pd-core__color-item">'+''.join('<img data-thumbnail="goodsDetailOptionWeb" src="'+url.replace('_01.','_'+cut+'.')+'">' for cut in cuts)+'</div>','html.parser')
 with patch.object(c,'fetch',fake_fetch):
  main,extra=c.gallery(Context('',[]),doc,c.SITE,'M264DMB07P','BE',{})
 return main,extra,calls


class MoimolnTests(unittest.TestCase):
 def test_official_parsing_and_optional_contract(self):
  offline()
 def test_image_policy_and_printed_garment(self):
  for fmt in ['JPEG','PNG','WEBP']:
   main,extra,_=run(encoded(fmt=fmt));assert main and len(extra.split(','))==5
  for data in [encoded((300,300)),encoded((800,2400)),encoded(fmt='GIF'),b'broken image',encoded(collage=True),b'x'*10000001]:
   assert not run(data)[0]
  assert not run(encoded(),('I1',))[2]
  assert not run(encoded(),())[0]
  assert run(encoded(),('01','02'))[0]
  # Live ivory garment contains small prints. It must pass without a hash allowlist.
  with Image.open(FIX/'iv_main.jpg') as im:assert not c.content_signal(im)
  print('IMAGE PASS: JPEG/PNG/static WebP, small/oversize/corrupt/GIF/tall/collage rejection, measurement-role not requested, zero/one/multiple candidates, printed ivory garment')

if __name__=='__main__':unittest.main()
