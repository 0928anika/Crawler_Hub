PORTAL_API_VERSION = 1
import html,json,re,time,io,http.client,socket,urllib.error,urllib.request
from urllib.parse import parse_qs,quote,unquote,urlencode,urljoin,urlsplit
from bs4 import BeautifulSoup
from PIL import Image
from crawler_rules import check_public_image,gallery_slots,clean_product_name
from regular_price import collect_regular_price
ORIGIN='https://www.elandmall.co.kr'
VENDOR_NO='LV20020853'
REVISION='2026-11-16-crocs-eland-tnc-1'
class CrawlError(RuntimeError):pass

def text(node):
    return node.get_text(" ", strip=True) if node else ""

def public_url(value, base=ORIGIN):
    url = urljoin(base, value.strip())
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise CrawlError("공개 HTTP 이미지/문서 주소가 아닙니다")
    return quote(url, safe=":/?=&%+;,@!~*'()-._")

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
        raise CrawlError('공개 HTTP/HTTPS 주소가 아님: '+url)
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
                    raise CrawlError('응답 크기 초과: ' + url)
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
                        raise CrawlError('응답 크기 초과: ' + url)
            ctx.checkpoint()
            ctx.log('공개 요청 완료 %.1f초: %s' % (time.monotonic()-started, url), 'INFO')
            return b''.join(chunks)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise CrawlError('HTTP 404: ' + url) from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt:
                raise CrawlError('HTTP %s: %s' % (exc.code, url)) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            if attempt:
                raise CrawlError('통신 실패: %s / %s' % (url, exc)) from exc
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CrawlError('요청 실패: ' + url)

def soup(ctx, url):
    return BeautifulSoup(fetch(ctx, url), "html.parser")

def api(ctx, main, selected=None):
    params = {"itemNo": main["itemNo"], "lowerVendNo": main["lowerVendNo"], "itemTcode": main["itemTcode"]}
    if selected is not None:
        params["itemOptionNoList"] = selected
    url = ORIGIN + "/v1/item/option/api?" + urlencode(params)
    try:
        data = json.loads(fetch(ctx, url))
    except (ValueError, UnicodeError) as exc:
        raise CrawlError("옵션 JSON 응답 오류: " + url) from exc
    if str(data.get("resultCode")) != "200":
        raise CrawlError("옵션 API 실패: " + str(data.get("resultMessage")))
    options = data.get("data", {}).get("itemOption")
    if not isinstance(options, list) or not all(isinstance(o, dict) for o in options):
        raise CrawlError("옵션 API 구조 변경")
    return options

def parse_item(page):
    for script in page.find_all("script"):
        raw = script.get_text()
        match = re.search(r"\bconst\s+itemDetailJson\s*=\s*", raw)
        if match:
            try:
                data = json.JSONDecoder().raw_decode(raw[match.end():])[0]["itemDetail"]
                if isinstance(data.get("itemMain"), dict):
                    return data
            except (ValueError, KeyError, TypeError) as exc:
                raise CrawlError("공식 itemDetailJson 파싱 실패") from exc
    raise CrawlError("공식 상품 데이터 없음/접근 제한/상품 삭제: 원인 확인 필요")

