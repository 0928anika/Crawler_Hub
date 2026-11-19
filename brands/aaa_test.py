PORTAL_API_VERSION = 1

"""Update smoke test only: no network calls or real product collection."""


def crawl(ctx):
    code = str(ctx.product_code).strip()
    if not code:
        raise ValueError('테스트 품번을 입력하세요. 예: TEST001')
    ctx.log('AAA 업데이트 테스트 v1: 최신 브랜드 반영 확인 성공', 'SUCCESS')
    record = dict(product_code=code, product_name='업데이트 확인용 테스트 상품',
                  color='테스트색상', color_name='테스트색상', color_code='',
                  product_url='https://github.com/0928anika/Crawler_Hub',
                  **{'테스트 결과': 'AAA_TEST_V1_OK / 실제 상품 아님'})
    if ctx.enabled('size'):
        record['size'] = 'TEST'
    if ctx.enabled('category'):
        record['category'] = '업데이트 테스트'
    if ctx.enabled('detail_page'):
        record['detail_page'] = '<p>AAA_TEST_V1_OK — 업데이트 확인용 테스트 결과입니다. 실제 상품이 아닙니다.</p>'
    ctx.emit_result(record)
