# 지오다노 공식 홈페이지 검증

2026-11-10. 브랜드 ID `giordano_official`, 소스명 `공식 홈페이지`.

## 확인한 공식 출처

- 검색 폼: `/shop/search_result.php?search_str=05586901`. 상세의 `STYLE` / `.style_code_gio[data-clipboard-text]`로 정확한 8자리 품번 확인. 브랜드명 포함 여부는 수집 조건이 아니다.
- 색상: `prdFrm .color_option`의 실제 옵션 ID, 옵션명 및 `colorchip_auto/{STYLE}{2자리 색상코드}.jpg` 전체 SKU 일치 확인.
- 브라우저에서 색상 선택 시 실제 XHR 확인: `getAddImgList`, `hash=ppno`, `opt_no=1`, `ino=색상 옵션 ID`. 색상별 응답의 갤러리만 사용하며 리뷰·칩·본문 이미지를 갤러리에 섞지 않는다.
- 사이즈: `optChipSet2`. `getOptionStockSKU`는 존재하지 않는 조합만 반환하므로 해당 ID만 제외. 품절 재고 조회로 사이즈를 제거하지 않는다.
- 판매가: `.info[data-sell_prc]`를 공식 스크립트가 `#cpn_list_sell_prc`에 표시한다. 정상가: 별도 `.info[data-normal_prc]` → `#cpn_list_normal_prc` 소비자가. `currency=원` 확인. 상단 및 JSON-LD의 회원 쿠폰가를 판매가로 쓰지 않는다.
- 상세: `#detail .detail_info .wing-detail-more-contents`, `#det_size_info .size_img`, `상품정보고시` 표시 이미지. 원본 이미지만 보존한다. 별도 소재정보 `ch.jpg` 및 배송/리뷰는 수집하지 않으며 표를 생성하지 않는다.
- 검색 더보기는 공식 `common.js`의 `prdMore` 파라미터로 처리. 실제 샘플 검색은 각각 1페이지. 여러 페이지 경로는 모의 응답으로 검증.

## 실제 Hub 워커와 Excel 결과

- `05586901`: 6색상 6행 정상. `01`~`06`, FREE, 판매가/소비자가 각각 29,800. 전체 약 8초.
- `05586901_02`, `05586901-03`: 지정 색상 1행씩 정상, 약 3초.
- `05356951`: 니트 9색상 9행 정상, 각 S,M,L, 판매가/소비자가 각각 59,800, 약 16초.
- 전 행의 실제 공개 갤러리 바이트를 검사하고 추가 이미지 5슬롯을 쉼표 문자열로 저장. 상세는 상품·사이즈·고시 원본 이미지 3장, 별도 table 없음. 실제 Excel 품번의 앞자리 0 보존 확인. 위 시간은 이 PC에서 측정한 값으로 네트워크 상태에 따라 변한다.
- 브라우저 없이 공개 HTTP 응답 직접 조회. 계정/쿠키/리뷰 API 요청을 하지 않는다.

## 자동 검증과 범위

`python -B -m unittest discover -s tests -p test_giordano.py`: 옵션 전체 해제와 필수 링크, 독립 가격 선택, 정상가 누락/0/잘못된 형식, 외화 제외, 공식 색상 ID 연결, 품절 사이즈/앞자리 0, 없는 SKU 제외, 부분 실패 결과 보존, 두 구분자, 중첩 HTML 및 없는 상세 정보, 검색 정렬 제외와 더보기 검증.

공통 `test_prices.py`에는 기존 미지원 정상가 브랜드 목록에 `jeepkids_official`이 빠진 기대값 오류가 남아 있어 전체 회귀 테스트는 통과하지 않는다. 이번 지오다노 구현과 별개이며 해당 기대값은 변경하지 않았다. 샌드박스 임시 폴더의 Excel 쓰기 권한 오류도 별도로 관찰했고, 실제 워커 실행은 정상 권한 환경에서 Excel 저장까지 통과했다.

미확인 품번 전체나 품절 상품 전반, 실제 WING 업로드 성공을 검증했다고 주장하지 않는다. 이미지 최소 500px·10MB·JPG/PNG 기준은 [쿠팡 공식 안내](https://marketplace.coupang.com/mba/listing)를 확인했으며 정지 WebP 허용과 최대 비율 1.8은 프로젝트 정책이다.