def discover(ctx, code):
    links, offset, total, seen = [], 0, None, set()
    for index in range(30):
        ctx.checkpoint()
        page = soup(ctx, ORIGIN + "/s/srch?" + urlencode({"q": code, "page": index + 1, "from": offset}))
        count = page.select_one("[data-searchresult-total]")
        if count is None or not text(count).replace(",", "").isdigit():
            raise CrawlError("검색 응답 오류: 상품 부재와 구분 필요")
        current_total = int(text(count).replace(",", ""))
        if total is None:
            total = current_total
        elif total != current_total:
            raise CrawlError("검색 중 상품 수 변경; 누락 방지를 위해 재실행 필요")
        cards = page.select("#searchPageList [data-item-section]")
        if total and not cards:
            raise CrawlError("검색 결과 카드 구조 변경")
        fresh = 0
        for card in cards:
            ctx.checkpoint()
            anchor = card.select_one("a.ui-item__link[href]")
            if anchor is None:
                raise CrawlError("검색 상품 링크 누락")
            url = public_url(anchor["href"])
            fields = parse_qs(urlsplit(url).query)
            key = (fields.get("itemNo", [""])[0], fields.get("lowerVendNo", [""])[0])
            if not all(key):
                raise CrawlError("검색 상품/셀러 ID 누락")
            if key in seen:
                continue
            seen.add(key)
            # 다른 지점은 상품 상세 요청 전에도 제외. 검색 결과 원문에서 확인한다.
            if key[1] == VENDOR_NO and url not in links:
                links.append(url)
            fresh += 1
        offset += fresh
        if offset >= total:
            if not links:raise CrawlError(("[검색 결과 0건] " if total==0 else "[검색 결과는 있으나 지정 판매자 상품 없음] ")+code)
            return links
        if not fresh:
            raise CrawlError("검색 페이지 진행 실패")
    raise CrawlError("검색 30페이지 한도 초과; 누락 방지를 위해 중단")

OFFICIAL_SIZE_TABLES = {'adult': '<h3>사이즈표</h3><table border="1"><tbody><tr><th>한국 사이즈(밀리미터)</th><th>210mm</th><th>220mm</th><th>230mm</th><th>240mm</th><th>250mm</th><th>260mm</th><th>265mm</th><th>270mm</th><th>280mm</th><th>290mm</th><th>300mm</th><th>310mm</th></tr><tr><th>미국 사이즈</th><td>M2 | W4</td><td>M3 | W5</td><td>M4 | W6</td><td>M5 | W7</td><td>M6 | W8</td><td>M7 | W9</td><td>M8 | W10</td><td>M9 | W11</td><td>M10 | W12</td><td>M11</td><td>M12</td><td>M13</td></tr><tr><th>유럽 사이즈</th><td>34</td><td>35</td><td>36-37</td><td>37-38</td><td>38-39</td><td>39-40</td><td>41-42</td><td>42-43</td><td>43-44</td><td>45-46</td><td>46-47</td><td>48-49</td></tr><tr><th>한국</th><td>W1</td><td>M1 | W2</td><td>M2 | W3</td><td>M3 | W4</td><td>M4 | W5</td><td>M5 | W6</td><td>M6 | W7</td><td>M7 | W8</td><td>M8 | W9</td><td>M9 | W10</td><td>M10</td><td>M12</td></tr></tbody></table><p>사이즈정보</p><p>팁: 사이즈가 애매하면 큰 사이즈 를 선택하세요. 발볼이 넓은 경우 평소 사이즈를 주문하세요.</p>', 'kids': '<h3>사이즈표</h3><table border="1"><tbody><tr><th>추천 연령</th><th colspan="3">토들러 (1 미만)</th><th colspan="3">(1 - 2세)</th><th colspan="4">(2 - 5세)</th><th colspan="9">키즈 ( 6세 이상)</th></tr><tr><th>한국 사이즈(밀리미터)</th><th>90mm</th><th>98mm</th><th>107mm</th><th>115mm</th><th>120mm</th><th>130mm</th><th>140mm</th><th>150mm</th><th>155mm</th><th>165mm</th><th>175mm</th><th>180mm</th><th>190mm</th><th>200mm</th><th>210mm</th><th>220mm</th><th>230mm</th><th>240mm</th><th>250mm</th></tr><tr><th>미국 사이즈</th><td>C1</td><td>C2</td><td>C3</td><td>C4</td><td>C5</td><td>C6</td><td>C7</td><td>C8</td><td>C9</td><td>C10</td><td>C11</td><td>C12</td><td>C13</td><td>J1</td><td>J2</td><td>J3</td><td>J4</td><td>J5</td><td>J6</td></tr><tr><th>유럽 사이즈</th><td>-</td><td>17-18</td><td>18-19</td><td>19-20</td><td>20-21</td><td>22-23</td><td>23-24</td><td>24-25</td><td>25-26</td><td>27-28</td><td>28-29</td><td>29-30</td><td>30-31</td><td>32-33</td><td>33-34</td><td>34-35</td><td>36-37</td><td>37-38</td><td>38-39</td></tr><tr><th>한국</th><td>-</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td><td>8</td><td>9</td><td>10</td><td>11</td><td>C12</td><td>13</td><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td></tr></tbody></table><p>사이즈정보</p><p>팁: 자녀가 사이즈 사이에 있다면 큰 사이즈 를 선택하세요. 주니어 사이즈 3 이상은 성인 사이즈도 쇼핑할 수 있습니다.</p>'}

