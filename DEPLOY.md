# AI 도난탐정 — Fly.io 배포 가이드

## 왜 Fly.io?

| | Render Free | Fly.io Free |
|---|---|---|
| 디스크 | Ephemeral (재시작 시 SQLite 초기화) | Persistent Volume (SQLite 영속) |
| 슬립 | 15분 비활성 후 슬립 | auto_stop 설정 가능 |
| 리전 | 미국 고정 | nrt (도쿄) — 한국 latency 낮음 |
| 무료 한도 | 무료 | 3 shared-cpu VM + 3GB 스토리지 무료 |

SQLite 데이터를 유지해야 하는 이 앱에는 Fly.io가 적합합니다.

---

## 사전 준비

- [ ] [Fly.io 계정 가입](https://fly.io) (카드 등록 필요, 무료 한도 내 과금 X)
- [ ] flyctl 설치

```bash
brew install flyctl
fly auth login
```

---

## Step 1. 앱 생성

```bash
cd /Users/jamie/Desktop/ai-theft-detective

# fly.toml 이미 존재하므로 --no-deploy 로 앱 등록만
fly launch --no-deploy --name noeullim-theft-ai
```

> 이미 fly.toml이 있으므로 "기존 설정 사용?" 물으면 Yes 선택.

---

## Step 2. Volume 생성 (SQLite 영속 디스크)

```bash
fly volumes create data --size 1 --region nrt
```

- 1GB / 도쿄 리전
- `/app/data` 에 마운트됨 → `theftdetective.db` + `scheduler.db` 영속 유지

---

## Step 3. 환경변수 (Secrets) 설정

```bash
# 필수
fly secrets set OPENAI_API_KEY=sk-...
fly secrets set RESEND_API_KEY=re_...
fly secrets set SITE_URL=https://noeullim-theft-ai.fly.dev

# 선택 (서울 CCTV 실데이터 원할 때)
fly secrets set PUBLIC_DATA_API_KEY=발급받은키
```

> API 키 발급 위치:
> - OPENAI_API_KEY: https://platform.openai.com/api-keys
> - RESEND_API_KEY: https://resend.com (무료 100통/일)
> - PUBLIC_DATA_API_KEY: https://data.go.kr → "서울특별시 CCTV 현황" 검색 후 활용신청

---

## Step 4. 배포

```bash
fly deploy
```

빌드 + 배포 약 3~5분 소요.

---

## Step 5. 배포 확인

```bash
# 헬스체크
curl https://noeullim-theft-ai.fly.dev/health

# 예상 응답:
# {"status":"ok","db":"connected","scheduler":"running (0 jobs)","version":"1.0.0","uptime_seconds":...}

# 로그 실시간 확인
fly logs
```

---

## 배포 후 URL

```
https://noeullim-theft-ai.fly.dev          # 메인 (index.html)
https://noeullim-theft-ai.fly.dev/health   # 헬스체크
https://noeullim-theft-ai.fly.dev/api/cases # 케이스 목록
```

---

## 운영 중 유용한 명령어

```bash
# 앱 상태 확인
fly status

# 로그
fly logs

# SSH 접속 (DB 직접 확인)
fly ssh console
# 접속 후:
# sqlite3 /app/data/theftdetective.db
# > SELECT * FROM cases;

# 재배포 (코드 변경 후)
fly deploy

# 머신 재시작
fly machine restart

# 볼륨 확인
fly volumes list

# Secrets 목록 (값은 안 보임)
fly secrets list
```

---

## 비용 예상

- shared-cpu-1x, 512MB, 1대 = **무료 한도 내** (월 $0)
- Volume 1GB = **무료 한도 내** (3GB 무료)
- 트래픽 없을 때 auto_stop = 과금 최소화
- 유료 전환 시점: VM 추가 or 메모리 1GB+ 업그레이드 시

---

## 한계 / 주의사항

1. **API 키는 Jamie 직접 발급** — OPENAI, RESEND, PUBLIC_DATA 모두 별도 가입 필요
2. **Volume 삭제 = 데이터 전부 사라짐** — `fly volumes destroy` 실수 주의
3. **auto_stop = true** — 슬립 후 첫 요청 콜드스타트 약 2~3초 지연
4. **당근마켓 크롤링** — 클라우드 IP 차단 가능성 있음 (Mock 데이터로 fallback 처리됨)
5. **폰트** — `fonts/NanumGothic.ttf` 없으면 PDF가 Helvetica로 생성됨 (한글 깨짐). 폰트 파일 포함 확인 필요

---

## fonts 폴더 확인

```bash
ls fonts/
# NanumGothic.ttf 있어야 PDF 한글 정상 출력
```

없으면:
```bash
# 나눔고딕 다운로드
curl -L "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf" \
  -o fonts/NanumGothic.ttf
```
