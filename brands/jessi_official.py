PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

import ast
import hashlib
import html
import io
import json
import re
import time
import warnings
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup
from PIL import Image

# 2026-11-04 공식 검색/상품 HTML과 실제 CDN 파일 조사.
# 매핑: 공식 검색 -> 상품 ID -> 자체상품코드 base_color -> 색상 옵션 -> 해당 갤러리.
# 상품명에 브랜드명이 있어야 한다는 검사는 사용하지 않는다.
# 사용자 예외 허용: 사진/실측표/고시/배송안내가 합쳐진 상세 원본은 그대로 포함.
# 상품 링크는 모든 결과의 필수 필드. 결과/임시파일을 별도로 쓰지 않는다.
# 규칙 기반 이미지 분류는 완전한 시각 인식이 아니다.
# 실제 실행: JG41JWBY3680/YGA1NPHC7780 각 2색상, 두 색상 입력 구분자, 전체 미선택 확인.
# 실제 CDN JPEG 1000x1500 확인. 0재고/품절 표시·WebP·손상·콜라주 신호 등은 오프라인 검증.
# Crawler Hub 최종 Excel/재실행 교체/검수 정제기와 실제 WING 등록은 별도 확인 필요.
ORIGIN = "https://jessinewyork.com"
MAX_HTML = 3_000_000
MAX_IMAGE = 10_000_000
TIMEOUT = 12
RETRIES = 1
POLICY = {
    "checked_on": "2026-11-04",
    "source": "https://marketplace.coupang.com/mba/listing",
    "route": "WING 웹/엑셀, 여성 의류",
    "official": "최소 500px, 권장 1000x1000, 10MB 이하, JPG/PNG",
    "project": "양변 500px 이상; 10000000바이트 이하; 비율 1.8 이하(자체 보수적 규칙)",
    "formats": ("JPEG", "PNG", "WEBP"),
    "webp": "사용자 등록 경로의 정지 WebP 허용; 모든 WING 경로 공식 보장 아님",
}
NEGATIVE = re.compile(r"size|guide|chart|measur|collage|montage|composite|banner|logo|swatch|notice|washing|shipping|사이즈|실측|측정|고시|콜라주|합성|배너|로고|스와치|배송|세탁",re.I)
GALLERY_SELECTORS = (".xans-product-image img.BigImage", ".xans-product-image .xans-product-addimage img.ThumbImage")


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def safe_url(url, base=ORIGIN):
    value = urljoin(base, str(url).strip())
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password:
        raise ValueError("공개 HTTP 이미지/페이지 URL이 아님")
    return quote(value, safe=":/?=&%+;,@!$'()*~-._#")


