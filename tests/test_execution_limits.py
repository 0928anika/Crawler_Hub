import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from execution_limits import resource_profile,blocked_reason,site_key
from registry import brands

def resource(site,browser=False):return dict(sites=[site],browser=browser,known=True)
def active(index,site,browser=False):return dict(id=str(index),name='작업'+str(index),resources=resource(site,browser))

class LimitsTests(unittest.TestCase):
    def test_four_distinct_sites_and_fifth_rejected(self):
        running=[]
        for index in range(4):
            self.assertEqual(blocked_reason(resource('site'+str(index)),running),'')
            running.append(active(index,'site'+str(index)))
        self.assertIn('최대 4개',blocked_reason(resource('fifth'),running))

    def test_same_site_browser_exclusion_and_http_coexistence(self):
        running=[active(1,'a',True)]
        self.assertIn('같은 사이트',blocked_reason(resource('a'),running))
        self.assertIn('브라우저',blocked_reason(resource('b',True),running))
        self.assertEqual(blocked_reason(resource('b'),running),'')
        self.assertEqual(blocked_reason(resource('a',True),running,exclude_id='1'),'')

    def test_unknown_resources_are_single_job_only(self):
        unknown=dict(sites=[],browser=True,known=False)
        self.assertEqual(blocked_reason(unknown,[]),'')
        self.assertIn('단독',blocked_reason(unknown,[active(1,'a')]))
        self.assertIn('단독',blocked_reason(resource('a'),[dict(id='1',name='unknown',resources=unknown)]))

    def test_domains_aliases_and_separate_cafe24_stores(self):
        self.assertEqual(site_key('img-studio.elandmall.com'),site_key('www.elandmall.co.kr'))
        self.assertEqual(site_key('item.elandrs.com'),site_key('www.elandmall.co.kr'))
        self.assertEqual(site_key('smartstore.naver.com'),site_key('brand.naver.com'))
        self.assertEqual(site_key('m.theredfacemall.co.kr'),site_key('www.theredfacemall.co.kr'))
        self.assertNotEqual(site_key('one.cafe24.com'),site_key('two.cafe24.com'))
        self.assertIsNone(site_key('marketplace.coupang.com'))

    def test_parser_detects_local_browser_import_and_refreshes_after_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'crawler.py'
            path.write_text("SITE='https://first.co.kr'\n# Chrome mentioned in a comment only\n",encoding='utf-8')
            self.assertEqual(resource_profile(path),dict(sites=['first.co.kr'],browser=False,known=True))
            path.write_text("SITE='https://second.co.kr'\ndef crawl():\n from playwright.sync_api import sync_playwright\n",encoding='utf-8')
            self.assertEqual(resource_profile(path),dict(sites=['second.co.kr'],browser=True,known=True))

    def test_all_published_crawlers_classified_and_shared_malls_conflict(self):
        catalog={b['id']:b for b in brands()}
        self.assertTrue(all(b['execution_resources']['known'] for b in catalog.values()))
        for first,second in [('blackyak_official','blackyakkids_official'),('polham_official','andz_official'),('leviskids_official','moimoln_official'),('tdg_eland_studio','ncbasic_eland_official'),('jeep_official','jeepkids_official')]:
            running=[dict(id='1',name=first,resources=catalog[first]['execution_resources'])]
            self.assertIn('같은 사이트',blocked_reason(catalog[second]['execution_resources'],running))
        for name in ['agabang_official','crocs_naver_official','redface_fashionplus']:
            self.assertTrue(catalog[name]['execution_resources']['browser'])
        for name in ['benetton_official','tdg_eland_studio','leviskids_official','fashionplus_item']:
            self.assertFalse(catalog[name]['execution_resources']['browser'])

if __name__=='__main__':unittest.main()
