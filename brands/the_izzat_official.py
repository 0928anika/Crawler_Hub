PORTAL_API_VERSION = 1

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
from bs4 import BeautifulSoup, Comment
from PIL import Image

# 2026-11-04 공식 HTML/JSON 확인. 이미지 매핑은 mdlNo -> goodsId -> 색상 고시 -> 해당 갤러리.
# 사용자 허용: 상세 미선택이어도 필수 색상코드 확인용 고시 JSON 조회.
# 고시 HTML 생성/실측 파싱/상세 이미지 검사는 detail_page 선택 시에만 수행.
# 규칙 기반 이미지 판정이며 사진 내용의 완전한 시각 인식은 아님.
# 실제 검증: ISRM0BL851 3색상, ISBA3LT081 2색상, BLL0 두 입력 구분자/전체 미선택.
# 실제 갤러리 JPEG 1500x2000 확인. 0재고/손상/WebP/콜라주 신호 등은 오프라인 검증.
# Crawler Hub 최종 Excel/재시작 교체/검수 정제기와 WING 등록은 별도 확인 필요.
ORIGIN = "https://pc.babathe.com"
IMAGE_ORIGIN = "https://image.babathe.com"
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
    "webp": "사용자 등록 경로의 정지 WebP 허용; 모든 WING 경로의 공식 보장 아님",
}
NEGATIVE = re.compile(
    r"size|guide|chart|measur|collage|montage|composite|banner|logo|swatch|notice|washing|shipping|"
    r"사이즈|실측|측정|고시|콜라주|합성|배너|로고|스와치|배송|세탁", re.I)
GALLERY = "#contentGoodsDtlGoodsDefaultBox .gallery-top .swiper-slide img"
DETAIL_IMAGES = ".prd-detail .img-box > div > img"
INPUT_PATTERN = re.compile(r"^([A-Z0-9]{10})(?:[_-]([A-Z0-9]{2,8}))?$")



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


def api(ctx, endpoint, **params):
    data, _, _ = get_bytes(ctx, ORIGIN + endpoint + "?" + urlencode(params), MAX_HTML)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("공개 JSON 구조 변경: " + endpoint)
    return value


def split_input(value):
    match = INPUT_PATTERN.fullmatch(str(value).strip().upper())
    if not match:
        raise ValueError("품번은 영문/숫자 10자리, 색상 지정은 _BLL0 또는 -BLL0 형식입니다")
    return match.groups()


def model_ids(ctx, base):
    data = api(ctx, "/goods/includeGoodsDtlModelNoList", mdlNo=base)
    rows = data.get("goodsMdlImgList")
    if not isinstance(rows, list):
        raise ValueError("동일 품번 목록 파싱 실패")
    result = []
    for row in rows:
        ctx.checkpoint()
        gid = row.get("goodsId", "")
        # 공식 mdlNo API는 GD뿐 아니라 GC 상품 ID도 반환한다.
        # IRCM2PD781의 GC 3색상과 IS8A0BL021의 GD 2색상 확인(2026-11-09).
        if not re.fullmatch(r"G[BCD]\d+", gid):
            raise ValueError("공식 상품 ID 누락/형식 변경")
        if gid not in result:
            result.append(gid)
    if not result:
        raise ValueError("공식 품번 검색 결과 없음: " + base)
    if len(result) > 100:
        raise ValueError("동일 품번 색상 수 안전 상한 초과")
    return result


def script_block(soup):
    scripts = "\n".join(node.get_text() for node in soup.find_all("script", src=False))
    match = re.search(r"var\s+_goodsDtlInfoData\s*=\s*\{(.*?)\};\s*this\.getGoodsDtlInfoData", scripts, re.S)
    if not match:
        raise ValueError("공식 상품 기본 정보 구조 변경")
    return scripts, match.group(1)


def literal(block, key):
    match = re.search(r"\b" + re.escape(key) + r'''\s*:\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''', block)
    if not match:
        raise ValueError("공식 상품 필수 값 누락: " + key)
    token = match.group(1)
    return json.loads(token) if token.startswith('"') else ast.literal_eval(token)


def plain(value):
    fragment = BeautifulSoup(str(value or ""), "html.parser")
    return fragment.get_text("\n", strip=True)


