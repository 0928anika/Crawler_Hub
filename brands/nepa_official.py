PORTAL_API_VERSION = 1

from regular_price import collect_regular_price, merge_regular_prices

import hashlib
from io import BytesIO
from html import escape
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs
from urllib.request import Request, build_opener
from bs4 import BeautifulSoup, Comment
from PIL import Image
from crawler_rules import (
    clean_product_name, resolve_input, check_public_image,
    gallery_slots, validate_gallery_image,
)

ORIGIN = "https://www.nplus.co.kr"
POLICY_DATE = "2026-11-06"
POLICY_SOURCE = "https://marketplace.coupang.com/mba/listing"
IMAGE_POLICY = {"min_side": 500, "max_bytes": 10000000, "max_aspect": 1.8}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
# WING 웹/엑셀: JPG/PNG, 최소 500px, 권장 1000x1000, 10MB 이하.
# 양변 500px, 10,000,000 bytes 및 비율 1.8은 프로젝트의 보수적 해석.
# 전체 판매 승인 또는 카테고리별 필수 인증 충족을 보장하지 않는다.
# 사용자 명시 변경: 실제 WING URL 등록 성공 경험에 따라 WEBP도 허용.
# WEBP는 위 공식 JPG/PNG 안내가 아니라 사용자 승인 프로젝트 정책이다.
# 실사이트 확인: 7M61860의 B65/J53, 별도 사이즈표/고시, 갤러리 16개.
# CDN은 Content-Type=image/jpeg이나 Pillow 판정은 WEBP였다.
# 별도 상품 7MF1562/B02의 품절 090 옵션도 확인했다.
# 로컬 실제 SDK 검사: 두 구분자, 하이픈 품번 오분리 방지, 선택 작업 생략,
# 일부 색상 실패/재실행 emit, 이미지 규격/제외 신호/슬롯, 중첩·긴 HTML,
# 누락 고시·사이즈표, 모의 HTTP 거부/HTML/통신 오류 구분을 확인했다.
# 전 사이즈 품절 상품 자체는 실사이트 미검증이며 재고로 건너뛰지 않는다.
# PORTAL GUI의 중단/이어하기/부분 결과 교체는 여기서 검증하지 못했다.
NEGATIVE = re.compile(r"size|guide|chart|measurement|measure|사이즈|실측|측정|고시|swatch|banner|collage|콜라주|배너|로고|logo|합성|통짜", re.I)


def soup(text):
    return BeautifulSoup(text, "html.parser")


def value(doc, name):
    node = doc.select_one('input[name="' + name + '"]')
    return node.get("value", "").strip() if node else ""


