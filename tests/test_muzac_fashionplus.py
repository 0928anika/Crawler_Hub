import importlib.util
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from crawler_sdk import CrawlContext


def load(brand_id):
    spec=importlib.util.spec_from_file_location(brand_id,ROOT/'brands'/(brand_id+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module,json.loads((ROOT/'brands'/(brand_id+'.json')).read_text(encoding='utf-8'))


studio,STUDIO=load('muzac_fashionplus')
fi,FI=load('muzac_fashionplus_fi_dj')
FIX=ROOT/'tests/fixtures/muzac'


def soup(name):
    return BeautifulSoup((FIX/name).read_bytes(),'html.parser')


class Context:
    product_code='MN7WJK16'
    enabled=lambda self,key:key=='main_image'
    checkpoint=lambda self:None
    stage=lambda self,*args:None
    log=lambda self,*args:None
    fail_item=lambda self,*args:None


class MuzacTests(unittest.TestCase):
    def test_exact_requested_ids_and_independent_source_modes(self):
        self.assertEqual(STUDIO['id'],'muzac_fashionplus')
        self.assertEqual(FI['id'],'muzac_fashionplus_fi_dj')
        for meta in (STUDIO,FI):
            self.assertEqual(meta['name'],'무자크')
            self.assertEqual(meta['mapping_mode'],'manual')
            self.assertTrue(meta['fields']['product_url']['supported'])
        self.assertIn('스튜디오컷',STUDIO['crawl_source'])
        self.assertIn('FI_DJ',FI['crawl_source'])

    def test_attached_studio_and_shop_examples(self):
        for x,expected in ((90,False),(267,False),(443,True),(620,True),(797,False)):
            with self.subTest(x=x):
                self.assertEqual(studio.studio_background(Image.open(FIX/('reference_'+str(x)+'.png')))[0],expected)

    def test_plain_wall_without_logo_is_rejected_before_product_request(self):
        ctx=Context()
        with patch.object(studio,'search',return_value=[('https://www.fashionplus.co.kr/goods/detail/432064603','','https://unused/thumb')]), \
             patch.object(studio,'image_get',return_value=Image.open(FIX/'reference_267.png')), \
             patch.object(studio,'soup_get') as forbidden:
            with self.assertRaises(studio.CollectionError):studio.crawl(ctx)
            forbidden.assert_not_called()

    def test_fi_accepts_only_actual_labelled_daejeon_address(self):
        accepted,addresses,marker=fi.seller_identity(soup('fi_product.html'))
        self.assertTrue(accepted)
        self.assertEqual(marker,'42970')
        self.assertIn('대전 동구 은어송로 72 패션아일랜드 1층 무자크 매장',addresses)
        self.assertFalse(fi.seller_identity(soup('studio_product.html'))[0])
        for text in ('서울 패션아일랜드','대전 다른 매장',''):
            page=BeautifulSoup('<tr><th>반품지주소</th><td>'+text+'</td></tr>','html.parser')
            self.assertFalse(fi.seller_identity(page)[0])
        self.assertFalse(fi.seller_identity(BeautifulSoup('<footer>대전 패션아일랜드</footer>','html.parser'))[0])

    def test_fi_rejects_studio_vendor_without_downloading_images_or_options(self):
        ctx=Context()
        with patch.object(fi,'search',return_value=[('https://www.fashionplus.co.kr/goods/detail/432064603','','https://unused/thumb')]), \
             patch.object(fi,'soup_get',return_value=soup('studio_product.html')), \
             patch.object(fi,'image_get') as images,patch.object(fi,'options') as options:
            with self.assertRaises(fi.CollectionError):fi.crawl(ctx)
            images.assert_not_called();options.assert_not_called()

    def test_studio_real_sdk_optional_price_and_original_detail(self):
        link=studio.BASE+'/goods/detail/432064603'
        for price_on,regular_on,detail_on in itertools.product((False,True),repeat=3):
            with self.subTest(price=price_on,regular=regular_on,detail=detail_on),tempfile.TemporaryDirectory() as tmp:
                events=[]
                selected=['main_image']+(['price'] if price_on else [])+(['regular_price'] if regular_on else [])+(['detail_page'] if detail_on else [])
                ctx=CrawlContext(Path(tmp)/'input.xlsx',Path(tmp)/'result.xlsx',0,selected,STUDIO['output_headers'],lambda:None,
                    lambda kind,**data:events.append((kind,data)),input_item={'품번':'MN7WJK16'},mapping_mode='manual')
                detail_calls=[]
                def page(ctx,url):
                    if url.endswith('/description-iframe'):
                        detail_calls.append(url);return soup('studio_body.html')
                    return soup('studio_product.html')
                calls=[];reader=studio.read_price
                def read(soup,regular=False):calls.append(regular);return reader(soup,regular)
                with patch.object(studio,'search',return_value=[(link,'','https://unused/thumb')]), \
                     patch.object(studio,'soup_get',side_effect=page), \
                     patch.object(studio,'image_get',return_value=Image.open(FIX/'reference_443.png').resize((800,1072))), \
                     patch.object(studio,'fetch',return_value=(FIX/'studio_options.json').read_bytes()), \
                     patch.object(studio,'read_price',side_effect=read):
                    studio.crawl(ctx);ctx.finish()
                rows=[v for kind,v in events if kind=='row' and not v.get('header')]
                self.assertEqual(len(rows),4)
                self.assertEqual(calls,([False] if price_on else [])+([True] if regular_on else []))
                self.assertEqual(bool(detail_calls),detail_on)
                row=dict(zip(ctx.headers,rows[0]['row']))
                self.assertEqual(row['상품 링크'],link)
                if detail_on:
                    self.assertIn('/muzak/N7/JPG/MN7WJK16.jpg',row['상세페이지 HTML'])
                    self.assertNotIn('/notice/',row['상세페이지 HTML'])
                    self.assertNotIn('BNP-delivery',row['상세페이지 HTML'])
                else:self.assertFalse(row['상세페이지 HTML'])

    def test_fi_original_gallery_and_mixed_shipping_notes(self):
        for fixture,code in [('fi_body.html','MQ8WJK03'),('fi_mixed_notes.html','MO8WKT04')]:
            with self.subTest(fixture=fixture):
                requests=[]
                class C(Context):
                    enabled=lambda self,key:True
                def image(ctx,url):requests.append(url);return Image.new('RGB',(1000,1000),'gray')
                with patch.object(fi,'soup_get',return_value=soup(fixture)),patch.object(fi,'image_get',side_effect=image):
                    photos,detail=fi.collect_product_images(C(),soup('fi_product.html'),fi.BASE+'/goods/detail/444317879',code)
                self.assertGreaterEqual(len(photos),6)
                markup=''.join(detail)
                self.assertIn('가슴단면',markup)
                self.assertNotIn('배송',markup);self.assertNotIn('환불',markup)
                self.assertNotIn('042-271',markup);self.assertNotIn('패션아일랜드 가오점',markup)
                self.assertNotIn('/md_image/',markup)
                self.assertNotIn('<table',markup)
                self.assertTrue(all('/product_img/' in u for u in requests))


if __name__=='__main__':unittest.main()
