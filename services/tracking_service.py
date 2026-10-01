"""
추적 지원 및 보상 서비스 (시범)

사업계획서의 '보험 청구 자동 패키징'을 전환한 기능이다.
국내에 자전거 도난보험 상품이 없어, 도난 자전거를 중고마켓 모니터링으로 탐지·추적하고
회수에 실패하면 보상을 제공하는 구조로 바꿨다.

사건 상태
  신고 접수 → 탐색 중 → 회수 완료
                     ↘ 회수 실패 판정 → 보상 심사 → 보상 확정 / 보상 반려

경계 (외주 중복 집행 방지)
  - 중고마켓 크롤링·이미지 대조·유사도 매칭은 만들지 않는다.
    외부 탐지 엔진의 결과를 '받는 자리'(add_detections)만 둔다.
  - 등록원부·고유 등록번호 발급은 만들지 않는다. 번호를 '받는 자리'(registration_no)만 둔다.
  - 보상 금액·재원 등 사업 조건은 정하지 않는다. 환경변수 자리만 두고 미정으로 표시한다.

회수 실패 판정 기준 (코드 고정)
  ① 경찰 신고 접수 (접수번호·신고일 기록)
  ② 신고 후 N일 경과 (COMP_WAIT_DAYS, 기본 30일)
  ③ 회수 미확인 (회수 완료 기록 없음)
  N=30 은 자동차 도난 보험금 청구 시 '도난사실확인원'이 신고일로부터 30일 경과 후
  발급되는 관행을 준용했다. (출처: KB손해보험 보험금청구 서류안내 — 도난 항목)
"""
import json
import os
from datetime import timedelta

from .clock import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Session


class TBase(DeclarativeBase):
    pass


STAGES = {
    "reported": "신고 접수",
    "searching": "탐색 중",
    "recovered": "회수 완료",
    "unrecovered": "회수 실패 판정",
    "review": "보상 심사",
    "approved": "보상 확정",
    "rejected": "보상 반려",
}
TERMINAL = {"recovered", "approved", "rejected"}
ALLOWED = {
    "reported": {"searching", "recovered", "unrecovered"},
    "searching": {"recovered", "unrecovered"},
    "unrecovered": {"review", "recovered"},
    "review": {"approved", "rejected", "recovered"},
}

WAIT_DAYS = int(os.environ.get("COMP_WAIT_DAYS", "30"))
WAIT_DAYS_BASIS = "자동차 도난 보험금 청구 시 도난사실확인원 '신고일로부터 30일 경과 후 발급' 관행 준용 (KB손해보험 보험금청구 서류안내)"

# 사업 조건 — 미정. 값이 비어 있으면 '미정(시범)'으로 표기한다.
COMP_CONDITIONS = {
    "보상 한도": os.environ.get("COMP_LIMIT_KRW", ""),
    "보상 비율": os.environ.get("COMP_RATE", ""),
    "보상 재원": os.environ.get("COMP_FUND", ""),
    "자기부담금": os.environ.get("COMP_DEDUCTIBLE", ""),
}

# 보상 신청 제출 서류 (출처 명시)
REQUIRED_DOCS = [
    {
        "doc": "도난사실확인원",
        "where": "관할 경찰서 민원실",
        "why": "도난 신고 사실과 일자를 공적으로 입증",
        "source": "KB손해보험·보험금 청구서류 안내(도난 항목: 도난사실확인원, 경찰서 발급)",
    },
    {
        "doc": "구매 증빙 (영수증·보증서·이체내역)",
        "where": "판매처 / 본인 보관",
        "why": "소유권과 구매가 입증",
        "source": "보험금 신청서류 안내(휴대품 도난: 구매 영수증)",
    },
    {
        "doc": "자전거 사진·차대번호",
        "where": "본인 보관",
        "why": "식별 정보. 경찰 신고 시에도 준비 항목",
        "source": "양천구청 '도난자전거 신고방법' (준비사항: 차대번호, 자전거 사진)",
    },
    {
        "doc": "신분증 사본·지급 계좌 사본",
        "where": "본인",
        "why": "보상금 수령인 확인",
        "source": "보험금 신청서류 안내(재물사고 공통서류: 신분증 사본, 계좌번호)",
    },
    {
        "doc": "개인정보 수집·이용 동의서",
        "where": "서비스 양식",
        "why": "심사 목적 정보 처리",
        "source": "개인정보 보호법 제15조",
    },
]