def get_html(ctx, url, form=None):
    body = urlencode(form).encode("utf-8") if form is not None else None
    for attempt in range(2):
        ctx.checkpoint()
        ctx.log("공식 HTML 요청: " + url, "INFO")
        try:
            request = Request(url, data=body, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"})
            with build_opener().open(request, timeout=20) as response:
                if urlsplit(response.geturl()).hostname != "www.nplus.co.kr":
                    raise ValueError("공식몰 외부로 이동한 응답")
                data = response.read(5000001)
                if len(data) > 5000000:
                    raise ValueError("HTML 최대 응답 크기 5MB 초과")
                text = data.decode(response.headers.get_content_charset() or "utf-8")
            if re.search(r"captcha|access denied|verify you are human", text, re.I):
                raise ValueError("접근 제한 또는 CAPTCHA 응답")
            return soup(text)
        except HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise RuntimeError("공식 HTML HTTP 오류: " + str(exc.code) + " " + url) from exc
            if attempt == 1:
                raise RuntimeError("공식 HTML 재시도 실패: " + url) from exc
        except (URLError, TimeoutError, OSError) as exc:
            if attempt == 1:
                raise RuntimeError("공식 HTML 통신 실패: " + url) from exc
        finally:
            ctx.checkpoint()
        time.sleep(0.5)
    raise RuntimeError("HTML 조회 실패: " + url)


def product_url(pno):
    if not re.fullmatch(r"\d+", str(pno)):
        raise ValueError("비정상 공식 상품 번호")
    return ORIGIN + "/product/view.asp?" + urlencode({"pno": pno})


def discover(ctx, base):
    doc = get_html(ctx, ORIGIN + "/search/search.asp?" + urlencode({"sWord": base}))
    roots = [doc]
    paging = doc.select_one("#paging")
    total = int(paging.get("data-tot", "0")) if paging else 0
    count = len(doc.select(".goods__list > li.goods"))
    form = [(n["name"], n.get("value", "")) for n in doc.select('#sFrm input[type="hidden"][name]')]
    for page in range(2, 102):
        if count >= total:
            break
        extra = get_html(ctx, ORIGIN + "/search/search_add.asp", form + [("page", page), ("mc", "0")])
        added = len(extra.select("li.goods"))
        if not added:
            raise RuntimeError("검색 결과 페이지가 중간에 비었습니다")
        roots.append(extra)
        count += added
    if count < total:
        raise RuntimeError("검색 페이지 상한 초과")
    found = {}
    for root in roots:
        for node in root.select('.goods__list li[data-no][data-code], li.goods li[data-no][data-code]'):
            code = node.get("data-code", "").upper()
            if code.startswith(base) and len(code) == len(base) + 3:
                found[node["data-no"]] = code[len(base):]
    if not found:
        raise RuntimeError("공식 검색 결과에서 해당 품번의 색상 변형을 찾지 못했습니다: " + base)
    return found


def public(ctx, url, cache):
    if url not in cache:
        ctx.checkpoint()
        cache[url] = check_public_image(url, timeout=10, max_bytes=10000000,
                                        retries=1, checkpoint=ctx.checkpoint)
        ctx.checkpoint()
        result = cache[url]
        for warning in result.get("warnings", []):
            ctx.log(url + " / " + warning, "WARNING")
        if result["status"] != "accessible":
            message = url + " / " + result["status"] + " / " + "; ".join(result.get("reasons", []))
            ctx.log(message, "WARNING")
            if result["status"] == "unknown":
                raise RuntimeError("선택 이미지의 공개 접근을 통신 오류로 확인하지 못함: " + message)
    result = cache[url]
    if result["status"] == "unknown":
        raise RuntimeError("공개 이미지 통신 실패: " + url)
    return result


def gallery(ctx, doc, page_url, base, color, cache):
    candidates, hashes = [], set()
    mapping = "공식 색상별 상품 페이지 " + page_url + "; ProductStyle=" + base + "; ProductColorCode=" + color
    for img in doc.select(".photo-slider-box .slide img[src]"):
        ctx.checkpoint()
        url = urljoin(page_url, img["src"])
        signals = url + " " + img.get("alt", "") + " " + img.get("title", "")
        slide = img.find_parent(class_="slide")
        if slide:
            signals += " " + slide.get_text(" ", strip=True)
        negative = NEGATIVE.search(signals)
        if negative:
            ctx.log(url + " / 갤러리 제외 신호: " + negative.group(0), "WARNING")
            continue
        response = public(ctx, url, cache)
        if response["status"] != "accessible":
            continue
        digest = hashlib.sha256(response["data"]).hexdigest()
        try:
            with Image.open(BytesIO(response["data"])) as photo:
                fmt, width, height = photo.format, photo.width, photo.height
                photo.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            ctx.log(url + " / 실제 파일 해독 실패: " + str(exc), "WARNING")
            continue
        ratio = max(width, height) / min(width, height)
        role_ok = bool(slide and img.find_parent(class_="photo__slider"))
        # 페이지의 공식 품번/색상은 crawl에서 대조했다. alt의 다른 품번은 거부한다.
        alt = img.get("alt", "")
        tokens = re.findall(r"(?<![A-Z0-9])[A-Z0-9]{10}(?![A-Z0-9])", alt.upper())
        mapped = value(doc, "ProductStyle") == base and value(doc, "ProductColorCode") == color
        mapped = mapped and all(token == base + color for token in tokens)
        evidence = ("DOM=.photo-slider-box .photo__slider .slide img; "
                    + "format=" + str(fmt) + "; pixels=" + str(width) + "x" + str(height)
                    + "; ratio=" + str(round(ratio, 3))
                    + "; alt=" + alt + "; 부정 신호 없음; 규칙 기반 개별 컷 판정")
        single = role_ok and fmt in ALLOWED_FORMATS and min(width, height) >= 500 and ratio <= 1.8
        validation = validate_gallery_image(response["data"], role="product_shot",
            mapping_verified=mapped, single_shot_verified=single,
            mapping_evidence=mapping, content_evidence=evidence, **IMAGE_POLICY)
        ctx.log(url + " / " + mapping + " / " + evidence, "INFO")
        if not validation.get("passed"):
            reason = "; ".join(validation.get("reasons", []))
            ctx.log(url + " / 제외: " + (reason or "UNKNOWN: 개별 컷 내용 미확인"), "WARNING")
            continue
        if digest not in hashes:
            hashes.add(digest)
            candidates.append({"url": url, "validation": validation})
    main, extras, warning = gallery_slots(candidates)
    if warning:
        ctx.log(str(warning), "WARNING")
    if not main:
        ctx.log("통과한 개별 컷 0장: 이미지 빈 값. 위 형식·공개 접근·DOM·제외 사유 확인 필요", "WARNING")
    return main or "", extras


def safe_fragment(ctx, node, page_url, cache):
    fragment = soup(str(node))
    for comment in fragment.find_all(string=lambda x: isinstance(x, Comment)):
        comment.extract()
    allowed = {"div", "p", "span", "br", "strong", "b", "em", "i", "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "h2", "h3", "h4", "img", "section"}
    for tag in list(fragment.find_all(True)):
        ctx.checkpoint()
        if tag.name is None or tag.parent is None:
            continue
        if tag.name in {"script", "style", "iframe", "object", "embed", "form", "input", "button", "svg", "link", "meta", "noscript"}:
            tag.decompose()
            continue
        if tag.name not in allowed:
            tag.unwrap()
            continue
        attrs = {}
        if tag.name in {"td", "th"}:
            attrs = {k: str(v) for k, v in tag.attrs.items() if k in {"colspan", "rowspan"} and str(v).isdigit()}
        if tag.name == "img":
            url = urljoin(page_url, tag.get("src", ""))
            if urlsplit(url).scheme not in {"http", "https"}:
                tag.decompose()
                continue
            response = public(ctx, url, cache)
            if response["status"] != "accessible":
                alt = tag.get("alt", "")
                if alt:
                    tag.replace_with(fragment.new_string(alt))
                else:
                    tag.decompose()
                continue
            attrs = {"src": url, "alt": tag.get("alt", ""), "style": "max-width:100%;height:auto"}
        tag.attrs = attrs
    return str(fragment)


def notice_table(doc):
    for button in doc.select(".detail-category-box button.open__btn"):
        if re.sub(r"\s+", "", button.get_text()) != "상품정보고시":
            continue
        content = button.parent.select_one(".con")
        if not content:
            return ""
        rows = []
        for label in content.find_all("dt"):
            value_node = label.find_next_sibling(["dt", "dd"])
            if value_node and value_node.name == "dd":
                key = label.get_text(" ", strip=True)
                val = value_node.get_text(" ", strip=True)
                if key and val:
                    rows.append((key, val))
        for tr in content.select("table tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"], recursive=False)]
            if len(cells) >= 2 and cells[0] and any(cells[1:]):
                rows.append((cells[0], " / ".join(cells[1:])))
        if not rows:
            lines = [line.strip() for line in content.get_text("\n", strip=True).splitlines() if line.strip()]
            return ("<table><tbody>" + "".join("<tr><td>" + escape(line) + "</td></tr>" for line in lines)
                    + "</tbody></table>") if lines else ""
        return ("<table><tbody>" + "".join("<tr><th>" + escape(key) + "</th><td>" + escape(val)
                                          + "</td></tr>" for key, val in dict.fromkeys(rows)) + "</tbody></table>")
    return ""


