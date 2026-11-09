PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

import html
import io
import json
import re
import time
import warnings
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import Request, build_opener

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

ORIGIN = "https://smartstore.naver.com"
STORE = "ncdjys164"
LIMIT = 10_000_000
BAD = re.compile(r"banner|logo|swatch|collage|montage|composite|size|measure|chart|guide|배너|로고|고시|사이즈|실측|측정|콜라주|합성", re.I)

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


def fetch(ctx, url, limit=3_000_000):
    url = public_url(url)
    ctx.log("GET " + url, "INFO")
    for attempt in range(2):
        ctx.checkpoint()
        failure = None
        retry = False
        try:
            # 쿠키 저장, 로그인, 인증 헤더, Referer를 사용하지 않는다.
            req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"})
            with build_opener().open(req, timeout=15) as response:
                data = bytearray()
                while True:
                    ctx.checkpoint()
                    block = response.read(min(65536, limit + 1 - len(data)))
                    if not block:
                        break
                    data.extend(block)
                    if len(data) > limit:
                        raise CrawlError("응답 크기 제한 초과: " + url)
                result = bytes(data)
        except HTTPError as exc:
            failure = "HTTP %s: %s" % (exc.code, url)
            retry = exc.code in (408, 429, 500, 502, 503, 504)
        except (URLError, TimeoutError, OSError) as exc:
            failure = "네트워크 오류 %s: %s" % (exc, url)
            retry = True
        ctx.checkpoint()
        if failure is None:
            return result
        ctx.log(failure, "WARNING")
        if not retry or attempt:
            raise CrawlError(failure)
        for _ in range(5):
            ctx.checkpoint()
            time.sleep(0.1)
    raise CrawlError("요청 실패: " + url)


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


def clean_table(table):
    rows = []
    for row in table.select("tr"):
        cells = []
        for cell in row.find_all(["th", "td"], recursive=False):
            attrs = ""
            for key in ("rowspan", "colspan"):
                value = cell.get(key, "")
                if str(value).isdigit():
                    attrs += ' %s="%s"' % (key, value)
            cells.append('<%s%s style="border:1px solid #ccc;padding:6px">%s</%s>' %
                         (cell.name, attrs, html.escape(text(cell)), cell.name))
        if cells:
            rows.append("<tr>" + "".join(cells) + "</tr>")
    if not rows:
        raise CrawlError("공식 실측표가 있으나 행/열을 읽지 못했습니다")
    return '<table style="border-collapse:collapse"><tbody>' + "".join(rows) + "</tbody></table>"


def state_from(page):
    for script in page.find_all("script"):
        source = script.get_text()
        match = re.search(r"window\.__PRELOADED_STATE__\s*=\s*", source)
        if match:
            # JavaScript를 실행하지 않고 문자열 밖 undefined 토큰만 null로 바꾼다.
            raw = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b',
                         lambda m: "null" if m[0] == "undefined" else m[0], source[match.end():])
            try:
                data = json.JSONDecoder().raw_decode(raw)[0]
            except ValueError as exc:
                raise CrawlError("공개 상품 상태 데이터 파싱 실패") from exc
            if isinstance(data, dict):
                if (data.get("channel") or {}).get("url") != STORE:
                    raise CrawlError("지정한 NC 유성점 스마트스토어의 응답이 아닙니다")
                return data
    raise CrawlError("공개 상품 상태 데이터 없음. 로그인/접근 제한/페이지 구조 변경 확인 필요")


def product_from(page):
    state = state_from(page)
    for key in ("product", "simpleProductForDetailPage"):
        product = state.get(key, {})
        if isinstance(product, dict) and product.get("id") and product.get("name"):
            return product
    raise CrawlError("상품 데이터가 로딩되지 않았습니다")


