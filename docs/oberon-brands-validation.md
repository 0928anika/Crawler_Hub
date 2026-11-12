# Oberon 신규 브랜드 11종 — 이식·검증 기록 (2026-11-12)

사용자가 운영하던 단독 크롤러(requests + 자체 엑셀 저장)를 Crawler Hub Level 1 SDK 규약으로 옮겼습니다.
URL·선택자·응답 키는 원본 단독 크롤러에서 운영 확인된 값을 그대로 사용했고, 결과 전달·선택 항목·실패 계약·상세 HTML은 세 제작 프롬프트 규약에 맞췄습니다.

## 등록 브랜드

| ID | 브랜드 (소스) | 매핑 | 원본 |
|---|---|---|---|
| bangbang_official | 뱅뱅 (공식 홈페이지) | 혼합 | bangbang_crawler.py |
| adhoc_official | 애드호크 (공식 홈페이지) | 혼합 | adhoc_crawler.py |
| decoand_official | 데코앤 (공식 홈페이지) | 혼합 | deco_crawler.py |
| dawnfield_official | 던필드 (던필드몰) | 혼합 | dawnfield_crawler.py |
| disneygolf_smfashion | 디즈니골프 (SM패션 공식몰) | 혼합 | disneygolf_crawler.py |
| renomakids_official | 레노마키즈 (공식 홈페이지) | 혼합 | 레노마키즈 크롤러 |
| minimum_official | 미니멈 (공식 홈페이지) | 혼합 | 미니멈 크롤러 |
| ecolier_soiemall | 에꼴리에 (소이몰) | 혼합 | ecolier_crawler_1.py |
| adidas_official | 아디다스 (공식 홈페이지) | 자동 | adidas_crawler.py |
| brentwood_kolonmall | 브렌우드 (코오롱몰) | 자동 | brentwood_crawler.py |
| ping_bucketstore | 핑 (버킷스토어) | 자동 | bucketstore_crawler.py |

혼합 매핑(`mapping_mode: manual`, `hybrid_mapping: true`)은 후아유와 같은 방식입니다.
색상 옵션이 1개인 상품 페이지와 공식 색상 연결 사진(Cafe24 `link_image`, 데코앤 색상 슬라이드)은 자동 결과로 저장하고,
연결 근거가 없는 갤러리 사진만 색상 매핑 화면에 보냅니다. 사진 색감으로 색상을 추측하지 않습니다.

## 공통 규약 적용

- 결과 키: product_code / product_name / color / product_url 필수, color_name·color_code 분리, `원본 상품명` 추가 열.
- 선택 항목은 `ctx.enabled` 안에서만 요청·파싱. 이미지 미선택 시 이미지 검사 요청 0회, 상세 미선택 시 상세 이미지 요청 0회.
- 판매가·정상가 독립. 정상가는 공식 독립 필드만(cafe24 `#span_product_price_custom`, 아디다스 `standard_price`, 코오롱 `wishPrice`, 버킷스토어 `tag`, 소이몰 `set_goods_fixedPrice`). 누락·0은 빈칸이며 품번 실패가 아님.
- 대표·추가 후보: 공개 GET(쿠키·Referer 없음) + Pillow 해독, JPEG/PNG/정지 WebP, 짧은 변 500px, 10,000,000바이트, 비율 1.8, 사이즈표·고시·배너 경로 신호 제외. 동일 바이트 중복 제거. 매핑 전 후보를 자르지 않음.
- 상세 HTML: 공식 상세 이미지 + HTML 고시가 있으면 항목-값 2열 표 + 사이즈(그림 → 표 → 주석). 배송·교환·공통 배너·care 경로는 요청하지 않음. 공개 접근이 거부된 상세 이미지(403/404/HTML)는 해당 파일만 경고 후 생략, 일시 오류는 수집 실패. `detail_store.excel_detail` 정리 후 32,767자 초과 시 실패.
- 일부 상품 링크 실패 시 성공 결과는 보존하고 `ctx.fail_item` + 예외로 입력 행을 미완료 처리. 일치 상품 0건은 실패.

## 검증 범위 (중요)

- **실사이트 실행: 미검증.** 이 코드를 작성한 환경은 각 쇼핑몰 접속이 프록시에서 차단되어 실제 응답으로 실행하지 못했습니다.
- **오프라인 검사: 통과.** `tests/test_oberon_brands.py` — 실제 `CrawlContext`로 각 사이트 구조를 본뜬 최소 fixture를 실행해 호출 형식, 혼합 매핑 분리, 품절 사이즈·앞자리 0 보존, 고시 표, 사이즈 그림→표 순서, 미선택 항목 무요청, 일부 링크 실패, 0건 실패를 확인했습니다.
- `python tools/manage_brands.py check` 통과(54개 브랜드). 전체 테스트는 기존부터 실패하던 `test_prices` 1건(지프 2종이 정상가 미지원 목록에 없음) 외 통과.
- 운영 투입 전 각 브랜드에서 시험 품번(설정의 guide.example)으로 새 작업을 실행해 결과 Excel·색상 매핑 화면을 확인하세요.

## 알려진 확인 필요 사항

- 아디다스 이미지 `w_1000` 변환과 사이즈표 API 응답 구조는 원본 크롤러 기준이며 사이즈표 구조는 원본에서도 미확인이었습니다(못 읽으면 경고 후 생략).
- 데코앤은 사이트 검색 결과가 없을 때 메인페이지에 공개된 Cafe24 프런트 앱 키로 상품명 검색을 합니다(읽기 전용, 비밀키·로그인 없음).
- 버킷스토어는 여러 브랜드를 판매합니다. ID는 핑으로 등록했고 실제 브랜드는 `사이트 브랜드` 열에 보존합니다.
- 네파·블랙야크·아가방·빈트릴 단독 크롤러는 Oberon에 이미 허브용 브랜드가 있어 덮어쓰지 않았습니다.
- 지그재그·이랜드몰(고잔점) 단독 크롤러는 대상 브랜드가 정해지지 않아 이번 등록에서 제외했습니다.