_engine = None


class CaseTracking(TBase):
    __tablename__ = "case_tracking"

    case_id = Column(String, primary_key=True)
    stage = Column(String, default="reported")
    theft_at = Column(DateTime, nullable=True)
    location = Column(String, default="")
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    bike = Column(Text, default="{}")
    registration_no = Column(String, default="")   # 등록원부 고유번호 — 외부 등록원부 연동 자리
    police_report_no = Column(String, default="")
    police_station = Column(String, default="")
    police_reported_at = Column(DateTime, nullable=True)
    recovered_at = Column(DateTime, nullable=True)
    review_at = Column(DateTime, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, default="")
    verification = Column(Text, default="")         # 도난 입증 판정 결과 — 외부 판정 로직 연동 자리
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class CaseEvent(TBase):
    __tablename__ = "case_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, index=True, nullable=False)
    at = Column(DateTime, nullable=False)
    kind = Column(String, default="")       # stage / police / detection / verification / evaluation / registration
    stage_from = Column(String, default="")
    stage_to = Column(String, default="")
    title = Column(String, default="")
    detail = Column(Text, default="")
    source = Column(String, default="system")


class CaseDetection(TBase):
    __tablename__ = "case_detections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(String, index=True, nullable=False)
    source = Column(String, default="")      # 예: marketplace-engine / test
    platform = Column(String, default="")
    title = Column(String, default="")
    price = Column(String, default="")
    url = Column(String, default="")
    image = Column(String, default="")
    similarity = Column(Integer, default=0)
    region = Column(String, default="")
    detected_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=False)
    status = Column(String, default="new")   # new / confirmed / dismissed / recovered


# ------------------------------------------------------------------ 기반

def init(engine):
    global _engine
    _engine = engine
    TBase.metadata.create_all(engine)


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def parse_dt(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if fmt.endswith("%S") else s[:len(fmt) + 2], fmt)
        except ValueError:
            continue
    return None


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat(sep=" ", timespec="minutes") if d else None


def _event(s: Session, case_id: str, kind: str, title: str, detail=None,
           stage_from: str = "", stage_to: str = "", source: str = "system"):
    s.add(CaseEvent(
        case_id=case_id, at=_now(), kind=kind, title=title,
        detail=json.dumps(detail, ensure_ascii=False) if detail is not None else "",
        stage_from=stage_from, stage_to=stage_to, source=source,
    ))


def _move(s: Session, t: CaseTracking, to: str, title: str, detail=None, source: str = "system"):
    frm = t.stage
    if frm in TERMINAL:
        raise ValueError(f"종결 상태({STAGES[frm]})에서는 변경할 수 없습니다")
    if to not in ALLOWED.get(frm, set()):
        raise ValueError(f"{STAGES.get(frm, frm)} → {STAGES.get(to, to)} 전환은 허용되지 않습니다")
    t.stage = to
    t.updated_at = _now()
    _event(s, t.case_id, "stage", title, detail, stage_from=frm, stage_to=to, source=source)


def _get(s: Session, case_id: str) -> CaseTracking:
    t = s.get(CaseTracking, case_id)
    if not t:
        raise KeyError("추적 정보가 없는 사건입니다")
    return t


# ------------------------------------------------------------------ 사건 등록·기록