def discover(ctx, code):
    links, seen = [], set()
    for number in range(1, 31):
        ctx.checkpoint()
        url = ORIGIN + "/" + STORE + "/search?" + urlencode({"q": code, "page": number})
        page = soup(ctx, url)
        search = state_from(page).get("keywordSearch", {})
        items = search.get("simpleProducts")
        if not isinstance(items, list) or str(search.get("keyword", "")).upper() != code:
            raise CrawlError("스토어 검색 응답/검색어 확인 실패")
        if int(search.get("page", 0)) != number:
            raise CrawlError("검색 페이지 넘김 불일치; 누락 방지를 위해 중단")
        total = search.get("totalCount")
        if not isinstance(total, int) or total < 0:
            raise CrawlError("공식 검색 결과 수를 읽을 수 없습니다")
        fresh = 0
        for item in items:
            ctx.checkpoint()
            item_id = str(item.get("id", ""))
            if not item_id.isdigit():
                raise CrawlError("검색 상품 ID 누락")
            if item_id in seen:
                continue
            seen.add(item_id)
            fresh += 1
            # 검색의 모델명과 상세 페이지의 모델명을 각각 재확인한다.
            model = item.get("naverShoppingSearchInfo", {}).get("modelName", "")
            if str(model).strip().upper() == code:
                links.append(ORIGIN + "/" + STORE + "/products/" + item_id)
        if len(seen) >= total:
            return links
        if not fresh:
            raise CrawlError("검색 결과가 반복되거나 일부 누락되었습니다")
    raise CrawlError("검색 30페이지 한도 초과")


def colors_from(product):
    colors = []
    for option in product.get("optionStandards", []):
        if str(option.get("optionGroupName", "")).strip() not in ("컬러", "색상", "Color", "COLOR"):
            continue
        value = str(option.get("optionName", "")).strip()
        if not value:
            continue
        match = re.fullmatch(r"\(([^()]+)\)\s*(.*)", value) or re.fullmatch(r"(\d+)\s+(.+)", value)
        color = {"color_name": match[2].strip(), "color_code": match[1].strip()} if match else {"color_name": value, "color_code": ""}
        if color not in colors:
            colors.append(color)
    if not colors:
        raise CrawlError("공식 색상 후보가 없습니다")
    return colors


def size_from(product):
    values = list(dict.fromkeys(str(o.get("optionName", "")).strip()
                  for o in product.get("optionStandards", [])
                  if str(o.get("optionGroupName", "")).strip() in ("사이즈", "Size", "SIZE")))
    # 조합별 데이터가 없는 원문에서 여러 사이즈를 색상 공통으로 추정하지 않는다.
    if len(values) != 1 or not values[0]:
        raise CrawlError("단일 공통 사이즈 상품만 지원: 색상별 조합 데이터 미확인")
    return values[0]


