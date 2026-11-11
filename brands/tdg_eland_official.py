PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

import html
import http.client
import socket
import urllib.error
import urllib.request
import io
import json
import re
import time
import warnings
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup, Comment
from PIL import Image, UnidentifiedImageError

ORIGIN = "https://www.elandmall.co.kr"
LIMIT = 10_000_000
VENDOR_NO = "LV17008491"
BRAND_NO = "1690000198"
SELLER = re.compile(r"(?:NC[\s_-]*)?강남(?:[\s_-]*점)?", re.I)
BAD = re.compile(r"banner|logo|swatch|collage|montage|composite|care[_-]|washing|shipping|size|measure|chart|guide|배너|로고|고시|사이즈|실측|측정|배송|세탁|콜라주|합성", re.I)

class CrawlError(RuntimeError):
    pass


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


def image_info(ctx, url, candidate=False):
    raw = fetch(ctx, url, LIMIT)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                fmt, width, height = image.format, image.width, image.height
                if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                    raise CrawlError("애니메이션 이미지")
                if fmt not in ("JPEG", "PNG", "WEBP"):
                    raise CrawlError("허용하지 않는 실제 형식: " + str(fmt))
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                if candidate and has_internal_gutter(image):
                    raise CrawlError("여러 컷 사이의 내부 흰 여백 감지(콜라주 후보)")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise CrawlError("이미지 해독 실패: " + str(exc)) from exc
    return width, height, fmt


def has_internal_gutter(image):
    # 비율뿐 아니라 내부 분할 띠도 검사한다. 무여백 합성의 완전 인식은 보장하지 않는다.
    thumb = image.convert("RGB")
    thumb.thumbnail((160, 160))
    for source in (thumb, thumb.transpose(Image.Transpose.ROTATE_90)):
        width, height = source.size
        pixels = source.load()
        def white(y):
            return sum(min(pixels[x, y]) >= 245 for x in range(width)) / width
        start, end = int(height * .18), int(height * .82)
        for y in range(start, end - 1):
            if white(y) >= .98 and white(y + 1) >= .98:
                above = any(white(v) < .85 for v in range(0, y))
                below = any(white(v) < .85 for v in range(y + 2, height))
                if above and below:
                    return True
    return False


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
            source = anchor.get("data-product-cttn", "").split("|^|")
            name = source[1] if len(source) >= 2 else text(card.select_one(".ui-item__name"))
            if key[1] == VENDOR_NO and url not in links:
                links.append(url)
            fresh += 1
        offset += fresh
        if offset >= total:
            return links
        if not fresh:
            raise CrawlError("검색 페이지 진행 실패")
    raise CrawlError("검색 30페이지 한도 초과; 누락 방지를 위해 중단")


