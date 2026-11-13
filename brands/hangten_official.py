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

# 2026-11-04: 공식 검색 HTML, 14379~14382 및 다른 품번 14546의 원본 확인.
# 실제 이미지: big 720x960 JPEG, extra/small 900x1200 JPEG. 경로 변조 없음.
# 고시/실측표는 래스터 이미지에만 존재. 사용자 지시에 따라 원본 이미지 보존.
# 네트워크 없는 경계 테스트와 공식 사이트 요청 테스트는 별도 검증 기록에 구분.
# Crawler Hub 실제 Excel/재시작/검수 렌더러 및 WING 등록은 여기서 검증하지 않음.
POLICY = {
    "checked_on": "2026-11-04",
    "source": "https://marketplace.coupang.com/mba/listing",
    "route": "WING 웹/엑셀, 아동 의류",
    "official": "최소 500px, 권장 1000x1000, 10MB 이하, JPG/PNG",
    "project": "양변 500px 이상; 10000000바이트 이하; 비율 1.8 이하",
    "formats": ("JPEG", "PNG", "WEBP"),
    "webp": "사용자가 확인한 등록 경로의 정지 WebP 허용; 전체 WING 보장 아님",
    "content": "DOM 역할/매핑/부정 신호에 의한 규칙 판정; 완전한 시각 인식 아님",
}
ORIGIN = "https://hangten.co.kr"
BASE_PATTERN = r"\d{5}-\d{3}-\d{3}-\d{2}"
INPUT_PATTERN = re.compile(r"^(?:" + BASE_PATTERN + r"|\d{13})(?:[_-]\d{3})?$")
NEGATIVE = re.compile(
    r"size|guide|chart|measur|collage|montage|composite|banner|logo|swatch|"
    r"notice|washing|shipping|사이즈|실측|측정|고시|콜라주|합성|배너|로고|스와치|배송|세탁",
    re.I,
)
GALLERY_SELECTORS = (
    ".xans-product-image .keyImg > .thumbnail > img",
    ".xans-product-image .xans-product-addimage li > img.ThumbImage",
)
MAX_HTML = 3_000_000
MAX_IMAGE = 10_000_000
TIMEOUT = 12
RETRIES = 1
MAX_PAGES = 20
MAX_PRODUCTS = 300


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def normalize_input(value):
    value = str(value).strip()
    if not INPUT_PATTERN.fullmatch(value):
        raise ValueError("품번 형식 오류: 14590-010-801-41 또는 1459001080141, 색상 지정은 _044/-044")
    match = re.fullmatch(r"(" + BASE_PATTERN + r"|\d{13})(?:[_-](\d{3}))?", value)
    base, requested = match.groups()
    if "-" not in base:
        base = "-".join((base[:5], base[5:8], base[8:11], base[11:]))
    return base, requested


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


def discover(ctx, base):
    first = ORIGIN + "/search.html?" + urlencode({"keyword": base})
    queue, visited, products, expected = [first], set(), {}, None
    while queue:
        ctx.checkpoint()
        if len(visited) >= MAX_PAGES:
            raise RuntimeError("검색 페이지 안전 상한 초과: 일부 색상만 완료 처리하지 않음")
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        soup, final = get_html(ctx, url)
        count = soup.select_one(".searchResult .record strong")
        if count is None or not re.fullmatch(r"[\d,]+", text(count)):
            raise RuntimeError("공식 검색 결과 수 파싱 실패")
        total = int(text(count).replace(",", ""))
        expected = total if expected is None else expected
        if total != expected:
            raise RuntimeError("검색 도중 결과 수 변경: 재실행 필요")
        if total > MAX_PRODUCTS:
            raise RuntimeError("검색 상품 수 안전 상한 초과")
        for card in soup.select(".xans-search-result .prdList > li[id^='anchorBoxId_']"):
            no = card.get("id", "").removeprefix("anchorBoxId_")
            a = card.select_one(".name a")
            if not no.isdigit() or a is None:
                raise RuntimeError("공식 검색 상품 링크 파싱 실패")
            link = safe_url(a.get("href", ""), final)
            if urlsplit(link).hostname != "hangten.co.kr":
                raise RuntimeError("공식몰 외부 상품 링크")
            products[no] = link
        for a in soup.select(".xans-search-paging a[href]"):
            link = safe_url(a["href"], final)
            params = parse_qs(urlsplit(link).query)
            if (urlsplit(link).hostname == "hangten.co.kr" and
                    params.get("keyword") == [base] and params.get("page", [""])[0].isdigit() and
                    link not in visited and link not in queue):
                queue.append(link)
    if not expected or not products:
        raise RuntimeError("검색 결과 없음: " + base)
    if len(products) != expected:
        raise RuntimeError(f"검색 결과 누락: 공식 {expected}개 / 추출 {len(products)}개")
    return products


