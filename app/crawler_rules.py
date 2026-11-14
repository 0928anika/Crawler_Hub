"""Level 1 gallery screening. Content evidence must come from the brand adapter.

WING web baseline: https://marketplace.coupang.com/mba/listing (2026-11-04).
Aspect cutoff is this project's conservative rule, not an official WING limit.
This validates candidate gallery images, not final marketplace approval.
"""
from io import BytesIO
import re
from PIL import Image, UnidentifiedImageError
from urllib.parse import urlsplit, parse_qsl
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
import time


def check_public_image(url, *, timeout=10, max_bytes=10_000_000, retries=1, checkpoint=None):
    """Bounded GET using a fresh opener: no session cookies/auth/referer.

    status: accessible / inaccessible (observed rejection) / unknown (transient).
    Accessible means observed public access now, never guaranteed future uptime.
    This checks access and actual image bytes, not gallery suitability.
    """
    result=dict(status='unknown',url=url,final_url=url,data=None,reasons=[],warnings=[])
    parts=urlsplit(url)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
        return result | dict(status='inaccessible',reasons=['유효한 공개 HTTP(S) URL이 아님'])
    if any(k.casefold() in ('expires','signature','sig','token','x-amz-signature','x-amz-expires','x-goog-signature') for k,_ in parse_qsl(parts.query)):
        result['warnings'].append('서명·토큰 URL: 만료 가능성 확인 필요')
    # A fresh opener bypasses a crawler's global cookie/auth opener.
    opener=build_opener()
    for attempt in range(max(0,min(int(retries),2))+1):
        if checkpoint:checkpoint()
        try:
            req=Request(url,headers={'User-Agent':'CrawlerPortal/0.5 image-check','Accept':'image/*'},method='GET')
            with opener.open(req,timeout=timeout) as response:
                result['final_url']=response.geturl()
                if urlsplit(result['final_url']).scheme not in ('http','https'):
                    return result | dict(status='inaccessible',reasons=['비 HTTP(S) 리다이렉트'])
                mime=response.headers.get('Content-Type','').split(';')[0].lower()
                data=response.read(max_bytes+1)
            if checkpoint:checkpoint()
            if len(data)>max_bytes:
                return result | dict(status='inaccessible',reasons=['응답 최대 용량 초과'])
            if mime in ('text/html','application/xhtml+xml'):
                return result | dict(status='inaccessible',reasons=['로그인·오류 HTML 응답'])
            try:
                with Image.open(BytesIO(data)) as im:
                    width,height=im.size;fmt=im.format;im.verify()
            except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
                return result | dict(status='inaccessible',reasons=['실제 이미지 해독 실패'])
            return result | dict(status='accessible',data=data,width=width,height=height,format=fmt)
        except HTTPError as exc:
            code=exc.code
            exc.close()
            if code in (401,403,404,410):
                return result | dict(status='inaccessible',reasons=['공개 GET 거부: HTTP '+str(code)])
            result['reasons']=['공개 GET 확인 실패: HTTP '+str(code)]
        except (URLError,TimeoutError,OSError) as exc:
            result['reasons']=['일시적 접근 확인 실패: '+str(exc)]
        if attempt<max(0,min(int(retries),2)):
            if checkpoint:checkpoint()
            time.sleep(.2)
    return result


def clean_product_name(name, brand_aliases, product_codes):
    for token in sorted(set(brand_aliases + product_codes), key=len, reverse=True):
        if token:
            name = re.sub(r'(?<![\w])' + re.escape(token) + r'(?![\w])', '', name, flags=re.I)
    # Strip separators, not bracket characters: '(소재)' and '[핏]' are product text.
    # Remove a pair only when token removal left its contents empty.
    previous=None
    while previous!=name:
        previous=name
        name=re.sub(r'\([\s_/-]*\)|\[[\s_/-]*\]|\{[\s_/-]*\}', '', name)
    return re.sub(r'\s+', ' ', name).strip(' -_/')


