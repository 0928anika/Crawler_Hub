"""Optional original retail price. Never substitute selling price or infer discounts."""
from decimal import Decimal, InvalidOperation
import re
from bs4 import BeautifulSoup


def amount(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(',', '')
    text = re.sub(r'^\s*(?:KRW|₩)\s*', '', text, flags=re.I)
    text = re.sub(r'\s*(?:KRW|원)\s*$', '', text, flags=re.I).strip()
    if not re.fullmatch(r'\d+(?:\.0+)?', text):
        return None
    try:
        number = Decimal(text)
        return int(number) if number > 0 else None
    except InvalidOperation:
        return None


def extract_regular_price(source, profile):
    if profile == 'unsupported':
        return None
    if profile == 'eland':
        # itemMain.orgSellprice: independently supplied original selling price.
        return amount(source.get('orgSellprice')) if isinstance(source, dict) else None
    if profile == 'babathe':
        # Only the verified product's _goodsDtlInfoData block, not recommendations.
        match = re.search(r'''(?:["']?orgAmt["']?)\s*:\s*["']?(\d+(?:\.0+)?)["']?(?=\s*[,}\n/])''', str(source))
        return amount(match[1]) if match else None
    doc = source if hasattr(source, 'select_one') else BeautifulSoup(source, 'html.parser')
    currency = doc.select_one('meta[property="product:price:currency"]')
    if currency and str(currency.get('content', '')).upper() not in ('', 'KRW'):
        return None
    selectors = {
        'cafe24': '#span_product_price_custom',
        'style24': '.price_blk .org_price',
        # On this site .normal and input#regular_price are SELLING prices.
        'lynx': '.goodsPrice .consumer',
        'daehyun': '.info_price .prc.consumer',
        'beentrill': '#ec-product-price-info[ec-data-custom]',
    }
    if profile not in selectors:
        raise ValueError('정상가 추출 프로필을 확인하세요: ' + str(profile))
    node = doc.select_one(selectors[profile])
    if node is not None:
        if profile == 'beentrill':
            return amount(node.get('ec-data-custom'))
        return amount(node.get_text(' ', strip=True))
    if profile == 'cafe24':
        for script in doc.find_all('script'):
            match = re.search(r'''\b(?:var|let|const)\s+product_custom\s*=\s*["']?(\d+(?:\.0+)?)["']?\s*;''', script.get_text())
            if match:
                return amount(match[1])
    return None


def collect_regular_price(ctx, record, source, profile):
    if not ctx.enabled('regular_price'):
        return
    ctx.checkpoint()
    try:
        value = extract_regular_price(source, profile)
    except Exception:
        value = None
    ctx.checkpoint()
    record['regular_price'] = value
    if value is None:
        ctx.log('독립 정상가를 확인하지 못해 정상가는 빈칸으로 유지합니다. 나머지 항목 수집은 계속합니다.', 'WARNING')


def merge_regular_prices(ctx, output, records):
    if not ctx.enabled('regular_price'):
        return
    values = [r.get('regular_price') for r in records]
    if values and values[0] is not None and all(v == values[0] for v in values):
        output['regular_price'] = values[0]
    else:
        output['regular_price'] = None
        ctx.log('상품 링크 간 정상가가 없거나 서로 달라 공통 정상가는 빈칸으로 유지합니다. 수집 성공 여부에는 영향을 주지 않습니다.', 'WARNING')
