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
import os
import shutil
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path
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
# 2026-11-09: Chrome을 작업 출력폴더의 전용 프로필로 독립 실행하고
# 품번별 작업 프로세스는 로컬 CDP 연결만 해제한다. 다음 품번도 같은 창/탭 재사용.
# 공식 페이지 도착/새 탭/구글 검색어에 들어간 공식 URL 모두 후보로 회수한다.
# 서로 다른 두 실제 Python 프로세스의 같은 Chrome endpoint/target/session 유지 검증.
# CAPTCHA 인증 재개/링크 변형은 모의 상태 검증. IPAA0JK231 공식몰 전체 수집 별도 검증.
# 실제 Google CAPTCHA를 사용자가 완료하는 전체 검색 과정은 미검증.
# click-r5: 실제 DOM href 및 공식몰 제목 클릭 지원. IP3M1PO931 공식 데이터 수집 검증.
# r6: 공식 고시 제품코드를 우선 대조. 열린 크롬 HTML/고시/공개 JS 응답 재사용.
# IPAA0JK231/IP3M1PO931 크롬 실제 수집 및 상품 HTML/고시 별도 HTTP 0회 검증.
ORIGIN = "https://pc.babathe.com"
GOOGLE_QUERY_LIMIT = 2
GOOGLE_CANDIDATE_LIMIT = 20
GOOGLE_MANUAL_WAIT_SECONDS = 300
GOOGLE_RENDER_WAIT_SECONDS = 15
ADAPTER_REVISION = "2026-11-09-browser-verify-r6"
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
    gid = parse_qs(urlsplit(url).query).get("goodsId", [""])[0]
    cached = getattr(ctx, "_google_page_cache", {}).get(gid)
    if cached is not None:
        return BeautifulSoup(cached[0], "html.parser"), cached[1]
    data, content_type, final = get_bytes(ctx, url, MAX_HTML)
    if "html" not in content_type.lower() and not data.lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("상품 HTML 응답이 아님: " + final)
    if urlsplit(final).hostname not in ("www.babathe.com", "pc.babathe.com"):
        raise ValueError("공식몰 밖으로 이동한 상품 링크 거부: " + final)
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
    browser_data = browser_loaded_api(ctx, endpoint, params)
    if browser_data is not None:
        return browser_data
    data, _, _ = get_bytes(ctx, ORIGIN + endpoint + "?" + urlencode(params), MAX_HTML)
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("공개 JSON 구조 변경: " + endpoint)
    return value


def browser_loaded_api(ctx, endpoint, params):
    browser = getattr(ctx, "_google_browser", None)
    if browser is None:
        return None
    # 공식 페이지가 이미 받아 둔 공개 데이터만 읽는다. 추가 HTTP 요청 없음.
    methods = {
        "/goods/includeGoodsDtlNotifyList": ("getGoodsNotifyListJson", "goodsNotifyList"),
        "/goods/includeGoodsDtlModelNoList": ("getGoodsModelNoListJson", "goodsMdlImgList"),
        "/goods/includeGoodsDtlOption": ("getGoodsOptionListData", "goodsAttrList"),
        "/goods/includeGoodsDtlItemStockQtyList": ("getGoodsItemStockQtyListJson", "itemAmtStockQtyVOList"),
    }
    if endpoint not in methods:
        return None
    from playwright.sync_api import Error as BrowserError
    method, key = methods[endpoint]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        ctx.checkpoint()
        target = None
        for page in browser.pages:
            if page.is_closed():
                continue
            link = official_google_link(page.url)
            if not link or urlsplit(page.url).hostname not in ("www.babathe.com", "pc.babathe.com", "babathe.com"):
                continue
            gid = parse_qs(urlsplit(link).query)["goodsId"][0]
            if params.get("goodsId") and gid != params["goodsId"]:
                continue
            try:
                if params.get("mdlNo") and page_model_code(BeautifulSoup(page.content(), "html.parser"))[0] != params["mdlNo"]:
                    continue
                target = page
                data = page.evaluate("""method => {
                    if (typeof babatheGoodsUtilSetData === 'undefined') return null;
                    const getter = babatheGoodsUtilSetData[method];
                    if (typeof getter !== 'function') return null;
                    const result = getter.call(babatheGoodsUtilSetData);
                    return result && result.data ? result.data : null;
                }""", method)
                if isinstance(data, dict) and isinstance(data.get(key), list):
                    ctx.log("크롬이 이미 받은 공식 데이터 재사용: " + endpoint, "INFO")
                    return data
            except (ValueError, BrowserError):
                continue
        if target is None:
            return None
        target.wait_for_timeout(200)
    return None


