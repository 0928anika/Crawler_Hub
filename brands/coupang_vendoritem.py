PORTAL_API_VERSION = 1
"""쿠팡 상품 — vendorItemId(viid) 기준 크롤러. 자동 색상 매핑 / URL 방식.

입력: viid 숫자(예: 96402909989) 또는 쿠팡 상품 주소(...?vendorItemId=96402909989).
쿠팡은 일반 요청(urllib)·앱 내장 브라우저를 403으로 막으므로, 아디다스 크롤러와 같은 방식으로
**사용자가 이미 쓰는 Chrome**(chrome://inspect/#remote-debugging 연결 허용)에 붙어 새 탭에서 읽고 탭만 닫는다.
새 브라우저·프로필·쿠키 복사 없음.

2026-11-27 실사이트 확인(사용자 Chrome, 버커루 B241KT550P · productId 7921593691):
- https://www.coupang.com/vp/products/0?vendorItemId=VIID 로 열면 쿠팡이 그 viid의 상품(productId)으로 열어 줌
  → viid만으로 조회 가능.
- 상품 데이터는 Next.js 스트림(self.__next_f)의 atfData: title·brand·productId·leafCategoryInfo,
  options.optionRows(옵션명 사이즈/색상 + valueId), options.attributeVendorItemMap('값ID:값ID' →
  vendorItemId·itemId·itemName·images[].origin·quantityBase[0].price{originPrice,salePrice,couponPrice}·soldOut·invalid).
- 필수 표기 정보: #itemBrief 표(제품 소재·색상·치수·제조자·제조국·세탁방법…), 아래로 스크롤해야 불러옴.
- 상품상세: .product-detail-content 이미지(image1.coupangcdn.com/image/vendor_inventory/…). 같은 영역의 판매자
  연락처·반품 주소 글은 상품 정보가 아니라 넣지 않음.
- 판매 종료 viid는 '현재 판매 중인 상품이 아닙니다'가 뜨고 상세가 비어 있음 → 결과의 '판매 상태'에 표시.
"""
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from crawler_rules import check_public_image, validate_gallery_image, gallery_slots

REVISION = '2026-11-27-coupang-viid-2-keeper'
SITE = 'https://www.coupang.com'
HOSTS = {'www.coupang.com', 'coupang.com', 'm.coupang.com'}
IMAGE_HOSTS = {'image.coupangcdn.com', 'image1.coupangcdn.com', 'image2.coupangcdn.com', 'image3.coupangcdn.com',
               'image4.coupangcdn.com', 'image5.coupangcdn.com', 'image6.coupangcdn.com', 'image7.coupangcdn.com',
               'image8.coupangcdn.com', 'image9.coupangcdn.com', 'image10.coupangcdn.com', 'thumbnail.coupangcdn.com',
               'thumbnail1.coupangcdn.com', 'thumbnail2.coupangcdn.com', 'thumbnail3.coupangcdn.com',
               'thumbnail4.coupangcdn.com', 'thumbnail5.coupangcdn.com', 'thumbnail6.coupangcdn.com',
               'thumbnail7.coupangcdn.com', 'thumbnail8.coupangcdn.com', 'thumbnail9.coupangcdn.com',
               'thumbnail10.coupangcdn.com'}
VIID = re.compile(r'\d{6,15}')
PAGE_GAP = 1.5          # 상품 사이 간격(초): 사람 속도 정도로 천천히
COLOR_NAMES = ('색상', '컬러', 'color', '색')
SIZE_NAMES = ('사이즈', '크기', 'size', '치수')


class CrawlError(Exception):
    """현재 입력 행의 수집 오류."""