def checked(ctx,url,cache,detail=False):
    key=(url,detail)
    if key not in cache:cache[key]=check_public_image(url,timeout=12,retries=1,max_bytes=64*1024*1024 if detail else 10000000,checkpoint=ctx.checkpoint)
    result=cache[key]
    if result['status']!='accessible':raise CrawlError('이미지 조회 실패: '+url+' / '+', '.join(result['reasons']))
    return result


def size_guide(options):
    values=' '.join(str(o.get('itemOptionName') or '').upper() for o in options)
    adult=bool(re.search(r'(?<![A-Z])[MW]\d+',values))
    kids=bool(re.search(r'(?<![A-Z])[CJ]\d+',values))
    kinds=['adult'] if adult and not kids else ['kids'] if kids and not adult else ['adult','kids']
    parts=[]
    for kind in kinds:
        source=BeautifulSoup(OFFICIAL_SIZE_TABLES[kind],'html.parser')
        heading=source.find('h3')
        if heading:heading.string='크록스 공식 '+('성인' if kind=='adult' else '키즈·토들러')+' 사이즈표'
        for table in source.select('table'):table['style']='width:100%;border-collapse:collapse;font-size:12px;text-align:center'
        for node in source.select('th,td'):node['style']='border:1px solid #ddd;padding:5px;text-align:center'
        parts.append(str(source))
    return ''.join(parts)


def body_urls(data,model):
    doc=BeautifulSoup(html.unescape((data.get('itemDesc') or {}).get('itemDescdocumentCon') or ''),'html.parser');urls=[]
    base=model.split('-')[0]
    for node in doc.select('img'):
        raw=node.get('data-src') or node.get('src') or '';url=public_url(raw);p=urlsplit(url);name=unquote(p.path).rsplit('/',1)[-1]
        # Includes all attached banned banners, seller-made chart and A/S notices.
        if re.search(r'tnc_|notice|gongji|CROCS_01|about_|size|chart|crocs_as|call|banner|shipping|delivery',name,re.I):continue
        if p.hostname!='tnc7070.godohosting.com' or not p.path.startswith('/CROCS/'):raise CrawlError('상세 이미지 상품/안내 구분 미확인: '+url)
        if not re.match(re.escape(base)+r'(?:[-_.]|$)',name,re.I):continue
        if re.match(r'\d{5,6}-[A-Z0-9]{3}',name,re.I) and not re.match(re.escape(model)+r'(?:[_.-]|$)',name,re.I):continue
        url=re.sub(r'^http:','https:',url)
        if url not in urls:urls.append(url)
    if not urls:raise CrawlError('안내·판매자 사이즈표 제외 후 상품 본문 이미지 없음')
    return urls


def detail(ctx,data,model,options,cache):
    parts=[]
    for url in body_urls(data,model):
        checked(ctx,url,cache,True)
        parts.append('<img src="'+html.escape(url,quote=True)+'" alt="상품 상세" style="display:block;width:100%;height:auto;margin:0 auto">')
    parts.append(size_guide(options))
    return '<div style="width:100%;max-width:780px;margin:0 auto;text-align:center">'+''.join(parts)+'</div>'


def gallery(ctx,doc,cache):
    urls=[]
    for node in doc.select('[data-itemview-slidewrap] img'):
        raw=node.get('data-src') or node.get('src') or ''
        if not raw:continue
        url=public_url(raw)
        if url in urls:continue
        result=checked(ctx,url,cache)
        if result['format'] not in ('JPEG','PNG','WEBP') or min(result['width'],result['height'])<300 or max(result['width'],result['height'])/min(result['width'],result['height'])>1.8:continue
        with Image.open(io.BytesIO(result['data'])) as im:
            if getattr(im,'is_animated',False):continue
        urls.append(url)
    if not urls:raise CrawlError('공식 대표·추가 갤러리 사진 없음')
    return urls


