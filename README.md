# AI 도난탐정 (ai-theft-detective)

자전거 도난 신고 즉시 당근마켓·번개장터를 AI가 24시간 자동 감시하고, 의심 매물 발견 시 이메일로 알립니다.

## 기능

| # | 기능 | 설명 |
|---|------|------|
| ① | 도난 신고 접수 | 자전거 정보 입력 → 케이스 ID 발급 → 즉시 1차 크롤링 |
| ② | 중고마켓 AI 감시 | 당근마켓·번개장터 자동 크롤링 + 텍스트 유사도 분석 |
| ③ | 자동 크롤링 스케줄 | 신고 후 48h=15분 / ~7일=1시간 / 7일+=6시간 간격 |
| ④ | 이메일 알림 | 유사도 ≥ 70% 의심 매물 발견 시 Resend로 즉시 알림 |
| ⑤ | 경찰 신고서 PDF | 112 제출용 신고서 자동 생성 (나눔고딕 한글 PDF) |
| ⑥ | CCTV 지도 | 도난 장소 반경 내 CCTV 위치 + 열람 절차 안내 |

## 설치

```bash
git clone https://github.com/your-repo/ai-theft-detective.git
cd ai-theft-detective

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env            # 환경변수 설정 후
uvicorn main:app --reload --port 8000
```

브라우저에서 `http://localhost:8000` 접속

## 환경변수

`.env.example` 참고:

| 변수명 | 필수 | 설명 |
|--------|------|------|
| `RESEND_API_KEY` | 선택 | Resend 이메일 API 키. 없으면 console log만 출력 |
| `SITE_URL` | 선택 | 이메일 링크용 서비스 URL (기본: http://localhost:8000) |
| `PUBLIC_DATA_API_KEY` | 선택 | 서울시 공공데이터 CCTV API 키. 없으면 Mock 데이터 사용 |

## 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/` | 메인 UI |
| `GET` | `/privacy` | 개인정보 처리방침 (PIPA 제30조) |
| `GET` | `/terms` | 이용약관 |
| `GET` | `/health` | 헬스체크 (DB 연결 + 스케줄러 상태) |
| `POST` | `/api/report` | 도난 신고 접수 + 즉시 크롤링 |
| `GET` | `/api/cases` | 전체 케이스 목록 |
| `GET` | `/api/case/{id}/listings` | 케이스별 의심 매물 목록 |
| `POST` | `/api/case/{id}/found` | 자전거 회수 처리 (크롤링 중단) |
| `POST` | `/api/cctv/nearby` | 주변 CCTV 목록 (lat/lng/radius) |
| `GET` | `/api/cctv/access-guide` | CCTV 열람 절차 가이드 |
| `GET` | `/api/police/nearby` | 주변 경찰서 + 온라인 신고 링크 |
| `POST` | `/api/report/112-form` | 경찰 신고서 PDF 생성 (다운로드) |
| `POST` | `/api/generate-report` | 신고서 텍스트 생성 |
| `POST` | `/api/predict-movement` | 도난 자전거 이동 패턴 예측 (데모) |

## 베타 운영 상태

- 베타 버전 — 무료 제공, 기능 변경·중단 가능
- 크롤링: 당근마켓 웹 스크래핑 + 번개장터 공개 API
- DB: SQLite (로컬) / Render 배포 시 영속 디스크 마운트 필요
- 스케줄러: APScheduler + SQLAlchemy JobStore (서버 재시작 후 잡 자동 복원)

## Disclaimer

> 본 서비스의 AI 분석 결과는 텍스트 유사도 기반 추정치입니다.  
> 실제 도난품과 다를 수 있으며, 법적 효력이 없습니다.  
> 의심 매물 발견 시 반드시 경찰(112)에 신고 후 판단하세요.  
> 중고거래 판매자를 도둑으로 단정하지 마세요.

## License

MIT © 2026 주식회사 무무익선