def start(case_id: str, theft_at=None, location: str = "", lat=None, lng=None,
          bike: Optional[dict] = None, source: str = "user") -> dict:
    """사건 추적 시작. 신고 접수 기록 후 곧바로 탐색 중(모니터링 대상 등록)으로 전환."""
    with Session(_engine) as s:
        t = s.get(CaseTracking, case_id)
        if t:
            return bundle(case_id)
        now = _now()
        t = CaseTracking(
            case_id=case_id, stage="reported", theft_at=parse_dt(theft_at),
            location=location or "", lat=lat, lng=lng,
            bike=json.dumps(bike or {}, ensure_ascii=False),
            created_at=now, updated_at=now,
        )
        s.add(t)
        _event(s, case_id, "stage", "도난 신고 접수",
               {"도난 일시": _iso(t.theft_at), "장소": location}, stage_to="reported", source=source)
        s.flush()
        _move(s, t, "searching", "중고마켓 모니터링 대상 등록",
              {"탐지": "외부 중고마켓 감시 엔진 결과 수신 대기"})
        s.commit()
    return bundle(case_id)


def set_police(case_id: str, report_no: str, station: str, reported_at) -> dict:
    d = parse_dt(reported_at)
    if not report_no or not d:
        raise ValueError("접수번호와 신고일이 필요합니다")
    with Session(_engine) as s:
        t = _get(s, case_id)
        t.police_report_no = report_no.strip()
        t.police_station = (station or "").strip()
        t.police_reported_at = d
        t.updated_at = _now()
        _event(s, case_id, "police", "112 신고 접수 정보 등록",
               {"접수번호": t.police_report_no, "관할": t.police_station, "신고일": _iso(d)}, source="user")
        s.commit()
    return bundle(case_id)


def set_registration(case_id: str, registration_no: str, source: str = "registry") -> dict:
    """등록원부 고유번호 수신 자리. 번호 발급 로직은 외부 등록원부가 담당한다."""
    with Session(_engine) as s:
        t = _get(s, case_id)
        t.registration_no = (registration_no or "").strip()
        t.updated_at = _now()
        _event(s, case_id, "registration", "등록원부 번호 연결", {"등록번호": t.registration_no}, source=source)
        s.commit()
    return bundle(case_id)


def add_detections(case_id: str, items: list, source: str) -> dict:
    """외부 탐지 엔진이 보낸 의심 매물 수신. 탐지·매칭 로직은 여기 없다."""
    if not isinstance(items, list) or not items:
        raise ValueError("items 가 비었습니다")
    now = _now()
    with Session(_engine) as s:
        t = _get(s, case_id)
        n = 0
        for it in items[:50]:
            s.add(CaseDetection(
                case_id=case_id, source=source[:40],
                platform=str(it.get("platform", ""))[:30],
                title=str(it.get("title", ""))[:120],
                price=str(it.get("price", ""))[:30],
                url=str(it.get("url", ""))[:500],
                image=str(it.get("image", ""))[:500],
                similarity=int(it.get("similarity", 0) or 0),
                region=str(it.get("region", ""))[:60],
                detected_at=parse_dt(it.get("detected_at")) or now,
                received_at=now, status="new",
            ))
            n += 1
        _event(s, case_id, "detection", f"의심 매물 {n}건 수신",
               {"출처": source, "최고 유사도": max((int(i.get('similarity', 0) or 0) for i in items[:50]), default=0)},
               source=source)
        if t.stage == "reported":
            _move(s, t, "searching", "탐지 결과 수신으로 탐색 단계 진입", source=source)
        t.updated_at = now
        s.commit()
    return bundle(case_id)


def set_detection_status(case_id: str, det_id: int, status: str) -> dict:
    if status not in ("confirmed", "dismissed", "recovered"):
        raise ValueError("status 는 confirmed / dismissed / recovered 중 하나")
    with Session(_engine) as s:
        t = _get(s, case_id)
        d = s.get(CaseDetection, det_id)
        if not d or d.case_id != case_id:
            raise KeyError("매물을 찾을 수 없습니다")
        d.status = status
        label = {"confirmed": "도난품 의심 확인", "dismissed": "무관 매물 처리", "recovered": "이 매물로 회수"}[status]
        _event(s, case_id, "detection", f"매물 상태: {label}", {"매물": d.title, "플랫폼": d.platform}, source="user")
        if status == "recovered" and t.stage not in TERMINAL:
            t.recovered_at = _now()
            _move(s, t, "recovered", "자전거 회수 완료", {"경로": f"{d.platform} 매물 확인"}, source="user")
        s.commit()
    return bundle(case_id)


