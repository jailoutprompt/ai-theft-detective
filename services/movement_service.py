"""
도난 이동패턴 예측

기존 구현은 지역과 무관하게 동일한 값을 반환하는 데모였다.
이 모듈은 아래 세 가지 관측 가능한 근거만으로 우선 수색 지역을 산출한다.

  1) 거리 감쇠      - 도난 지점에서 멀수록 출현 확률 하락
  2) 중고 거래 밀도 - 중고 매물이 실제로 모이는 거점까지의 접근성
  3) 시간 경과      - 경과 시간이 길수록 탐색 반경 확대

학습 모델이 아니라 규칙 기반 추정이며, 반환값에 근거를 함께 실어
어떤 계산으로 나온 수치인지 확인할 수 있게 한다.
"""
import math
from datetime import datetime
from typing import Optional

# 중고거래 거점: 실제 중고 매물 밀집도가 높은 지역
# (당근·번개장터 지역 검색 시 매물 수 기준으로 선정한 고정 좌표)
HUBS = [
    {"name": "서울 중랑 중고시장", "lat": 37.6065, "lng": 127.0927, "weight": 1.00},
    {"name": "서울 용산 전자상가", "lat": 37.5299, "lng": 126.9648, "weight": 0.85},
    {"name": "성남 모란시장", "lat": 37.4322, "lng": 127.1290, "weight": 0.95},
    {"name": "수원 영통", "lat": 37.2595, "lng": 127.0466, "weight": 0.80},
    {"name": "인천 부평", "lat": 37.4894, "lng": 126.7247, "weight": 0.80},
    {"name": "고양 화정", "lat": 37.6345, "lng": 126.8324, "weight": 0.75},
    {"name": "부산 서면", "lat": 35.1578, "lng": 129.0596, "weight": 0.90},
    {"name": "부산 구포시장", "lat": 35.2103, "lng": 128.9954, "weight": 0.75},
    {"name": "창원 상남시장", "lat": 35.2280, "lng": 128.6811, "weight": 0.70},
    {"name": "김해 내동", "lat": 35.2285, "lng": 128.8894, "weight": 0.65},
    {"name": "대구 칠성시장", "lat": 35.8797, "lng": 128.5990, "weight": 0.85},
    {"name": "대전 중앙시장", "lat": 36.3286, "lng": 127.4290, "weight": 0.80},
    {"name": "광주 말바우시장", "lat": 35.1730, "lng": 126.9200, "weight": 0.75},
    {"name": "울산 남구", "lat": 35.5384, "lng": 129.3114, "weight": 0.65},
]

# 도난 자전거가 하루에 이동하는 통상 반경 (km)
DAILY_RADIUS_KM = 12.0
MAX_RADIUS_KM = 60.0


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
    도난 지점 좌표와 경과 시간으로 우선 수색 지역을 산출한다.

    반환 확률은 상대 순위를 나타내는 지표이며 실제 발생 확률이 아니다.
    """
    days = max(hours_elapsed / 24.0, 0.25)
    # 경과 시간에 따른 탐색 반경 (하루 12km, 최대 60km)
    search_radius = min(DAILY_RADIUS_KM * days, MAX_RADIUS_KM)

    scored = []
    for h in HUBS:
        d = _dist_km(lat, lng, h["lat"], h["lng"])
        if d > search_radius * 2.5:
            continue
        # 거리 감쇠: 탐색 반경에서 1.0, 멀어질수록 지수적으로 감소
        decay = math.exp(-max(d - search_radius * 0.3, 0) / max(search_radius, 1.0))
        score = decay * h["weight"]
        scored.append({
            "area": h["name"],
            "lat": h["lat"],
            "lng": h["lng"],
            "distance_km": round(d, 1),
            "_score": score,
        })

    if not scored:
        return {
            "predictions": [],
            "search_radius_km": round(search_radius, 1),
            "hours_elapsed": hours_elapsed,
            "method": "distance_decay_x_hub_density",
            "note": "탐색 반경 내 중고거래 거점이 없습니다. 반경을 넓히거나 인접 지역을 직접 지정하십시오.",
        }

    scored.sort(key=lambda s: s["_score"], reverse=True)
    top = scored[:top_n]

    # 반환 대상(top_n) 기준으로 정규화해야 합이 100%가 된다.
    total = sum(s["_score"] for s in top) or 1.0

    out = []
    for s in top:
        out.append({
            "area": s["area"],
            "lat": s["lat"],
            "lng": s["lng"],
            "distance_km": s["distance_km"],
            "probability": round(s["_score"] / total * 100),
            "basis": f"거리 {s['distance_km']}km · 탐색반경 {round(search_radius,1)}km 내 상대 순위",
        })

    # 반올림 오차를 1순위에 흡수시켜 합계를 100%로 맞춘다.
    if out:
        gap = 100 - sum(o["probability"] for o in out)
        out[0]["probability"] += gap

    return {
        "predictions": out,
        "search_radius_km": round(search_radius, 1),
        "hours_elapsed": hours_elapsed,
        "method": "distance_decay_x_hub_density",
        "formula": "score = exp(-max(d - 0.3R, 0) / R) x hub_weight,  R = 12km/일 x 경과일수 (상한 60km)",
        "disclaimer": "상대 순위 지표이며 통계적 발생 확률이 아님. 중고 매물 모니터링 대상 지역 선정에 사용.",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def monitoring_regions(lat: float, lng: float, hours_elapsed: float = 24.0) -> list:
    """중고마켓 크롤링 시 우선 감시할 지역명 목록."""
    r = predict(lat, lng, hours_elapsed, top_n=5)
    return [p["area"] for p in r["predictions"]]
