import sys,unittest
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch,call
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'brands')]
import the_izzat_google as c

class Context:
    product_code='NEW-STYLE_12345'
    def __init__(self):self.rows=[];self.failures=[]
    def log(self,*args):pass
    def stage(self,*args):pass
    def checkpoint(self):pass
    def enabled(self,key):return False
    def fail_item(self,message):self.failures.append(message)
    def emit_result(self,row):self.rows.append(row)

def document(code,mdl=None):
    script='var _goodsDtlInfoData = {goodsId: "GC123", goodsNm: "상품"}; this.getGoodsDtlInfoData = function(){};'
    if mdl is not None:script+="invoke('/goods/includeGoodsDtlModelNoList', {mdlNo: '"+mdl+"'});"
    return BeautifulSoup('<script>'+script+'</script><a id="contentGoodsDtlBrandLink" href="/brand/theizzat"></a><table id="contentGoodsDtlGoodsNotifyListContent"><tr><th>제품코드</th><td>'+code+'</td></tr></table>','html.parser')

class Tests(unittest.TestCase):
    def test_varied_models_are_searched_without_rejection_or_early_splitting(self):
        for code in ['SHORT9','NEW-STYLE_12345','ISRM0BL851_BLL0']:
            ctx=Context()
            with patch.object(c,'model_ids',return_value=['GC123']) as lookup:
                self.assertEqual(c.resolve_google_model(ctx,code.lower()),(code,None,['GC123']))
                lookup.assert_called_once_with(ctx,code)

    def test_legacy_color_falls_back_only_after_complete_input_was_searched(self):
        ctx=Context()
        with patch.object(c,'model_ids',side_effect=[c.GoogleModelNotFound('no matching page'),['GC123']]) as lookup:
            self.assertEqual(c.resolve_google_model(ctx,'ISRM0BL851_BLL0'),('ISRM0BL851','BLL0',['GC123']))
            self.assertEqual(lookup.call_args_list,[call(ctx,'ISRM0BL851_BLL0'),call(ctx,'ISRM0BL851')])

    def test_transient_errors_do_not_trigger_suffix_guess(self):
        with patch.object(c,'model_ids',side_effect=TimeoutError('timed out')) as lookup:
            with self.assertRaises(TimeoutError):c.resolve_google_model(Context(),'ISRM0BL851_BLL0')
            lookup.assert_called_once()

    def test_unfamiliar_official_page_code_is_accepted_but_mismatch_rejected(self):
        code='NEW-STYLE_12345'
        doc=document(code,code)
        self.assertEqual(c.page_model_code(doc)[0],code)
        c.verify_candidate_page(Context(),doc,'GC123',code,c.ORIGIN+'/goods/indexGoodsDetail?goodsId=GC123')
        with self.assertRaisesRegex(ValueError,'품번 검증 탈락'):
            c.verify_candidate_page(Context(),doc,'GC123','DIFFERENT','https://pc.babathe.com/')
        with self.assertRaisesRegex(ValueError,'불일치'):c.page_model_code(document(code,'OTHER'))

    def test_crawl_enters_browser_and_search_for_nonstandard_input(self):
        ctx=Context();doc=document(ctx.product_code,ctx.product_code);browser=SimpleNamespace(pages=[])
        info=dict(name='상품',color='블랙',color_name='블랙',color_code='BK10',block='')
        with patch.object(c,'google_browser_session',return_value=nullcontext(browser)) as session,patch.object(c,'model_ids',return_value=['GC123']) as lookup,patch.object(c,'browser_product_html',return_value=(doc,c.ORIGIN+'/goods/indexGoodsDetail?goodsId=GC123')),patch.object(c,'identity',return_value=info),patch.object(c,'collect_regular_price'):
            c.crawl(ctx)
        session.assert_called_once_with(ctx);lookup.assert_called_once_with(ctx,ctx.product_code)
        self.assertEqual(ctx.rows[0]['product_code'],ctx.product_code);self.assertFalse(ctx.failures)

    def test_only_blank_input_is_rejected_before_search(self):
        with patch.object(c,'model_ids') as lookup:
            with self.assertRaisesRegex(ValueError,'비어'):c.resolve_google_model(Context(),'   ')
            lookup.assert_not_called()

if __name__=='__main__':unittest.main()
