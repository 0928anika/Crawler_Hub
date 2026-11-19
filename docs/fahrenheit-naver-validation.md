# 파렌하이트 네이버 공식 크롤러 검증 (2026-11-19)

- ID: fahrenheit_naver_official. 공식 스토어 https://brand.naver.com/fahrenheit.
- 공식성: 신원 https://www.shinwon.com/kor/m31.php 의 FAHRENHEIT ONLINE SHOP 연결 확인.
- 기존 베스띠벨리의 일반 비로그인 Edge/Playwright 공개 페이지 및 상품·contents 응답 수집 방식 사용. 다른 브랜드 파일은 수정하지 않음.
- 검색어 FPFAH4203을 네이버가 한글로 자동 변환하는 현상 확인. 사이트가 제공하는 원래 검색어 검색 링크 ignoreExp=1을 사용해 정확한 영문 검색 보장.
- FPFAH4203: 검색1건 / 모델 정확 일치1건 / 상품13751254548. 공식 채널UID 2sWDwOAqnOfsVqMqaeQ0A 검증.
- 검색부터 SDK 엑셀 저장까지 두 번 연속 성공. 판매가119400원, 다크그레이 GYD, 사이즈78/82, 갤러리8장.
- 품절 여부로 옵션을 필터링하지 않음. 색상별 사이즈를 수동 매핑 메타데이터에 보존.
- frht/2026P/FPFAH4203/FPFAH4203_01.jpg 원본 상세 보존. event/news.jpg, siegfa_banner.jpg, styleid_public/info/notice.jpg 및 common.jpg는 요청 전 제외.
- 독립 정상가 필드 미확인으로 정상가 미지원. 판매가를 정상가로 재사용하지 않음.
- 이미지/상세 공개 GET 실제 바이트 검증. 색상 추정 없이 전체 유효 갤러리를 수동 매핑 후보로 제공. 추가이미지 최대9장, 최종 앱 매핑에서 최소5슬롯.
- HTTP401/403/429는 원인을 표시하며 제한을 우회하지 않음. 이번 일반 Edge 실검증에서는 정상200 응답 확인.
- 실제 공개 상품/상세 응답 fixture 기반 회귀 테스트7개 통과. 영문 검색어 유지, 공식 스토어 구분, 모델·옵션·가격·상품명, 배너 사전 제외, 상세 품번 검증, 접근제한 오류, SDK 매핑 검증.
- 환경: Windows Microsoft Edge + Playwright. 다른 환경의 상시 접근 성공을 보장하는 것은 아님.