def split_input(value):
    match = INPUT_PATTERN.fullmatch(str(value).strip().upper())
    if not match:
        raise ValueError("품번은 영문/숫자 10자리, 색상 지정은 _BLL0 또는 -BLL0 형식입니다")
    return match.groups()


def official_google_link(href):
    """실제로 발견한 URL/구글 이동 URL만 풀고 공식 상품 URL로 정리한다."""
    pending = [html.unescape(str(href or "")).strip()]
    seen = set()
    for _ in range(8):
        if not pending:
            break
        link = pending.pop(0).strip().strip('"')
        if link in seen:
            continue
        seen.add(link)
        if link.startswith("/"):
            link = urljoin("https://www.google.com", link)
        parts = urlsplit(link)
        if parts.hostname in ("www.google.com", "google.com", "www.google.co.kr") and parts.path in ("/url", "/search"):
            query = parse_qs(parts.query)
            # Google은 q가 아닌 url에 이동 대상을 넣는 형식도 사용한다.
            for key in ("url", "q"):
                pending.extend(query.get(key, []))
            continue
        if parts.scheme not in ("http", "https") or parts.hostname not in ("babathe.com", "www.babathe.com", "pc.babathe.com") or parts.username or parts.password:
            continue
        if "%3f" in parts.path.lower():
            pending.append(unquote(link))
            continue
        path = parts.path.split(";", 1)[0].rstrip("/")
        if path != "/goods/indexGoodsDetail":
            continue
        gid = parse_qs(parts.query).get("goodsId", [""])[0]
        if re.fullmatch(r"G[BCD]\d+", gid):
            return "https://www.babathe.com/goods/indexGoodsDetail?" + urlencode({"goodsId": gid})
    return ""


def google_blocked(soup, final):
    body = text(soup)
    return (urlsplit(final).path.startswith("/sorry")
            or soup.select_one('form[action*="sorry"], .g-recaptcha, iframe[src*="recaptcha"]') is not None
            or "비정상적인 트래픽" in body
            or "unusual traffic" in body.lower())


def google_links(soup):
    links = []
    for node in soup.select("a[href]"):
        link = official_google_link(node["href"])
        if link and link not in links:
            links.append(link)
    return links[:GOOGLE_CANDIDATE_LIMIT]


def google_live_candidates(page):
    # 정적 HTML 문자열뿐 아니라 브라우저가 해석한 실제 href와 제목도 읽는다.
    return page.locator("a").evaluate_all("""els => els.map((e, index) => {
        const heading = e.querySelector('h3,[role="heading"]');
        let card = e;
        if (heading) {
            for (let depth=0; depth<4 && card.parentElement; depth++) {
                if (card.querySelector('cite')) break;
                const parent = card.parentElement;
                if ((parent.innerText || '').length > 2500 || parent.querySelectorAll('h3').length > 2) break;
                card = parent;
            }
        }
        return {index, href:e.href || '', raw:e.getAttribute('href') || '',
                title:heading ? heading.innerText : '',
                visible:!!(e.getClientRects().length),
                citation:heading ? Array.from(card.querySelectorAll('cite')).map(c=>c.innerText).join(' ') : ''};
    })""")


def google_live_links(candidates):
    links = []
    for candidate in candidates:
        for raw in (candidate.get("href"), candidate.get("raw")):
            link = official_google_link(raw)
            if link and link not in links:
                links.append(link)
    return links[:GOOGLE_CANDIDATE_LIMIT]