def identity(soup, no, base):
    row = next((r for r in soup.select(".xans-product-detaildesign tr")
                if r.th and text(r.th) == "자체상품코드"), None)
    own = text(row.td) if row and row.td else ""
    match = re.fullmatch("(" + BASE_PATTERN + r")-(\d{3})(?:-[A-Za-z0-9]+)?", own)
    if not match:
        raise ValueError("자체상품코드 구조 미확인: " + own)
    official_base, code = match.groups()
    if official_base != base:
        return None
    option = soup.select_one("#product_option_id1")
    if option is None or option.get("option_product_no") != str(no) or option.get("option_title") != "색상-사이즈":
        raise ValueError("색상-사이즈 옵션/상품 ID 연결 미확인")
    names, options = [], []
    for node in option.select("option"):
        key = node.get("value", "")
        if key in ("", "*", "**"):
            continue
        label = text(node)
        # 품절/재고/disabled 여부로 필터링하지 않음.
        label = re.sub(r"\s*[\[(]품절[\])]\s*$", "", label).strip()
        stem, separator, _ = label.rpartition("-")
        found = re.fullmatch(r"(.+?)" + re.escape(code), stem) if separator else None
        if not found:
            raise ValueError("단일 색상 상품 갤러리와 옵션 색상 불일치: " + label)
        name = found.group(1)
        names.append(name.strip())
        options.append((key, label))
    if not options or len(set(names)) != 1:
        raise ValueError("색상명과 공식 색상코드 연결 미확인")
    heading = soup.select_one(".xans-product-detaildesign h1")
    name = text(heading)
    # 공식 상품 여부는 검색 링크/자체상품코드/상품 ID/색상 옵션으로 검증한다.
    # 같은 공식몰의 성인 행텐 상품을 관리자 표시명 '행텐틴즈'로 거르지 않는다.
    if not name:
        raise ValueError("공식 상품명 누락")
    return own, code, names[0], options, name


def clean_name(name, own, base):
    patterns = [r"행텐\s*틴즈", r"HANG\s*TEN\s*TEENS", r"HANG\s*TEN", r"행텐"]
    patterns += [re.escape(v) for v in (own, base, base.replace("-", ""))]
    for pattern in patterns:
        name = re.sub(r"(?<![A-Za-z0-9가-힣])(?:" + pattern + r")(?![A-Za-z0-9가-힣])", " ", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" _-/()[]")
    if not name:
        raise ValueError("브랜드/품번 제거 후 실제 상품명이 없음")
    return name


def prices(ctx, soup, options):
    node = soup.select_one("#span_product_price_text")
    shown = text(node)
    currencies = {m.get("content", "").upper() for m in soup.select('meta[property="product:price:currency"]')}
    currencies.discard("")
    if (currencies and currencies != {"KRW"}) or (not currencies and "원" not in shown):
        ctx.log("KRW 확인 불가/외화 가격: " + str(currencies) + " / " + shown, "WARNING")
        return None
    script = "\n".join(s.get_text() for s in soup.find_all("script", src=False))
    match = re.search(r"\bvar\s+option_stock_data\s*=\s*('(?:\\.|[^'\\])*')\s*;", script)
    if not match:
        raise ValueError("옵션별 판매가 응답 option_stock_data 파싱 실패")
    stock = json.loads(ast.literal_eval(match.group(1)))
    amounts = []
    for key, _ in options:
        row = stock.get(key)
        if not isinstance(row, dict) or row.get("option_price") is None:
            raise ValueError("옵션별 판매가 누락: " + key)
        amount = Decimal(str(row["option_price"]).replace(",", ""))
        if not amount.is_finite() or amount < 0:
            raise ValueError("유효하지 않은 공식 판매가")
        amounts.append(amount)
    if len(set(amounts)) != 1:
        ctx.log("사이즈별 판매가가 달라 가격 단일 숫자 출력 불가", "WARNING")
        return None
    amount = amounts[0]
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def sizes(options):
    values = []
    for _, label in options:
        value = label.rsplit("-", 1)[-1].strip()
        if not value or any(c in value for c in (",", "\r", "\n")):
            raise ValueError("사이즈 셀 직렬화 불가: " + label)
        if value not in values:
            values.append(value)
    return ",".join(values)