def clean_name(product, code):
    value = str(product["name"]).strip()
    brand = str(product.get("naverShoppingSearchInfo", {}).get("brandName", "")).strip()
    if brand:
        value = re.sub(r"^\s*(?:\[" + re.escape(brand) + r"\]|" + re.escape(brand) + r"(?=\s))\s*", "", value)
    value = re.sub(r"\(\s*" + re.escape(code) + r"\s*\)", "", value, flags=re.I)
    value = re.sub(r"(?<![A-Z0-9_-])" + re.escape(code) + r"(?![A-Z0-9_-])", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip(" _-/")
    if not value:
        raise CrawlError("정리한 상품명이 비어 있습니다")
    return value


def gallery(ctx, product):
    values = [product.get("representativeImageUrl", "")] + product.get("optionalImageUrls", [])
    result = []
    for value in values:
        ctx.checkpoint()
        if not value:
            continue
        url = public_url(value)
        if url in result:
            continue
        if BAD.search(urlsplit(url).path):
            ctx.log("명시적인 비상품컷 제외: " + url, "WARNING")
            continue
        try:
            width, height, fmt = image_info(ctx, url, candidate=True)
            if min(width, height) < 500 or max(width, height) / min(width, height) > 1.8:
                raise CrawlError("후보 크기/비율 불일치: %sx%s" % (width, height))
        except CrawlError as exc:
            ctx.log("사진 제외: %s / %s" % (url, exc), "WARNING")
            continue
        ctx.log("공식 갤러리 %s %sx%s: %s" % (fmt, width, height, url), "SUCCESS")
        result.append(url)
    return result


def render_detail(ctx, url):
    # 정상 공개 HTML의 상세 본문만 동적 로딩한다. 429/로그인/인증 화면은 우회하지 않는다.
    try:
        from playwright.sync_api import sync_playwright, Error as BrowserError
    except ImportError as exc:
        raise CrawlError("상세 동적 로딩에는 playwright와 Chromium 설치가 필요합니다") from exc
    ctx.checkpoint()
    try:
        with sync_playwright() as engine:
            browser = engine.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_default_timeout(12000)
                page.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media", "font") else route.continue_())
                response = page.goto(url, wait_until="domcontentloaded", timeout=25000)
                ctx.checkpoint()
                if response is None or response.status >= 400:
                    raise CrawlError("상세 브라우저 접속 실패: HTTP %s" % (response.status if response else "응답 없음"))
                if urlsplit(page.url).hostname not in ("smartstore.naver.com", "m.smartstore.naver.com"):
                    raise CrawlError("로그인/다른 사이트로 이동되어 상세 수집 중단")
                tab = page.get_by_role("button", name="상세정보", exact=True)
                if tab.count() and tab.first.is_visible():
                    tab.first.click()
                expand = page.locator('button[data-shp-area="pdinfo.more"]')
                if expand.count() and expand.first.is_visible():
                    expand.first.click()
                area = page.locator(".se-main-container").first
                for _ in range(20):
                    ctx.checkpoint()
                    if area.count() and area.is_visible():
                        area.scroll_into_view_if_needed()
                        break
                    page.mouse.wheel(0, 700)
                    page.wait_for_timeout(250)
                area.wait_for(state="attached", timeout=12000)
                markup = area.evaluate("el => el.outerHTML")
                if len(markup.encode("utf-8")) > 4_000_000:
                    raise CrawlError("상세 DOM 크기 제한 초과")
                ctx.checkpoint()
                return BeautifulSoup(markup, "html.parser")
            finally:
                browser.close()
    except BrowserError as exc:
        raise CrawlError("공개 상세 브라우저 로딩 실패: " + str(exc)) from exc


def detail(ctx, page):
    area = page.select_one(".se-main-container")
    if area is None:
        raise CrawlError("실제 상세 본문 영역이 없습니다")
    parts, seen = [], set()
    # 사용자 요청: 상세 영역 이미지 전체. 갤러리로 대체하거나 색상별로 나누지 않는다.
    for node in area.select("img"):
        ctx.checkpoint()
        value = node.get("data-src") or node.get("src", "")
        if not value:
            raise CrawlError("상세 이미지 URL이 비어 있습니다")
        url = public_url(value)
        if url in seen:
            continue
        seen.add(url)
        image_info(ctx, url)
        parts.append('<img src="%s" alt="" style="max-width:100%%;height:auto">' % html.escape(url, quote=True))
    mode = None
    for paragraph in area.select("p"):
        ctx.checkpoint()
        if paragraph.find_parent("table"):
            continue
        value = text(paragraph).replace("\u200b", "").strip()
        if not value:
            continue
        if value == "소재":
            mode = "material"
            parts.append("<h3>소재</h3>")
            continue
        if value.startswith("실측 사이즈"):
            mode = "size"
            parts.append("<h3>실측 사이즈</h3>")
            continue
        if re.match(r"[·ㆍ*\s]*(취급|관리|상담|배송|교환|반품|추가 정보|디테일 정보)", value):
            mode = None
        if mode in ("material", "size"):
            parts.append("<p>" + html.escape(value) + "</p>")
    for table in area.select("table"):
        if re.search(r"사이즈|총길이|가슴|허리|SIZE", text(table), re.I):
            parts.append(clean_table(table))
    if not parts:
        raise CrawlError("상세 이미지/소재/사이즈 본문이 비어 있습니다")
    return parts


def crawl(ctx):
    code = str(ctx.product_code).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{4,29}", code):
        ctx.fail_item("색상 접미사 없는 기본 품번을 입력하세요")
        raise CrawlError("품번 형식 오류")
    if not (ctx.enabled("main_image") or ctx.enabled("additional_images")):
        ctx.fail_item("수동 매핑에는 대표 또는 추가이미지 선택이 필요합니다")
        raise CrawlError("사진 수집 미선택")
    ctx.stage("지정 스마트스토어에서 기본 품번 검색")
    try:
        links = discover(ctx, code)
    except CrawlError as exc:
        ctx.fail_item(str(exc))
        raise
    records, colors, images, fragments, errors = [], [], [], [], []
    for url in links:
        ctx.checkpoint()
        try:
            page = soup(ctx, url)
            product = product_from(page)
            if str(product.get("naverShoppingSearchInfo", {}).get("modelName", "")).strip().upper() != code:
                ctx.log("다른 공식 모델명 제외: " + url, "INFO")
                continue
            record = {"product_code": code, "product_name": clean_name(product, code)}
            choices = colors_from(product)
            collect_regular_price(ctx, record, None, 'unsupported')
            records.append(record)
            for color in choices:
                if color not in colors:
                    colors.append(color)
            if ctx.enabled("size"):
                try:
                    record["size"] = size_from(product)
                except CrawlError as exc:
                    errors.append(url + " / " + str(exc))
            if ctx.enabled("price"):
                errors.append("옵션별 가격 공통 여부 미확인으로 가격 수집 미지원")
            if ctx.enabled("category"):
                value = str((product.get("category") or {}).get("wholeCategoryName", "")).strip()
                if value:
                    record["category"] = value
                else:
                    errors.append("공식 분류 누락: " + url)
            if ctx.enabled("detail_page"):
                try:
                    source = page if page.select_one(".se-main-container") else render_detail(ctx, url)
                    for part in detail(ctx, source):
                        if part not in fragments:
                            fragments.append(part)
                except CrawlError as exc:
                    errors.append(url + " / " + str(exc))
            candidates = gallery(ctx, product)
            if not candidates:
                errors.append("이 상품 링크의 유효 사진이 0장입니다: " + url)
            for image in candidates:
                if image not in images:
                    images.append(image)
        except CrawlError as exc:
            errors.append(url + " / " + str(exc))
    output = {"product_code": code}
    if records:
        output["product_name"] = records[0]["product_name"]
        for field in ("size", "category"):
            if ctx.enabled(field):
                values = [record.get(field) for record in records]
                if all(value is not None and value == values[0] for value in values):
                    output[field] = values[0]
                else:
                    errors.append("링크 간 공통 " + field + " 값 미확정")
    else:
        errors.append("지정 스토어에 정확한 기본 품번 상품이 없습니다: " + code)
    if ctx.enabled("detail_page") and fragments:
        markup = "<div>" + "".join(fragments) + "</div>"
        if len(markup.encode("utf-16-le")) // 2 > 32767:
            errors.append("공통 상세 HTML이 Excel 셀 32767자 한도 초과")
        else:
            output["detail_page"] = markup
    if not colors or not images:
        errors.append("공식 색상 또는 유효 개별 사진이 없어 수동 매핑 불가")
    merge_regular_prices(ctx, output, records)
    if records and colors:
        for image in images:
            ctx.checkpoint()
            ctx.emit_unmapped(dict(output), [dict(c) for c in colors], image, shared_detail_html=output.get("detail_page", ""))
    if errors:
        reason = "\n".join(dict.fromkeys(errors))
        ctx.fail_item(reason)
        raise CrawlError(reason)
    ctx.log("%s: 색상 %s개, 개별 사진 %s장 수집 완료" % (code, len(colors), len(images)), "SUCCESS")