def click_official_google_title(ctx, page, candidates, attempted):
    from playwright.sync_api import Error as BrowserError
    citation_pattern = re.compile(r"(?<![A-Za-z0-9.-])(?:https?://)?(?:www\.|pc\.)?babathe\.com(?=[/\s›»]|$)", re.I)
    for candidate in candidates:
        if not candidate.get("visible") or not candidate.get("title") or not citation_pattern.search(candidate.get("citation", "")):
            continue
        key = (candidate.get("href", ""), candidate["title"])
        if key in attempted:
            continue
        parsed = urlsplit(urljoin(page.url, candidate.get("href") or candidate.get("raw") or "#"))
        if parsed.hostname not in ("www.google.com", "google.com", "www.google.co.kr", "www.babathe.com", "pc.babathe.com", "babathe.com"):
            continue
        attempted.add(key)
        handles = page.query_selector_all("a")
        index = candidate["index"]
        if index >= len(handles):
            continue
        handle = handles[index]
        # 결과가 갱신돼 요소 순서가 달라졌다면 다른 링크를 누르지 않는다.
        current = handle.evaluate("e => ({href:e.href || '', title:(e.querySelector('h3,[role=heading]') || {}).innerText || ''})")
        if current.get("href") != candidate.get("href") or current.get("title") != candidate["title"]:
            continue
        try:
            ctx.log("공식몰로 표시된 검색 결과 제목을 직접 엽니다: " + candidate["title"], "INFO")
            handle.click(timeout=5000)
            return True
        except BrowserError as exc:
            ctx.log("검색 결과 제목 열기 확인 중: " + str(exc).splitlines()[0], "WARNING")
    return False


