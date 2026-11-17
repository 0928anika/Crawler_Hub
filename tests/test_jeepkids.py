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


class JeepKidsDependentOptionTests(unittest.TestCase):
    def docs(self):return [(n,c.parse((FIX/('KO3TPU905_'+n+'.html')).read_text(encoding='utf-8'))) for n in ['986','985','984']]
    def test_original_stock_sizes_include_soldout_and_empty_select(self):
        for n,doc in self.docs():
            self.assertEqual(c.sizes(doc),'120,130,145,155',n)
        black=self.docs()[0][1]
        self.assertFalse(black.select('select[option_title="사이즈"] option[value]:not([value="*"]):not([value="**"])'))
    def test_detail_includes_exact_product_color_and_size_only(self):
        for n,doc in self.docs():
            urls=c.detail_urls(doc,c.SITE)
            self.assertEqual(len(urls),3,n)
            code=c.identity(doc,'KO3TPU905')['color_code']
            self.assertTrue(any('00_KO3TPU905_'+code+'.jpg' in u for u in urls))
            self.assertTrue(any('01_KO3TPU905_'+code+'.jpg' in u for u in urls))
            self.assertTrue(any('KO3TPU905_size.jpg' in u for u in urls))
            self.assertFalse(any('washing' in u or 'foot.jpg' in u for u in urls))
    def test_wrong_external_host_product_and_color_are_excluded(self):
        doc=self.docs()[0][1];area=doc.select_one('#prdDetail .cont')
        for src in ['https://hollhaus.speedgabia.com/jeep/detail/00_KO3TPU905_LE.jpg','https://hollhaus.speedgabia.com/jeep/detail/00_OTHER0000_BK.jpg','https://evil.example/jeep/detail/00_KO3TPU905_BK.jpg']:
            image=doc.new_tag('img',src=src);area.append(image)
        urls=c.detail_urls(doc,c.SITE);self.assertEqual(len(urls),3)
    def test_detail_has_separate_large_image_limit_and_cache(self):
        url='https://hollhaus.speedgabia.com/jeep/detail/00_KO3TPU905_BK.jpg';cache={url:dict(status='inaccessible',reasons=['size limit'])}
        result=dict(status='accessible',data=b'checked',reasons=[])
        with patch.object(c,'check_public_image',return_value=result) as check:
            self.assertIs(c.public_image(Context(),url,cache,detail=True),result)
            self.assertEqual(check.call_args.kwargs['max_bytes'],64*1024*1024)
            c.public_image(Context(),url,cache,detail=True);self.assertEqual(check.call_count,1)
        self.assertEqual(cache[url]['status'],'inaccessible')
    def test_missing_selected_detail_does_not_succeed_empty(self):
        with self.assertRaisesRegex(ValueError,'상세페이지 수집 실패'):c.detail(Context(),[],{})
    def test_all_three_colors_complete_with_sizes_and_original_detail(self):
        pages={c.SITE+'/product/detail.html?product_no='+n:doc for n,doc in self.docs()}
        with tempfile.TemporaryDirectory() as folder:
            ctx=Context(folder,'KO3TPU905',('size','detail_page'))
            with patch('playwright.sync_api.sync_playwright',return_value=MagicMock()),patch.object(c,'search',return_value=list(pages)),patch.object(c,'visit',side_effect=lambda ctx,page,url:(pages[url],url)),patch.object(c,'public_image',return_value={'status':'accessible'}):c.crawl(ctx)
            self.assertFalse(ctx.failures);self.assertEqual(len(ctx.rows),3)
            self.assertEqual({r['color_code'] for r in ctx.rows},{'BK','LE','NA'})
            for row in ctx.rows:
                self.assertEqual(row['size'],'120,130,145,155');self.assertEqual(row['detail_page'].count('<img'),3);self.assertNotIn('<table',row['detail_page'])


class JeepKidsLogRegressionTests(unittest.TestCase):
    def setup_doc(self):
        return c.parse('''<div class="xans-product-headcategory"><ol><li><a>KIDS</a></li></ol></div>
          <div class="infoArea"><table><tr><th>상품명</th><td>후드집업 와이드 셋업_CG</td></tr>
          <tr><th>색상</th><td><select><option value="*">- [필수] 옵션을 선택해 주세요 -</option>
          <option value="**">-------------------</option><option value="CG" disabled>CG [품절]</option></select></td></tr></table></div>
          <div id="prdDetail"><div class="cont">
          <img ec-data-src="https://hollhaus.speedgabia.com/jeepkids/detail/01_KP4TZU098_CG.jpg">
          <img ec-data-src="https://hollhaus.speedgabia.com/jeepkids/detail/02_KP4TZU098_CG.jpg">
          <img ec-data-src="https://hollhaus.speedgabia.com/jeepkids/detail/KP4TZU098_size.jpg">
          <img ec-data-src="https://hollhaus.speedgabia.com/jeep/detail/KC_detail.jpg">
          <img ec-data-src="https://hollhaus.speedgabia.com/jeep/detail/washing.jpg"></div></div>''')

    def test_option_widget_does_not_pollute_color_name(self):
        row=c.identity(self.setup_doc(),'KP4TZU098')
        self.assertEqual(row['color'],'CG')
        self.assertEqual(row['color_code'],'CG')
        self.assertEqual(row['color_name'],'')
        self.assertEqual(row['product_name'],'후드집업 와이드 셋업')

    def test_jeepkids_detail_path_uses_exact_product_color_and_size(self):
        urls=c.detail_urls(self.setup_doc(),c.SITE)
        self.assertEqual(len(urls),3)
        self.assertTrue(all('/jeepkids/detail/' in url for url in urls))

    def test_official_shared_setup_alias_does_not_change_set_name(self):
        row=c.identity(self.setup_doc(),'KP4TPU098')
        self.assertEqual(row['product_code'],'KP4TPU098')
        self.assertEqual(row['product_name'],'후드집업 와이드 셋업')
        self.assertIsNone(c.identity(self.setup_doc(),'KP4TPU099'))

    def test_suffix_is_not_sufficient_without_matching_official_image_proof(self):
        doc=self.setup_doc()
        doc.select_one('#prdDetail').decompose()
        with self.assertRaises(ValueError):c.identity(doc,'KP4TZU098')

    def test_mismatched_title_and_detail_color_are_rejected(self):
        doc=c.parse(str(self.setup_doc()).replace('셋업_CG','셋업_MG'))
        with self.assertRaises(ValueError):c.identity(doc,'KP4TZU098')

    def test_literal_p_prefix_on_official_size_chart_is_preserved(self):
        doc=self.setup_doc()
        node=doc.select_one('img[ec-data-src$="_size.jpg"]')
        node['ec-data-src']='https://hollhaus.speedgabia.com/jeepkids/detail/pKP4TZU098_size.jpg'
        self.assertIn(node['ec-data-src'],c.detail_urls(doc,c.SITE))

if __name__=='__main__':unittest.main()
