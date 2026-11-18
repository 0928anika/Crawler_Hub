# Oberon 신규 브랜드 11종 — 이식·검증 기록 (2026-11-12)

사용자가 운영하던 단독 크롤러(requests + 자체 엑셀 저장)를 Crawler Hub Level 1 SDK 규약으로 옮겼습니다.
URL·선택자·응답 키는 원본 단독 크롤러에서 운영 확인된 값을 그대로 사용했고, 결과 전달·선택 항목·실패 계약·상세 HTML은 세 제작 프롬프트 규약에 맞췄습니다.

## 등록 브랜드

| ID | 브랜드 (소스) | 매핑 | 원본 |
|---|---|---|---|
| bangbang_official | 뱅뱅 (공식 홈페이지) | 혼합 | bangbang_crawler.py |
| adhoc_official | 애드호크 (공식 홈페이지) | 혼합 | adhoc_crawler.py |
| decoand_official | 데코앤 (공식 홈페이지) | 혼합 | deco_crawler.py |
| crocodile_official | 크로커다일 (던필드몰) | 혼합 | dawnfield_crawler.py |
| disneygolf_official | 디즈니골프 (SM패션 공식몰) | 혼합 | disneygolf_crawler.py |
| renomakids_official | 레노마키즈 (공식 홈페이지) | 혼합 | 레노마키즈 크롤러 |
| minimum_official | 미니멈 (공식 홈페이지) | 혼합 | 미니멈 크롤러 |
| ecolier_official | 에꼴리에 (소이몰) | 혼합 | ecolier_crawler_1.py |
| adidas_official | 아디다스 (공식 홈페이지) | 자동 | adidas_crawler.py |
| brentwood_official | 브렌우드 (코오롱몰) | 자동 | brentwood_crawler.py |
| ping_official | 핑 (버킷스토어) | 자동 | bucketstore_crawler.py |

혼합 매핑(`mapping_mode: manual`, `hybrid_mapping: true`)은 후아유와 같은 방식입니다.
색상 옵션이 1개인 상품 페이지와 공식 색상 연결 사진(Cafe24 `link_image`, 데코앤 색상 슬라이드)은 자동 결과로 저장하고,
연결 근거가 없는 갤러리 사진만 색상 매핑 화면에 보냅니다. 사진 색감으로 색상을 추측하지 않습니다.

## 2차 등록 (2026-11-18)

| ID | 브랜드 | 매핑 | 원본 |
|---|---|---|---|
| jdx_official | JDX | 혼합 (색상별 페이지는 자동) | JDX 크롤러 |
| sfit_official | 에스핏 | 혼합 | 에스핏 크롤러 |
| elcanto_official | 엘칸토 | 혼합 | 엘칸토 크롤러 |
| riverclassy_favlook | 리버클래시 (페이브룩) | 자동 (공식 색상 표기 없으면 실패) | 리버클래시 크롤러 |
| zensai_official | 젠사이 | 자동 | 젠사이 크롤러 |
| mountia_official | 마운티아 | 자동 | 기존 blackyak_official 구조 재사용 |

- JDX: 상품명 끝 `(품번+색상 2자리)`로 대조, 같은 품번의 다른 색상 페이지 링크를 따라가며 11자리 전체 코드 입력 시 그 색상만 수집.
- 에스핏: 공식 상품 이미지 파일명에 품번이 정확히 있을 때만 일치로 보고, `색상/사이즈` 결합 옵션을 분리(`(n)` 재고 표기 제거).
- Cafe24 공통 엔진 수정(색상/사이즈 결합 옵션, 색상별 페이지 따라가기, 전체 코드 상품명 정리)이 기존 Cafe24 7개 브랜드에도 반영되어 코드 해시가 바뀌었습니다.
- 제외: 레노마키즈·미니멈·지그재그는 1차와 같은 파일(중복), 볼빅은 Oberon에 이미 등록, 지그재그는 요청대로 제외.
- 2차 6개도 실사이트 미검증이며 fixture 검사만 통과했습니다.

## 3차 등록 (2026-11-18)