def record_identity(data,raw):
    main=data['itemMain']
    if str(main.get('lowerVendNo'))!=VENDOR_NO:return None
    if '크록스' not in str(main.get('brandName')) and 'CROCS' not in str(main.get('brandName')).upper():return None
    model=str(main.get('modelNo') or '').strip().upper()
    if not re.fullmatch(r'\d{5,6}-[A-Z0-9]{3}',model):raise CrawlError('공식 품번-색상코드 확인 실패: '+model)
    if model!=raw and model.split('-')[0]!=raw:return None
    base,color=model.rsplit('-',1)
    name=str(main.get('itemName') or '')
    name=re.sub(r'\[\s*크록스(?:\s*정품)?\s*\]','',name,flags=re.I)
    name=clean_product_name(name,['크록스','CROCS'],[model,base])
    name=re.sub(r'티[앤엔]씨아이엔티(?:\(주\))?','',name).strip(' _-')
    if not name:raise CrawlError('상품명 누락')
    color_name=''
    for row in (data.get('itemDesc') or {}).get('itemProvideMntList') or []:
        if row.get('itemProvideMntArtiName')=='색상':color_name=BeautifulSoup(row.get('itemProvideMntArtiCon') or '','html.parser').get_text(' ',strip=True)
    return dict(product_code=raw,product_name=name,color=color_name or color,color_name=color_name,color_code=color),model


def money(value):
    if isinstance(value,bool):return None
    try:
        result=float(value)
        return int(result) if result>0 and result.is_integer() else None
    except (ValueError,TypeError,OverflowError):return None


def crawl(ctx):
    raw=str(ctx.product_code).strip().upper()
    if not raw:raise CrawlError('품번이 비어 있습니다')
    cache={};completed=set();errors=[]
    try:
        ctx.stage('크록스 이랜드몰 티앤씨아이엔티 검색: '+raw)
        for url in discover(ctx,raw):
            try:
                doc=soup(ctx,url);data=parse_item(doc);identity=record_identity(data,raw)
                if identity is None:continue
                record,model=identity
                if model in completed:continue
                main=data['itemMain'];params=parse_qs(urlsplit(url).query)
                if str(main.get('itemNo'))!=params.get('itemNo',[''])[0]:raise CrawlError('요청 상품번호 불일치')
                options=api(ctx,main) if ctx.enabled('size') or ctx.enabled('detail_page') else []
                if options and any(str(o.get('itemOptionTcode'))!='20' for o in options):raise CrawlError('색상별 단일 사이즈 옵션 구조 확인 필요')
                record['product_url']=url
                if ctx.enabled('size'):record['size']=','.join(dict.fromkeys(str(o.get('itemOptionName') or '') for o in options))
                if ctx.enabled('price'):record['price']=money(main.get('sellprice'))
                collect_regular_price(ctx,record,main,'eland')
                if ctx.enabled('category'):record['category']=' > '.join(text(n) for n in doc.select('.cp-loct-depth__inner .location a.lnk')) or main.get('depth4StandardCategoryName') or ''
                if ctx.enabled('main_image') or ctx.enabled('additional_images'):
                    urls=gallery(ctx,doc,cache)
                    if ctx.enabled('main_image'):record['main_image']=urls[0]
                    extras=urls[1:6] or urls[:1]
                    if ctx.enabled('additional_images'):record['additional_images']=','.join(extras[i%len(extras)] for i in range(5))
                if ctx.enabled('detail_page'):record['detail_page']=detail(ctx,data,model,options,cache)
                ctx.emit_result(record);completed.add(model)
            except (CrawlError,ValueError,KeyError) as exc:errors.append(url+' / '+str(exc))
        if errors:raise CrawlError('일부 상품 수집 미완료: '+' / '.join(errors))
        if not completed:raise CrawlError('지정 판매자 내 동일 모델명 상품 없음: '+raw)
    except Exception as exc:ctx.fail_item(str(exc));raise