# 브라우저 안에서 실행: 스크롤로 필수 표기·상세를 불러온 뒤 필요한 값만 돌려줌.
EXTRACT_JS = r"""async () => {
  const ready = () => document.querySelector('#itemBrief') && document.querySelector('.product-detail-content');
  for (let y = 0; y < document.body.scrollHeight && y < 30000 && !ready(); y += 700) {
    window.scrollTo(0, y); await new Promise(r => setTimeout(r, 250));
  }
  for (let i = 0; i < 20 && !ready(); i++) await new Promise(r => setTimeout(r, 300));
  const text = document.body.innerText;
  const flight = (self.__next_f || []).map(x => (x && x[1]) || '').join('');
  let atf = null;
  for (const line of flight.split('\n')) {
    if (!line.includes('"atfData"')) continue;
    const m = line.match(/^[0-9a-f]+:([\[{][\s\S]*)$/);
    if (!m) continue;
    try {
      const walk = o => { if (!o || typeof o !== 'object' || atf) return; if (o.atfData) { atf = o.atfData; return; } for (const k in o) walk(o[k]); };
      walk(JSON.parse(m[1]));
    } catch (e) {}
    if (atf) break;
  }
  const keep = v => ({vendorItemId: v.vendorItemId, itemId: v.itemId, itemName: v.itemName, soldOut: v.soldOut,
    invalid: v.invalid, inventory: v.inventory, images: (v.images || []).map(i => i.origin || i.detailImage),
    price: (v.quantityBase || [])[0] && v.quantityBase[0].price ? [v.quantityBase[0].price.originPrice,
      v.quantityBase[0].price.salePrice, v.quantityBase[0].price.couponPrice] : null});
  const options = atf && atf.options ? {
    optionRows: (atf.options.optionRows || []).map(o => ({name: o.name, attributes: (o.attributes || []).map(x => ({valueId: String(x.valueId), name: x.name, selected: !!x.selected}))})),
    attributeVendorItemMap: Object.fromEntries(Object.entries(atf.options.attributeVendorItemMap || {}).map(([k, v]) => [k, keep(v)]))} : null;
  const brief = [...document.querySelectorAll('#itemBrief tr')].flatMap(tr => {
    const c = [...tr.children].map(x => x.innerText.trim()); const out = [];
    for (let i = 0; i + 1 < c.length; i += 2) out.push([c[i], c[i + 1]]); return out; });
  const det = document.querySelector('.product-detail-content');
  let crumbs = [];
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { for (const x of [].concat(JSON.parse(s.textContent))) if (x['@type'] === 'BreadcrumbList') crumbs = x.itemListElement.map(i => i.name); } catch (e) {}
  }
  return {
    url: location.href, status: document.title,
    atf: atf ? {title: atf.title, pureTitle: atf.pureTitle, productId: atf.productId, brand: atf.brand,
      leafCategoryInfo: atf.leafCategoryInfo, attributeTitle: atf.attributeTitle, options} : null,
    brief, detail: det ? (det.querySelectorAll('img').length ? [...det.querySelectorAll('img')].map(i => i.getAttribute('src') || i.dataset.src || '').filter(Boolean) : []) : null,
    crumbs, mpn: (text.match(/Manufacturer Part Number:\s*([^\n]+)/) || [])[1] || '',
    coupangIds: (text.match(/쿠팡상품번호:\s*(\d+)\s*-\s*(\d+)/) || []).slice(1),
    notSelling: /현재 판매 중인 상품이 아닙니다/.test(text),
    blocked: /요청하신 페이지의 사용권한이 없습니다/.test(text)
  };
}"""


# ───────────────────────── 기존 Chrome 연결(아디다스 크롤러와 같은 방식) ─────────────────────────
def existing_chrome_profile():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Google/Chrome/User Data'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Google/Chrome'
    return Path.home() / '.config/google-chrome'


def chrome_endpoint(profile):
    """Chrome이 연결 허용 시 남기는 DevToolsActivePort 파일만 읽음(쿠키·프로필 복사 없음)."""
    for attempt in range(3):
        try:
            lines = (profile / 'DevToolsActivePort').read_text('utf-8').splitlines()
            port, path = int(lines[0]), lines[1].strip()
            if 1 <= port <= 65535 and re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+', path):
                return 'ws://127.0.0.1:' + str(port) + path
        except (OSError, ValueError, IndexError):
            pass
        if attempt < 2:
            time.sleep(.15)
    return None


