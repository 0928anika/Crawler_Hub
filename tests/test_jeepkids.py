import io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import jeepkids_official as c
FIX=ROOT/'tests/fixtures/jeepkids'


class Context:
    def __init__(self,folder='.',code='KR3TPU051',fields=()):
        self.output_dir=Path(folder);self.product_code=code;self.fields=fields
        self.rows=[];self.failures=[];self.logs=[]
    def checkpoint(self):return None
    def enabled(self,key):return key in self.fields
    def stage(self,message):return None
    def log(self,message,level='INFO'):self.logs.append((level,message))
    def emit_result(self,row):self.rows.append(row)
    def fail_item(self,message):self.failures.append(message)


class JeepKidsTests(unittest.TestCase):
    def setUp(self):
        self.doc=c.parse((FIX/'KR3TPU051_0.html').read_text(encoding='utf-8'))

    def test_identity_and_original_name_are_preserved(self):
        row=c.identity(self.doc,'KR3TPU051')
        self.assertEqual(row['product_name'],'융기모 트레이닝팬츠')
        self.assertEqual(row['color_code'],'KH')
        self.assertEqual(row['color'],'KH')
        self.assertEqual(row['color_name'],'')
        self.assertIn('KR3TPU051KH',row['원본상품명'])
        self.assertIsNone(c.identity(self.doc,'KR3DPU535'))
        wrong=c.parse(str(self.doc).replace('KIDS','WOMEN'))
        with self.assertRaises(ValueError):c.identity(wrong,'KR3TPU051')

    def test_real_color_name_is_used_when_a_labelled_field_exists(self):
        doc=c.parse(str(self.doc))
        row=c.parse('<table><tr><th>색상</th><td>카키</td></tr></table>').find('tr')
        doc.select_one('.infoArea table').append(row)
        record=c.identity(doc,'KR3TPU051')
        self.assertEqual(record['color_name'],'카키')
        self.assertEqual(record['color'],'카키')
        self.assertEqual(record['color_code'],'KH')

    def test_big_original_comes_from_matching_product_group(self):
        raw=c.field(self.doc,'상품명')
        urls=c.gallery_urls(self.doc,c.SITE,raw)
        self.assertEqual(urls,['https://jeepwear.co.kr/web/product/big/202610/f7537006117e0dc5dbfed437f5a2f781.jpg'])
        self.assertNotIn('a5df0bfd',urls[0])
        self.assertFalse(c.gallery_urls(self.doc,c.SITE,'different product'))

    def test_sold_out_colors_use_matching_page_og_image_and_keep_sizes(self):
        for filename,base,color,count in [('KR3TSU023_0.html','KR3TSU023','IV',4),('KR3TSU023_1.html','KR3TSU023','BK',5),('KR3TSU023_2.html','KR3TSU023','WH',5),('KR4TSU824_2.html','KR4TSU824','YE',5)]:
            with self.subTest(filename=filename):
                doc=c.parse((FIX/filename).read_text(encoding='utf-8'))
                raw=c.field(doc,'상품명')
                self.assertEqual(c.product_groups(doc,raw)[0]['hasVariant'],[])
                self.assertEqual(c.identity(doc,base)['color_code'],color)
                urls=c.gallery_urls(doc,c.SITE,raw)
                self.assertEqual(urls,[doc.find('meta',property='og:image')['content']])
                sizes=c.sizes(doc)
                self.assertEqual(len(sizes.split(',')),count)
                self.assertNotIn('품절',sizes)
                self.assertFalse(c.gallery_urls(doc,c.SITE,'different product'))
                doc.find('meta',property='og:image')['content']='https://other.example/web/product/big/wrong.jpg'
                self.assertFalse(c.gallery_urls(doc,c.SITE,raw))

    def test_explicit_jeepkids_title_in_new_arrivals_category(self):
        doc=c.parse((FIX/'KR4JPU403_0.html').read_text(encoding='utf-8'))
        self.assertEqual(c.categories(doc),['26FW NEW'])
        record=c.identity(doc,'KR4JPU403')
        self.assertEqual(record['product_name'],'JXP 다운점퍼')
        self.assertEqual(record['color_code'],'BK')
        without_proof=c.parse(str(doc).replace('JEEPKIDS JXP','JXP'))
        with self.assertRaises(ValueError):c.identity(without_proof,'KR4JPU403')

    def test_failure_in_one_color_keeps_completed_other_color(self):
        black=c.parse((FIX/'KR3TPU051_1.html').read_text(encoding='utf-8'))
        pages={'https://jeepwear.co.kr/kh':self.doc,'https://jeepwear.co.kr/bk':black}
        with tempfile.TemporaryDirectory() as folder:
            ctx=Context(folder,fields=('main_image',))
            with patch('playwright.sync_api.sync_playwright',return_value=MagicMock()),patch.object(c,'search',return_value=list(pages)),patch.object(c,'visit',side_effect=lambda ctx,page,url:(pages[url],url)),patch.object(c,'gallery',side_effect=[ValueError('first color failed'),('https://jeepwear.co.kr/valid.jpg',[],'')]):
                with self.assertRaises(RuntimeError):c.crawl(ctx)
            self.assertEqual(len(ctx.rows),1)
            self.assertEqual(ctx.rows[0]['color_code'],'BK')
            self.assertTrue(ctx.failures)

    def test_sizes_include_disabled_options_and_category_is_official(self):
        self.doc.select_one('select option[value="P0000HWN000A"]')['disabled']=''
        self.assertEqual(c.sizes(self.doc),'130,145,155,165')
        self.assertEqual(c.categories(self.doc),['KIDS','BOTTOM'])
        self.assertEqual(c.price(Context(),self.doc),85000)

    def test_all_colors_suffixes_and_optional_work(self):
        black=c.parse((FIX/'KR3TPU051_1.html').read_text(encoding='utf-8'))
        pages={'https://jeepwear.co.kr/kh':self.doc,'https://jeepwear.co.kr/bk':black}
        for code,count in [('kr3tpu051',2),('KR3TPU051KH',1),('KR3TPU051_KH',1),('KR3TPU051-BK',1)]:
            for fields in ((),('price',),('regular_price',),('price','regular_price')):
                with self.subTest(code=code,fields=fields),tempfile.TemporaryDirectory() as folder:
                    ctx=Context(folder,code,fields)
                    with patch('playwright.sync_api.sync_playwright',return_value=MagicMock()), \
                         patch.object(c,'search',return_value=list(pages)),patch.object(c,'visit',side_effect=lambda ctx,page,url:(pages[url],url)), \
                         patch.object(c,'gallery_urls',side_effect=AssertionError('unselected gallery')), \
                         patch.object(c,'detail_urls',side_effect=AssertionError('unselected detail')), \
                         patch.object(c,'price',wraps=c.price) as price:
                        c.crawl(ctx)
                    self.assertEqual(len(ctx.rows),count)
                    self.assertFalse(ctx.failures)
                    self.assertEqual(price.call_count,count if 'price' in fields else 0)
                    for row in ctx.rows:
                        self.assertEqual('price' in row,'price' in fields)
                        self.assertEqual('regular_price' in row,'regular_price' in fields)
                        if 'regular_price' in fields:self.assertIsNone(row['regular_price'])
                        self.assertIn(row['product_url'],pages)

    def test_detail_keeps_all_original_images_without_tables(self):
        urls=c.detail_urls(self.doc,c.SITE)
        self.assertEqual(len(urls),2)
        self.assertTrue(all('/web/upload/NNEditor/' in u for u in urls))
        with patch.object(c,'public_image',return_value={'status':'accessible'}):
            markup=c.detail(Context(),urls,{})
        self.assertEqual(markup.count('<img'),2)
        self.assertNotIn('<table',markup)
        for url in urls:self.assertIn(url,markup)

    def test_image_slots_use_real_file_checks(self):
        url=c.gallery_urls(self.doc,c.SITE,c.field(self.doc,'상품명'))[0]
        for size,expected in [((721,865),True),((100,100),False),((600,2000),False)]:
            data=io.BytesIO();Image.new('RGB',size,'white').save(data,format='WEBP')
            response=dict(status='accessible',data=data.getvalue(),width=size[0],height=size[1],format='WEBP')
            with patch.object(c,'public_image',return_value=response):
                main,extras,_=c.gallery(Context(),[url],'KH',{})
            self.assertEqual(bool(main),expected)
            self.assertEqual(len(extras),5 if expected else 0)

    def test_regular_price_is_disabled_in_brand_definition(self):
        meta=json.loads((ROOT/'brands/jeepkids_official.json').read_text(encoding='utf-8'))
        self.assertFalse(meta['capabilities']['regular_price']['supported'])
        self.assertFalse(meta['fields']['regular_price']['supported'])
        self.assertEqual(meta['capabilities']['regular_price']['status'],'unsupported')


if __name__=='__main__':unittest.main()