def resolve_input(value, official_variants, has_color_codes=False, hyphen_alias=False):
    """Use verified full identifiers; never split arbitrary style-code hyphens.

    official_variants: [(base_code, real_color_code), ...], discovered on site.
    Returns (official base, None) for all colors or (base, specific color).
    """
    text = str(value).strip().casefold()
    bases = {str(base).casefold(): str(base) for base, _ in official_variants}
    if text in bases:
        return bases[text], None
    if has_color_codes:
        matches = {(str(base), str(color)) for base, color in official_variants if color and
                   text in {f'{base}_{color}'.casefold(),
                            f'{base}-{color}'.casefold() if hyphen_alias else ''}}
        if len(matches) == 1:
            return next(iter(matches))
    raise ValueError('공식 품번·색상코드로 확인되지 않은 입력입니다.')


def validate_gallery_image(data, *, role, mapping_verified, single_shot_verified,
                           evidence='', mapping_evidence='', content_evidence='', max_aspect=1.8, min_side=500,
                           max_bytes=10_000_000):
    """Fail closed. True booleans require adapter/content evidence, not dimensions.

    role must be product_shot; size charts, measurements, collages and unknown
    roles never pass. Download with a byte limit before calling this helper.
    """
    reasons = []
    if role != 'product_shot': reasons.append('개별 상품 컷이 아님: ' + str(role))
    if not mapping_verified: reasons.append('상품·색상 이미지 매핑 미확인')
    # evidence is retained for frozen v0.4 adapters; new adapters should provide
    # the two independent evidence strings. No automatic content classifier.
    if not single_shot_verified or not (content_evidence or evidence).strip(): reasons.append('개별 컷 근거 미확인')
    if (mapping_evidence or content_evidence) and not mapping_evidence.strip():reasons.append('색상 매핑 근거 없음')
    if len(data) > max_bytes: reasons.append('파일 용량 초과')
    try:
        with Image.open(BytesIO(data)) as im:
            width, height = im.size
            fmt = im.format
            animated = getattr(im,'is_animated',False)
            im.verify()
        if fmt not in ('JPEG', 'PNG', 'WEBP'): reasons.append('JPG/PNG/WebP 실제 파일 형식 아님')
        if animated: reasons.append('애니메이션 이미지: 정지 상품컷만 허용')
        if min(width, height) < min_side: reasons.append('짧은 변 최소 픽셀 미달')
        if min(width, height) <= 0 or max(width, height) / min(width, height) > max_aspect:
            reasons.append('통짜·배너 의심 비율: 프로젝트 보수적 기준')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        width = height = 0
        reasons.append('이미지 해독·검증 실패')
    unknown=not mapping_verified or not single_shot_verified or not (content_evidence or evidence).strip() or bool((mapping_evidence or content_evidence) and not mapping_evidence.strip())
    return dict(passed=not reasons, status='PASS' if not reasons else 'UNKNOWN' if unknown else 'REJECT',
                reasons=reasons, width=width, height=height, evidence=evidence,
                mapping_evidence=mapping_evidence,content_evidence=content_evidence)


def gallery_slots(candidates):
    """Candidates are {'url': original_url, 'validation': helper_result}.

    Rejected images can never reenter through padding. A sole valid main shot
    may fill extra slots, explicitly reported as a fallback.
    """
    urls = list(dict.fromkeys(c['url'] for c in candidates
                            if c.get('validation', {}).get('passed') is True
                            and re.match(r'^https?://', c.get('url', ''))))
    if not urls:
        return None, [], '유효한 개별 상품 이미지 없음 · 검수 필요'
    main = urls[0]
    extras = urls[1:6]
    warning = ''
    if not extras:
        extras = [main]
        warning = '유효한 개별 컷이 대표 1장뿐이므로 추가이미지에 재사용'
    while len(extras) < 5:
        extras.append(extras[-1])
    return main, extras, warning
