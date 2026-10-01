"""
모니터링 우선 지역 산출 (도난 이동패턴)

도난 지점과 경과 시간으로 "중고마켓 감시 엔진이 먼저 볼 지역"의 우선순위를 낸다.
학습 모델이 아니라 규칙 기반 산출이며, 응답에 근거 데이터와 계산식을 함께 싣는다.

2026-10-01 데이터 근거화 (P4)
  이전: 중고거래 거점 14곳과 가중치(0.65~1.00)를 손으로 정했다.
  지금: 후보 지역·가중치·좌표를 모두 공개 데이터에서 산출한다.
    - 후보 지역 : 경찰청 범죄 통계의 시군구 227곳
    - 가중치    : 2024년 시군구별 절도범죄 발생 건수 (경찰청, 공공데이터포털 3074462)
    - 좌표      : 「전국 CCTV 표준데이터」 좌표의 시군구별 중앙값
  빌드: scripts/build_region_weights.py → data_src/region_weights.json

남아 있는 가정값 (데이터 근거 없음, 응답의 assumptions 에 그대로 노출)
  - 하루 이동 반경 12km, 상한 60km
"""
import json
import math
import os
from typing import Optional

from .clock import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "data_src", "region_weights.json")

with open(_DATA, encoding="utf-8") as _f:
    _BUNDLE = json.load(_f)

META = _BUNDLE["meta"]
REGIONS = [r for r in _BUNDLE["regions"] if r["theft_2024"] > 0]
_MAX_THEFT = max(r["theft_2024"] for r in REGIONS)

# 가정값: 도난 자전거가 하루에 이동하는 통상 반경 (km)
DAILY_RADIUS_KM = 12.0
MAX_RADIUS_KM = 60.0
MIN_CANDIDATES = 3

FORMULA = ("score = exp(-max(d - 0.3R, 0) / R) x w,  "
           "w = 시군구 절도 발생 건수 / 전국 최다 시군구 건수 (2024, 경찰청),  "
           "R = 12km/일 x 경과일수 (상한 60km)")

DATA_SOURCES = [
    {"use": "가중치", "name": META["weight_source"], "url": META["weight_source_url"],
     "note": META["weight_note"]},
    {"use": "지역 좌표", "name": META["coord_source"],
     "url": "https://www.data.go.kr/data/15013094/standard.do"},
]


def _dist_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p = math.radians
    dla = p(lat2 - lat1)
    dlo = p(lng2 - lng1)
    a = math.sin(dla / 2) ** 2 + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def predict(lat: float, lng: float, hours_elapsed: float = 24.0,
            top_n: int = 5) -> dict:
    """
    도난 지점 좌표와 경과 시간으로 모니터링 우선 지역을 산출한다.

    반환 확률은 상대 순위를 나타내는 지표이며 실제 발생 확률이 아니다.
    """
    days = max(hours_elapsed / 24.0, 0.25)
    search_radius = min(DAILY_RADIUS_KM * days, MAX_RADIUS_KM)

    dists = [(_dist_km(lat, lng, r["lat"], r["lng"]), r) for r in REGIONS]
    dists.sort(key=lambda x: x[0])
    cand = [x for x in dists if x[0] <= search_radius * 2.5]
    if len(cand) < MIN_CANDIDATES:
        # 시군구 중심이 멀리 떨어진 지역(도서·산간)에서도 결과가 비지 않게 가장 가까운 곳을 보탠다
        cand = dists[:max(MIN_CANDIDATES, len(cand))]

    scored = []
    for d, r in cand:
        decay = math.exp(-max(d - search_radius * 0.3, 0) / max(search_radius, 1.0))
        w = r["theft_2024"] / _MAX_THEFT
        scored.append({
            "area": r["region"],
            "lat": r["lat"],
            "lng": r["lng"],
            "distance_km": round(d, 1),
            "theft_2024": r["theft_2024"],
            "weight": round(w, 4),
            "decay": round(decay, 4),
            "_score": decay * w,
        })

    scored.sort(key=lambda s: s["_score"], reverse=True)
    top = scored[:top_n]
    total = sum(s["_score"] for s in top) or 1.0

    out = []
    for s in top:
        out.append({
            "area": s["area"],
            "lat": s["lat"],
            "lng": s["lng"],
            "distance_km": s["distance_km"],
            "probability": round(s["_score"] / total * 100),
            "theft_2024": s["theft_2024"],
            "weight": s["weight"],
            "basis": f"2024년 절도 {s['theft_2024']:,}건(경찰청) · 거리 {s['distance_km']}km",
        })

    if out:
        gap = 100 - sum(o["probability"] for o in out)
        out[0]["probability"] += gap
    # 반올림해 0%가 된 지역(예: 제주에서 바다 건너 내륙)은 감시 대상에서 뚜다
    out = [o for o in out if o["probability"] > 0]

    return {
        "predictions": out,
        "search_radius_km": round(search_radius, 1),
        "hours_elapsed": hours_elapsed,
        "method": "distance_decay_x_regional_theft_2024",
        "formula": FORMULA,
        "candidates": {"regions_total": len(REGIONS), "regions_in_range": len(cand)},
        "data_sources": DATA_SOURCES,
        "assumptions": [
            "하루 이동 반경 12km·상한 60km는 설정값 (관측 데이터 없음)",
            "자전거 절도 시군구 통계 미공개로 절도범죄 전체 건수를 대리 지표로 사용",
        ],
        "disclaimer": "상대 순위 지표이며 통계적 발생 확률이 아님. 중고 매물 모니터링 대상 지역 선정에 사용.",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def monitoring_regions(lat: float, lng: float, hours_elapsed: float = 24.0) -> list:
    """중고마켓 감시 엔진에 넘길 우선 감시 지역명 목록."""
    r = predict(lat, lng, hours_elapsed, top_n=5)
    return [p["area"] for p in r["predictions"]]