# ── Chrome 연결 유지 도우미 ──
# 허브는 입력 행(viid)마다 새 작업 프로세스를 띄우므로, 행마다 Chrome에 새로 붙으면 Chrome이 매번
# '원격 디버깅 허용' 창을 띄운다. 그래서 첫 행에서 작은 도우미 프로세스를 하나 띄워 Chrome에 한 번만 연결하고
# (허용 창 1번), 이후 행은 이 도우미(127.0.0.1, 임의 토큰)에게 viid만 넘겨 결과를 받는다.
# 도우미는 마지막 요청 뒤 KEEPER_IDLE초 동안 요청이 없거나 Chrome 연결이 끊기면 스스로 연결을 풀고 끝난다.
KEEPER_IDLE = 15 * 60
KEEPER_CONNECT_WAIT = 300
KEEPER_SOURCE = r"""
import json, secrets, sys, time, traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

state_path, endpoint, idle, revision = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
EXTRACT_JS = json.loads(EXTRACT_JSON)
SITE = 'https://www.coupang.com'
token = secrets.token_hex(16)

from playwright.sync_api import sync_playwright
runtime = sync_playwright().start()
import inspect as _inspect
kwargs = {'timeout': CONNECT_WAIT * 1000}
if 'no_defaults' in _inspect.signature(runtime.chromium.connect_over_cdp).parameters:
    kwargs['no_defaults'] = True
browser = runtime.chromium.connect_over_cdp(endpoint, **kwargs)
if not browser.contexts:
    raise SystemExit('기존 Chrome 기본 세션을 찾지 못했습니다.')
context = browser.contexts[0]
context.set_default_timeout(15000)
context.set_default_navigation_timeout(40000)
last = [time.monotonic()]
stop = [False]


def read(viid):
    page = context.new_page()
    try:
        response = page.goto(SITE + '/vp/products/0?vendorItemId=' + viid, wait_until='domcontentloaded')
        status = response.status if response is not None else 0
        if status in (403, 429):
            return dict(ok=False, error='[접근 제한] 쿠팡 HTTP ' + str(status) + ': 잠시 후 다시 실행하세요(상품 없음이 아님).')
        if status == 404:
            return dict(ok=False, error='[상품 없음] 쿠팡 HTTP 404: ' + viid)
        try:
            page.wait_for_function('() => (self.__next_f || []).length > 0', timeout=15000)
        except Exception:
            pass
        return dict(ok=True, data=page.evaluate(EXTRACT_JS))
    finally:
        try:
            page.close()
        except Exception:
            pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, status=200):
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.headers.get('X-Keeper-Token') != token:
            return self.reply(dict(ok=False, error='토큰 불일치'), 403)
        length = int(self.headers.get('Content-Length') or 0)
        request = json.loads(self.rfile.read(min(length, 10000)) or b'{}')
        last[0] = time.monotonic()
        if self.path == '/ping':
            return self.reply(dict(ok=browser.is_connected(), revision=revision))
        if self.path == '/quit':
            stop[0] = True
            return self.reply(dict(ok=True))
        if self.path == '/read':
            viid = str(request.get('viid') or '')
            if not viid.isdigit():
                return self.reply(dict(ok=False, error='viid 형식 오류'), 400)
            try:
                result = read(viid)
            except Exception as exc:
                result = dict(ok=False, error='쿠팡 페이지 읽기 오류(' + type(exc).__name__ + '): ' + str(exc)[:300])
                if not browser.is_connected():
                    stop[0] = True  # Chrome이 닫힘 → 도우미 종료, 다음 행에서 새로 연결
                    result['reconnect'] = True
            last[0] = time.monotonic()
            return self.reply(result)
        return self.reply(dict(ok=False, error='알 수 없는 요청'), 404)


server = HTTPServer(('127.0.0.1', 0), Handler)
server.timeout = 2
temp = state_path.with_suffix('.tmp')
temp.write_text(json.dumps(dict(port=server.server_address[1], token=token, revision=revision, endpoint=endpoint)), 'utf-8')
temp.replace(state_path)
try:
    while not stop[0] and browser.is_connected() and time.monotonic() - last[0] < idle:
        server.handle_request()
finally:
    try:
        current = json.loads(state_path.read_text('utf-8'))
        if current.get('token') == token:
            state_path.unlink()
    except Exception:
        pass
    server.server_close()
    try:
        runtime.stop()  # 연결만 풀고 사용자 Chrome은 그대로
    except Exception:
        pass
"""


