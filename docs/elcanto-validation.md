# 엘칸토 공식몰 검증 (2026-11-19)

- ID elcanto_official / https://www.elcanto.co.kr 공식 온라인 스토어.
- Makeshop 검색 후 공식 Product.mpn 정확 대조. 품번 길이로 조회 전 거절하지 않음.
- LCWD89I513 실제 검색→상품→옵션→이미지→상세→SDK 엑셀 저장 완료.
- 블랙(품절 포함), 베이지. 각각225/230/235/240/245/250 보존. 공식 옵션에 색상코드가 없어 빈칸. 숫자 파일명19/35 등을 코드로 추정하지 않음.
- 공식 optionJsonData의 opt_ids와 select opt_id를 대조해 색상/사이즈 조합 연결. HIDE/품절 옵션도 보존. JS 실행 없이 literal 필드만 파싱.
- Product.offers 판매가29900 및 sto_price 추가금, 공식 strike 정상가139000 확인.
- 갤러리3장 수집. 다색상은 사진별 수동 매핑 및 색상별 사이즈 메타데이터 보존. 단색은 자동. 추가이미지 최대9장, 부족하면 최소5슬롯 반복.
- 상세는 해당 품번 speedgabia 폴더의 원본10개(상품컷/디테일/밑창/SPEC)와 #productWrap 상품고시 보존. in_top.jpg, cs_1.jpg, cs_2.jpg 및 GIF 제외.
- 실제 HTML fixture 기반 테스트7개 통과: 품번/등록, 품절 옵션, 독립 가격, 상세/고시/배너, GIF/타상품제외, 갤러리/슬롯, SDK 매핑.
- 검증 범위는 예시1상품. 비표준 옵션은 구조 확인 실패로 명확히 표시.