def mark_recovered(case_id: str, note: str = "", source: str = "user") -> dict:
    with Session(_engine) as s:
        t = _get(s, case_id)
        if t.stage == "recovered":
            return bundle(case_id)
        t.recovered_at = _now()
        _move(s, t, "recovered", "자전거 회수 완료", {"메모": note} if note else None, source=source)
        s.commit()
    return bundle(case_id)


def set_verification(case_id: str, verdict: str, confidence=None, evidence=None, source: str = "verifier") -> dict:
    """도난 입증 판정 결과 수신 자리 (허위 신고 방지). 판정 로직은 외부 모듈."""
    payload = {"verdict": verdict, "confidence": confidence, "evidence": evidence or [],
               "source": source, "received_at": _iso(_now())}
    with Session(_engine) as s:
        t = _get(s, case_id)
        t.verification = json.dumps(payload, ensure_ascii=False)
        t.updated_at = _now()
        _event(s, case_id, "verification", f"도난 입증 판정 수신: {verdict}", payload, source=source)
        s.commit()
    return bundle(case_id)


# ------------------------------------------------------------------ 회수 실패 판정

def evaluate(case_id: str, as_of=None) -> dict:
    """회수 실패 판정 기준 점검. as_of 는 미리보기용이며 상태는 바꾸지 않는다."""
    ref = parse_dt(as_of) or _now()
    with Session(_engine) as s:
        t = _get(s, case_id)
        police_ok = bool(t.police_report_no and t.police_reported_at)
        days = (ref - t.police_reported_at).days if t.police_reported_at else None
        elapsed_ok = days is not None and days >= WAIT_DAYS
        not_recovered = t.stage != "recovered" and t.recovered_at is None
        eligible_from = (t.police_reported_at + timedelta(days=WAIT_DAYS)) if t.police_reported_at else None
        ver = json.loads(t.verification) if t.verification else None
        criteria = [
            {"key": "police", "label": "경찰 신고 접수", "required": True, "passed": police_ok,
             "value": f"{t.police_station} {t.police_report_no}".strip() if police_ok else "접수번호·신고일 미등록"},
            {"key": "elapsed", "label": f"신고 후 {WAIT_DAYS}일 경과", "required": True, "passed": elapsed_ok,
             "value": (f"{days}일 경과" if days is not None else "신고일 없음"),
             "days_elapsed": days, "days_left": (max(WAIT_DAYS - days, 0) if days is not None else None)},
            {"key": "not_recovered", "label": "회수 미확인", "required": True, "passed": not_recovered,
             "value": "회수 기록 없음" if not_recovered else "회수 완료 기록 있음"},
            {"key": "registration", "label": "등록원부 등록 (참고)", "required": False,
             "passed": bool(t.registration_no), "value": t.registration_no or "등록원부 연동 전"},
            {"key": "verification", "label": "도난 입증 판정 (참고)", "required": False,
             "passed": bool(ver and str(ver.get("verdict", "")).startswith("도난")),
             "value": (ver or {}).get("verdict") or "판정 결과 미수신"},
        ]
        eligible = all(c["passed"] for c in criteria if c["required"])
        return {
            "case_id": case_id, "stage": t.stage, "stage_label": STAGES.get(t.stage, t.stage),
            "as_of": _iso(ref), "wait_days": WAIT_DAYS, "wait_days_basis": WAIT_DAYS_BASIS,
            "eligible": eligible, "eligible_from": _iso(eligible_from),
            "criteria": criteria,
            "rule": "필수 3개(경찰 신고 접수 · 신고 후 N일 경과 · 회수 미확인) 모두 충족 시 회수 실패 판정",
        }