def identity(ctx, soup, gid, base):
    scripts, block = script_block(soup)
    match = re.search(r'''includeGoodsDtlModelNoList['"]\s*,\s*\{\s*mdlNo\s*:\s*['"]([^'"]+)['"]''', scripts)
    if not match or match.group(1) != base or literal(block, "goodsId") != gid:
        raise ValueError("공식 품번과 상품 ID 연결 불일치")
    brand = soup.select_one("#contentGoodsDtlBrandLink")
    if not brand or brand.get("href", "").rstrip("/") != "/brand/theizzat":
        raise ValueError("더아이잗 공식 브랜드 연결 불일치")
    raw_name = literal(block, "goodsNm").strip()
    if not raw_name:
        raise ValueError("공식 상품명 누락")
    # 사용자 허용 예외: 필수 색상코드 확인을 위한 고시 JSON 조회.
    data = api(ctx, "/goods/includeGoodsDtlNotifyList", goodsId=gid, stId="1", svcGbCd="10")
    rows = data.get("goodsNotifyList")
    if not isinstance(rows, list):
        raise ValueError("필수 색상 정보 JSON 파싱 실패")
    if any(not isinstance(row, dict) or row.get("goodsId") != gid for row in rows):
        raise ValueError("색상/고시 JSON 상품 ID 불일치")
    colors = [plain(row.get("itemVal")) for row in rows if row.get("itemNm") == "색상"]
    code, name = "", ""
    if len(colors) == 1:
        match = re.fullmatch(r"([A-Z0-9]{2,8})\s*\((.+)\)", colors[0], re.S)
        if match:
            code, name = match.group(1), match.group(2).strip()
        else:
            name = colors[0].strip()
    elif len(colors) > 1:
        raise ValueError("한 상품 ID에 여러 색상 고시가 있어 갤러리 연결 불명확")
    if not name:
        match = re.search(r"\(([^()]*)\)\s*$", raw_name)
        name = match.group(1).strip() if match else ""
        ctx.log("공식 색상코드 없음: 상품명에 명시된 색상명만 사용 / " + gid, "WARNING")
    if not name and not code:
        raise ValueError("공식 색상 정보 누락")
    suffix = re.search(r"\(([^()]*)\)\s*$", raw_name)
    if suffix and name and suffix.group(1).strip().casefold() != name.casefold():
        raise ValueError("상품명 색상과 고시 색상 불일치")
    name_without_color = re.sub(r"\s*\(" + re.escape(name) + r"\)\s*$", "", raw_name, flags=re.I) if name else raw_name
    for token in (r"더\s*아이잗", r"THE\s+IZZAT", re.escape(base)):
        name_without_color = re.sub(r"(?<![A-Za-z0-9가-힣])(?:" + token + r")(?![A-Za-z0-9가-힣])", " ", name_without_color, flags=re.I)
    product_name = re.sub(r"\s+", " ", name_without_color).strip(" _-()[]")
    if not product_name:
        raise ValueError("브랜드/품번 제거 후 상품명 없음")
    return dict(name=product_name, color=name or code, color_name=name, color_code=code,
                notice=rows, block=block)


def product_json(soup):
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(node.get_text())
        except (ValueError, TypeError):
            continue
        nodes = value.get("@graph", [value]) if isinstance(value, dict) else value if isinstance(value, list) else []
        for product in nodes:
            if isinstance(product, dict) and product.get("@type") == "Product":
                return product
    return {}


def size_values(ctx, gid):
    data = api(ctx, "/goods/includeGoodsDtlOption", goodsId=gid, goodsTpCd="10")
    attrs = data.get("goodsAttrList")
    if not isinstance(attrs, list):
        raise ValueError("공식 사이즈 옵션 구조 변경")
    found = [row for row in attrs if row.get("attrNm") == "사이즈"]
    if not found:
        ctx.log("공식 사이즈 옵션 없음 / " + gid, "WARNING")
        return ""
    if len(found) != 1 or not isinstance(found[0].get("goodsAttrValueList"), list):
        raise ValueError("공식 사이즈 옵션 파싱 실패")
    if found[0].get("goodsId") != gid:
        raise ValueError("사이즈 옵션 상품 ID 불일치")
    values = []
    for row in found[0]["goodsAttrValueList"]:
        ctx.checkpoint()
        value = str(row.get("attrVal") or "").strip()
        if not value or any(c in value for c in (",", "\r", "\n")):
            raise ValueError("사이즈 값 누락/직렬화 불가")
        # 재고/useYn에 따른 제외 없이 공식 표시 순서 유지.
        if value not in values:
            values.append(value)
    return ",".join(values)