def get_bytes(ctx, url, limit):
    url = safe_url(url)
    last = None
    for attempt in range(RETRIES + 1):
        ctx.checkpoint()
        try:
            ctx.log("GET " + url, "INFO")
            request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"})
            with build_opener().open(request, timeout=TIMEOUT) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > limit:
                    raise ValueError("응답 용량 상한 초과")
                chunks, size = [], 0
                deadline = time.monotonic() + 35
                while True:
                    ctx.checkpoint()
                    if time.monotonic() > deadline:
                        raise TimeoutError("응답 읽기 시간 상한 초과")
                    chunk = response.read(min(65536, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("응답 용량 상한 초과")
                return b"".join(chunks), response.headers.get("Content-Type", ""), response.geturl()
        except HTTPError as exc:
            last = exc
            if exc.code < 500 and exc.code != 429:
                raise
        except (URLError, TimeoutError, OSError) as exc:
            last = exc
        finally:
            ctx.checkpoint()
        if attempt < RETRIES:
            ctx.log("일시 통신 오류: 제한된 재시도 " + str(last), "WARNING")
            for _ in range(5):
                ctx.checkpoint()
                time.sleep(0.2)
    raise RuntimeError("재시도 후 조회 실패: " + url + " / " + str(last)) from last


def get_html(ctx, url):
    data, content_type, final = get_bytes(ctx, url, MAX_HTML)
    if "html" not in content_type.lower() and not data.lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("상품 HTML 응답이 아님: " + final)
    soup = BeautifulSoup(data, "html.parser")
    if soup.select_one('input[name="captcha"], .g-recaptcha, #challenge-running'):
        raise RuntimeError("사이트 인증/CAPTCHA 발생: 자동 우회하지 않음")
    return soup, final


def inspect_image(data):
    if not data or len(data) > MAX_IMAGE:
        raise ValueError("빈 파일/10000000바이트 초과")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as im:
            fmt, (width, height) = im.format, im.size
            frames = getattr(im, "n_frames", 1)
            if width * height > 40_000_000:
                raise ValueError("이미지 픽셀 안전 상한 초과")
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im.load()
    return fmt, width, height, frames


def public_image(ctx, url, cache):
    if url in cache:
        return cache[url]
    ctx.checkpoint()
    try:
        data, content_type, final = get_bytes(ctx, url, MAX_IMAGE)
        try:
            fmt, width, height, frames = inspect_image(data)
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
                Image.DecompressionBombWarning) as exc:
            raise ValueError("실제 이미지 해독/verify 실패: " + str(exc)) from exc
        result = dict(status="accessible", data=data, final_url=final, format=fmt,
                      width=width, height=height, frames=frames, content_type=content_type, reasons=[])
        ctx.log(f"공개 이미지: {content_type}; 실제 {fmt}; {width}x{height}; frames={frames}; bytes={len(data)}; {url}", "INFO")
        if re.search(r"[?&](?:token|signature|expires|x-amz-signature|x-amz-expires)=", url, re.I):
            ctx.log("서명/만료 URL 위험: " + url, "WARNING")
    except HTTPError as exc:
        result = dict(status="inaccessible", reasons=["HTTP " + str(exc.code)])
    except (ValueError, SyntaxError, Image.UnidentifiedImageError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        result = dict(status="inaccessible", reasons=[str(exc)])
    except (RuntimeError, OSError) as exc:
        result = dict(status="unknown", reasons=[str(exc)])
    finally:
        ctx.checkpoint()
    cache[url] = result
    return result








def split_input(value):
    value = str(value).strip().upper()
    if not value:
        raise ValueError("검색할 품번이 비어 있습니다")
    return value, None


class NoSearchResults(ValueError):
    """공식 검색에서 관측된 0개 결과. 통신/파싱 오류와 구분한다."""


def discover_input(ctx, value):
    # 입력 전체를 먼저 검색한다. 내부 하이픈을 색상 구분자로 선분리하지 않는다.
    try:
        return discover(ctx, value)
    except NoSearchResults:
        # 전체 검색이 0개일 때만 색상 지정의 가능한 별칭을 추가 검색한다.
        # 최종 분리는 반드시 상품 페이지의 공식 base/color와 대조한다.
        separator = "_" if "_" in value else "-"
        candidate, found, color = value.rpartition(separator)
        if not found or not candidate or not re.fullmatch(r"[A-Z0-9]{2,6}", color):
            raise
        ctx.log("입력 전체 검색 0개: 색상 지정 가능성을 공식 품번과 대조 / " + candidate, "INFO")
        return discover(ctx, candidate)


def in_store(url):
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and parts.hostname in ("jessinewyork.com", "www.jessinewyork.com") and not parts.username and not parts.password


def discover(ctx, base):
    queue = [ORIGIN + "/product/search.html?" + urlencode({"keyword": base})]
    visited, products, expected = set(), {}, None
    while queue:
        ctx.checkpoint()
        url = queue.pop(0)
        if url in visited:
            continue
        if len(visited) >= 20:
            raise ValueError("검색 페이지 안전 상한 초과: 일부 결과만 완료하지 않음")
        visited.add(url)
        soup, final = get_html(ctx, url)
        if not in_store(final):
            raise ValueError("공식몰 외부 검색 응답")
        node = soup.select_one(".searchResult .prdCount strong")
        if not node or not re.fullmatch(r"[\d,]+", text(node)):
            raise ValueError("공식 검색 결과 수 파싱 실패")
        total = int(text(node).replace(",", ""))
        if total > 300:
            raise ValueError("검색 상품 수 안전 상한 초과")
        if expected is not None and expected != total:
            raise ValueError("검색 중 상품 수 변경: 재실행 필요")
        expected = total
        for card in soup.select("li[id^='anchorBoxId_']"):
            no = card.get("id", "").removeprefix("anchorBoxId_")
            anchor = card.select_one("a[href]")
            if not no.isdigit() or not anchor:
                raise ValueError("검색 상품 ID/링크 파싱 실패")
            link = safe_url(anchor["href"], final)
            if not in_store(link) or not re.search("/" + re.escape(no) + r"(?:/|$)", urlsplit(link).path):
                raise ValueError("검색 상품 링크의 공식몰/상품 ID 불일치")
            products[no] = link
        for anchor in soup.select(".xans-search-paging a[href]"):
            link = safe_url(anchor["href"], final)
            query = parse_qs(urlsplit(link).query)
            if in_store(link) and query.get("keyword") == [base] and query.get("page", [""])[0].isdigit() and link not in visited and link not in queue:
                queue.append(link)
    if expected == 0:
        raise NoSearchResults("공식 검색 결과 없음: " + base)
    if len(products) != expected:
        raise ValueError(f"공식 검색 누락: {expected}개 중 {len(products)}개만 확인")
    return products


def field_value(soup, css):
    row = soup.select_one(".xans-product-detaildesign tr." + css)
    return text(row.find("td")) if row else ""


def verify_product_url(final, no):
    url = safe_url(final)
    if not in_store(url):
        raise ValueError("상품 링크가 공식몰 공개 HTTP 주소가 아님")
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if not re.search("/" + re.escape(no) + r"(?:/|$)", parts.path) and query.get("product_no") != [no]:
        raise ValueError("조회한 상품 링크와 상품 ID 불일치")
    return url


def identity(soup, no, base, final):
    meta = soup.select_one('meta[property="product:productId"]')
    if not meta or meta.get("content") != no:
        raise ValueError("상품 페이지의 공식 상품 ID 불일치")
    own = field_value(soup, "ma_product_code_css")
    official_base, separator, code = own.rpartition("_")
    if not separator or not official_base or not code:
        raise ValueError("자체상품코드 구조 미확인: " + own)
    if base is not None and official_base != base:
        return None
    base = official_base
    select = soup.select_one('[option_title="색상"], [option_title="컬러"]')
    if not select or select.get("option_product_no") != no:
        raise ValueError("색상 옵션과 상품 ID 연결 미확인")
    names, labels = [], []
    for option in select.select("option, li[option_value]"):
        if option.get("option_value", option.get("value", "")) in ("", "*", "**"):
            continue
        label = re.sub(r"\s*[\[(]품절[\])]\s*$", "", text(option)).strip()
        if label == code:
            names.append('')
        elif label.startswith(code + "_"):
            names.append(label[len(code) + 1:].strip())
        else:
            raise ValueError("단일 색상 상품과 공식 색상 옵션 불일치: " + label)
        labels.append(label)
    if not labels:
        raise ValueError("공식 색상명 확인 실패")
    raw_name = field_value(soup, "product_name_css")
    if not raw_name:
        raise ValueError("공식 상품명 누락")
    name = raw_name
    for token in (re.escape(own), re.escape(base), r"제시\s*뉴욕", r"JESSI\s*NEW\s*YORK", r"JESSI\s+BY\s+JESSI"):
        name = re.sub(r"(?<![A-Za-z0-9가-힣])(?:" + token + r")(?![A-Za-z0-9가-힣])", " ", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" _-()[]")
    if not name:
        raise ValueError("브랜드/품번 정리 후 실제 상품명 없음")
    return dict(base=base, own=own, code=code, color=next((n for n in names if n), code),
                color_name=next((n for n in names if n), ''), option_labels=list(dict.fromkeys(labels)), name=name, raw_name=raw_name,
                product_url=verify_product_url(final, no))


def sizes(ctx, soup, no):
    select = soup.select_one('select[option_title="사이즈"]')
    if not select:
        ctx.log("공식 사이즈 옵션 없음", "WARNING")
        return ""
    if select.get("option_product_no") != no:
        raise ValueError("사이즈 옵션 상품 ID 불일치")
    values = []
    for option in select.select("option"):
        ctx.checkpoint()
        if option.get("value", "") in ("", "*", "**"):
            continue
        # 품절/disabled 옵션도 포함한다. 앞자리 0은 문자열로 보존한다.
        value = re.sub(r"\s*[\[(]품절[\])]\s*$", "", text(option)).strip()
        if not value or any(c in value for c in (",", "\r", "\n")):
            raise ValueError("사이즈 값 누락/직렬화 불가")
        if value not in values:
            values.append(value)
    if not values:
        ctx.log("공개 사이즈 옵션 값 없음", "WARNING")
    return ",".join(values)


def price(ctx, soup, info):
    currency = soup.select_one('meta[property="product:price:currency"]')
    if not currency or currency.get("content") != "KRW":
        ctx.log("KRW 확인 불가/외화 가격: " + str(currency), "WARNING")
        return None
    script = "\n".join(n.get_text() for n in soup.find_all("script", src=False))
    match = re.search(r"\bvar\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*')\s*;", script)
    if not match:
        raise ValueError("옵션별 가격 JSON 파싱 실패")
    stock = json.loads(ast.literal_eval(match.group(1)))
    if not isinstance(stock, dict) or not stock:
        ctx.log("공개 옵션별 가격 없음", "WARNING")
        return None
    amounts = []
    expected = info.get('option_labels', [info["code"] + "_" + info["color"]])
    for row in stock.values():
        original = row.get("option_value_orginal")
        if not isinstance(original, list) or not original or original[0] not in expected:
            raise ValueError("가격 옵션과 해당 색상 불일치")
        if row.get("option_price") is None:
            raise ValueError("실제 옵션 판매가 누락")
        amount = Decimal(str(row["option_price"]))
        if not amount.is_finite() or amount < 0:
            raise ValueError("실제 옵션 판매가가 유효한 숫자가 아님")
        amounts.append(amount)
    if len(set(amounts)) != 1:
        ctx.log("사이즈별 가격이 달라 단일 가격 출력 불가", "WARNING")
        return None
    amount = amounts[0]
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def category(ctx, soup):
    values = [text(n) for n in soup.select(".xans-product-headcategory ol li a") if text(n) and text(n) != "홈"]
    if not values:
        ctx.log("공식 카테고리 없음", "WARNING")
        return ""
    return " > ".join(values)


def gallery_candidates(ctx, soup, no):
    candidates = []
    # 공개 구조화 데이터의 SKU/그룹 ID로 현재 상품 소속을 확인한다.
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.get_text())
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("@type") == "Product":
            entries = [data]
        elif data.get("@type") == "ProductGroup" and data.get("productGroupID") == "cafe24_jessishop1_1_" + no:
            entries = data.get("hasVariant", [])
        else:
            continue
        for entry in entries:
            if not re.fullmatch(r"cafe24_jessishop1_1_" + re.escape(no) + r"(?:_.+)?", str(entry.get("sku", ""))):
                continue
            urls = entry.get("image", [])
            if isinstance(urls, str):
                urls = [urls]
            for url in urls:
                ctx.checkpoint()
                if isinstance(url, str) and url:
                    candidates.append((safe_url(url), "공식 구조화 Product.image; SKU=" + entry["sku"]))
    for selector in GALLERY_SELECTORS:
        for node in soup.select(selector):
            ctx.checkpoint()
            if node.get("src"):
                context = " ".join((selector, node.get("alt", ""), node.get("title", ""), " ".join(node.get("class", [])), " ".join(node.parent.get("class", [])) if node.parent else ""))
                candidates.append((safe_url(node["src"]), context))
    # schema 우선. 동일 URL을 다시 검사하지 않는다.
    contexts = {}
    for url, context in candidates:
        contexts.setdefault(url, [])
        if context not in contexts[url]:
            contexts[url].append(context)
    return [(url, " ".join(values)) for url, values in contexts.items()]


def gallery(ctx, soup, no, info, cache):
    ctx.stage("색상 " + info["code"] + " 갤러리 검사")
    valid, hashes = [], set()
    for url, context in gallery_candidates(ctx, soup, no):
        ctx.checkpoint()
        signal = NEGATIVE.search(unquote(urlsplit(url).path) + " " + context)
        if signal:
            ctx.log("갤러리 제외 신호 " + signal.group() + " / " + url, "INFO")
            continue
        result = public_image(ctx, url, cache)
        if result["status"] != "accessible":
            ctx.log("갤러리 " + result["status"] + ": " + "; ".join(result["reasons"]) + " / " + url, "INFO")
            continue
        fmt, w, h, frames = (result[k] for k in ("format", "width", "height", "frames"))
        ratio = max(w, h) / min(w, h)
        reasons = []
        if fmt not in POLICY["formats"]:
            reasons.append("미지원 실제 형식 " + str(fmt))
        if frames != 1:
            reasons.append("애니메이션")
        if min(w, h) < 500:
            reasons.append("양변 500px 미달")
        if ratio > 1.8:
            reasons.append("통짜/배너 의심 비율 1.8 초과(프로젝트 규칙)")
        if reasons:
            ctx.log("갤러리 제외 " + "; ".join(reasons) + " / " + url, "INFO")
            continue
        digest = hashlib.sha256(result["data"]).hexdigest()
        if digest in hashes:
            continue
        hashes.add(digest)
        valid.append(url)
        ctx.log(f"채택: product_no={no}, 자체상품코드={info['own']}, 색상옵션={info['code']}_{info['color']}; role=product_shot; {fmt} {w}x{h}, ratio={ratio:.3f}, 정지; 경로/alt/title/클래스 부정 신호 없음; source={context}; 규칙 기반 개별 컷 / {url}", "SUCCESS")
    if not valid:
        ctx.log("최종 갤러리 0장: 접근/형식/역할/규격/제외 로그 검수 필요", "WARNING")
        return "", ""
    main, extras = valid[0], valid[1:]
    if not extras:
        ctx.log("유효 컷 1장: 대표 컷을 추가 5슬롯에도 재사용", "WARNING")
        extras = [main]
    return main, ",".join(extras[i % len(extras)] for i in range(5))


def composite_detail_url(url, own):
    parts = urlsplit(url)
    target = parse_qs(parts.query).get("url", [""])[0]
    source = urlsplit(target if '://' in target else 'https://' + target)
    return (parts.scheme == "https" and parts.hostname == "images.weserv.nl"
            and source.hostname == 'js_img.etec.co.kr' and source.path.startswith('/ONLINE/' + own + '/')
            and bool(re.search(r"\.(?:jpe?g|png|webp)$", source.path, re.I)))


def detail(ctx, soup, no, info, cache):
    ctx.stage("색상 " + info["code"] + " 상세 HTML")
    nodes = soup.select("#prdDetail > .cont img")
    composites = []
    for node in nodes:
        raw = node.get("ec-data-src") or node.get("src", "")
        if not raw:
            continue
        url = safe_url(raw)
        if composite_detail_url(url, info["own"]):
            composites.append((url, "공식 상품별 통합 상세 원본"))
    if nodes and not composites:
        raise ValueError("상세 이미지의 공식 품번/색상 연결 구조 미확인")
    if composites:
        candidates = composites
        ctx.log("사용자 허용: 통합 원본 안의 고시·실측표·배송/반품 안내도 함께 보존", "INFO")
    else:
        ctx.log("공식 상세 본문 비어 있음: 검증된 상품 갤러리 사진으로 구성. 상품별 고시/실측표 원본 없음", "WARNING")
        candidates = [(url, "공식 색상 상품사진") for url, _ in gallery_candidates(ctx, soup, no)]
    output, hashes, seen = [], set(), set()
    for url, role in candidates:
        ctx.checkpoint()
        if url in seen:
            continue
        seen.add(url)
        result = public_image(ctx, url, cache)
        if result["status"] != "accessible":
            ctx.log("상세 " + result["status"] + ": " + "; ".join(result["reasons"]) + " / " + url, "WARNING")
            if result["status"] == "unknown":
                raise RuntimeError("선택한 상세 이미지 통신 확인 미완료: " + url)
            continue
        # 통합 상세/사이즈 자료에 갤러리용 비율·최소변 규칙을 적용하지 않는다.
        digest = hashlib.sha256(result["data"]).hexdigest()
        if digest in hashes:
            continue
        hashes.add(digest)
        output.append('<p><img src="' + html.escape(url, quote=True) + '" alt="' + html.escape(role, quote=True) + '"></p>')
    # 사용자 지정: 사이즈표는 상세 원본 이미지 안의 내용만 보존한다.
    # 공통 환산표나 별도 HTML 사이즈표를 추가 생성하지 않는다.
    result_html = "<div>" + "".join(output) + "</div>" if output else ""
    if len(result_html.encode("utf-16-le")) // 2 > 32767:
        raise ValueError("상세 HTML 32767자 상한 초과: 임의 절단하지 않음")
    if not result_html:
        ctx.log("최종 상세 자료 없음: 검수 필요", "WARNING")
    return result_html


def crawl(ctx):
    failures, matched, emitted = [], set(), set()
    try:
        input_value, _ = split_input(ctx.product_code)
        ctx.stage("제시뉴욕 공식 검색: " + input_value)
        products = discover_input(ctx, input_value)
    except Exception as exc:
        ctx.fail_item(str(exc))
        raise
    for no, url in products.items():
        ctx.checkpoint()
        try:
            ctx.stage("공식 상품 조회: " + no)
            soup, final = get_html(ctx, url)
            info = identity(soup, no, None, final)
            base = info["base"]
            code = info["code"]
            if input_value not in (base, base + "_" + code, base + "-" + code):
                ctx.log("검색 유사 품번 제외: " + no, "INFO")
                continue
            matched.add(code)
            if code in emitted:
                ctx.log("동일 공식 품번·색상의 중복 상품 페이지 생략: " + info['own'] + ' / ' + no, 'INFO')
                continue
            record = dict(product_code=base, product_name=info["name"], color=info["color"], product_url=info["product_url"], color_name=info.get("color_name",info["color"]), color_code=code)
            if ctx.enabled("size"):
                record["size"] = sizes(ctx, soup, no)
            if ctx.enabled("price"):
                record["price"] = price(ctx, soup, info)
            if ctx.enabled("category"):
                record["category"] = category(ctx, soup)
            cache = {}
            if ctx.enabled("main_image") or ctx.enabled("additional_images"):
                main, extras = gallery(ctx, soup, no, info, cache)
                if ctx.enabled("main_image"):
                    record["main_image"] = main
                if ctx.enabled("additional_images"):
                    record["additional_images"] = extras
            if ctx.enabled("detail_page"):
                record["detail_page"] = detail(ctx, soup, no, info, cache)
            record["원본 상품명"] = info["raw_name"]
            ctx.checkpoint()
            collect_regular_price(ctx, record, soup, 'cafe24')
            ctx.emit_result(record)
            emitted.add(code)
        except Exception as exc:
            message = "상품 " + no + ": " + str(exc)
            ctx.fail_item(message)
            failures.append(message)
    if not failures and not emitted:
        message = "공식 검색 후 일치하는 품번/지정 색상 없음: " + input_value
        ctx.fail_item(message)
        failures.append(message)
    if failures:
        raise RuntimeError("; ".join(failures))