| ID | 브랜드 | 매핑 | 원본 |
|---|---|---|---|
| lenic_official | 르니앤맥코이 | 혼합 (Cafe24 공통 엔진) | lenic_crawler.py |
| lecoqgolf_official | 르꼬끄골프 (dk-on) | 자동 (ld+json hasVariant 색상별 사진) | lecoq_crawler.py |
| underarmour_official | 언더아머 | 자동 (이미지 파일명 색상코드) | ua_crawler.py |
| millet_official | 밀레 | 자동 (색상별 상품 페이지, 고시 색상코드 일치 필수) | millet_crawler.py |
| fila_official | 휠라 | 자동 (색상별 Shopify 상품) | fila_crawler.py |

- 원본과 달라진 점: 상세에서 설명 문단(언더아머 DNA·휠라 설명·체크포인트 공용 화보) 제외, 색상코드→한글명 변환표(르니앤맥코이)·.webp→.jpg 추측 요청 제거, 르꼬끄 setRecentViewProd(최근 본 상품 등록) 호출 제거, 르꼬끄 앞글자 전체 검색은 상품코드 정확 일치로 축소.
- 휠라 기본 품번(색상코드 없음) 입력은 Shopify 공개 추천검색(/search/suggest.json)을 쓰며 원본 크롤러에 없던 경로라 실사이트 확인 필요.
- 밀레 카테고리는 원본 크롤러에서 위치가 확인되지 않아 미지원으로 등록.
- 제외(이미 등록·동일 파일): 에꼴리에·던필드·뱅뱅·빈트릴·벤제프·링스·리버클래시(페이브룩).
- 3차 5개도 실사이트 미검증이며 fixture 검사만 통과했습니다.

## 4차 등록 — 이미지 저장·크롭 유형 (2026-11-18)

| ID | 브랜드 | 유형 | 근거 |
|---|---|---|---|
| redface_official | 레드페이스 | 3번 로컬 이미지 저장·크롭 (manual/local) | 공식몰 메인으로 Cafe24·품번 형식 확인 |

- 상세 통이미지를 배경 여백 경계로만 분할, 색상 옵션 link_image 사진만 사전 매핑, 나머지 미매핑.
- 상품 상세 페이지는 작업 환경에서 열지 못해 Cafe24 표준 선택자 기준. 작성 프롬프트: docs/prompts/redface_official_03_image_crop.txt
- fixture: 문구 띠·실측 격자 제외, 나란한 2컷 분리, 비율·500px 규칙, 공통 상세 HTML, 선택 항목 생략, 이미지 미선택 실패.

## 5차 등록 — 패션플러스 크롭필요건 (2026-11-19)

| ID | 브랜드 | 유형 | 근거 |
|---|---|---|---|
| fashionplus_item_crop | 패션플러스 크롭필요건 | 3번 로컬 이미지 저장·크롭 (manual/local) | 416083574 실조회(브라우저) + fixture |

- 조회·옵션·상세는 fashionplus_item 로직, 분할·OCR은 레드페이스 크롭 엔진 재사용. 색상명 위치 'below'.
- 작성 프롬프트: docs/prompts/fashionplus_item_crop_03_image_crop.txt

## 6차 등록 — 레드페이스 OCR 추천 버전 (2026-11-19)

| ID | 브랜드 | 유형 | 근거 |
|---|---|---|---|
| redface_official_ocr | 레드페이스 OCR | 3번 로컬 이미지 저장·크롭 (manual/local) + 스와치 추천 매핑 | REWFJKWBF560 실이미지(브라우저 계산) + fixture |

- 기존 redface_official은 그대로 유지. 수집·크롭 동일, ③ 스와치 추천만 추가.
- COLOR 영역 색상명(OCR, 공식 옵션명 정답지) 아래 스와치(저장 안 함)의 상품 색과 같은 화소 비율로 컷별 추천. 20% 이상 + 2위의 2배 이상일 때만 선택, 글자 없는 스와치가 1위면 미매핑.
- 560 실측: 갤러리 블랙컷 블랙 0.92 / 카키컷 카키 0.88 / 아이보리 단독·모델컷 아이보리 0.68~0.93, 통이미지 큰 컷 7장 모두 아이보리 0.85~0.99(2위 0.07 이하).

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
