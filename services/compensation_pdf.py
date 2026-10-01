"""
보상 신청 패키지 PDF (추적 지원 및 보상 서비스 — 시범)

구성
  1. 사건 요약          4. 탐지·추적 이력
  2. 112 신고 정보      5. 증거 확보 정보 (CCTV · 열람 기한)
  3. 회수 실패 판정     6. 제출 서류 체크리스트 (출처 표기)
                       7. 보상 조건 (미정 — 시범)
"""
import io
import os
from .clock import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_FONT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts", "NanumGothic.ttf")
if os.path.exists(_FONT_PATH):
    if "NanumGothic" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("NanumGothic", _FONT_PATH))
    FONT = "NanumGothic"
else:
    FONT = "Helvetica"

NAVY = colors.HexColor("#1a1a2e")
S = {
    "title": ParagraphStyle("t", fontName=FONT, fontSize=16, leading=22, alignment=1, spaceAfter=3),
    "sub": ParagraphStyle("s", fontName=FONT, fontSize=9, textColor=colors.grey, alignment=1, spaceAfter=8),
    "sec": ParagraphStyle("h", fontName=FONT, fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=4),
    "body": ParagraphStyle("b", fontName=FONT, fontSize=8.8, leading=13.5, spaceAfter=2),
    "cell": ParagraphStyle("c", fontName=FONT, fontSize=8.2, leading=11.5),
    "head": ParagraphStyle("hd", fontName=FONT, fontSize=8.2, leading=11.5, textColor=colors.white),
    "small": ParagraphStyle("x", fontName=FONT, fontSize=7.4, leading=10, textColor=colors.HexColor("#555555")),
    "warn": ParagraphStyle("w", fontName=FONT, fontSize=8, leading=12, textColor=colors.HexColor("#b03030")),
    "foot": ParagraphStyle("f", fontName=FONT, fontSize=7.5, textColor=colors.grey, alignment=1),
}


def _esc(v) -> str:
    return str(v if v not in (None, "") else "-").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(v, style="cell"):
    return Paragraph(_esc(v), S[style])


