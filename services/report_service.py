"""
신고 자동화 보강

기존 _build_112_pdf 는 사용자가 입력한 값만 신고서에 옮겨 담았다.
이 모듈은 신고서에 함께 들어가야 실제로 수사에 도움이 되는 항목을 자동 산출한다.

  - 관할 경찰서 매칭 (도난 지점 기준 최근접)
  - 반경 내 CCTV 목록 및 열람 마감일 (보관기간 역산)
  - 우선 수색 지역 (이동패턴 예측 연동)
  - 신고 접수 시 진술 체크리스트

LLM 은 사용하지 않는다. 모든 값이 좌표·공공데이터·경과시간에서 계산된다.
"""
from datetime import datetime, timedelta
from typing import Optional

from . import cctv_service, movement_service


def _parse_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip()[:len(fmt) + 2], fmt)
        except ValueError:
            continue
    return None


def build_evidence_pack(lat: float, lng: float,
                        stolen_time: Optional[str] = None,
                        radius_m: int = 200) -> dict:
    """도난 좌표 기준으로 신고서에 첨부할 증거 확보 정보를 생성한다."""
    now = datetime.now()
    t0 = _parse_time(stolen_time) or now
    hours = max((now - t0).total_seconds() / 3600.0, 0.5)

    # 1) 주변 CCTV
    cctvs = cctv_service.find_nearby(lat, lng, radius_m=radius_m, limit=20)
    summary = cctv_service.summarize(cctvs)

    # 2) 열람 마감일: 도난 시각 + 최단 보관기간
    deadline = None
    days_left = None
    if summary["min_retention_days"]:
        deadline_dt = t0 + timedelta(days=summary["min_retention_days"])
        deadline = deadline_dt.strftime("%Y-%m-%d")
        days_left = (deadline_dt - now).days

    # 3) 우선 수색 지역
    move = movement_service.predict(lat, lng, hours_elapsed=hours, top_n=5)

    # 4) 촬영 시간대 권고 (도난 추정 시각 앞뒤 30분)
    window = None
    if stolen_time and _parse_time(stolen_time):
        window = {
            "from": (t0 - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M"),
            "to": (t0 + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M"),
        }

    return {
        "cctv": {
            "count": len(cctvs),
            "total_cameras": summary["total_cameras"],
            "radius_m": radius_m,
            "agencies": summary["agencies"],
            "min_retention_days": summary["min_retention_days"],
            "request_deadline": deadline,
            "days_left": days_left,
            "request_window": window,
            "list": cctvs[:10],
        },
        "search_areas": move["predictions"],
        "search_radius_km": move["search_radius_km"],
        "hours_elapsed": round(hours, 1),
        "checklist": _checklist(cctvs, days_left),
        "generated_at": now.isoformat(timespec="seconds"),
    }


def _checklist(cctvs: list, days_left: Optional[int]) -> list:
    items = [
        {"item": "도난 일시를 30분 단위까지 특정", "done": False,
         "why": "CCTV 열람 시 확인 구간을 좁혀야 실제 영상을 받을 수 있음"},
        {"item": "자전거 고유 등록번호 또는 차대번호 확인", "done": False,
         "why": "소유 증명과 중고 매물 대조의 기준값"},
        {"item": "구입 영수증 또는 보증서 확보", "done": False,
         "why": "소유권 입증 자료"},
        {"item": "자전거 식별 사진 (전체·프레임·안장·부착물)", "done": False,
         "why": "중고 매물 유사도 대조에 사용"},
    ]
    if cctvs:
        items.append({
            "item": f"주변 CCTV {len(cctvs)}개소 관리기관에 열람 문의",
            "done": False,
            "why": "관리기관별 신청 창구가 달라 사전 확인 필요",
        })
    if days_left is not None and days_left <= 7:
        items.insert(0, {
            "item": f"CCTV 열람 신청 마감 임박 (잔여 {days_left}일)",
            "done": False,
            "why": "보관기간 경과 시 영상이 자동 삭제됨",
            "urgent": True,
        })
    return items


def format_for_pdf(pack: dict) -> list:
    """_build_112_pdf 에 넘길 표 데이터 (라벨, 값) 리스트."""
    c = pack["cctv"]
    rows = [
        ["주변 CCTV", f'반경 {c["radius_m"]}m 내 {c["count"]}개소 / 카메라 {c["total_cameras"]}대'],
    ]
    if c["agencies"]:
        top = c["agencies"][0]
        rows.append(["최근접 관리기관", f'{top["agency"]} ({top["tel"] or "연락처 미등록"})'])
    if c["request_deadline"]:
        left = f' (잔여 {c["days_left"]}일)' if c["days_left"] is not None else ""
        rows.append(["CCTV 열람 마감", f'{c["request_deadline"]}{left}'])
    if c["request_window"]:
        rows.append(["열람 요청 구간", f'{c["request_window"]["from"]} ~ {c["request_window"]["to"]}'])
    if pack["search_areas"]:
        areas = ", ".join(f'{a["area"]}({a["probability"]}%)' for a in pack["search_areas"][:3])
        rows.append(["우선 수색 지역", areas])
    return rows