def clean_name(name, code, brand):
    value = SELLER.sub("", name)
    value = re.sub(r"(?<![A-Z0-9])" + re.escape(code) + r"(?![A-Z0-9])", "", value, flags=re.I)
    if brand:
        value = re.sub(r"^\s*(?:\[" + re.escape(brand) + r"\]|" + re.escape(brand) + r"(?=[\s_]))\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip(" _-/[]()")
    if not value:
        raise CrawlError("정리 후 상품명이 비어 있습니다")
    return value


def split_color(value):
    value = str(value).strip()
    match = re.fullmatch(r"\(([^()]+)\)\s*(.*)", value)
    if not match:
        match = re.fullmatch(r"(\d+)\s+(.+)", value)
    return {"color_name": match[2].strip(), "color_code": match[1].strip()} if match else {"color_name": value, "color_code": ""}


def optional_values(ctx, main, options):
    result, failures = {}, []
    sizes_by_color, prices = [], []
    if not (ctx.enabled("size") or ctx.enabled("price")):
        return result, failures
    for color in options:
        ctx.checkpoint()
        # 색상별 사이즈/가격 확인은 선택된 경우에만 수행한다.
        children = api(ctx, main, color["itemOptionNo"])
        if not children or any(str(o.get("itemOptionTcode")) != "20" for o in children):
            raise CrawlError("색상→사이즈 2단 옵션 구조가 아닙니다")
        if ctx.enabled("size"):
            values = list(dict.fromkeys(str(o.get("itemOptionName", "")).strip() for o in children))
            if not all(values):
                raise CrawlError("공식 사이즈명 누락")
            sizes_by_color.append(values)
            ctx.log("색상별 사이즈: 상품 %s / 셀러 %s / 색상 %s / 사이즈: %s" %
                    (main["itemNo"], main["lowerVendNo"], color.get("itemOptionName", ""), ",".join(values)), "INFO")
        if ctx.enabled("price"):
            for option in children:
                try:
                    value = Decimal(str(option.get("sellPrice", "")))
                except InvalidOperation as exc:
                    raise CrawlError("공식 옵션 판매가 누락") from exc
                if not value.is_finite() or value < 0 or value != value.to_integral_value():
                    raise CrawlError("공식 옵션 판매가 형식 오류")
                prices.append(int(value))
    if ctx.enabled("size"):
        result["size"] = ",".join(sizes_by_color[0])
        if any(set(v) != set(sizes_by_color[0]) for v in sizes_by_color):
            ctx.log("색상별 사이즈 차이: 사용자 지정 통일 규칙에 따라 상품 %s의 첫 번째 색상 %s 기준으로 적용 / 적용 사이즈: %s" %
                    (main["itemNo"], options[0].get("itemOptionName", ""), result["size"]), "WARNING")
    if ctx.enabled("price"):
        if prices and len(set(prices)) == 1:
            result["price"] = prices[0]
        else:
            failures.append("옵션별 판매가가 달라 공통 가격을 출력할 수 없습니다")
    return result, failures


def gallery(ctx, page):
    images = []
    for node in page.select("[data-itemview-slidewrap] img"):
        ctx.checkpoint()
        value = node.get("data-src") or node.get("src", "")
        if not value:
            continue
        url = public_url(value)
        if url in images:
            continue
        if BAD.search(urlsplit(url).path + " " + node.get("alt", "")):
            ctx.log("사진 제외(명시된 비상품컷): " + url, "WARNING")
            continue
        try:
            width, height, fmt = image_info(ctx, url, candidate=True)
            if min(width, height) < 500 or max(width, height) / min(width, height) > 1.8:
                raise CrawlError("양변 500px/비율 1.8 정책 불일치: %sx%s" % (width, height))
        except CrawlError as exc:
            ctx.log("사진 제외: %s / %s" % (url, exc), "WARNING")
            continue
        ctx.log("개별 갤러리 후보 %s %sx%s: %s" % (fmt, width, height, url), "SUCCESS")
        images.append(url)
    return images


def detail(ctx, page, data):
    parts = []
    desc = data.get("itemDesc", {})
    # 판매자 원본 상세의 통이미지/사이즈표 순서를 보존한다. 갤러리로 대체하지 않는다.
    content = BeautifulSoup(html.unescape(desc.get("itemDescdocumentCon", "")), "html.parser")
    for comment in content.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    allowed = {"div", "p", "span", "br", "strong", "b", "em", "i", "h1", "h2", "h3", "h4",
               "ul", "ol", "li", "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td", "img"}
    seen = set()
    for node in list(content.find_all(True)):
        ctx.checkpoint()
        if not node.name:
            continue
        if node.name in {"script", "style", "iframe", "object", "embed", "form", "link", "meta"}:
            node.decompose()
            continue
        if node.name not in allowed:
            node.unwrap()
            continue
        if node.name == "img":
            value = node.get("data-src") or node.get("ec-data-src") or node.get("src", "")
            if not value:
                node.decompose()
                continue
            url = public_url(value)
            if url in seen:
                node.decompose()
                continue
            seen.add(url)
            image_info(ctx, url)
            node.attrs = {"src": url, "alt": node.get("alt", ""), "style": "max-width:100%;height:auto"}
        else:
            spans = {key: str(node[key]) for key in ("rowspan", "colspan")
                     if node.name in {"th", "td"} and str(node.get(key, "")).isdigit()}
            node.attrs = spans
    if content.find("img") or text(content):
        parts.append(str(content))
    # 고시 항목의 소재/취급주의는 그대로 보존. 공지 배너와 배송 영역은 읽지 않는다.
    rows = []
    for item in desc.get("itemProvideMntList", []):
        ctx.checkpoint()
        key, value = item.get("itemProvideMntArtiName", ""), item.get("itemProvideMntArtiCon", "")
        key, value = text(BeautifulSoup(str(key), "html.parser")), text(BeautifulSoup(str(value), "html.parser"))
        if not key or not value:
            raise CrawlError("상품정보고시 항목/값이 비어 있습니다")
        rows.append('<tr><th style="border:1px solid #ccc;padding:6px">%s</th><td style="border:1px solid #ccc;padding:6px">%s</td></tr>' % (html.escape(key), html.escape(value)))
    if rows:
        parts.append('<h3>상품정보고시</h3><table style="border-collapse:collapse"><tbody>' + "".join(rows) + "</tbody></table>")
    else:
        ctx.log("공식 상품정보고시 없음", "INFO")
    if not parts:
        raise CrawlError("허용된 공통 상세 콘텐츠가 없습니다")
    return parts


def crawl(ctx):
    code = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{4,29}", code):
        ctx.fail_item("색상 접미사 없는 기본 품번을 입력하세요")
        raise CrawlError("품번 입력 형식 오류")
    if not (ctx.enabled("main_image") or ctx.enabled("additional_images")):
        ctx.fail_item("대표이미지 또는 추가이미지 선택이 필요합니다")
        raise CrawlError("수동 매핑 사진 수집 미선택")
    ctx.stage("이랜드몰 검색 및 공식 원본 상품명 확인")
    try:
        links = discover(ctx, code)
    except CrawlError as exc:
        ctx.fail_item(str(exc))
        raise
    records, colors, images, parts, errors = [], [], [], [], []
    record_links = []
    for url in links:
        ctx.checkpoint()
        try:
            page = soup(ctx, url)
            data = parse_item(page)
            main = data["itemMain"]
            original = str(main.get("itemName", ""))
            params = parse_qs(urlsplit(url).query)
            if str(main.get("lowerVendNo", "")) != VENDOR_NO:
                ctx.log("다른 판매자 ID 제외: " + str(main.get("lowerVendNo")), "INFO")
                continue
            if str(main.get("itemNo", "")) != params.get("itemNo", [""])[0]:
                raise CrawlError("요청 상품과 상세 상품 ID 불일치")
            if str(main.get("brandNo", "")) != BRAND_NO:
                ctx.log("다른 브랜드 ID 제외: " + str(main.get("brandNo")), "INFO")
                continue
            if str(main.get("modelNo", "")).strip().upper() != code:
                ctx.log("다른 공식 modelNo 제외: " + original, "INFO")
                continue
            options = api(ctx, main)
            if not options or any(str(o.get("itemOptionTcode")) != "10" for o in options):
                raise CrawlError("공식 색상 옵션이 없거나 색상 1단 구조가 아닙니다")
            choices = [split_color(o.get("itemOptionName", "")) for o in options]
            if any(not (c["color_name"] or c["color_code"]) for c in choices):
                raise CrawlError("공식 색상 후보명이 비어 있습니다")
            record = {"product_code": code, "product_url": url, "product_name": clean_name(original, code, str(main.get("brandName", ""))), "원본 상품명": original}
            ctx.log("상품명: " + record["product_name"] + " / 원본 상품명: " + original, "SUCCESS")
            collect_regular_price(ctx, record, main, 'eland')
            records.append(record)
            record_links.append(url)
            for choice in choices:
                if choice not in colors:
                    colors.append(choice)
            try:
                values, failures = optional_values(ctx, main, options)
                record.update(values)
                errors.extend(url + " / " + value for value in failures)
            except CrawlError as exc:
                errors.append(url + " / " + str(exc))
            if ctx.enabled("size"):
                ctx.log("링크별 사이즈: %s / 원본 상품명: %s / 사이즈: %s" %
                        (url, original, record.get("size", "확인 실패")), "INFO")
            if ctx.enabled("category"):
                category = " > ".join(text(n) for n in page.select(".cp-loct-depth__inner .location a.lnk"))
                if category:
                    record["category"] = category
                else:
                    errors.append("공식 분류를 읽지 못했습니다: " + url)
            if ctx.enabled("detail_page"):
                try:
                    for part in detail(ctx, page, data):
                        if part not in parts:
                            parts.append(part)
                except CrawlError as exc:
                    errors.append(url + " / " + str(exc))
            candidates = gallery(ctx, page)
            if not candidates:
                errors.append("이 상품 링크의 유효 사진이 0장입니다: " + url)
            images.extend(v for v in candidates if v not in images)
        except CrawlError as exc:
            errors.append(url + " / " + str(exc))
    output = {"product_code": code}
    if records:
        output["product_name"] = records[0]["product_name"]
        output["product_url"] = records[0]["product_url"]
        output["원본 상품명"] = "\n".join(dict.fromkeys(r["원본 상품명"] for r in records))
        if ctx.enabled("size"):
            reference = next(((link, r["size"]) for link, r in zip(record_links, records) if r.get("size")), None)
            if reference:
                output["size"] = reference[1]
                differing = any(r.get("size") and set(r["size"].split(",")) != set(reference[1].split(",")) for r in records)
                ctx.log("사이즈 통일 기준: 첫 번째 사이즈 수집 성공 링크 %s / 적용 사이즈: %s%s" %
                        (reference[0], reference[1], " / 다른 링크와 사이즈가 달라 기준 링크 값으로 통일" if differing else ""),
                        "WARNING" if differing else "INFO")
            else:
                errors.append("사이즈 수집에 성공한 상품 링크가 없습니다")
        for field in ("price", "category"):
            if ctx.enabled(field):
                values = [r.get(field) for r in records]
                if all(v is not None and v == values[0] for v in values):
                    output[field] = values[0]
                else:
                    errors.append("상품 링크 간 공통 " + field + " 값 미확정")
    else:
        errors.append("공식 판매자 ID와 공식 modelNo가 일치하는 상품이 없습니다: " + code)
    if ctx.enabled("detail_page") and parts:
        markup = "<div>" + "".join(parts) + "</div>"
        if len(markup.encode("utf-16-le")) // 2 > 32767:
            errors.append("상세 HTML이 Excel 셀 32767자 한도 초과")
        else:
            output["detail_page"] = markup
    if not colors or not images:
        errors.append("공식 색상 후보 또는 유효 개별 사진이 없어 수동 매핑 불가")
    merge_regular_prices(ctx, output, records)
    if records and colors:
        for image in images:
            ctx.checkpoint()
            ctx.emit_unmapped(dict(output), [dict(c) for c in colors], image, shared_detail_html=output.get("detail_page", ""))
    if errors:
        reason = "\n".join(dict.fromkeys(errors))
        ctx.fail_item(reason)
        raise CrawlError(reason)
    ctx.log("%s: 공식 상품 %s개, 색상 %s개, 사진 %s장" % (code, len(records), len(colors), len(images)), "SUCCESS")