def _kv(rows, w=(42 * mm, 128 * mm)):
    t = Table([[_p(k), _p(v)] for k, v in rows], colWidths=list(w))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f8")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _grid(header, rows, widths):
    data = [[_p(h, "head") for h in header]] + [[_p(c) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def build(b: dict, evidence: dict = None) -> bytes:
    """b: tracking_service.bundle() 결과, evidence: report_service.build_evidence_pack() 결과"""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=16 * mm,
                            title=f"보상 신청 패키지 {b['case_id']}", author="AI 도난탐정")
    now = datetime.now()
    ev = b["evaluation"]
    bike = b.get("bike") or {}
    story = [
        Paragraph("도난 자전거 보상 신청 패키지", S["title"]),
        Paragraph("추적 지원 및 보상 서비스 — 시범 버전  |  AI 도난탐정 · 주식회사 무무익선", S["sub"]),
        HRFlowable(width="100%", thickness=1, color=NAVY, spaceAfter=6),
        Paragraph("본 서비스는 시범 운영 중이며 보상 한도·재원 등 조건은 확정 전입니다. "
                  "본 문서는 보상 심사를 위한 사건 기록 묶음이며 보험 상품이 아닙니다.", S["warn"]),
    ]

    # 1. 사건 요약
    story += [Paragraph("■ 1. 사건 요약", S["sec"]), _kv([
        ("사건 번호", b["case_id"]),
        ("현재 단계", b["stage_label"]),
        ("자전거", " ".join(x for x in [bike.get("brand"), bike.get("model"), bike.get("color")] if x) or "-"),
        ("차대번호", bike.get("serial") or "-"),
        ("등록원부 번호", b.get("registration_no") or "등록원부 연동 전 (외부 등록원부 연계 예정)"),
        ("도난 일시", b.get("theft_at") or "-"),
        ("도난 장소", b.get("location") or (f"{b.get('lat')}, {b.get('lng')}" if b.get("lat") else "-")),
    ])]

    # 2. 112 신고 정보
    pol = b.get("police") or {}
    story += [Paragraph("■ 2. 112 신고 정보", S["sec"]), _kv([
        ("접수번호", pol.get("report_no") or "미등록"),
        ("관할 경찰관서", pol.get("station") or "미등록"),
        ("신고일", pol.get("reported_at") or "미등록"),
    ])]

    # 3. 회수 실패 판정
    story += [Paragraph("■ 3. 회수 실패 판정", S["sec"]),
              Paragraph(f"판정 기준 시점: {ev['as_of']}  |  결과: "
                        f"{'충족 — 보상 심사 대상' if ev['eligible'] else '미충족'}"
                        + (f"  |  판정 가능일: {ev['eligible_from']}" if ev.get('eligible_from') else ""), S["body"])]
    story.append(_grid(
        ["기준", "구분", "충족", "확인값"],
        [[c["label"], "필수" if c["required"] else "참고", "O" if c["passed"] else "X", c["value"]]
         for c in ev["criteria"]],
        [52 * mm, 18 * mm, 14 * mm, 86 * mm],
    ))
    story.append(Paragraph(f"N={ev['wait_days']}일 근거: {ev['wait_days_basis']}", S["small"]))

    # 4. 탐지·추적 이력
    story.append(Paragraph("■ 4. 탐지·추적 이력", S["sec"]))
    evs = b.get("events") or []
    story.append(_grid(["일시", "구분", "내용"],
                       [[e["at"], {"stage": "상태", "police": "신고", "detection": "탐지",
                                   "verification": "입증", "registration": "등록"}.get(e["kind"], e["kind"]),
                         e["title"]] for e in evs[-14:]],
                       [32 * mm, 16 * mm, 122 * mm]))
    dets = b.get("detections") or []
    if dets:
        story.append(Spacer(1, 2 * mm))
        story.append(_grid(["플랫폼", "매물", "지역", "유사도", "상태"],
                           [[d["platform"], d["title"], d["region"], f"{d['similarity']}%",
                             {"new": "확인 전", "confirmed": "의심 확인", "dismissed": "무관",
                              "recovered": "회수"}.get(d["status"], d["status"])] for d in dets[:8]],
                           [22 * mm, 74 * mm, 30 * mm, 16 * mm, 28 * mm]))
        story.append(Paragraph("탐지 결과는 외부 중고마켓 감시 엔진에서 수신한 값이며 유사도는 추정치입니다.", S["small"]))
    else:
        story.append(Paragraph("수신된 의심 매물 없음 — 중고마켓 모니터링 결과 회수 단서 미발견", S["body"]))

    # 5. 증거 확보 정보
    if evidence:
        c = evidence.get("cctv", {})
        story.append(Paragraph("■ 5. 증거 확보 정보 (공공데이터 기반 자동 산출)", S["sec"]))
        rows = [("주변 공공 CCTV", f"반경 {c.get('radius_m')}m 내 {c.get('count')}개소 / 카메라 {c.get('total_cameras')}대")]
        if c.get("min_retention_days"):
            rows.append(("영상 보관기간", f"최단 {c['min_retention_days']}일 — 도난일 기준 열람 신청 기한 산정"))
        if c.get("request_window"):
            rows.append(("열람 요청 구간", f"{c['request_window']['from']} ~ {c['request_window']['to']}"))
        story.append(_kv(rows))
        ags = c.get("agencies") or []
        if ags:
            story.append(Spacer(1, 2 * mm))
            story.append(_grid(["CCTV 관리기관", "연락처", "보유", "최단거리"],
                               [[a["agency"], a.get("tel") or "-", f"{a['count']}개소", f"{a['nearest_m']}m"]
                                for a in ags[:5]],
                               [70 * mm, 40 * mm, 28 * mm, 32 * mm]))
        story.append(Paragraph("출처: 공공데이터포털 「전국 CCTV 표준데이터」 (377,243건)", S["small"]))

    # 6. 제출 서류
    story.append(Paragraph("■ 6. 제출 서류 체크리스트", S["sec"]))
    story.append(_grid(["서류", "발급·준비처", "용도", "근거"],
                       [[d["doc"], d["where"], d["why"], d["source"]] for d in b.get("required_docs", [])],
                       [40 * mm, 30 * mm, 42 * mm, 58 * mm]))

    # 7. 보상 조건
    story.append(Paragraph("■ 7. 보상 조건", S["sec"]))
    story.append(_kv(list((b.get("conditions") or {}).items())))
    story.append(Paragraph("보상 조건은 시범 운영 결과를 반영해 확정합니다. 확정 전에는 금액을 안내하지 않습니다.", S["small"]))

    story += [Spacer(1, 4 * mm), HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=3),
              Paragraph(f"생성 {now.strftime('%Y-%m-%d %H:%M')}  |  AI 도난탐정 추적 지원 및 보상 서비스(시범)  |  "
                        f"사건 {b['case_id']}", S["foot"])]
    doc.build(story)
    return buf.getvalue()