def price_value(ctx, soup, gid, block):
    offer = product_json(soup).get("offers", {})
    if not isinstance(offer, dict) or offer.get("priceCurrency") != "KRW":
        ctx.log("KRW 가격 확인 불가: " + str(offer), "WARNING")
        return None
    match = re.search(r"\bsaleAmt\s*:\s*(\d+(?:\.\d+)?)", block)
    if not match:
        raise ValueError("공식 일반 판매가 saleAmt 파싱 실패")
    amount = Decimal(match.group(1))
    stock = api(ctx, "/goods/includeGoodsDtlItemStockQtyList", goodsId=gid)
    rows = stock.get("itemAmtStockQtyVOList")
    if not isinstance(rows, list):
        raise ValueError("옵션별 추가금 응답 파싱 실패")
    additions = set()
    for row in rows:
        if row.get("goodsId") != gid or row.get("addSaleAmt") is None:
            raise ValueError("옵션별 상품 ID/추가금 누락")
        extra = Decimal(str(row["addSaleAmt"]))
        if not extra.is_finite():
            raise ValueError("옵션별 추가금이 유효한 숫자가 아님")
        additions.add(extra)
    if len(additions) > 1:
        ctx.log("사이즈별 판매가가 달라 가격 단일 숫자 출력 불가", "WARNING")
        return None
    amount += next(iter(additions), Decimal(0))
    if amount < 0:
        raise ValueError("공식 판매가가 음수")
    return int(amount) if amount == amount.to_integral_value() else float(amount)


def category_value(ctx, soup):
    nav = soup.select_one("#contentGoodsDtlCateNavigationBox")
    if nav is None:
        # 이 사이트는 공식 breadcrumb HTML이 주석 처리되어 응답된다.
        for node in soup.find_all(string=lambda value: isinstance(value, Comment)):
            if 'id="contentGoodsDtlCateNavigationBox"' in node:
                nav = BeautifulSoup(str(node), "html.parser").select_one("#contentGoodsDtlCateNavigationBox")
                break
    values = [text(node) for node in nav.select("li")] if nav else []
    values = [value for value in values if value and value.upper() != "HOME"]
    if not values:
        ctx.log("공식 분류 경로 없음", "WARNING")
        return ""
    return " > ".join(values)


def image_key(url, gid):
    parts = urlsplit(url)
    match = re.match(r"^/goods/" + re.escape(gid) + "/" + re.escape(gid) + r"_(\d+)\.[A-Za-z0-9]+(?:/|$)", parts.path)
    return (gid, match.group(1)) if parts.hostname == "image.babathe.com" and match else None


def gallery_urls(ctx, soup, gid, info, cache):
    ctx.stage("색상 " + info["color"] + " 갤러리 검사")
    nodes = soup.select(GALLERY)
    candidates = [(safe_url(n.get("src", "")), " ".join((n.get("alt", ""), n.get("title", ""), " ".join(n.get("class", [])), " ".join(n.parent.get("class", [])) if n.parent else ""))) for n in nodes if n.get("src")]
    keys = {image_key(url, gid) for url, _ in candidates}
    original = product_json(soup).get("image")
    if isinstance(original, str) and image_key(original, gid) in keys and image_key(original, gid):
        candidates.insert(0, (safe_url(original), "구조화 Product.image; 갤러리와 동일 공식 이미지 ID"))
    accepted, seen, hashes, accepted_keys = [], set(), set(), set()
    for url, context in candidates:
        ctx.checkpoint()
        if url in seen:
            continue
        seen.add(url)
        key = image_key(url, gid)
        if not key:
            ctx.log("갤러리 상품 ID/이미지 역할 불명확 제외 / " + url, "INFO")
            continue
        if key in accepted_keys:
            continue
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
            ctx.log("갤러리 제외: " + "; ".join(reasons) + " / " + url, "INFO")
            continue
        digest = hashlib.sha256(result["data"]).hexdigest()
        if digest in hashes:
            continue
        hashes.add(digest)
        accepted_keys.add(key)
        accepted.append(url)
        ctx.log(f"채택: goodsId={gid}, color={info['color_code']}({info['color_name']}); 공식 mdlNo 목록/고시/상품별 갤러리 연결; DOM={GALLERY}; {fmt} {w}x{h}, ratio={ratio:.3f}, 정지; 경로/alt/title/클래스 부정 신호 없음; 규칙 기반 개별 촬영컷 / {url}", "SUCCESS")
    if not accepted:
        ctx.log("최종 갤러리 0장: 공개 접근/규격/매핑/제외 로그 검수 필요", "WARNING")
        return "", ""
    main, extras = accepted[0], accepted[1:]
    if not extras:
        ctx.log("유효 컷 1장: 추가 5슬롯에 대표 컷 재사용", "WARNING")
        extras = [main]
    return main, ",".join(extras[i % len(extras)] for i in range(5))


