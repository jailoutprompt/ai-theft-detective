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
import re
import urllib.error
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
    a = post("/api/cctv/nearby", {"lat": 37.4979, "lng": 127.0276, "radius": 200})
    b = post("/api/cctv/nearby", {"lat": 37.4979, "lng": 127.0276, "radius": 500})
    na, nb = len(a.get("cctvs", [])), len(b.get("cctvs", []))
    check("반경 200m < 500m 결과 수 증가", nb > na and not a.get("expanded"), f"200m={na}개소, 500m={nb}개소")

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


def raw(path, body=None, method=None, timeout=120):
    """(status, content-type, bytes) — PDF 등 비 JSON 응답용"""
    req = urllib.request.Request(
        BASE + path, method=method or ("POST" if body is not None else "GET"),
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("content-type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read()


def jcall(path, body=None, method=None):
    st, _, b = raw(path, body, method)
    try:
        return st, json.loads(b or b"{}")
    except ValueError:
        return st, {}


def t0_pages():
    print("\n[0] 화면 — 사용자 앱·도난 대응 화면 배포 여부")
    h = get("/healthcheck")
    try:
        gap = abs((datetime.strptime(h.get("server_time", ""), "%Y-%m-%d %H:%M:%S") - datetime.now()).total_seconds())
    except ValueError:
        gap = 1e9
    check("서버 시각이 한국 시간과 일치 (±3분)", gap <= 180, f"server={h.get('server_time')} tz={h.get('tz')} 차이 {int(gap)}초")
    st, _, _ = raw("/app")
    check("사용자 앱 /app 응답 200", st == 200, f"HTTP {st}")
    st, _, b = raw("/service")
    html = b.decode("utf-8", "ignore")
    check("도난 대응 화면 /service 응답 200 + 지도(Leaflet·OSM) 포함",
          st == 200 and "leaflet" in html and "tile.openstreetmap.org" in html, f"HTTP {st}")


def t4_tracking_lifecycle():
    print("\n[4] 추적 지원·보상 — 상태 추적과 회수 실패 판정")
    now = datetime.now()
    f = lambda d: d.strftime("%Y-%m-%d %H:%M")

    # A: 35일 전 신고 → 기준 충족
    st, a = jcall("/api/track/start", {"brand": "검증", "model": "A", "lat": 37.4979, "lng": 127.0276,
                                        "theft_at": f(now - timedelta(days=36))})
    A = a.get("case_id")
    check("사건 등록 시 신고 접수→탐색 중 자동 전환", st == 200 and a.get("stage") == "searching"
          and len(a.get("events", [])) >= 2, f"stage={a.get('stage')}, events={len(a.get('events', []))}")
    _, e0 = jcall(f"/api/track/{A}/eligibility")
    check("112 신고 전에는 판정 불가", e0.get("eligible") is False,
          str([c["value"] for c in e0.get("criteria", []) if c["key"] == "police"]))
    jcall(f"/api/track/{A}/police", {"report_no": "V-0001", "station": "검증서",
                                      "reported_at": f(now - timedelta(days=35))})
    st, e1 = jcall(f"/api/track/{A}/evaluate", {}, "POST")
    check("신고 35일 경과 → 회수 실패 판정 → 보상 심사 자동 전환",
          e1.get("transitioned") == ["unrecovered", "review"] and e1.get("stage") == "review",
          f"transitioned={e1.get('transitioned')}, stage={e1.get('stage')}")

    # B: 5일 전 신고 → 미충족, 잔여일 계산
    _, b = jcall("/api/track/start", {"brand": "검증", "model": "B", "lat": 35.1578, "lng": 129.0596,
                                       "theft_at": f(now - timedelta(days=6))})
    B = b.get("case_id")
    jcall(f"/api/track/{B}/police", {"report_no": "V-0002", "station": "검증서",
                                      "reported_at": f(now - timedelta(days=5))})
    _, e2 = jcall(f"/api/track/{B}/evaluate", {}, "POST")
    el = [c for c in e2.get("criteria", []) if c["key"] == "elapsed"]
    left = el[0].get("days_left") if el else None
    check("신고 5일 경과 → 미충족 유지, 잔여일 = 기준일 - 5",
          e2.get("eligible") is False and e2.get("stage") == "searching" and left == e2.get("wait_days", 30) - 5,
          f"eligible={e2.get('eligible')}, 잔여={left}일")
    future = (now + timedelta(days=e2.get("wait_days", 30))).strftime("%Y-%m-%d")
    _, e3 = jcall(f"/api/track/{B}/eligibility?as_of={future}")
    _, bb = jcall(f"/api/track/{B}")
    check("미래 시점 미리보기는 충족이지만 상태는 바뀌지 않음",
          e3.get("eligible") is True and bb.get("stage") == "searching", f"preview={e3.get('eligible')}, stage={bb.get('stage')}")

    # 탐지 결과 수신 자리
    _, b2 = jcall(f"/api/track/{B}/detections", {"source": "verify", "items": [
        {"platform": "번개장터", "title": "검증 매물", "similarity": 77, "region": "부산"}]})
    check("외부 탐지 결과 수신 → 사건 기록·매물 목록 반영",
          len(b2.get("detections", [])) == 1 and any(e["kind"] == "detection" for e in b2.get("events", [])),
          f"매물 {len(b2.get('detections', []))}건")

    # 회수 → 판정 불가 + 결정 불가
    jcall(f"/api/track/{B}/recovered", {"note": "verify"})
    _, e4 = jcall(f"/api/track/{B}/eligibility?as_of={future}")
    st, _ = jcall(f"/api/track/{B}/decision", {"decision": "approved"})
    check("회수 후에는 미래 시점에도 판정 불가, 보상 결정 거부(400)",
          e4.get("eligible") is False and st == 400, f"eligible={e4.get('eligible')}, 결정 HTTP {st}")

    # 보상 신청 패키지 PDF
    st, ct, pdf = raw(f"/api/track/{A}/compensation-pack")
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    check("보상 신청 패키지 PDF 생성", st == 200 and "pdf" in ct and pdf[:5] == b"%PDF-" and pages >= 1,
          f"HTTP {st}, {len(pdf):,}B, {pages}쪽")
    st, a2 = jcall(f"/api/track/{A}/decision", {"decision": "approved", "note": "verify"})
    check("보상 심사 → 보상 확정 전환", a2.get("stage") == "approved", f"stage={a2.get('stage')}")


def t5_report_pdf():
    print("\n[5] 112 신고서 — 사진·증거팩·공식 신고경로")
    import base64
    # 2x2 JPEG (외부 라이브러리 없이 고정 바이트)
    jpg = base64.b64encode(bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f"
        "141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080002000201"
        "011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b51000020103030204"
        "03050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a"
        "161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778"
        "797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3"
        "d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbfcffd9")).decode()
    # 픽셀이 같은 사진은 PDF가 이미지 객체 1개로 재사용한다.
    # 양자화 표 첫 값(DC)만 바꿔 픽셀이 서로 다른 3장을 만든다 (PIL로 1·3·5 → 고유 픽셀 3종 확인)
    base = bytearray(base64.b64decode(jpg))
    qi = bytes(base).index(b"\xff\xdb\x00\x43\x00") + 5
    photos = []
    for v in (1, 3, 5):
        b = bytearray(base)
        b[qi] = v
        photos.append("data:image/jpeg;base64," + base64.b64encode(bytes(b)).decode())
    body = {"stolen_info": {"brand": "검증", "model": "PDF", "location": "강남역",
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M")},
            "lat": 37.4979, "lng": 127.0276, "photos": photos}
    st, ct, pdf = raw("/api/report/112-form", body)
    imgs = len(re.findall(rb"/Subtype\s*/Image", pdf))
    check("신고서 PDF에 업로드 사진 3장 포함", st == 200 and imgs >= 3, f"HTTP {st}, 이미지 {imgs}개")
    body2 = dict(body, photos=[])
    body2.pop("lat"); body2.pop("lng")
    _, _, pdf2 = raw("/api/report/112-form", body2)
    check("좌표 있을 때 증거팩 섹션이 추가됨 (문서 분량 증가)",
          len(pdf) > len(pdf2) and b"safe182" not in pdf, f"좌표 O {len(pdf):,}B / 좌표 X {len(pdf2):,}B")
    # 5-x. 신고 준비서 개편 (2026-10-02): 좌표가 있으면 지도 이미지가 사진과 별도로 들어간다
    body3 = dict(body, photos=photos[:1])
    _, _, pdf3 = raw("/api/report/112-form", body3)
    n3 = len(re.findall(rb"/Subtype\s*/Image", pdf3))
    check("좌표 있으면 사진 1장 + 지도 이미지 포함 (이미지 2개 이상)", n3 >= 2, f"이미지 {n3}개")
    _, _, pdf4 = raw("/api/report/112-form", dict(body2, photos=photos[:1]))
    n4 = len(re.findall(rb"/Subtype\s*/Image", pdf4))
    check("좌표 없으면 지도 없이 사진만 (이미지 1개)", n4 == 1, f"이미지 {n4}개")
    _, pn = jcall("/api/police/nearby?lat=37.4979&lng=127.0276")
    ors = pn.get("online_report", {})
    check("신고 경로에서 실종자 사이트(safe182) 제거, 출처 명시",
          "safe182" not in json.dumps(ors) and "lost112" in ors and bool(ors.get("source")), ",".join(ors.keys()))


def t6_cctv_expand():
    print("\n[6] CCTV — 목업 제거와 반경 자동 확대")
    d = post("/api/cctv/nearby", {"lat": 34.95, "lng": 126.60, "radius": 200})
    check("200m 0건 지역 → 500m 자동 확대 후 실데이터",
          d.get("expanded") and d.get("radius_used") == 500 and d.get("total", 0) >= 1 and d.get("source") == "public_data",
          f"tried={d.get('tried')}, total={d.get('total')}")
    z = post("/api/cctv/nearby", {"lat": 36.40, "lng": 128.95, "radius": 200})
    check("1000m까지 0건이면 목업 없이 0건 그대로", z.get("total") == 0 and z.get("source") == "public_data",
          f"tried={z.get('tried')}, total={z.get('total')}, source={z.get('source')}")
    g = post("/api/cctv/nearby", {"lat": 37.4979, "lng": 127.0276, "radius": 200})
    check("실데이터 응답에 'Mock' 문구 없음", "mock" not in (g.get("note", "") + d.get("note", "")).lower(),
          g.get("note", ""))


def _police_theft_raw():
    """서비스 코드가 아니라 경찰청 원본 CSV를 직접 읽어 정답을 만든다 (채점표 격리)."""
    import csv
    import io
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data_src", "police_crime_region_2024.csv")
    rows = list(csv.reader(io.StringIO(open(p, "rb").read().decode("cp949"))))
    head = rows[0]
    row = next(r for r in rows if len(r) > 1 and r[1].strip() == "절도범죄")
    out = {}
    for label, v in zip(head[2:], row[2:]):
        if label.startswith("외국"):
            continue
        out[label.replace("경기도 ", "경기 ").replace("강원도 ", "강원 ")] = int(v or 0)
    return out


def t7_movement_data():
    print("\n[7] 모니터링 우선 지역 — 공개 데이터 근거화 (손으로 정한 거점·가중치 제거)")
    raw = _police_theft_raw()
    mx = max(raw.values())
    outs = {n: post("/api/predict-movement", {"lat": la, "lng": lo, "hours_elapsed": 24}) for n, la, lo in SPOTS}
    preds = [p for d in outs.values() for p in d.get("predictions", [])]

    # 7-1. 가중치 출처가 응답에 있음
    src = json.dumps(outs["서울 강남역"].get("data_sources", []), ensure_ascii=False)
    check("가중치 출처(경찰청 2024 지역별 통계) 응답에 명시",
          "경찰청" in src and "3074462" in src and "CCTV" in src, src[:120])

    # 7-2. 반환된 지역별 절도 건수가 경찰청 원본과 전부 일치
    mism = [f"{p['area']}:{p.get('theft_2024')}≠{raw.get(p['area'])}" for p in preds
            if raw.get(p["area"]) != p.get("theft_2024")]
    check("지역별 절도 건수 = 경찰청 원본 CSV (전 지역 대조)", len(preds) >= 15 and not mism,
          f"대조 {len(preds)}건, 불일치 {len(mism)}건 {mism[:3]}")

    # 7-3. 가중치 = 건수 / 전국 최다 (손으로 정한 값이 아님)
    bad = [p["area"] for p in preds if abs(p.get("weight", -1) - p.get("theft_2024", 0) / mx) > 1e-3]
    check("가중치 = 절도 건수 ÷ 전국 최다 시군구 (역산 일치)", not bad,
          f"전국 최다 {mx:,}건 기준, 불일치 {bad[:3]}")

    # 7-4. 이전 수기 거점 이름이 더 이상 나오지 않음
    old = {"서울 중랑 중고시장", "서울 용산 전자상가", "성남 모란시장", "수원 영통", "인천 부평", "고양 화정",
           "부산 구포시장", "창원 상남시장", "김해 내동", "대구 칠성시장", "대전 중앙시장", "광주 말바우시장"}
    hit = sorted({p["area"] for p in preds} & old)
    check("손으로 정한 거점 14곳 미사용", not hit, str(hit) if hit else "없음")

    # 7-5. 후보가 시군구 전체 (14곳 고정 목록이 아님)
    cands = outs["서울 강남역"].get("candidates", {})
    check("후보 지역 = 전국 시군구 200곳 이상", cands.get("regions_total", 0) >= 200, str(cands))

    # 7-6. 도난 직후(2시간)에는 1순위가 도난 지점과 같은 시도
    sido = {"서울 강남역": "서울", "부산 서면": "부산", "김해시청": "경남", "대구 칠성시장": "대구", "제주시청": "제주"}
    tops = {}
    for n, la, lo in SPOTS:
        d = post("/api/predict-movement", {"lat": la, "lng": lo, "hours_elapsed": 2})
        tops[n] = (d.get("predictions") or [{}])[0].get("area", "")
    wrong = {n: a for n, a in tops.items() if not a.startswith(sido[n])}
    check("도난 2시간 후 1순위 = 도난 지점 시도 (5곳)", not wrong, " / ".join(f"{n}→{a}" for n, a in tops.items()))

    # 7-7. 남은 가정값을 숨기지 않음
    asm = outs["서울 강남역"].get("assumptions", [])
    check("근거 없는 가정값(이동 반경 12km/일) 응답에 명시", any("12km" in a for a in asm), "; ".join(asm))


def main():
    print(f"대상: {BASE}")
    print(f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        h = get("/healthcheck")
        print(f"헬스체크: {h.get('status')} / db={h.get('db')}")
    except Exception as e:
        print(f"서버 접속 실패: {e}")
        sys.exit(1)

    t0_pages()
    t1_cctv_real_data()
    t2_movement_computed()
    t3_report_computed()
    t4_tracking_lifecycle()
    t5_report_pdf()
    t6_cctv_expand()
    t7_movement_data()

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