def apply_evaluation(case_id: str) -> dict:
    """현재 시각 기준 판정. 충족 시 회수 실패 판정 → 보상 심사 대상으로 자동 전환."""
    ev = evaluate(case_id)
    moved = []
    if ev["eligible"] and ev["stage"] in ("reported", "searching"):
        with Session(_engine) as s:
            t = _get(s, case_id)
            crit = {c["label"]: c["value"] for c in ev["criteria"] if c["required"]}
            _move(s, t, "unrecovered", "회수 실패 판정 (필수 기준 3/3 충족)", crit)
            t.review_at = _now()
            _move(s, t, "review", "보상 심사 대상 자동 전환")
            s.commit()
            moved = ["unrecovered", "review"]
    ev2 = evaluate(case_id)
    ev2["transitioned"] = moved
    return ev2


def sweep() -> dict:
    """진행 중 사건 전체 판정 (스케줄러용)."""
    with Session(_engine) as s:
        ids = [r[0] for r in s.query(CaseTracking.case_id)
               .filter(CaseTracking.stage.in_(("reported", "searching"))).all()]
    moved = []
    for cid in ids:
        try:
            if apply_evaluation(cid).get("transitioned"):
                moved.append(cid)
        except Exception:
            continue
    return {"checked": len(ids), "moved_to_review": moved, "at": _iso(_now())}


def decide(case_id: str, decision: str, note: str = "") -> dict:
    if decision not in ("approved", "rejected"):
        raise ValueError("decision 은 approved / rejected")
    with Session(_engine) as s:
        t = _get(s, case_id)
        if t.stage != "review":
            raise ValueError("보상 심사 단계에서만 결정할 수 있습니다")
        t.decided_at = _now()
        t.decision_note = note or ""
        _move(s, t, decision, "보상 확정" if decision == "approved" else "보상 반려",
              {"사유": note} if note else None, source="admin")
        s.commit()
    return bundle(case_id)


# ------------------------------------------------------------------ 조회

def bundle(case_id: str) -> dict:
    with Session(_engine) as s:
        t = _get(s, case_id)
        evs = (s.query(CaseEvent).filter(CaseEvent.case_id == case_id)
               .order_by(CaseEvent.at, CaseEvent.id).all())
        dets = (s.query(CaseDetection).filter(CaseDetection.case_id == case_id)
                .order_by(CaseDetection.received_at.desc(), CaseDetection.id.desc()).all())
        out = {
            "case_id": t.case_id, "stage": t.stage, "stage_label": STAGES.get(t.stage, t.stage),
            "stages": [{"code": k, "label": v} for k, v in STAGES.items()],
            "theft_at": _iso(t.theft_at), "location": t.location, "lat": t.lat, "lng": t.lng,
            "bike": json.loads(t.bike or "{}"),
            "registration_no": t.registration_no,
            "police": {"report_no": t.police_report_no, "station": t.police_station,
                       "reported_at": _iso(t.police_reported_at)},
            "recovered_at": _iso(t.recovered_at), "review_at": _iso(t.review_at),
            "decided_at": _iso(t.decided_at), "decision_note": t.decision_note,
            "verification": json.loads(t.verification) if t.verification else None,
            "events": [{
                "at": _iso(e.at), "kind": e.kind, "title": e.title,
                "stage_from": e.stage_from, "stage_to": e.stage_to,
                "detail": json.loads(e.detail) if e.detail else None, "source": e.source,
            } for e in evs],
            "detections": [{
                "id": d.id, "source": d.source, "platform": d.platform, "title": d.title,
                "price": d.price, "url": d.url, "image": d.image, "similarity": d.similarity,
                "region": d.region, "detected_at": _iso(d.detected_at), "status": d.status,
            } for d in dets],
            "conditions": {k: (v or "미정(시범)") for k, v in COMP_CONDITIONS.items()},
            "required_docs": REQUIRED_DOCS,
        }
    out["evaluation"] = evaluate(case_id)
    return out