def escaped(value):
    return html.escape(plain(value), quote=True).replace("\n", "<br>")


def notice_table(ctx, rows, base):
    if not rows:
        ctx.log("공식 상품정보고시 항목 없음", "WARNING")
        return ""
    pairs = []
    for row in rows:
        label, value = plain(row.get("itemNm")), plain(row.get("itemVal"))
        if not label:
            raise ValueError("상품고시 항목명 파싱 실패")
        pairs.append((label, value))
    if not any(value for _, value in pairs):
        raise ValueError("원본 고시 항목은 있으나 값이 전부 비어 있음")
    body = '<tr><th>제품코드</th><td>' + html.escape(base) + '</td></tr>'
    body += "".join("<tr><th>" + html.escape(k) + "</th><td>" + html.escape(v).replace("\n", "<br>") + "</td></tr>" for k, v in pairs)
    return '<h3>상품정보고시</h3><table border="1" cellpadding="6" style="width:100%;border-collapse:collapse"><tbody>' + body + '</tbody></table>'


def safe_size_table(table):
    output = []
    for row in table.select("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = []
        for cell in row.find_all(("th", "td"), recursive=False):
            attrs = ""
            for name in ("rowspan", "colspan"):
                value = str(cell.get(name, ""))
                if value.isdigit() and 0 < int(value) <= 100:
                    attrs += " " + name + '="' + value + '"'
            cells.append("<" + cell.name + attrs + ">" + escaped(str(cell)) + "</" + cell.name + ">")
        if cells:
            output.append("<tr>" + "".join(cells) + "</tr>")
    if not output:
        raise ValueError("공식 실측 HTML 표가 있으나 행 추출 실패")
    return '<table border="1" cellpadding="6"><tbody>' + "".join(output) + '</tbody></table>'


def detail_html(ctx, soup, gid, base, info, cache):
    ctx.stage("색상 " + info["color"] + " 상세/고시/실측표")
    parts, used, cut_ids = [], set(), set()
    for node in soup.select(DETAIL_IMAGES):
        ctx.checkpoint()
        raw = node.get("src", "")
        if not raw:
            continue
        url = safe_url(raw)
        key = image_key(url, gid)
        if not key or key in cut_ids:
            continue
        cut_ids.add(key)
        result = public_image(ctx, url, cache)
        if result["status"] != "accessible":
            ctx.log("상세 이미지 " + result["status"] + ": " + "; ".join(result["reasons"]) + " / " + url, "WARNING")
            if result["status"] == "unknown":
                raise RuntimeError("선택된 상세 이미지 통신 확인 미완료: " + url)
            continue
        parts.append('<p><img src="' + html.escape(url, quote=True) + '" alt="상품 상세 이미지"></p>')
        used.add(url)
    if not parts:
        ctx.log("선택한 공식 상품 상세 이미지 없음/공개 접근 불가", "WARNING")
    parts.append(notice_table(ctx, info["notice"], base))
    sizes = soup.select(".prd-size")
    if sizes:
        parts.append("<h3>실측 사이즈</h3>")
    else:
        ctx.log("공식 실측 사이즈표 없음", "WARNING")
    table_seen = set()
    for section in sizes:
        ctx.checkpoint()
        # 실측 구역은 측정 그림 → 단위/치수표 → 측정 주석 순서로 출력한다.
        for node in section.select(".img img[src]"):
            ctx.checkpoint()
            url = safe_url(node["src"])
            if url in used or urlsplit(url).hostname != "image.babathe.com" or not urlsplit(url).path.startswith("/goods/realsize/"):
                continue
            result = public_image(ctx, url, cache)
            if result["status"] == "accessible":
                parts.append('<p><img src="' + html.escape(url, quote=True) + '" alt="공식 실측 위치 도식"></p>')
                used.add(url)
            else:
                ctx.log("실측 도식 " + result["status"] + " / " + url, "WARNING")
                if result["status"] == "unknown":
                    raise RuntimeError("실측 도식 통신 확인 미완료")
        unit = section.select_one(".tb-top")
        if unit:
            parts.append("<p>" + escaped(str(unit)) + "</p>")
        tables = section.select(".info table")
        for table in tables:
            built = safe_size_table(table)
            if built not in table_seen:
                parts.append(built)
                table_seen.add(built)
        columns = section.select(".tb-colCustom > li")
        if not tables and columns:
            rows = [[text(cell) for cell in column.find_all("span", recursive=False)] for column in columns]
            if not rows[0] or any(len(row) != len(rows[0]) for row in rows):
                raise ValueError("실측 치수 행/열 대응 파싱 실패")
            built = '<table border="1" cellpadding="6"><tbody>'
            for i, row in enumerate(rows):
                tag = "th" if i == 0 else "td"
                built += "<tr>" + "".join("<" + tag + ">" + html.escape(v) + "</" + tag + ">" for v in row) + "</tr>"
            built += '</tbody></table>'
            if built not in table_seen:
                parts.append(built)
                table_seen.add(built)
        if not tables and not columns:
            if section.select_one(".info"):
                raise ValueError("실측 정보 영역은 있으나 표 구조 파싱 실패")
        parent = section.find_parent(class_="detail-unit")
        if parent:
            for node in parent.select("ul.list-group > li"):
                note = text(node)
                if "오차" in note or "실측사이즈" in note or "실측 사이즈" in note:
                    parts.append("<p>" + html.escape(note) + "</p>")
    result = "<div>" + "".join(parts) + "</div>"
    if len(result.encode("utf-16-le")) // 2 > 32767:
        raise ValueError("상세 HTML 셀 32767자 초과: 임의 절단하지 않음")
    return result


def crawl(ctx):
    failures, matched, emitted = [], set(), set()
    try:
        base, requested = split_input(ctx.product_code)
        ctx.stage("공식 동일 품번 전체 색상 조회: " + base)
        ids = model_ids(ctx, base)
    except Exception as exc:
        ctx.fail_item(str(exc))
        raise
    for gid in ids:
        ctx.checkpoint()
        try:
            ctx.stage("공식 색상 상품 조회: " + gid)
            soup, final = get_html(ctx, ORIGIN + "/goods/indexGoodsDetail?" + urlencode({"goodsId": gid}))
            info = identity(ctx, soup, gid, base)
            code = info["color_code"]
            matched.add(code)
            if requested and code != requested:
                continue
            key = code or info["color_name"]
            if key in emitted:
                raise ValueError("같은 색상에 공식 상품 ID 중복: " + key)
            record = dict(product_code=base, product_name=info["name"], product_url=final, color=info["color"], color_name=info["color_name"], color_code=code)
            if ctx.enabled("size"):
                record["size"] = size_values(ctx, gid)
            if ctx.enabled("price"):
                record["price"] = price_value(ctx, soup, gid, info["block"])
            if ctx.enabled("category"):
                record["category"] = category_value(ctx, soup)
            cache = {}
            if ctx.enabled("main_image") or ctx.enabled("additional_images"):
                main, extras = gallery_urls(ctx, soup, gid, info, cache)
                if ctx.enabled("main_image"):
                    record["main_image"] = main
                if ctx.enabled("additional_images"):
                    record["additional_images"] = extras
            if ctx.enabled("detail_page"):
                record["detail_page"] = detail_html(ctx, soup, gid, base, info, cache)
            ctx.checkpoint()
            ctx.emit_result(record)
            emitted.add(key)
        except Exception as exc:
            message = gid + ": " + str(exc)
            failures.append(message)
            ctx.fail_item(message)
    if not failures and requested and requested not in matched:
        message = "공식 품번에 지정 색상코드 없음: " + base + "_" + requested
        ctx.fail_item(message)
        failures.append(message)
    if failures:
        raise RuntimeError("; ".join(failures))