def chrome_executable(runtime):
    candidates = [shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium")]
    candidates.extend([
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        str(Path.home() / 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
    ])
    for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if root:
            candidates.append(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    candidates.append(runtime.chromium.executable_path)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Chrome 또는 Playwright Chromium이 필요합니다. Chrome 설치 또는 python -m playwright install chromium 실행 후 재시도하세요")


def chrome_endpoint(work):
    # 이 작업 전용 프로필이 기록한 로컬 Chrome 연결 정보만 사용한다.
    try:
        lines = (work / "profile" / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
        port = int(lines[0])
        if not 1 <= port <= 65535 or len(lines) < 2 or not lines[1].startswith("/devtools/browser/"):
            return None
        with build_opener().open("http://127.0.0.1:" + str(port) + "/json/version", timeout=1) as response:
            value = json.loads(response.read(65536))
        endpoint = value.get("webSocketDebuggerUrl", "")
        parts = urlsplit(endpoint)
        if parts.scheme == "ws" and parts.hostname in ("127.0.0.1", "localhost") and parts.port == port and parts.path == lines[1]:
            return endpoint
    except (OSError, ValueError, IndexError, URLError):
        return None
    return None


@contextmanager
def google_browser_session(ctx):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("수집용 크롬 연결에 Playwright가 필요합니다") from exc
    work = (Path(ctx.output_dir) / "google_chrome").resolve()
    (work / "profile").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as runtime:
        ctx.checkpoint()
        endpoint = chrome_endpoint(work)
        if not endpoint:
            # 별도 Chrome 프로세스를 시작하고 각 품번 작업은 CDP로 연결만 한다.
            # 작업 프로세스 종료 시에도 browser.close/context.close를 호출하지 않는다.
            lock = work / "starting.lock"
            owner = False
            try:
                try:
                    handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(handle)
                    owner = True
                except FileExistsError:
                    if time.time() - lock.stat().st_mtime > 60:
                        lock.unlink()
                        raise RuntimeError("이전 크롬 시작 잠금이 남아 정리했습니다. 해당 품번을 재실행하세요")
                if owner:
                    executable = chrome_executable(runtime)
                    command = [executable, "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + str(work / "profile"), "--no-first-run", "--no-default-browser-check", "about:blank"]
                    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
                    subprocess.Popen(command, cwd=str(work), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != "nt")
                deadline = time.monotonic() + 30
                while not endpoint and time.monotonic() < deadline:
                    ctx.checkpoint()
                    time.sleep(0.2)
                    endpoint = chrome_endpoint(work)
                if not endpoint:
                    raise RuntimeError("수집용 크롬 연결 실패. 이전 버전 수집용 창이 열려 있으면 한 번 닫고 재실행하세요. 이후에는 같은 창을 계속 재사용합니다")
                ctx.log("수집용 크롬 창을 처음 열었습니다. 다음 품번에서도 유지합니다", "INFO")
            finally:
                if owner:
                    lock.unlink(missing_ok=True)
        else:
            ctx.log("이미 인증한 수집용 크롬 창에 다시 연결했습니다", "SUCCESS")
        browser = runtime.chromium.connect_over_cdp(endpoint, timeout=15000)
        if not browser.contexts:
            raise RuntimeError("수집용 크롬 기본 창을 찾지 못했습니다")
        try:
            yield browser.contexts[0]
        finally:
            # Playwright 통신만 끊는다. 독립 실행한 Chrome 창/탭은 닫지 않는다.
            ctx.log("현재 품번의 크롬 연결 해제. 창은 다음 품번을 위해 유지합니다", "INFO")


def open_official_tabs(page, base=None):
    from playwright.sync_api import Error as BrowserError
    context = getattr(page, "context", None)
    pages = list(context.pages) if context is not None else [page]
    urls = []
    for tab in pages:
        if tab.is_closed():
            continue
        link = official_google_link(tab.url)
        if not link:
            continue
        if tab is not page:
            # 前 품번의 다른 탭을 다음 품번 결과로 재사용하지 않는다.
            if not base:
                continue
            try:
                soup = BeautifulSoup(tab.content(), "html.parser")
                model, _ = page_model_code(soup)
                if model != base:
                    continue
            except (ValueError, RuntimeError, BrowserError):
                continue
        if link not in urls:
            urls.append(link)
    return urls


def wait_google_page(ctx, page, initial_status=None, base=None):
    from playwright.sync_api import Error as BrowserError
    started = time.monotonic()
    render_started = started
    manual_started = None
    waiting = False
    last_notice = started
    attempted_clicks = set()
    last_click = started - 2
    while True:
        ctx.checkpoint()
        direct_links = open_official_tabs(page, base)
        if direct_links:
            ctx.log("이미 도착한 공식 상품 URL 확인. 추가 검색 없이 수집으로 전달: " + ", ".join(direct_links), "SUCCESS")
            return direct_links
        if page.is_closed():
            raise RuntimeError("수집용 크롬 창이 닫혔습니다. 해당 품번을 재실행하세요")
        try:
            source = page.content()
        except BrowserError:
            # 사용자 인증 후 페이지 전환 중에는 DOM 읽기가 일시적으로 실패할 수 있다.
            if page.is_closed():
                raise RuntimeError("수집용 크롬 창이 닫혔습니다. 해당 품번을 재실행하세요")
            if time.monotonic() - started > GOOGLE_MANUAL_WAIT_SECONDS:
                raise RuntimeError("구글 페이지 전환 대기 시간 초과")
            page.wait_for_timeout(250)
            continue
        soup = BeautifulSoup(source, "html.parser")
        current_url = page.url
        host = urlsplit(current_url).hostname
        direct = official_google_link(current_url)
        if direct:
            ctx.log("수집용 창에서 연 공식 상품 링크 확인", "SUCCESS")
            return [direct]
        if host not in ("www.google.com", "google.com", "www.google.co.kr", "consent.google.com", "consent.google.co.kr", "accounts.google.com"):
            raise RuntimeError("구글/바바더 공식몰 이외 페이지로 이동했습니다: " + current_url)
        results_page = host in ("www.google.com", "google.com", "www.google.co.kr") and urlsplit(current_url).path == "/search"
        blocked = google_blocked(soup, current_url)
        links = google_links(soup) if results_page and not blocked else []
        candidates = []
        if results_page and not blocked:
            try:
                candidates = google_live_candidates(page)
                for link in google_live_links(candidates):
                    if link not in links:
                        links.append(link)
            except BrowserError:
                # 인증/검색 결과가 전환 중이면 다음 체크포인트에서 다시 읽는다.
                pass
        if links:
            if waiting:
                ctx.log("직접 인증 후 검색 결과 확인. 수집을 자동으로 이어갑니다", "SUCCESS")
            return links
        body = text(soup)
        no_results = "일치하는 검색결과가 없습니다" in body or "did not match any documents" in body
        rendered = results_page and not blocked and (soup.select_one("#search, #rso") is not None or no_results)
        needs_human = blocked or not results_page or (initial_status in (403, 429) and not rendered)
        now = time.monotonic()
        if results_page and not blocked and candidates and len(attempted_clicks) < 3 and now - last_click >= 2:
            if click_official_google_title(ctx, page, candidates, attempted_clicks):
                last_click = now
                render_started = now
                page.wait_for_timeout(250)
                continue
        if needs_human:
            if manual_started is None:
                manual_started = getattr(ctx, "_google_auth_started", now)
                ctx._google_auth_started = manual_started
            if not waiting:
                ctx.stage("사용자 인증 대기: 수집용 크롬 창에서 CAPTCHA/동의를 직접 완료해주세요")
                ctx.log("현재 품번에서 최대 5분 인증을 기다립니다. 인증 후 자동 재개하며 창은 다음 품번에서도 유지합니다", "WARNING")
                waiting = True
                last_notice = now
            if now - manual_started >= GOOGLE_MANUAL_WAIT_SECONDS:
                raise RuntimeError("사용자 인증 대기 5분 초과. 인증을 완료한 후 품번을 재실행하세요")
            if now - last_notice >= 30:
                ctx.log("사용자 인증 대기 중: 수집용 크롬 창을 확인해주세요", "INFO")
                last_notice = now
        else:
            if waiting:
                ctx.log("인증 화면 종료 확인. 검색 결과를 기다립니다", "INFO")
                ctx.stage("인증 후 구글 검색 결과 확인")
                waiting = False
                render_started = now
            if no_results or now - render_started >= GOOGLE_RENDER_WAIT_SECONDS:
                ctx.log("공식 링크 추출 0건. 현재 검색창 URL=" + current_url + ", 링크 요소=" + str(len(soup.select('a[href]'))), "WARNING")
                return []
        page.wait_for_timeout(250)


def google_browser_links(ctx, page, url, base=None):
    from playwright.sync_api import TimeoutError as NavigationTimeout
    ctx.checkpoint()
    response = None
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except NavigationTimeout:
        ctx.log("구글 페이지 로딩 지연: 열린 창에서 현재 화면을 확인합니다", "WARNING")
    page.bring_to_front()
    links = wait_google_page(ctx, page, response.status if response else None, base=base)
    for link in links:
        ctx.log("공식 상품 링크 발견: " + link, "SUCCESS")
    if links:
        # 공식 URL을 찾으면 검색을 반복하지 않고 실제 공식 페이지를 연다.
        target = links[0]
        if official_google_link(page.url) != target or urlsplit(page.url).hostname not in ("www.babathe.com", "pc.babathe.com", "babathe.com"):
            ctx.stage("발견한 공식 상품 페이지 열기")
            ctx.log("공식 상품 페이지 접근: " + target, "INFO")
            try:
                page.goto(target, wait_until="domcontentloaded", timeout=30000)
            except NavigationTimeout:
                ctx.log("공식 페이지 표시 지연. 발견한 URL의 품번 검증/수집을 계속합니다", "WARNING")
        actual = official_google_link(page.url)
        if actual and actual not in links:
            links.insert(0, actual)
    return links


def page_model_code(soup):
    scripts, block = script_block(soup)
    codes = []
    def read_code(root):
        for row in root.select("tr"):
            label, value = row.find("th"), row.find("td")
            if label is not None and value is not None and re.sub(r"\s+", "", text(label)) == "제품코드":
                code = text(value).strip().upper()
                if not re.fullmatch(r"[A-Z0-9]{10}", code):
                    raise ValueError("공식 고시 제품코드 형식 확인 필요: " + code)
                if code not in codes:
                    codes.append(code)
    table = soup.select_one("#contentGoodsDtlGoodsNotifyListContent")
    if table is not None:
        read_code(table)
    if not codes:
        # 정적 응답의 tbody는 비어 있고 사이트 JS가 아래 원본 행을 넣는다.
        # 크롤러가 만든 출력 표가 아니라 공식 응답의 원본 행만 읽는다.
        for match in re.finditer(r"""\b_objInit011Html\s*=\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')""", scripts):
            token = match.group(1)
            fragment = json.loads(token) if token.startswith('"') else ast.literal_eval(token)
            read_code(BeautifulSoup(fragment, "html.parser"))
    if len(codes) > 1:
        raise ValueError("공식 고시 제품코드가 서로 다릅니다: " + ",".join(codes))
    # Java 서버가 경로에 ;jsessionid를 붙여도 mdlNo 자체는 동일하다.
    # 상품명/검색 요약문으로 품번을 대신 판단하지 않는다.
    match = re.search(r"""includeGoodsDtlModelNoList(?:[;?][^'\"]*)?['\"]\s*,\s*\{[^}]*?['\"]?mdlNo['\"]?\s*:\s*['\"]([^'\"]+)['\"]""", scripts, re.S)
    if match:
        model = match.group(1).strip().upper()
        if codes and codes[0] != model:
            raise ValueError("공식 고시 제품코드=" + codes[0] + ", mdlNo=" + model + " 불일치")
        return (codes[0] if codes else model), block
    if codes:
        return codes[0], block
    model = product_json(soup).get("mpn")
    if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9]{10}", model.strip()):
        return model.strip().upper(), block
    raise ValueError("공식 mdlNo/MPN 필드를 확인하지 못했습니다")


def official_brand_link(soup):
    brand = soup.select_one("#contentGoodsDtlBrandLink")
    parts = urlsplit(urljoin(ORIGIN, brand.get("href", ""))) if brand else None
    return bool(parts and parts.hostname in ("www.babathe.com", "pc.babathe.com", "babathe.com") and parts.path.split(";", 1)[0].rstrip("/") == "/brand/theizzat")


def verify_candidate_page(ctx, soup, gid, base, url):
    model, block = page_model_code(soup)
    actual_gid = literal(block, "goodsId")
    belongs = official_brand_link(soup)
    if model != base or actual_gid != gid or not belongs:
        raise ValueError("품번 검증 탈락: 요청=" + base + ", 페이지=" + model + ", 요청ID=" + gid + ", 페이지ID=" + str(actual_gid) + ", 더아이잗=" + str(belongs) + ", URL=" + url)
    ctx.log("공식 페이지 검증 통과: 요청=" + base + ", 페이지=" + model + ", goodsId=" + gid, "SUCCESS")
    return block


def browser_product_html(ctx, browser, url):
    from playwright.sync_api import Error as BrowserError, TimeoutError as NavigationTimeout
    target = official_google_link(url)
    if not target:
        raise ValueError("바바더 공식 상품 URL이 아닙니다: " + url)
    page = next((tab for tab in browser.pages if not tab.is_closed() and official_google_link(tab.url) == target), None)
    if page is None:
        page = browser.pages[0] if browser.pages else browser.new_page()
        ctx.checkpoint()
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=30000)
        except NavigationTimeout:
            ctx.log("공식 상품 페이지 표시 지연: 열린 크롬 DOM을 기다립니다", "WARNING")
    deadline = time.monotonic() + 20
    last_error = "상품 기본 정보 로딩 대기"
    last_valid = None
    ready_at = None
    while time.monotonic() < deadline:
        ctx.checkpoint()
        if page.is_closed():
            raise RuntimeError("공식 상품 검증 중 크롬 탭이 닫혔습니다")
        try:
            final = official_google_link(page.url)
            if not final:
                raise ValueError("공식 상품 페이지에서 다른 주소로 이동: " + page.url)
            soup = BeautifulSoup(page.content(), "html.parser")
            script_block(soup)
            last_valid = (soup, final)
            if ready_at is None:
                ready_at = time.monotonic()
            table = soup.select_one("#contentGoodsDtlGoodsNotifyListContent")
            if table is not None and any(text(row.find("th")) == "색상" and text(row.find("td")) for row in table.select("tr")):
                return soup, final
            if time.monotonic() - ready_at >= 5:
                return soup, final
        except (ValueError, BrowserError) as exc:
            last_error = str(exc)
        page.wait_for_timeout(250)
    if last_valid:
        return last_valid
    raise RuntimeError("크롬 공식 상품 HTML 확인 실패: " + last_error)


def google_candidate_urls(ctx, base):
    links = []
    queries = [base, '"' + base + '" site:babathe.com/goods/indexGoodsDetail']
    # 검색은 처음부터 보이는 크롬에서 실행. 별도 HTTP 요청이 CAPTCHA를 만나
    # 수집용 창을 열기도 전에 실패하는 이전 경로를 사용하지 않는다.
    active = getattr(ctx, "_google_browser", None)
    session = nullcontext(active) if active is not None else google_browser_session(ctx)
    rejected = []
    with session as browser:
        page = browser.pages[0] if browser.pages else browser.new_page()
        for query in queries[:GOOGLE_QUERY_LIMIT]:
            ctx.stage("수집용 크롬에서 공식몰 상품 검색: " + query)
            url = "https://www.google.com/search?" + urlencode({"q": query, "hl": "ko", "num": 10})
            found = google_browser_links(ctx, page, url, base=base)
            for link in found:
                ctx.checkpoint()
                gid = parse_qs(urlsplit(link).query)["goodsId"][0]
                try:
                    soup, final = browser_product_html(ctx, browser, link)
                    verify_candidate_page(ctx, soup, gid, base, final)
                    if not hasattr(ctx, "_google_page_cache"):
                        ctx._google_page_cache = {}
                    ctx._google_page_cache[gid] = (str(soup), final)
                    if final not in links:
                        links.append(final)
                except Exception as exc:
                    message = link + " / " + str(exc)
                    rejected.append(message)
                    ctx.log("검색 후보 검증 실패: " + message, "WARNING")
            if links:
                break
    if not links:
        if rejected:
            raise ValueError("공식 링크는 찾았으나 상품 검증 실패: " + " / ".join(rejected))
        raise ValueError("구글 검색에서 바바더 공식 상품 링크를 찾지 못했습니다: " + base)
    return links[:GOOGLE_CANDIDATE_LIMIT]


def model_ids(ctx, base):
    # 구글은 링크 발견에만 사용한다. 상품 데이터는 공식몰에서만 수집한다.
    result, failures = [], []
    for url in google_candidate_urls(ctx, base):
        ctx.checkpoint()
        gid = parse_qs(urlsplit(url).query)["goodsId"][0]
        try:
            soup, final = get_html(ctx, url)
            verify_candidate_page(ctx, soup, gid, base, final)
            if gid not in result:
                result.append(gid)
        except Exception as exc:
            failures.append(str(exc))
            ctx.log("구글 후보 상품 확인 실패: " + url + " / " + str(exc), "WARNING")
    if not result:
        raise ValueError("구글 공식 링크에서 정확한 더아이잗 품번을 확인하지 못했습니다: " + base + (" / " + "; ".join(failures) if failures else ""))
    # 검색으로 찾은 상품을 기준으로 공식 색상 목록이 있으면 함께 확장한다.
    data = api(ctx, "/goods/includeGoodsDtlModelNoList", mdlNo=base)
    rows = data.get("goodsMdlImgList")
    if not isinstance(rows, list):
        raise ValueError("공식 동일 품번 색상 목록 구조 변경")
    for row in rows:
        gid = row.get("goodsId", "")
        if not re.fullmatch(r"G[BCD]\d+", gid):
            raise ValueError("공식 상품 ID 누락/형식 변경")
        if gid not in result:
            result.append(gid)
    if not rows:
        ctx.log("공식 동일 품번 색상 목록 없음: 구글에서 발견하고 품번을 검증한 색상만 수집. 미발견 색상은 보장할 수 없음", "WARNING")
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
    block = verify_candidate_page(ctx, soup, gid, base, "goodsId=" + gid)
    raw_name = literal(block, "goodsNm").strip()
    if not raw_name:
        raise ValueError("공식 상품명 누락")
    # 공식 크롬 고시 DOM을 우선 사용하고 없을 때만 공개 고시 데이터 조회.
    rows = []
    for tr in soup.select("#contentGoodsDtlGoodsNotifyListContent tr"):
        label, value = tr.find("th"), tr.find("td")
        if label is not None and value is not None and re.sub(r"\s+", "", text(label)) != "제품코드":
            rows.append(dict(goodsId=gid, itemNm=text(label), itemVal=str(value)))
    if rows:
        ctx.log("열린 크롬의 공식 고시 표를 사용합니다. 고시 재조회 HTTP 요청 생략", "INFO")
    else:
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


def _crawl(ctx):
    failures, matched, emitted = [], set(), set()
    try:
        base, requested = split_input(ctx.product_code)
        ctx.stage("공홈 구글링 버전: " + base)
        ids = model_ids(ctx, base)
    except Exception as exc:
        ctx.fail_item(str(exc))
        raise
    for gid in ids:
        ctx.checkpoint()
        try:
            ctx.stage("공식 색상 상품 조회: " + gid)
            url = ORIGIN + "/goods/indexGoodsDetail?" + urlencode({"goodsId": gid})
            browser = getattr(ctx, "_google_browser", None)
            if browser is not None and gid not in getattr(ctx, "_google_page_cache", {}):
                soup, final = browser_product_html(ctx, browser, url)
                verify_candidate_page(ctx, soup, gid, base, final)
                ctx._google_page_cache[gid] = (str(soup), final)
            else:
                soup, final = get_html(ctx, url)
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
            collect_regular_price(ctx, record, info["block"], 'babathe')
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


def crawl(ctx):
    ctx.log("더아이잗 공홈 구글링 코드 " + ADAPTER_REVISION, "INFO")
    entered = False
    try:
        split_input(ctx.product_code)
        with google_browser_session(ctx) as browser:
            ctx._google_browser = browser
            ctx._google_page_cache = {}
            entered = True
            try:
                _crawl(ctx)
            finally:
                del ctx._google_browser
    except Exception as exc:
        if not entered:
            ctx.fail_item(str(exc))
        raise
