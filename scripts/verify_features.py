#!/usr/bin/env python3
"""
자체개발 기능 자동 검증

"실제로 계산되고 있는가"를 반증 가능한 방식으로 판정한다.
각 테스트는 기능이 하드코딩/목업일 경우 반드시 FAIL 하도록 설계했다.

사용:
    python3 scripts/verify_features.py                      # 배포 서버
    python3 scripts/verify_features.py http://127.0.0.1:8000  # 로컬
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "https://ai-theft-detective.onrender.com"

PASS, FAIL = [], []


def post(path, body, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(path, timeout=120):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read())


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if detail:
        print(f"        {detail}")
    return cond


# 전국 각지 좌표 (하드코딩이면 결과가 같거나 엉뚱하게 나옴)
SPOTS = [
    ("서울 강남역", 37.4979, 127.0276),
    ("부산 서면", 35.1578, 129.0596),
    ("김해시청", 35.2285, 128.8894),
    ("대구 칠성시장", 35.8797, 128.5990),
    ("제주시청", 33.4996, 126.5312),
]


def t1_cctv_real_data():
    print("\n[1] CCTV 조회 — 공공데이터 실측 여부")
    results = {}
    for name, la, lo in SPOTS:
        d = post("/api/cctv/nearby", {"lat": la, "lng": lo, "radius": 300})
        results[name] = d

    # 1-1. 데이터 출처가 목업이 아님
    srcs = {n: d.get("source") for n, d in results.items()}
    check("모든 지역에서 source=public_data",
          all(s == "public_data" for s in srcs.values()),
          str(srcs))

    # 1-2. 지역마다 관리기관이 실제로 다름 (하드코딩이면 동일)
    agencies = {}
    for n, d in results.items():
        lst = d.get("cctvs", [])
        agencies[n] = lst[0]["agency"] if lst else None
    uniq = len({a for a in agencies.values() if a})
    check("지역별 관리기관이 서로 다름 (5곳 중 4곳 이상 고유)",
          uniq >= 4, str(agencies))

    # 1-3. 주소의 시도명이 조회 좌표와 일치 (엉뚱한 지역 데이터가 아님)
    expect = {"서울 강남역": "서울", "부산 서면": "부산", "김해시청": "경상남도",
              "대구 칠성시장": "대구", "제주시청": "제주"}
    ok = True
    detail = []
    for n, d in results.items():
        lst = d.get("cctvs", [])
        if not lst:
            ok = False
            detail.append(f"{n}: 결과없음")
            continue
        addr = lst[0].get("address", "")
        if expect[n] not in addr:
            ok = False
            detail.append(f"{n}: {addr[:25]}")
    check("조회 좌표와 결과 주소의 시도가 일치", ok, "; ".join(detail))

    # 1-4. 반경을 넓히면 결과가 늘어남 (거리 계산이 실제로 동작)
    a = post("/api/cctv/nearby", {"lat": 37.4979, "lng": 127.0276, "radius": 100})
    b = post("/api/cctv/nearby", {"lat": 37.4979, "lng": 127.0276, "radius": 500})
    na, nb = len(a.get("cctvs", [])), len(b.get("cctvs", []))
    check("반경 100m < 500m 결과 수 증가", nb > na, f"100m={na}개소, 500m={nb}개소")

    # 1-5. 반환된 거리가 반경 조건을 실제로 만족
    over = [c for c in b.get("cctvs", []) if c["distance_m"] > 500]
    check("반경 초과 데이터 미포함", len(over) == 0, f"초과 {len(over)}건")


def t2_movement_computed():
    print("\n[2] 이동패턴 예측 — 계산 여부")
    outs = {}
    for name, la, lo in SPOTS[:4]:
        outs[name] = post("/api/predict-movement", {"lat": la, "lng": lo, "hours_elapsed": 24})

    # 2-1. 기존 하드코딩 값(성남78/수원45/안양23)이 그대로 나오지 않음
    hard = {"성남 중고시장": 78, "수원 영통": 45, "안양 평촌": 23}
    hit = []
    for n, d in outs.items():
        for p in d.get("predictions", []):
            if hard.get(p["area"]) == p["probability"]:
                hit.append(f"{n}:{p['area']}{p['probability']}%")
    check("기존 데모 하드코딩 값 미출현", len(hit) == 0, str(hit))

    # 2-2. 지역마다 1순위가 다름
    tops = {n: (d["predictions"][0]["area"] if d.get("predictions") else None) for n, d in outs.items()}
    check("지역별 1순위 수색지가 서로 다름 (4곳 중 3곳 이상 고유)",
          len(set(tops.values())) >= 3, str(tops))

    # 2-3. 경과시간이 늘면 탐색 반경이 커짐
    radii = []
    for h in (6, 24, 72):
        d = post("/api/predict-movement", {"lat": 37.4979, "lng": 127.0276, "hours_elapsed": h})
        radii.append(d["search_radius_km"])
    check("경과시간 증가 → 탐색반경 단조 증가",
          radii[0] < radii[1] < radii[2], f"6h={radii[0]}, 24h={radii[1]}, 72h={radii[2]}km")

    # 2-4. 좌표를 조금 옮기면 거리값도 바뀜 (좌표를 실제로 쓰고 있음)
    d1 = post("/api/predict-movement", {"lat": 37.4979, "lng": 127.0276, "hours_elapsed": 24})
    d2 = post("/api/predict-movement", {"lat": 37.6000, "lng": 127.0900, "hours_elapsed": 24})
    same = (json.dumps(d1["predictions"], ensure_ascii=False) ==
            json.dumps(d2["predictions"], ensure_ascii=False))
    check("좌표 변경 시 예측 결과가 달라짐", not same,
          f'A1={d1["predictions"][0]["area"]}({d1["predictions"][0]["distance_km"]}km) / '
          f'B1={d2["predictions"][0]["area"]}({d2["predictions"][0]["distance_km"]}km)')

    # 2-5. 확률 합이 100 근처 (정규화가 실제로 수행됨)
    tot = sum(p["probability"] for p in d1["predictions"])
    check("상대순위 합계가 100% 근처(±3)", 97 <= tot <= 103, f"합계 {tot}%")

    # 2-6. 계산식과 면책 문구가 응답에 포함
    check("계산식·disclaimer 명시", bool(d1.get("formula")) and bool(d1.get("disclaimer")))


def t3_report_computed():
    print("\n[3] 신고 자동화 — 산출 여부")
    now = datetime.now()

    # 3-1. 도난 시각이 다르면 열람 마감일도 달라짐 (보관기간 역산)
    packs = {}
    for h in (2, 240):  # 2시간 전, 10일 전
        st = (now - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M")
        packs[h] = post("/api/evidence-pack",
                        {"lat": 37.4979, "lng": 127.0276, "stolen_time": st, "radius": 300})
    d1, d2 = packs[2]["cctv"]["request_deadline"], packs[240]["cctv"]["request_deadline"]
    check("도난시각 변경 → 열람 마감일 변경", d1 != d2, f"2시간전={d1}, 10일전={d2}")

    # 3-2. 잔여일수 = 마감일 - 오늘 (실제 역산인지)
    left1, left2 = packs[2]["cctv"]["days_left"], packs[240]["cctv"]["days_left"]
    check("오래된 사건일수록 잔여일 적음", left2 < left1, f"2시간전={left1}일, 10일전={left2}일")

    # 3-3. 요청 구간이 도난시각 ±30분
    w = packs[2]["cctv"]["request_window"]
    f_ = datetime.strptime(w["from"], "%Y-%m-%d %H:%M")
    t_ = datetime.strptime(w["to"], "%Y-%m-%d %H:%M")
    check("영상 요청 구간이 정확히 60분", (t_ - f_) == timedelta(minutes=60),
          f'{w["from"]} ~ {w["to"]}')

    # 3-4. 좌표별 관할기관이 실제로 다름
    ags = {}
    for name, la, lo in SPOTS[:4]:
        st = (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
        p = post("/api/evidence-pack", {"lat": la, "lng": lo, "stolen_time": st, "radius": 300})
        a = p["cctv"]["agencies"]
        ags[name] = a[0]["agency"] if a else None
    check("지역별 관할기관이 서로 다름", len({v for v in ags.values() if v}) >= 3, str(ags))

    # 3-5. 관할기관 전화번호 지역번호가 좌표와 맞음
    tel_ok = True
    tels = {}
    for name, la, lo in [("서울 강남역", 37.4979, 127.0276), ("부산 서면", 35.1578, 129.0596)]:
        st = (now - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
        p = post("/api/evidence-pack", {"lat": la, "lng": lo, "stolen_time": st, "radius": 300})
        tel = (p["cctv"]["agencies"] or [{}])[0].get("tel", "")
        tels[name] = tel
        want = "02-" if "서울" in name else "051-"
        if not tel.startswith(want):
            tel_ok = False
    check("관할기관 전화 지역번호가 좌표와 일치", tel_ok, str(tels))

    # 3-6. 긴급 항목이 조건부로 삽입되는지 (마감 임박 시)
    st = (now - timedelta(days=27)).strftime("%Y-%m-%d %H:%M")
    p = post("/api/evidence-pack", {"lat": 37.4979, "lng": 127.0276, "stolen_time": st, "radius": 300})
    urgent = [i for i in p["checklist"] if i.get("urgent")]
    check("마감 임박(27일 경과) 시 긴급 항목 자동 삽입", len(urgent) >= 1,
          urgent[0]["item"] if urgent else "없음")


def main():
    print(f"대상: {BASE}")
    print(f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        h = get("/healthcheck")
        print(f"헬스체크: {h.get('status')} / db={h.get('db')}")
    except Exception as e:
        print(f"서버 접속 실패: {e}")
        sys.exit(1)

    t1_cctv_real_data()
    t2_movement_computed()
    t3_report_computed()

    total = len(PASS) + len(FAIL)
    print(f"\n{'=' * 52}")
    print(f"결과: {len(PASS)}/{total} PASS")
    if FAIL:
        print("실패 항목:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print("전 항목 통과 — 실데이터/실계산 확인됨")


if __name__ == "__main__":
    main()