def category(soup):
    nodes = soup.select(".xans-product-headcategory ol li a")
    values = [text(n) for n in nodes if text(n) and text(n) != "홈"]
    if not values:
        raise ValueError("공식 카테고리 경로 파싱 실패")
    return values[-1]


def official_category(ctx, soup, final):
    canonical = soup.select_one('link[rel="canonical"][href]')
    if canonical:
        link = safe_url(canonical["href"], final)
        if urlsplit(link).hostname != "hangten.co.kr":
            raise ValueError("공식 카테고리 canonical 호스트 불일치")
        if link != final:
            soup, _ = get_html(ctx, link)
    return category(soup)


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


def gallery(ctx, soup, url, no, code, own):
    ctx.stage("색상 " + code + " 공식 갤러리 검사")
    accepted, seen_urls, seen_hashes, cache = [], set(), set(), {}
    for selector in GALLERY_SELECTORS:
        for node in soup.select(selector):
            ctx.checkpoint()
            raw = node.get("ec-data-src") or node.get("src", "")
            if not raw:
                continue
            image_url = safe_url(raw, url)
            if image_url in seen_urls:
                continue
            seen_urls.add(image_url)
            context = " ".join((unquote(urlsplit(image_url).path), node.get("alt", ""),
                                node.get("title", ""), " ".join(node.get("class", [])),
                                node.parent.get("title", "") if node.parent else "",
                                " ".join(node.parent.get("class", [])) if node.parent else ""))
            signal = NEGATIVE.search(context)
            if signal:
                ctx.log("갤러리 제외 신호 " + signal.group() + ": " + image_url, "INFO")
                continue
            result = public_image(ctx, image_url, cache)
            if result["status"] != "accessible":
                ctx.log("갤러리 " + result["status"] + ": " + "; ".join(result["reasons"]) + " / " + image_url, "INFO")
                continue
            fmt, w, h, frames = (result[k] for k in ("format", "width", "height", "frames"))
            reasons = []
            if fmt not in POLICY["formats"]:
                reasons.append("미지원 실제 형식 " + str(fmt))
            if frames != 1:
                reasons.append("애니메이션 제외")
            if min(w, h) < 500:
                reasons.append("양변 500px 미달")
            ratio = max(w, h) / min(w, h)
            if ratio > 1.8:
                reasons.append("통짜/배너 의심 비율 1.8 초과(프로젝트 규칙)")
            if reasons:
                ctx.log("갤러리 제외 " + "; ".join(reasons) + " / " + image_url, "INFO")
                continue
            digest = hashlib.sha256(result["data"]).hexdigest()
            if digest in seen_hashes:
                ctx.log("동일 바이트 컷 중복 제외: " + image_url, "INFO")
                continue
            seen_hashes.add(digest)
            ctx.log(f"갤러리 채택: product_no={no}, 자체상품코드={own}, 옵션색상={code}; "
                    f"DOM={selector}; role=product_shot; {w}x{h}; ratio={ratio:.3f}; "
                    "경로/alt/title 부정 신호 없음; 규칙 기반 개별 컷 / " + image_url, "SUCCESS")
            accepted.append(image_url)
    if not accepted:
        ctx.log("최종 이미지 0장: 공개 접근/실제 파일/갤러리 제외 로그 검수 필요", "WARNING")
        return "", ""
    main = accepted[0]
    extra = accepted[1:]
    if not extra:
        ctx.log("유효 컷 1장: 동일 컷을 추가이미지 5슬롯에도 재사용", "WARNING")
        extra = [main]
    elif len(extra) < 5:
        ctx.log("통과한 추가 컷만 반복해 5슬롯 구성", "INFO")
    slots = [extra[i % len(extra)] for i in range(5)]
    return main, ",".join(slots)