def detail(ctx, doc, page_url, pno, cache):
    parts = []
    seen = set()
    for img in doc.select(".photo-slider-box .photo__slider .slide img[src], .goods-detail-box > .left img[src]"):
        url = urljoin(page_url, img["src"])
        if url not in seen:
            seen.add(url)
            parts.append(safe_fragment(ctx, img, page_url, cache))
    notice = notice_table(doc)
    if notice:
        parts.append("<h2>상품정보고시</h2>" + notice)
    if doc.select_one(".sizeguide__btn"):
        guide_url = ORIGIN + "/pc/product/pop_sizeguide.asp?" + urlencode({"pno": pno})
        guide = get_html(ctx, guide_url)
        nodes = guide.select(".sizeguide__layer .con .img-box img[src], .sizeguide__layer .con .table-box")
        if any(node.select_one("table tr td, table tr th") for node in nodes):
            parts.append("<h2>사이즈가이드</h2>" + "".join(safe_fragment(ctx, node, guide_url, cache) for node in nodes))
    markup = "".join(parts)
    if len(markup.encode("utf-16-le")) // 2 > 32767:
        raise ValueError("상세 HTML이 Excel 셀 한도를 초과했습니다")
    return markup


def crawl(ctx):
    raw = str(ctx.product_code).strip().upper()
    match = re.fullmatch(r"([A-Z0-9]{7})(?:[_-]([A-Z0-9]{3}))?", raw)
    if not match:
        message = "지원 형식: 기본품번 7자리 또는 기본품번_공식색상코드 3자리 (하이픈도 허용)"
        ctx.fail_item(message)
        raise ValueError(message)
    base = match.group(1)
    failures = []
    try:
        ctx.stage("공식 품번 검색 및 색상 목록 확인")
        found = discover(ctx, base)
        resolved, requested = resolve_input(raw, [(base, color) for color in found.values()],
                                             has_color_codes=True, hyphen_alias=True)
        if resolved != base or (requested is not None and requested not in found.values()):
            raise ValueError("공식 조회 결과에 없는 색상코드")
    except Exception as exc:
        ctx.fail_item(str(exc))
        raise
    queue = list(found)
    processed = set()
    for pno in queue:
        ctx.checkpoint()
        if pno in processed:
            continue
        processed.add(pno)
        expected = found[pno]
        try:
            ctx.stage("색상 수집: " + base + "_" + expected)
            url = product_url(pno)
            doc = get_html(ctx, url)
            actual_base, color = value(doc, "ProductStyle"), value(doc, "ProductColorCode")
            if actual_base != base or color != expected:
                raise ValueError("공식 페이지 품번·색상 불일치: " + url)
            # 실제 색상 링크도 대조한다. 검색에서 빠진 형제 색상도 큐에 넣는다.
            for link in doc.select("._color a[href]"):
                href = urljoin(url, link["href"])
                other = parse_qs(urlsplit(href).query).get("pno", [""])[0]
                img = link.select_one("img[alt]")
                token = re.search(r"(?<![A-Z0-9])" + re.escape(base) + r"([A-Z0-9]{3})(?![A-Z0-9])", img.get("alt", "") if img else "")
                if other and token and other not in found:
                    found[other] = token.group(1)
                    queue.append(other)
            if requested and color != requested:
                continue
            name = clean_product_name(value(doc, "gaProductname"),
                ["NEPA", "NPLUS", "N+", "네파", "엔플러스"],
                [base, value(doc, "ProductCode"), base + "_" + color, base + "-" + color])
            color_name = value(doc, "ProductColor")
            if not name or not (color_name or color):
                raise ValueError("공식 필수 상품명·색상 누락")
            record = {"product_code": base, "product_name": name, "product_url": url, "color": color_name or color,
                      "color_name": color_name, "color_code": color}
            if ctx.enabled("size"):
                sizes = list(dict.fromkeys(n.get("data-size", "").strip() for n in doc.select('input[name="optSize"][data-size]')))
                record["size"] = ",".join(s for s in sizes if s)
                if not record["size"]:
                    ctx.log("공식 사이즈 옵션 없음", "WARNING")
            if ctx.enabled("price"):
                price = doc.select_one(".goods-info .price .sale")
                text = price.get_text(" ", strip=True) if price else ""
                amount = re.fullmatch(r"([\d,]+)\s*원", text)
                record["price"] = int(amount.group(1).replace(",", "")) if amount else None
                if not amount:
                    ctx.log("KRW 일반 판매가를 확인하지 못해 가격 빈 값: " + text, "WARNING")
            if ctx.enabled("category"):
                record["category"] = value(doc, "gaCategory").replace("|", " > ")
                if not record["category"]:
                    ctx.log("공식 카테고리 없음", "WARNING")
            cache = {}
            if ctx.enabled("main_image") or ctx.enabled("additional_images"):
                main, extras = gallery(ctx, doc, url, base, color, cache)
                if ctx.enabled("main_image"):
                    record["main_image"] = main
                if ctx.enabled("additional_images"):
                    record["additional_images"] = ",".join(extras)
            if ctx.enabled("detail_page"):
                record["detail_page"] = detail(ctx, doc, url, pno, cache)
            ctx.checkpoint()
            collect_regular_price(ctx, record, None, 'unsupported')
            ctx.emit_result(record)
            ctx.log(base + "_" + color + " 완료", "SUCCESS")
        except Exception as exc:
            message = base + "_" + expected + ": " + str(exc)
            ctx.fail_item(message)
            failures.append(message)
    if failures:
        raise RuntimeError("일부 색상 수집 실패: " + " | ".join(failures))
