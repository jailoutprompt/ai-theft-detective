# 맥락: AI 도난탐정 MVP

## 왜 이걸 하는가
- 창업중심대학 선정됨 (정부지원 7천만원)
- 발표에서 "구동 시켜보라고 할 수 있다" → 실제 작동하는 데모 필요
- 사업계획서에 "현재 MVP 모델 구현 완료, 베타 테스트 진행 중"이라고 적음

## 결정 사항
- 웹앱으로 구현 (앱 빌드 없이 브라우저에서 바로 데모)
- 크롤링은 실제 당근마켓 검색 + fallback으로 mock 데이터
- 6개 기능 중 1, 2, 5번 우선 (나머지는 UI만)

## 참고 자료
- 앱 목업 HTML: ~/Desktop/창업중심대학_제출서류/Ai theft detective 6 features mockup.txt
- 발표 스크립트: ~/Desktop/창업중심대학_제출서류/창업중심대학 발표 스크립트.txt
- 사업계획서: ~/Desktop/창업중심대학_제출서류/2026년도+창업중심대학...pdf
- 기존 AI 모델: ~/Desktop/무무익선/brain_woolrim_l-main 4/brian_woolim_l_py-main/model_assets/bicycle_theft_risk_model.pkl
- 기존 theft-risk 컴포넌트: ~/Desktop/무무익선/brain_woolrim_l-main 4/app/components/theft-risk/

## 제약 조건
- 발표장 인터넷 불안정할 수 있음 → offline fallback 필요
- 당근마켓 크롤링 차단 가능 → mock 데이터 백업 필수
- OpenAI API 비용 → 데모 수준이면 미미