def detail_page(ctx, soup, url, base, code):
    ctx.stage("색상 " + code + " 상세 원본 이미지 수집")
    # 공식 상세 영역 내부를 조사한다. 일부 상품은 빈 center 뒤에 이미지가 있다.
    # URL을 생성하지 않고 이 영역에 실제 있는 공식 링크만 선택한다.
    images = soup.select("#prdDetail > .cont img")
    prefix = base + "-" + code
    product_path = re.compile(
        r"^/Product/HTN/[^/]+/detail/" + re.escape(prefix) + r"_[^/]+\.(?:jpe?g|png|webp)$",
        re.I,
    )
    size_path = "/Product/HTN/banner/TEENS_SIZE.jpg"
    candidates, seen = [], set()
    for node in images:
        ctx.checkpoint()
        raw = node.get("ec-data-src") or node.get("src", "")
        if not raw:
            continue
        link = safe_url(raw, url)
        parts = urlsplit(link)
        path = unquote(parts.path)
        if parts.hostname != "blskorea.hgodo.com":
            continue
        if product_path.fullmatch(path):
            role = "상품 상세/상품고시/실측표 원본"
        elif path == size_path:
            role = "공식 틴즈 사이즈가이드"
        else:
            # intro_teens.jpg, info.jpg, care/배송 등의 공통 자료는
            # 다운로드하거나 해독·404 검사하지 않는다.
            continue
        if link not in seen:
            candidates.append((link, role))
            seen.add(link)
    if not any(role != "공식 틴즈 사이즈가이드" for _, role in candidates):
        raise ValueError("상품별 상세 이미지 영역/공식 연결을 확인할 수 없음: 사이트 구조 확인 필요")
    output, cache, hashes, failures = [], {}, set(), []
    for link, role in candidates:
        ctx.checkpoint()
        result = public_image(ctx, link, cache)
        if result["status"] != "accessible":
            reason = role + " " + result["status"] + ": " + "; ".join(result["reasons"]) + " / " + link
            ctx.log(reason, "WARNING")
            if result["status"] == "unknown":
                failures.append(reason)
            continue
        # 상세의 통짜 이미지/실측 도식은 허용한다.
        # 대표·추가용 최소변/비율/측정사진 제외 규칙을 적용하지 않는다.
        digest = hashlib.sha256(result["data"]).hexdigest()
        if digest in hashes:
            continue
        hashes.add(digest)
        output.append('<p><img src="' + html.escape(link, quote=True) +
                      '" alt="' + html.escape(role, quote=True) + '"></p>')
    if failures:
        raise RuntimeError("상세 이미지 통신 확인 미완료: " + "; ".join(failures))
    result_html = "<div>" + "".join(output) + "</div>" if output else ""
    # Excel 제한은 UTF-16 코드 단위까지 보수적으로 확인한다.
    if len(result_html.encode("utf-16-le")) // 2 > 32767:
        raise ValueError("상세 HTML 셀 32767자 상한 초과: 임의 절단하지 않음")
    if not result_html:
        ctx.log("최종 상세 이미지 0장: 공개 접근 실패 사유 검수 필요", "WARNING")
    return result_html


def crawl(ctx):
    failures, matched, emitted = [], set(), set()
    try:
        base, requested = normalize_input(ctx.product_code)
        ctx.stage("공식 검색: " + base)
        products = discover(ctx, base)
    except Exception as exc:
        ctx.fail_item(str(exc))
        raise
    for no, url in products.items():
        ctx.checkpoint()
        try:
            ctx.stage("공식 상품 조회: " + no)
            soup, final = get_html(ctx, url)
            info = identity(soup, no, base)
            if info is None:
                ctx.log("검색 유사 품번 제외: " + no, "INFO")
                continue
            own, code, color_name, options, raw_name = info
            matched.add(code)
            if requested and requested != code:
                continue
            if code in emitted:
                raise ValueError("동일 색상에 공식 상품 ID가 중복됨: " + code)
            record = dict(product_code=base, product_name=clean_name(raw_name, own, base), product_url=final,
                          color=color_name or code, color_name=color_name, color_code=code)
            if ctx.enabled("size"):
                record["size"] = sizes(options)
            if ctx.enabled("price"):
                record["price"] = prices(ctx, soup, options)
            if ctx.enabled("category"):
                record["category"] = official_category(ctx, soup, final)
            if ctx.enabled("main_image") or ctx.enabled("additional_images"):
                main, extras = gallery(ctx, soup, final, no, code, own)
                if ctx.enabled("main_image"):
                    record["main_image"] = main
                if ctx.enabled("additional_images"):
                    record["additional_images"] = extras
            if ctx.enabled("detail_page"):
                record["detail_page"] = detail_page(ctx, soup, final, base, code)
            ctx.checkpoint()
            collect_regular_price(ctx, record, soup, 'cafe24')
            ctx.emit_result(record)
            emitted.add(code)
        except Exception as exc:
            message = f"상품 {no}: {exc}"
            failures.append(message)
            ctx.fail_item(message)
    if not failures and (not matched or (requested and requested not in matched)):
        message = "공식 품번/지정 색상 없음: " + base + ("_" + requested if requested else "")
        failures.append(message)
        ctx.fail_item(message)
    if failures:
        raise RuntimeError("; ".join(failures))
