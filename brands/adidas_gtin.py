PORTAL_API_VERSION = 1

import argparse
import http.client
import json
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit
import urllib.error
import urllib.request
from bs4 import BeautifulSoup

class CollectionError(RuntimeError):
    """Source/transport validation failed."""

class MissingResource(CollectionError):
    """Official product was not found."""

def public_opener(ctx, deadline):
    """Request-local dual-stack fallback; never change global socket settings.

    The observed Windows network publishes a nonresponsive DNS64 IPv6 address.
    Try DNS-provided IPv4 addresses first, retaining IPv6 as a fallback. A shared
    deadline covers address attempts, TLS, body reads and the optional retry.
    """
    def connect(address, timeout, source_address=None, **unused):
        host, port = address
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses.sort(key=lambda item: item[0] != socket.AF_INET)
        last_error = None
        for family, socktype, protocol, _, target in addresses:
            ctx.checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 시간 제한 초과')
            connection = socket.socket(family, socktype, protocol)
            try:
                connection.settimeout(min(3, remaining))
                if source_address:
                    connection.bind(source_address)
                connection.connect(target)
                connection.settimeout(min(5, max(.01, deadline-time.monotonic())))
                return connection
            except OSError as exc:
                connection.close()
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError('공개 호스트 주소 없음: ' + host)

    def factory(tls):
        def create(host, **kwargs):
            cls = http.client.HTTPSConnection if tls else http.client.HTTPConnection
            connection = cls(host, **kwargs)
            connection._create_connection = connect
            return connection
        return create

    class HTTP(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.do_open(factory(False), request)

    class HTTPS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(factory(True), request, context=self._context)

    return urllib.request.build_opener(HTTP(), HTTPS())


def fetch(ctx, url, limit=4_000_000):
    """Anonymous GET. No cookies, credentials or Referer, including images."""
    if urlsplit(url).scheme not in ('http','https'):
        raise CollectionError('공개 HTTP/HTTPS 주소가 아님: '+url)
    deadline = time.monotonic() + 20
    started = time.monotonic()
    ctx.log('공개 요청: ' + url, 'INFO')
    opener = public_opener(ctx, deadline)
    for attempt in range(2):
        ctx.checkpoint()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공개 요청 전체 20초 제한 초과')
            request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with opener.open(request, timeout=min(5, remaining)) as response:
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise CollectionError('응답 크기 초과: ' + url)
                chunks, size = [], 0
                while True:
                    ctx.checkpoint()
                    if time.monotonic() >= deadline:
                        raise TimeoutError('공개 응답 전체 20초 제한 초과')
                    block = response.read1(min(65536, limit + 1 - size))
                    if not block:
                        break
                    chunks.append(block)
                    size += len(block)
                    if size > limit:
                        raise CollectionError('응답 크기 초과: ' + url)
            ctx.checkpoint()
            ctx.log('공개 요청 완료 %.1f초: %s' % (time.monotonic()-started, url), 'INFO')
            return b''.join(chunks)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise MissingResource('HTTP 404: ' + url) from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise CollectionError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt:
                raise CollectionError('통신 실패: %s / %s' % (url, exc)) from exc
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CollectionError('요청 실패: ' + url)


def product_from_source(source,code):
    if isinstance(source,bytes):source=source.decode('utf-8-sig')
    if isinstance(source,str):
        if source.lstrip().startswith(('{','[')):data=json.loads(source)
        else:
            node=BeautifulSoup(source,'html.parser').find('script',id='__NEXT_DATA__')
            if node is None:raise CollectionError('원문에 __NEXT_DATA__ 상품 JSON이 없습니다')
            data=json.loads(node.string or node.get_text())
    else:data=source
    found=[]
    def walk(value):
        if isinstance(value,dict):
            if str(value.get('id','')).upper()==code and isinstance(value.get('variation_list'),list):found.append(value)
            for child in value.values():walk(child)
        elif isinstance(value,list):
            for child in value:walk(child)
    walk(data)
    if not found:raise CollectionError('해당 품번의 variation_list 없음: '+code)
    # NEXT_DATA repeats the same product in query cache and initialState.
    signatures={json.dumps([g.get('attribute_list',{}).get('color'),g['variation_list']],sort_keys=True) for g in found}
    if len(signatures)!=1:raise CollectionError('같은 품번의 중복 원문 GTIN 정보가 서로 다릅니다')
    return found[0]


def barcode_rows(product,code):
    color=str(product.get('attribute_list',{}).get('color') or '').strip()
    if not color:raise CollectionError('공식 색상 누락')
    rows=[];seen={}
    for option in product['variation_list']:
        sku=str(option.get('sku') or '')
        size=str(option.get('size') or '').strip()
        gtin=option.get('gtin')
        # Keep GTIN as text, including leading zeroes. Never derive from SKU.
        if not sku.startswith(code+'_') or not size or not isinstance(gtin,str) or not re.fullmatch(r'(?:\d{8}|\d{12}|\d{13}|\d{14})',gtin):
            raise CollectionError('옵션 품번/사이즈/GTIN 누락 또는 형식 오류: '+sku)
        digits=[int(x) for x in gtin]
        if (sum(d*(3 if i%2==0 else 1) for i,d in enumerate(reversed(digits[:-1])))+digits[-1])%10:
            raise CollectionError('GTIN 체크섬 오류: '+sku)
        if size in seen:
            if seen[size]!=gtin:raise CollectionError('같은 사이즈에 서로 다른 GTIN: '+size)
            continue
        seen[size]=gtin;rows.append({'품번':code,'색상':color,'옵션사이즈':size,'GTIN':gtin,'옵션SKU':sku})
    if not rows:raise CollectionError('GTIN 옵션이 없습니다')
    return rows


def collect(ctx,code,source_path=''):
    if not re.fullmatch(r'[A-Z0-9]{6}',code):raise CollectionError('아디다스 6자리 품번을 입력하세요')
    if source_path:
        path=Path(source_path).expanduser()
        if path.stat().st_size>10_000_000:raise CollectionError('원문 파일 10MB 초과')
        source=path.read_bytes();ctx.log('저장된 페이지 원문에서 수집 (실시간 조회 아님): '+str(path),'INFO')
    else:
        try:source=fetch(ctx,'https://www.adidas.co.kr/api/products/'+code,10_000_000)
        except CollectionError as exc:
            raise CollectionError(str(exc)+' / 접근 차단 시 입력 엑셀에 원문파일 열을 추가하고 Ctrl+U로 저장한 원문 경로를 입력하세요') from exc
    product=product_from_source(source,code)
    return product,barcode_rows(product,code)


def crawl(ctx):
    code=str(ctx.product_code).strip().upper()
    try:
        product,rows=collect(ctx,code,str(ctx.item.get('원문파일') or '').strip())
        for row in rows:
            ctx.emit_result({'product_code':code,'product_name':product.get('name') or code,
                'color':row['색상'],'product_url':'https://www.adidas.co.kr/'+code+'.html',
                '옵션사이즈':row['옵션사이즈'],'GTIN':row['GTIN'],'옵션SKU':row['옵션SKU']})
        ctx.log('색상·사이즈별 GTIN '+str(len(rows))+'개 수집 완료','SUCCESS')
    except (CollectionError,OSError,ValueError) as exc:
        ctx.fail_item(str(exc));raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='아디다스 색상/사이즈별 GTIN 전용 수집')
    parser.add_argument('code');parser.add_argument('--source',default='');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    class Console:
        def checkpoint(self):return None
        def log(self,message,level='INFO'):print(message)
    product,rows=collect(Console(),args.code.strip().upper(),args.source)
    from openpyxl import Workbook
    workbook=Workbook();sheet=workbook.active;sheet.title='바코드'
    sheet.append(['품번','색상','사이즈','GTIN'])
    for row in rows:
        sheet.append([row['품번'],row['색상'],row['옵션사이즈'],row['GTIN']])
        sheet.cell(sheet.max_row,4).number_format='@'
    workbook.save(args.output)
    print(str(len(rows))+'개 저장: '+args.output)
