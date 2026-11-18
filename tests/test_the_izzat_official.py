import sys,unittest
from pathlib import Path
from unittest.mock import patch,call

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import the_izzat_official as c

class Context:
    product_code='NEW-STYLE-12345'
    def __init__(self):self.rows=[];self.failures=[]
    def checkpoint(self):pass
    def stage(self,*args):pass
    def log(self,*args):pass
    def enabled(self,key):return False
    def emit_result(self,row):self.rows.append(row)
    def fail_item(self,error):self.failures.append(error)

class Tests(unittest.TestCase):
    def test_nonstandard_lengths_and_delimiters_query_complete_input(self):
        for code in ['ABC123','NEW-STYLE-12345','LONGER_MODEL_2026','ISRM0BL851_BLL0']:
            with self.subTest(code=code):
                ctx=Context()
                with patch.object(c,'model_ids',return_value=['GC123']) as lookup:
                    self.assertEqual(c.resolve_model(ctx,code.lower()),(code,None,['GC123']))
                    lookup.assert_called_once_with(ctx,code)

    def test_legacy_color_is_split_only_after_full_input_lookup_returns_no_results(self):
        for separator in ['_','-']:
            ctx=Context();raw='ISRM0BL851'+separator+'BLL0'
            with patch.object(c,'model_ids',side_effect=[c.ModelNotFound('0 results'),['GC123']]) as lookup:
                self.assertEqual(c.resolve_model(ctx,raw),('ISRM0BL851','BLL0',['GC123']))
                self.assertEqual(lookup.call_args_list,[call(ctx,raw),call(ctx,'ISRM0BL851')])

    def test_lookup_failure_is_not_reinterpreted_as_format_failure(self):
        ctx=Context()
        with patch.object(c,'model_ids',side_effect=TimeoutError('network timed out')) as lookup:
            with self.assertRaises(TimeoutError):c.resolve_model(ctx,'ISRM0BL851_BLL0')
        lookup.assert_called_once()

    def test_unfamiliar_code_is_reported_missing_only_after_real_lookup(self):
        ctx=Context()
        with patch.object(c,'api',return_value=dict(goodsMdlImgList=[])) as api:
            with self.assertRaisesRegex(c.ModelNotFound,'공식 품번 검색 결과 없음'):
                c.resolve_model(ctx,'NEW-STYLE-12345')
        api.assert_called_once_with(ctx,'/goods/includeGoodsDtlModelNoList',mdlNo='NEW-STYLE-12345')

    def test_blank_input_does_not_send_empty_search(self):
        with patch.object(c,'model_ids') as lookup:
            with self.assertRaisesRegex(ValueError,'비어'):c.resolve_model(Context(),'  ')
            lookup.assert_not_called()

    def test_crawl_reaches_official_lookup_and_emits_nonstandard_model(self):
        ctx=Context();info=dict(name='테스트 상품',color='블랙',color_name='블랙',color_code='BK10',block='')
        with patch.object(c,'model_ids',return_value=['GC123']) as lookup,patch.object(c,'get_html',return_value=(None,c.ORIGIN+'/goods/indexGoodsDetail?goodsId=GC123')),patch.object(c,'identity',return_value=info) as identity,patch.object(c,'collect_regular_price'):
            c.crawl(ctx)
        lookup.assert_called_once_with(ctx,'NEW-STYLE-12345')
        self.assertEqual(identity.call_args.args[-1],'NEW-STYLE-12345')
        self.assertEqual(ctx.rows[0]['product_code'],'NEW-STYLE-12345');self.assertFalse(ctx.failures)

if __name__=='__main__':unittest.main()
