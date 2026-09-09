"""
CCTV 조회 · 열람 안내

공공데이터 '전국 CCTV 표준데이터' 377,243건을 SQLite 격자 인덱스로 조회.
mock 폴백 없이 실데이터만 사용한다.
"""
import math
import os
import sqlite3
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CCTV_DB = os.path.join(BASE_DIR, "data", "cctv.db")
GRID = 0.01

# 도난 사건에서 실제로 열람 가치가 있는 목적 (교통단속·시설물관리 등은 후순위)
PRIORITY_PURPOSE = ("생활방범", "다목적", "차량방범", "어린이보호")


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p = math.radians
    dla = p(lat2 - lat1)
    dlo = p(lng2 - lng1)
    a = math.sin(dla / 2) ** 2 + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def db_available() -> bool:
    return os.path.exists(CCTV_DB)


def _connect():
    con = sqlite3.connect(CCTV_DB)
    con.row_factory = sqlite3.Row
    return con


def find_nearby(lat: float, lng: float, radius_m: float = 200,
                limit: int = 30, purpose: Optional[str] = None) -> list:
    """반경 내 CCTV를 가까운 순으로 반환."""
    if not db_available():
        return []

    # 격자 반경: 위도 0.01도 ≈ 1.11km
    span = max(1, int(radius_m / 1000 / (GRID * 111) ) + 1)
    gx, gy = int(lat / GRID), int(lng / GRID)

    sql = ("SELECT agency, addr, purpose, cams, keep_days, tel, lat, lng "
           "FROM cctv WHERE gx BETWEEN ? AND ? AND gy BETWEEN ? AND ?")
    params = [gx - span, gx + span, gy - span, gy + span]
    if purpose:
        sql += " AND purpose = ?"
        params.append(purpose)

    con = _connect()
    rows = con.execute(sql, params).fetchall()
    con.close()

    out = []
    for r in rows:
        d = haversine_m(lat, lng, r["lat"], r["lng"])
        if d > radius_m:
            continue
        out.append({
            "agency": r["agency"],
            "address": r["addr"],
            "purpose": r["purpose"],
            "cameras": r["cams"],
            "retention_days": r["keep_days"] or 30,
            "tel": r["tel"],
            "lat": r["lat"],
            "lng": r["lng"],
            "distance_m": round(d),
            "priority": r["purpose"] in PRIORITY_PURPOSE,
        })

    # 도난 열람 가치가 높은 목적을 앞에, 그다음 거리순
    out.sort(key=lambda c: (not c["priority"], c["distance_m"]))
    return out[:limit]


def summarize(cctvs: list) -> dict:
    """열람 신청 시 필요한 요약 정보."""
    if not cctvs:
        return {"total_cameras": 0, "agencies": [], "min_retention_days": None, "deadline_hint": None}

    agencies = {}
    for c in cctvs:
        key = c["agency"]
        if key not in agencies:
            agencies[key] = {"agency": key, "tel": c["tel"], "count": 0, "nearest_m": c["distance_m"]}
        agencies[key]["count"] += 1
        agencies[key]["nearest_m"] = min(agencies[key]["nearest_m"], c["distance_m"])

    keeps = [c["retention_days"] for c in cctvs if c["retention_days"]]
    min_keep = min(keeps) if keeps else 30

    return {
        "total_cameras": sum(c["cameras"] or 1 for c in cctvs),
        "agencies": sorted(agencies.values(), key=lambda a: a["nearest_m"]),
        "min_retention_days": min_keep,
        "deadline_hint": f"가장 짧은 보관기간이 {min_keep}일입니다. 사건 발생일로부터 {min_keep}일 이내에 열람을 신청해야 합니다.",
    }


def access_guide(cctvs: Optional[list] = None) -> dict:
    """열람 절차 안내. 조회 결과가 있으면 관할 기관을 반영한다."""
    steps = [
        {"step": 1, "title": "112 신고 및 사건번호 확보",
         "detail": "CCTV 열람은 수사 목적이 확인되어야 가능합니다. 먼저 도난 신고를 접수하고 사건번호를 받으십시오."},
        {"step": 2, "title": "관할 기관 확인",
         "detail": "아래 조회된 CCTV의 관리기관(지자체 또는 경찰)에 연락하여 열람 절차를 확인합니다."},
        {"step": 3, "title": "열람 신청서 제출",
         "detail": "신분증, 사건번호, 도난 일시·장소를 준비해 정보주체 열람 요구서를 제출합니다."},
        {"step": 4, "title": "열람 또는 수사기관 제공",
         "detail": "본인이 촬영된 영상은 직접 열람이 가능하고, 제3자(도난범)가 촬영된 영상은 수사기관을 통해 확인합니다."},
    ]
    tips = [
        "보관기간이 지나면 영상이 자동 삭제되므로 신고 직후 바로 신청해야 합니다.",
        "도난 발생 추정 시각의 앞뒤 30분을 함께 요청하면 이동 방향 파악에 유리합니다.",
        "생활방범 CCTV가 우선 열람 대상입니다. 교통단속용은 촬영 각도상 활용도가 낮습니다.",
    ]
    result = {
        "steps": steps,
        "tips": tips,
        "legal_basis": "개인정보 보호법 제35조(개인정보의 열람), 같은 법 제25조(고정형 영상정보처리기기의 설치·운영 제한)",
        "contact": "관할 경찰서 민원실 또는 112",
    }
    if cctvs:
        s = summarize(cctvs)
        result["retention_notice"] = s["deadline_hint"]
        result["target_agencies"] = s["agencies"][:5]
    return result