def keeper_dir():
    folder = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or (Path.home() / '.cache')) / 'CrawlerHub' / 'coupang_keeper'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def keeper_call(state, path, body=None, timeout=10):
    import urllib.request
    request = urllib.request.Request('http://127.0.0.1:' + str(int(state['port'])) + path, data=json.dumps(body or {}).encode('utf-8'),
                                     headers={'X-Keeper-Token': state['token'], 'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 127.0.0.1은 프록시를 거치지 않음
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def live_keeper(state_path, endpoint):
    """살아 있고 같은 버전·같은 Chrome에 붙은 도우미 정보, 아니면 None(오래된 도우미는 종료 요청)."""
    try:
        state = json.loads(state_path.read_text('utf-8'))
    except (OSError, ValueError):
        return None
    try:
        answer = keeper_call(state, '/ping', timeout=5)
    except Exception:
        state_path.unlink(missing_ok=True)
        return None
    if answer.get('ok') and state.get('revision') == REVISION and state.get('endpoint') == endpoint:
        return state
    try:
        keeper_call(state, '/quit', timeout=5)
    except Exception:
        pass
    state_path.unlink(missing_ok=True)
    return None


def start_keeper(ctx, state_path, endpoint):
    import subprocess
    folder = state_path.parent
    script = folder / 'keeper.py'
    script.write_text(KEEPER_SOURCE.replace('CONNECT_WAIT', str(KEEPER_CONNECT_WAIT))
                      .replace('EXTRACT_JSON', repr(json.dumps(EXTRACT_JS))), 'utf-8')
    log = folder / 'keeper.log'
    flags = 0
    extra = {}
    if os.name == 'nt':
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0x8) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200)
    else:
        extra['start_new_session'] = True
    with open(log, 'w', encoding='utf-8') as output:
        # 허브 작업 창의 출력과 분리(작업이 끝나도 허브가 도우미 출력을 기다리지 않게)
        process = subprocess.Popen([sys.executable, '-B', str(script), str(state_path), endpoint, str(KEEPER_IDLE), REVISION],
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                   creationflags=flags, cwd=str(folder), **extra)
    ctx.stage('기존 Chrome 연결 · Chrome에 "원격 디버깅 허용" 창이 뜨면 한 번만 허용하세요')
    ctx.log('Chrome 연결 도우미를 시작했습니다. 허용은 이번 한 번이면 되고, 이후 viid는 같은 연결로 읽습니다(' + str(KEEPER_IDLE // 60) + '분 동안 요청이 없으면 연결 해제).', 'INFO')
    deadline = time.monotonic() + KEEPER_CONNECT_WAIT + 15
    while time.monotonic() < deadline:
        ctx.checkpoint()
        if state_path.exists():
            state = live_keeper(state_path, endpoint)
            if state:
                ctx.log('기존 Chrome에 연결했습니다(새 브라우저·프로필 없음).', 'SUCCESS')
                return state
        if process.poll() is not None:
            detail = ''
            try:
                detail = log.read_text('utf-8', errors='replace').strip().splitlines()[-1][:300]
            except (OSError, IndexError):
                pass
            raise CrawlError('[기존 Chrome 연결 실패] Chrome의 원격 디버깅 허용 창을 허용했는지 확인하세요. ' + detail)
        time.sleep(.5)
    try:
        process.kill()
    except OSError:
        pass
    raise CrawlError('[기존 Chrome 연결 시간 초과] Chrome에 뜬 원격 디버깅 허용 창을 허용한 뒤 다시 실행하세요.')


def read_viid(ctx, viid):
    """도우미를 통해 viid 페이지를 읽음(없으면 도우미 시작 → Chrome 허용 1회)."""
    endpoint = chrome_endpoint(existing_chrome_profile())
    if not endpoint:
        raise CrawlError('[기존 Chrome 연결 설정 필요] 쿠팡은 일반 요청을 차단합니다. 평소 쓰는 Chrome에서 '
                         'chrome://inspect/#remote-debugging 연결 허용을 켜고 Chrome을 켜 둔 채 다시 실행하세요.')
    state_path = keeper_dir() / 'state.json'
    state = live_keeper(state_path, endpoint) or start_keeper(ctx, state_path, endpoint)
    ctx.stage('쿠팡 상품 열기: ' + viid)
    ctx.checkpoint()
    try:
        answer = keeper_call(state, '/read', {'viid': viid}, timeout=120)
    except Exception as exc:
        state_path.unlink(missing_ok=True)
        raise CrawlError('Chrome 연결 도우미 응답 없음(' + type(exc).__name__ + '): 다시 실행하면 새로 연결합니다.') from exc
    ctx.checkpoint()
    if not answer.get('ok'):
        if answer.get('reconnect'):
            state_path.unlink(missing_ok=True)
        raise CrawlError(str(answer.get('error') or '쿠팡 페이지를 읽지 못했습니다.'))
    return answer.get('data') or {}


# ───────────────────────── 해석(브라우저 없이 테스트 가능) ─────────────────────────
def parse_input(raw):
    raw = str(raw or '').strip()
    if raw.lower().startswith(('http://', 'https://')) or 'coupang.com' in raw.lower():
        parts = urlsplit(raw if '://' in raw else 'https://' + raw)
        value = (parse_qs(parts.query).get('vendorItemId') or [''])[0]
        if parts.hostname not in HOSTS or not VIID.fullmatch(value):
            raise CrawlError('쿠팡 상품 주소에 vendorItemId가 없습니다: ' + raw)
        return value
    value = re.sub(r'[\s,]', '', raw)
    if not VIID.fullmatch(value):
        raise CrawlError('vendorItemId(숫자) 또는 쿠팡 상품 주소를 입력하세요: ' + raw)
    return value


def money(value):
    digits = re.sub(r'[^\d]', '', str(value or ''))
    return int(digits) if digits else None


def image_url(raw):
    """쿠팡 CDN 원본 주소로 정리(썸네일 크기 경로 제거, https)."""
    raw = str(raw or '').strip()
    if not raw:
        return ''
    if raw.startswith('//'):
        raw = 'https:' + raw
    parts = urlsplit(raw)
    if parts.hostname not in IMAGE_HOSTS:
        return ''
    path = re.sub(r'^/thumbnails/remote/[^/]+/', '/', parts.path)
    if not path.startswith('/image/'):
        return ''
    host = 'image.coupangcdn.com' if parts.hostname.startswith('thumbnail') else parts.hostname
    return 'https://' + host + path


def option_kind(name):
    low = str(name or '').lower()
    if any(k in low for k in COLOR_NAMES):
        return 'color'
    if any(k in low for k in SIZE_NAMES):
        return 'size'
    return 'other'


def selected_item(data, viid):
    """(옵션 값 {행이름: 값}, 해당 vendor item). viid가 지도에 없으면 화면에서 선택된 옵션으로."""
    atf = data.get('atf') or {}
    options = atf.get('options') or {}
    rows = options.get('optionRows') or []
    names = {a['valueId']: (row['name'], a['name']) for row in rows for a in row.get('attributes') or []}
    mapping = options.get('attributeVendorItemMap') or {}
    for key, item in mapping.items():
        if str(item.get('vendorItemId')) == viid:
            values = {}
            for value_id in key.split(':'):
                if value_id in names:
                    values[names[value_id][0]] = names[value_id][1]
            return values, item
    chosen = {row['name']: a['name'] for row in rows for a in row.get('attributes') or [] if a.get('selected')}
    if rows and not chosen:
        raise CrawlError('[viid 확인 실패] 쿠팡 상품의 옵션 목록에서 ' + viid + '를 찾지 못했습니다.')
    if mapping:
        ids = {a['valueId'] for row in rows for a in row.get('attributes') or [] if a.get('selected')}
        for key, item in mapping.items():
            if set(key.split(':')) == ids:
                return chosen, dict(item, vendorItemId=int(viid), _fallback=True)
    return chosen, {'vendorItemId': int(viid), 'itemName': atf.get('title'), 'images': [], 'price': None, '_fallback': True}


def category_of(data):
    crumbs = [c for c in data.get('crumbs') or [] if c and c != '쿠팡 홈']
    if crumbs:
        return ' > '.join(crumbs)
    leaf = (data.get('atf') or {}).get('leafCategoryInfo') or {}
    parents = [p for p in reversed(leaf.get('parentsCategoryNames') or []) if p not in ('HOME', 'KAN')]
    return ' > '.join(parents + ([leaf['name']] if leaf.get('name') else [])) or None


def brief_table(rows):
    rows = [(k, v) for k, v in rows if k and v]
    if not rows:
        return ''
    body = ''.join('<tr><th>' + html.escape(k) + '</th><td>' + html.escape(v) + '</td></tr>' for k, v in rows)
    return '<h3>필수 표기 정보</h3><table border="1" cellpadding="6" style="border-collapse:collapse;width:100%"><tbody>' + body + '</tbody></table>'


def checked(ctx, url, cache):
    if url not in cache:
        cache[url] = check_public_image(url, timeout=12, retries=1, max_bytes=40_000_000, checkpoint=ctx.checkpoint)
    value = cache[url]
    if value['status'] == 'unknown':
        raise CrawlError('이미지 통신 미완료: ' + url + ' / ' + ', '.join(value['reasons']))
    if value['status'] != 'accessible':
        ctx.log('이미지 없음/접근불가: ' + url, 'WARNING')
        return None
    return value


def gallery(ctx, urls, viid, cache):
    accepted, seen = [], set()
    for url in urls:
        value = checked(ctx, url, cache)
        if not value:
            continue
        valid = validate_gallery_image(value['data'], role='product_shot', mapping_verified=True, single_shot_verified=True,
                                       mapping_evidence='쿠팡 vendorItemId ' + viid + ' 의 공식 상품 이미지(attributeVendorItemMap.images)',
                                       content_evidence='쿠팡 상품 이미지 영역. 형식/픽셀/용량/비율 검사.')
        if valid['passed'] and url not in seen:
            accepted.append(dict(url=url, validation=valid))
            seen.add(url)
        elif not valid['passed']:
            ctx.log('이미지 규격 제외: ' + url + ' / ' + ', '.join(valid['reasons']), 'WARNING')
        if len(accepted) >= 10:
            break
    if not accepted:
        raise CrawlError('규격을 통과한 상품 이미지 없음: ' + viid)
    return gallery_slots(accepted)


def detail_html(ctx, data, cache):
    parts = []
    for raw in data.get('detail') or []:
        url = image_url(raw)
        if url and url not in [p[0] for p in parts] and checked(ctx, url, cache):
            parts.append((url, '<p><img src="' + html.escape(url, quote=True) + '" alt="상품 상세" style="max-width:100%"></p>'))
    markup = ''.join(p[1] for p in parts) + brief_table(data.get('brief') or [])
    if not markup:
        ctx.log('상품상세 이미지·필수 표기 정보 없음(판매 종료 상품이거나 불러오지 못함): 상세 빈칸', 'WARNING')
        return ''
    if len(markup) > 32767:
        raise CrawlError('상세 HTML이 Excel 셀 한도(32,767자) 초과')
    return '<div>' + markup + '</div>'


def build_record(ctx, data, viid):
    if data.get('blocked'):
        raise CrawlError('[접근 제한] 쿠팡이 이 Chrome의 접근을 막았습니다. Chrome에서 쿠팡이 정상으로 열리는지 확인하세요.')
    atf = data.get('atf')
    if not atf:
        raise CrawlError('[상품 데이터 없음] 쿠팡 페이지에서 상품 정보를 읽지 못했습니다: ' + viid)
    values, item = selected_item(data, viid)
    if item.get('_fallback'):
        ctx.log('옵션 지도에 viid가 직접 없어 화면에서 선택된 옵션으로 확인: ' + ' / '.join(values.values()), 'WARNING')
    color = next((v for k, v in values.items() if option_kind(k) == 'color'), '')
    size = next((v for k, v in values.items() if option_kind(k) == 'size'), '')
    others = [f'{k}: {v}' for k, v in values.items() if option_kind(k) == 'other']
    product_id = str(atf.get('productId') or (data.get('coupangIds') or [''])[0] or '')
    status = '판매 종료' if data.get('notSelling') or item.get('invalid') else '품절' if item.get('soldOut') else '판매 중'
    record = {
        'product_code': viid,
        'product_name': str(item.get('itemName') or atf.get('title') or '').strip(),
        'product_url': SITE + '/vp/products/' + product_id + '?vendorItemId=' + viid if product_id else SITE + '/vp/products/0?vendorItemId=' + viid,
        'color': color or '단일색상', 'color_name': color or '단일색상', 'color_code': '',
        '브랜드': str(atf.get('brand') or ''), '쿠팡 상품번호': product_id, 'itemId': str(item.get('itemId') or ''),
        '모델번호': data.get('mpn') or '', '판매 상태': status, '기타 옵션': ', '.join(others),
    }
    if not record['product_name']:
        raise CrawlError('상품명 없음: ' + viid)
    if ctx.enabled('size'):
        record['size'] = size
    if ctx.enabled('price') or ctx.enabled('regular_price'):
        origin, sale, coupon = (list(item.get('price') or []) + [None] * 3)[:3]
        if ctx.enabled('price'):
            record['price'] = money(sale) if status == '판매 중' else None
            if status != '판매 중':
                ctx.log(f'{status}: 판매가 빈칸', 'WARNING')
        if ctx.enabled('regular_price'):
            record['regular_price'] = money(origin)
        if money(coupon) and money(coupon) != money(sale):
            record['쿠폰 적용가'] = money(coupon)
    if ctx.enabled('category'):
        record['category'] = category_of(data)
    cache = {}
    if ctx.enabled('main_image') or ctx.enabled('additional_images'):
        urls = list(dict.fromkeys(u for u in (image_url(x) for x in item.get('images') or []) if u))
        if not urls:
            raise CrawlError('해당 viid의 쿠팡 상품 이미지가 없습니다: ' + viid)
        main, extras, warning = gallery(ctx, urls, viid, cache)
        if warning:
            ctx.log(warning, 'WARNING')
        if ctx.enabled('main_image'):
            record['main_image'] = main
        if ctx.enabled('additional_images'):
            record['additional_images'] = ','.join(extras)
    if ctx.enabled('detail_page'):
        record['detail_page'] = detail_html(ctx, data, cache)
    return record


def crawl(ctx):
    ctx.log('쿠팡 viid 크롤러 ' + REVISION, 'INFO')
    try:
        viid = parse_input(ctx.product_code)
    except CrawlError as exc:
        ctx.fail_item(str(exc))
        raise
    try:
        data = read_viid(ctx, viid)
        record = build_record(ctx, data, viid)
    except CrawlError as exc:
        ctx.fail_item(str(exc))
        raise
    except Exception as exc:
        message = '쿠팡 수집 오류(' + type(exc).__name__ + '): ' + str(exc)[:300]
        ctx.fail_item(message)
        raise CrawlError(message) from exc
    ctx.emit_result(record)
    ctx.log(f"{viid}: {record['product_name']} / {record['color']} {record.get('size') or ''} / {record['판매 상태']}", 'SUCCESS')
    time.sleep(PAGE_GAP)
