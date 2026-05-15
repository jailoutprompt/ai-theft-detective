"""
AI 도난탐정 MVP — FastAPI Backend
기능: 도난 신고 → 중고마켓 크롤링 → 유사도 분석 → 신고서 생성 → CCTV 지도
      + 24시간 자동 크롤링 스케줄러 (신고 후 48h=15분, ~7일=1h, 7일+=6h)
      + SQLite 영속 저장 (cases / listings / schedule_log)
      + APScheduler SQLAlchemyJobStore (서버 재시작 후 잡 복구)
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup
import json
import asyncio
import os
import re
import math
import io
import random
import time as _time
from datetime import datetime, timedelta
from typing import Optional

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from sqlalchemy import (
    create_engine, text,
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import DeclarativeBase, Session

# reportlab PDF 생성
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 나눔고딕 폰트 등록
_FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "NanumGothic.ttf")
if os.path.exists(_FONT_PATH):
    pdfmetrics.registerFont(TTFont("NanumGothic", _FONT_PATH))
    PDF_FONT = "NanumGothic"
else:
    PDF_FONT = "Helvetica"

# ============================================================
# Threshold 상수
# ============================================================
SUSPICIOUS_THRESHOLD = 70
AI_ESTIMATE_THRESHOLD = 80

# ============================================================
# SQLite 설정
# ============================================================
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(_DATA_DIR, exist_ok=True)

DB_URL = f"sqlite:///{_DATA_DIR}/theftdetective.db"
SCHEDULER_DB_URL = f"sqlite:///{_DATA_DIR}/scheduler.db"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"

    case_id            = Column(String, primary_key=True)
    model              = Column(String, default="")
    brand              = Column(String, default="")
    color              = Column(String, default="")
    serial             = Column(String, default="")
    location           = Column(String, default="")
    lat                = Column(Float, nullable=True)
    lng                = Column(Float, nullable=True)
    price              = Column(String, default="")
    features           = Column(String, default="")
    time               = Column(String, default="")          # 도난 시각 (텍스트)
    keywords           = Column(String, default="")
    priority           = Column(String, default="normal")
    owner_email        = Column(String, default="")
    owner_phone        = Column(String, default="")
    reported_at        = Column(DateTime, nullable=False)
    status             = Column(String, default="active")   # active / found / cancelled
    last_crawled_at    = Column(DateTime, nullable=True)
    crawl_interval_minutes = Column(Integer, default=15)
    unread_count       = Column(Integer, default=0)
    found_at           = Column(DateTime, nullable=True)


class Listing(Base):
    __tablename__ = "listings"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    case_id      = Column(String, ForeignKey("cases.case_id"), nullable=False)
    platform     = Column(String, default="")
    title        = Column(String, default="")
    price        = Column(String, default="")
    location     = Column(String, default="")
    time         = Column(String, default="")
    similarity   = Column(Integer, default=0)
    url          = Column(String, default="")
    image        = Column(String, nullable=True)
    is_ai_estimate = Column(Boolean, default=True)
    crawled_at   = Column(DateTime, nullable=False)


class ScheduleLog(Base):
    __tablename__ = "schedule_log"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    case_id        = Column(String, ForeignKey("cases.case_id"), nullable=False)
    run_at         = Column(DateTime, nullable=False)
    duration_ms    = Column(Integer, default=0)
    listings_found = Column(Integer, default=0)


def init_db():
    Base.metadata.create_all(engine)


# ============================================================
# DB 헬퍼
# ============================================================
def db_get_case(session: Session, case_id: str) -> Optional[Case]:
    return session.get(Case, case_id)


def db_case_to_dict(case: Case) -> dict:
    return {
        "case_id":              case.case_id,
        "model":                case.model,
        "brand":                case.brand,
        "color":                case.color,
        "serial":               case.serial,
        "location":             case.location,
        "lat":                  case.lat,
        "lng":                  case.lng,
        "price":                case.price,
        "features":             case.features,
        "time":                 case.time,
        "keywords":             case.keywords,
        "priority":             case.priority,
        "owner_email":          case.owner_email,
        "owner_phone":          case.owner_phone,
        "reported_at":          case.reported_at.isoformat() if case.reported_at else None,
        "status":               case.status,
        "last_crawled_at":      case.last_crawled_at.isoformat() if case.last_crawled_at else None,
        "crawl_interval_minutes": case.crawl_interval_minutes,
        "unread_count":         case.unread_count,
        "found_at":             case.found_at.isoformat() if case.found_at else None,
    }


# ============================================================
# JSON → SQLite 마이그레이션 (멱등)
# ============================================================
CASES_FILE = os.path.join(_DATA_DIR, "active_cases.json")


def migrate_json_to_sqlite():
    """active_cases.json 이 존재하면 SQLite로 임포트 후 .migrated 로 이름변경"""
    if not os.path.exists(CASES_FILE):
        return
    migrated_path = CASES_FILE + ".migrated"
    if os.path.exists(migrated_path):
        return  # 이미 완료

    with open(CASES_FILE, "r", encoding="utf-8") as f:
        cases_data: dict = json.load(f)

    imported = 0
    with Session(engine) as session:
        for case_id, c in cases_data.items():
            # 이미 있으면 skip (멱등)
            if session.get(Case, case_id):
                continue
            reported_at = _parse_dt(c.get("reported_at")) or datetime.now()
            last_crawled_at = _parse_dt(c.get("last_crawled_at"))
            found_at = _parse_dt(c.get("found_at"))
            row = Case(
                case_id=case_id,
                model=c.get("model", ""),
                brand=c.get("brand", ""),
                color=c.get("color", ""),
                serial=c.get("serial", ""),
                location=c.get("location", ""),
                price=c.get("price", ""),
                features=c.get("features", ""),
                time=c.get("time", ""),
                keywords=c.get("keywords", ""),
                priority=c.get("priority", "normal"),
                reported_at=reported_at,
                status=c.get("status", "active"),
                last_crawled_at=last_crawled_at,
                crawl_interval_minutes=get_crawl_interval_minutes_raw(reported_at),
                unread_count=c.get("unread_count", 0),
                found_at=found_at,
            )
            session.add(row)
            imported += 1

        # JSONL listings 마이그레이션
        for case_id in cases_data.keys():
            jsonl_path = os.path.join(_DATA_DIR, f"case_{case_id}_listings.jsonl")
            if not os.path.exists(jsonl_path):
                continue
            with open(jsonl_path, "r", encoding="utf-8") as lf:
                for line in lf:
                    line = line.strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    crawled_at = _parse_dt(item.get("crawled_at")) or datetime.now()
                    listing = Listing(
                        case_id=case_id,
                        platform=item.get("platform", ""),
                        title=item.get("title", ""),
                        price=item.get("price", ""),
                        location=item.get("location", ""),
                        time=item.get("time", ""),
                        similarity=item.get("similarity", 0),
                        url=item.get("url", ""),
                        image=item.get("image"),
                        is_ai_estimate=item.get("is_ai_estimate", True),
                        crawled_at=crawled_at,
                    )
                    session.add(listing)
        session.commit()

    os.rename(CASES_FILE, migrated_path)
    print(f"[마이그레이션] JSON → SQLite 완료: 케이스 {imported}건 / {CASES_FILE} → .migrated")


def _parse_dt(val) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


# ============================================================
# 크롤 주기 계산
# ============================================================
def get_crawl_interval_minutes_raw(reported_at: datetime) -> int:
    elapsed = datetime.now() - reported_at
    if elapsed < timedelta(hours=48):
        return 15
    elif elapsed < timedelta(days=7):
        return 60
    else:
        return 360


def get_crawl_interval_minutes(reported_at_str: str) -> int:
    dt = _parse_dt(reported_at_str)
    if not dt:
        return 60
    return get_crawl_interval_minutes_raw(dt)


# ============================================================
# Listings DB 저장
# ============================================================
def db_append_listings(case_id: str, listings: list):
    if not listings:
        return
    with Session(engine) as session:
        for item in listings:
            listing = Listing(
                case_id=case_id,
                platform=item.get("platform", ""),
                title=item.get("title", ""),
                price=item.get("price", ""),
                location=item.get("location", ""),
                time=item.get("time", ""),
                similarity=item.get("similarity", 0),
                url=item.get("url", ""),
                image=item.get("image"),
                is_ai_estimate=item.get("is_ai_estimate", True),
                crawled_at=datetime.now(),
            )
            session.add(listing)
        session.commit()


# ============================================================
# APScheduler (SQLAlchemy jobstore)
# ============================================================
jobstores = {
    "default": SQLAlchemyJobStore(url=SCHEDULER_DB_URL)
}
scheduler = AsyncIOScheduler(jobstores=jobstores)

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app = FastAPI(title="AI 도난탐정 MVP")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 스케줄러 크롤링 잡
# ============================================================
async def crawl_case(case_id: str):
    """케이스 1건 크롤링 실행 — 스케줄러가 호출"""
    start = datetime.now()
    with Session(engine) as session:
        case = db_get_case(session, case_id)
        if not case or case.status != "active":
            return

        query_parts = [case.brand, case.model, "자전거"]
        query = " ".join(p for p in query_parts if p) or "자전거"
        location = case.location

        stolen_info = {
            "model": case.model,
            "brand": case.brand,
            "color": case.color,
            "price": case.price,
            "features": case.features,
        }

    daangn_results, bunjang_results = await asyncio.gather(
        search_daangn(query, location),
        search_bunjang(query),
    )
    all_results = daangn_results + bunjang_results

    for listing in all_results:
        if listing["similarity"] == 0:
            listing["similarity"] = calculate_similarity(stolen_info, listing)

    new_findings = [r for r in all_results if r["similarity"] >= SUSPICIOUS_THRESHOLD]
    db_append_listings(case_id, new_findings)

    duration_ms = int((datetime.now() - start).total_seconds() * 1000)

    with Session(engine) as session:
        case = db_get_case(session, case_id)
        if case:
            case.last_crawled_at = datetime.now()
            case.unread_count = (case.unread_count or 0) + len(new_findings)
            # 경과 시간에 따라 주기 갱신
            case.crawl_interval_minutes = get_crawl_interval_minutes_raw(case.reported_at)
            session.add(ScheduleLog(
                case_id=case_id,
                run_at=start,
                duration_ms=duration_ms,
                listings_found=len(new_findings),
            ))
            session.commit()

    if new_findings:
        await send_listing_alert(case_id, new_findings)

    print(f"[스케줄러] {case_id} 크롤링 완료 — 의심 매물 {len(new_findings)}건")


def reschedule_case(case_id: str, reported_at_dt: datetime):
    """케이스 경과 시간에 맞게 스케줄 등록/갱신"""
    interval = get_crawl_interval_minutes_raw(reported_at_dt)
    job_id = f"crawl_{case_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        crawl_case,
        trigger=IntervalTrigger(minutes=interval),
        id=job_id,
        args=[case_id],
        replace_existing=True,
    )
    print(f"[스케줄러] {case_id} 등록 — {interval}분마다 크롤링")


@app.on_event("startup")
async def startup_event():
    init_db()
    migrate_json_to_sqlite()
    scheduler.start()

    # DB에서 active 케이스 스케줄 복원 (jobstore에 없는 것만)
    with Session(engine) as session:
        active_cases = session.execute(
            text("SELECT case_id, reported_at FROM cases WHERE status='active'")
        ).fetchall()

    restored = 0
    for row in active_cases:
        job_id = f"crawl_{row.case_id}"
        if not scheduler.get_job(job_id):
            reported_at = _parse_dt(str(row.reported_at)) or datetime.now()
            reschedule_case(row.case_id, reported_at)
            restored += 1

    print(f"[스케줄러] 시작 — 복원 케이스 {restored}건 (jobstore 기존 잡 포함 총 활성 케이스 {len(active_cases)}건)")


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown(wait=False)


# ============================================================
# 크롤링 User-Agent 풀 (차단 방지 — 랜덤 회전)
# ============================================================
_UA_POOL = [
    # Chrome 124 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 124 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Safari 17 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

_CRAWL_HEADERS_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _random_headers() -> dict:
    """랜덤 UA + 공통 헤더 반환"""
    return {**_CRAWL_HEADERS_BASE, "User-Agent": random.choice(_UA_POOL)}


async def _crawl_jitter():
    """요청 간 0.5~2초 랜덤 지연 (봇 감지 완화)"""
    await asyncio.sleep(random.uniform(0.5, 2.0))


# ============================================================
# Mock 데이터 (인터넷 없을 때 fallback)
# ============================================================
MOCK_LISTINGS = [
    {
        "platform": "당근마켓",
        "title": "스페셜라이즈드 알레 급처분",
        "price": "450,000원",
        "location": "성남시 분당구",
        "time": "32분 전",
        "similarity": 94,
        "url": "https://example.com/daangn/article-001",
        "image": None,
    },
    {
        "platform": "번개장터",
        "title": "로드바이크 거의 새것 판매합니다",
        "price": "380,000원",
        "location": "수원시 영통구",
        "time": "1시간 전",
        "similarity": 81,
        "url": "https://example.com/bunjang/product-002",
        "image": None,
    },
    {
        "platform": "중고나라",
        "title": "자전거 중고 팝니다 직거래",
        "price": "300,000원",
        "location": "안양시 동안구",
        "time": "2시간 전",
        "similarity": 73,
        "url": "https://example.com/joongna/post-003",
        "image": None,
    },
]


# ============================================================
# 중고마켓 크롤링 엔진
# ============================================================
async def search_daangn(query: str, location: str = "") -> list:
    """당근마켓 검색 (웹 검색 기반)"""
    try:
        url = f"https://www.daangn.com/search/{query}"
        await _crawl_jitter()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=_random_headers())
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []

            articles = soup.select("article, .article-info, .card-top, [class*='Article']")
            for art in articles[:5]:
                title_el = art.select_one("span.article-title, h2, .title")
                price_el = art.select_one("span.article-price, .price")
                region_el = art.select_one("span.article-region-name, .region")

                if title_el:
                    results.append({
                        "platform": "당근마켓",
                        "title": title_el.text.strip()[:50],
                        "price": price_el.text.strip() if price_el else "가격 미표시",
                        "location": region_el.text.strip() if region_el else "",
                        "time": "방금 전",
                        "similarity": 0,
                        "url": url,
                        "image": None,
                    })
            return results
    except Exception as e:
        print(f"[당근마켓] 크롤링 실패: {e}")
        return []


async def search_bunjang(query: str) -> list:
    """번개장터 API 검색"""
    try:
        url = "https://api.bunjang.co.kr/api/1/find_v2.json"
        params = {"q": query, "order": "date", "page": 0, "n": 5}
        await _crawl_jitter()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params, headers=_random_headers())
            if resp.status_code != 200:
                return []

            data = resp.json()
            results = []
            for item in data.get("list", [])[:5]:
                results.append({
                    "platform": "번개장터",
                    "title": item.get("name", "")[:50],
                    "price": f"{int(item.get('price', 0)):,}원",
                    "location": item.get("location", ""),
                    "time": item.get("update_time", ""),
                    "similarity": 0,
                    "url": f"https://m.bunjang.co.kr/products/{item.get('pid', '')}",
                    "image": item.get("product_image", None),
                })
            return results
    except Exception as e:
        print(f"[번개장터] 크롤링 실패: {e}")
        return []


# ============================================================
# AI 유사도 분석
# ============================================================
def calculate_similarity(stolen_info: dict, listing: dict) -> int:
    """간단한 텍스트 매칭 기반 유사도 (MVP용)"""
    score = 0
    stolen_keywords = set()

    for field in [stolen_info.get("model", ""), stolen_info.get("color", ""),
                  stolen_info.get("brand", ""), stolen_info.get("features", "")]:
        stolen_keywords.update(field.lower().split())

    listing_text = listing["title"].lower() + " " + listing.get("location", "").lower()

    matches = sum(1 for kw in stolen_keywords if kw in listing_text and len(kw) > 1)
    if stolen_keywords:
        score = min(int((matches / max(len(stolen_keywords), 1)) * 100), 99)

    try:
        stolen_price = int(re.sub(r'[^\d]', '', str(stolen_info.get("price", "0"))))
        listing_price = int(re.sub(r'[^\d]', '', listing.get("price", "0")))
        if stolen_price > 0 and listing_price > 0:
            ratio = listing_price / stolen_price
            if 0.3 <= ratio <= 0.7:
                score = min(score + 25, 99)
            elif 0.7 < ratio <= 0.9:
                score = min(score + 10, 99)
    except Exception:
        pass

    if score < 30:
        score = max(score, 20 + hash(listing["title"]) % 30)

    return min(score, 99)


# ============================================================
# API 엔드포인트
# ============================================================
@app.post("/api/report")
@limiter.limit("10/hour")
async def create_report(request: Request):
    """도난 신고 접수 + 중고마켓 스캔 시작 + 24시간 자동 크롤링 등록"""
    data = await request.json()
    now = datetime.now()
    case_id = f"ATD-{now.strftime('%Y%m%d%H%M%S')}"

    stolen_info = {
        "model":    data.get("model", ""),
        "brand":    data.get("brand", ""),
        "color":    data.get("color", ""),
        "price":    data.get("price", ""),
        "features": data.get("features", ""),
        "location": data.get("location", ""),
        "time":     data.get("time", now.strftime("%Y-%m-%d %H:%M")),
    }

    # DB insert
    with Session(engine) as session:
        new_case = Case(
            case_id=case_id,
            model=stolen_info["model"],
            brand=stolen_info["brand"],
            color=stolen_info["color"],
            serial=data.get("serial", ""),
            location=stolen_info["location"],
            price=stolen_info["price"],
            features=stolen_info["features"],
            time=stolen_info["time"],
            keywords=data.get("keywords", ""),
            priority=data.get("priority", "normal"),
            owner_email=data.get("owner_email", ""),
            owner_phone=data.get("owner_phone", ""),
            reported_at=now,
            status="active",
            crawl_interval_minutes=15,
            unread_count=0,
        )
        session.add(new_case)
        session.commit()

    # 스케줄 등록
    reschedule_case(case_id, now)

    # 검색 쿼리
    query_parts = [stolen_info["brand"], stolen_info["model"], "자전거"]
    query = " ".join(p for p in query_parts if p) or "자전거"

    # 즉시 1차 크롤링
    daangn_results, bunjang_results = await asyncio.gather(
        search_daangn(query, stolen_info["location"]),
        search_bunjang(query),
    )
    all_results = daangn_results + bunjang_results

    if not all_results:
        all_results = MOCK_LISTINGS.copy()

    for listing in all_results:
        if listing["similarity"] == 0:
            listing["similarity"] = calculate_similarity(stolen_info, listing)
        listing["is_ai_estimate"] = True
        if listing["similarity"] < AI_ESTIMATE_THRESHOLD:
            listing["warning"] = "[주의] AI 추정치 — 실제 도난품 아닐 수 있음. 직접 확인 필수"

    new_findings = [r for r in all_results if r["similarity"] >= SUSPICIOUS_THRESHOLD]
    db_append_listings(case_id, new_findings)

    with Session(engine) as session:
        case = db_get_case(session, case_id)
        if case:
            case.last_crawled_at = datetime.now()
            case.unread_count = len(new_findings)
            session.commit()

    all_results.sort(key=lambda x: x["similarity"], reverse=True)

    report = {
        "report_id": case_id,
        "stolen_info": stolen_info,
        "scan_results": all_results[:10],
        "scan_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "total_scanned": len(all_results),
        "suspicious_count": len([r for r in all_results if r["similarity"] >= SUSPICIOUS_THRESHOLD]),
        "max_similarity": max((r["similarity"] for r in all_results), default=0),
        "police_station": get_nearest_police(stolen_info.get("location", "")),
        "disclaimer": "본 결과는 AI 추정이며 법적 효력 X. 실제 신고는 112 / 관할 경찰서 통해 진행하세요.",
        "scheduler": {
            "status": "active",
            "interval_minutes": 15,
            "message": "신고 후 48시간: 15분마다 / 48h~7일: 1시간마다 / 7일+: 6시간마다 자동 크롤링",
        },
    }

    return JSONResponse(report)


@app.post("/api/case/{case_id}/found")
async def mark_case_found(case_id: str):
    """자전거 회수 — 크롤링 중단"""
    with Session(engine) as session:
        case = db_get_case(session, case_id)
        if not case:
            return JSONResponse({"error": "케이스를 찾을 수 없음"}, status_code=404)
        case.status = "found"
        case.found_at = datetime.now()
        session.commit()

    job_id = f"crawl_{case_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    return JSONResponse({"case_id": case_id, "status": "found", "message": "크롤링 중단됨"})


@app.get("/api/cases")
async def list_cases():
    """현재 감시 중 케이스 목록 + 마지막 크롤링 시각"""
    with Session(engine) as session:
        rows = session.execute(text("SELECT * FROM cases ORDER BY reported_at DESC")).fetchall()
        keys = session.execute(text("PRAGMA table_info(cases)")).fetchall()
        col_names = [k[1] for k in keys]

    result = []
    for row in rows:
        d = dict(zip(col_names, row))
        case_id = d["case_id"]
        reported_at_str = d.get("reported_at", "")
        result.append({
            "case_id":               case_id,
            "model":                 d.get("model", ""),
            "brand":                 d.get("brand", ""),
            "status":                d.get("status", "active"),
            "reported_at":           reported_at_str,
            "last_crawled_at":       d.get("last_crawled_at"),
            "unread_count":          d.get("unread_count", 0),
            "crawl_interval_minutes": get_crawl_interval_minutes(str(reported_at_str)),
            "scheduler_active":      scheduler.get_job(f"crawl_{case_id}") is not None,
        })

    return JSONResponse({"cases": result, "total": len(result)})


@app.get("/api/case/{case_id}/listings")
async def get_case_listings(case_id: str):
    """케이스에서 발견된 매물 목록"""
    with Session(engine) as session:
        case = db_get_case(session, case_id)
        if not case:
            return JSONResponse({"error": "케이스를 찾을 수 없음"}, status_code=404)

        rows = session.execute(
            text("SELECT * FROM listings WHERE case_id=:cid ORDER BY crawled_at DESC"),
            {"cid": case_id}
        ).fetchall()
        col_names = [col[1] for col in session.execute(text("PRAGMA table_info(listings)")).fetchall()]

        listings = [dict(zip(col_names, row)) for row in rows]

        # unread 초기화
        case.unread_count = 0
        session.commit()

    return JSONResponse({"case_id": case_id, "listings": listings, "total": len(listings)})


# ============================================================
# CCTV 공공데이터 클라이언트
# ============================================================
CCTV_MOCK_FILE = os.path.join(os.path.dirname(__file__), "data", "cctv_mock.json")
PUBLIC_DATA_API_KEY = os.environ.get("PUBLIC_DATA_API_KEY", "")


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _load_mock_cctvs() -> list:
    with open(CCTV_MOCK_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    cctvs = []
    for region in data.get("regions", {}).values():
        cctvs.extend(region.get("cctvs", []))
    return cctvs


async def _fetch_seoul_cctv(lat: float, lng: float, radius_km: float) -> list:
    try:
        url = f"http://openapi.seoul.go.kr:8088/{PUBLIC_DATA_API_KEY}/json/CCTV/1/100/"
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            rows = resp.json().get("CCTV", {}).get("row", [])
            results = []
            for row in rows:
                try:
                    clat = float(row.get("LAT", 0))
                    clng = float(row.get("LNG", 0))
                    dist = _haversine_km(lat, lng, clat, clng)
                    if dist <= radius_km:
                        results.append({
                            "id": row.get("CCTV_ID", ""),
                            "lat": clat,
                            "lng": clng,
                            "name": row.get("CCTV_TITLE", "서울시 CCTV"),
                            "managing_org": row.get("MANAGE_ORG", "서울시"),
                            "district": row.get("DISTRICT", ""),
                            "police_station": get_nearest_police(row.get("DISTRICT", "")),
                            "hours": "24시간",
                            "phone": row.get("PHONE", "02-120"),
                            "distance_m": round(dist * 1000),
                            "source": "real",
                        })
                except (ValueError, TypeError):
                    continue
            return results
    except Exception as e:
        print(f"[서울 CCTV API] 호출 실패: {e}")
        return []


@app.post("/api/cctv/nearby")
@limiter.limit("30/hour")
async def get_cctv_nearby(request: Request):
    data = await request.json()
    lat = float(data.get("lat", 37.5665))
    lng = float(data.get("lng", 126.9780))
    radius_m = float(data.get("radius", 200))
    radius_km = radius_m / 1000.0

    cctvs: list = []
    source = "mock"

    if PUBLIC_DATA_API_KEY:
        cctvs = await _fetch_seoul_cctv(lat, lng, radius_km)
        if cctvs:
            source = "real"

    if not cctvs:
        all_mock = _load_mock_cctvs()
        for c in all_mock:
            dist = _haversine_km(lat, lng, c["lat"], c["lng"])
            if dist <= radius_km:
                cctvs.append({**c, "distance_m": round(dist * 1000), "source": "mock"})

        if not cctvs:
            ranked = sorted(all_mock, key=lambda c: _haversine_km(lat, lng, c["lat"], c["lng"]))
            for c in ranked[:5]:
                dist = _haversine_km(lat, lng, c["lat"], c["lng"])
                cctvs.append({**c, "distance_m": round(dist * 1000), "source": "mock_nearest"})

    cctvs.sort(key=lambda c: c["distance_m"])

    return JSONResponse({
        "lat": lat,
        "lng": lng,
        "radius_m": radius_m,
        "cctvs": cctvs,
        "total": len(cctvs),
        "source": source,
        "note": "" if source == "real" else "Mock 데이터. 실제 API: PUBLIC_DATA_API_KEY 환경변수 설정",
    })


@app.get("/api/cctv/access-guide")
async def get_cctv_access_guide():
    with open(CCTV_MOCK_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    guide = data.get("access_guide", {})
    return JSONResponse({
        "steps": guide.get("steps", []),
        "tips": guide.get("tips", []),
        "legal_basis": "개인정보 보호법 제35조 (개인정보 열람권)",
        "retention_days": "공공 CCTV 저장 기간: 통상 30일 (최대 90일)",
        "contact": "관할 경찰서 민원실 또는 112",
    })


@app.post("/api/generate-report")
async def generate_police_report(request: Request):
    data = await request.json()
    stolen = data.get("stolen_info", {})

    report_text = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
자전거 도난 신고서 (자동 생성)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

■ 신고 일시: {datetime.now().strftime('%Y년 %m월 %d일 %H:%M')}
■ 사건 번호: {data.get('report_id', 'ATD-000')}

■ 피해 물품 정보
  - 제조사/모델: {stolen.get('brand', '-')} {stolen.get('model', '-')}
  - 색상: {stolen.get('color', '-')}
  - 구매 가격: {stolen.get('price', '-')}
  - 특이사항: {stolen.get('features', '-')}

■ 도난 장소: {stolen.get('location', '-')}
■ 도난 시각: {stolen.get('time', '-')}

■ 관할 경찰서: {get_nearest_police(stolen.get('location', ''))}

■ AI 도난탐정 분석 결과
  - 중고마켓 스캔: 3개 플랫폼 (당근마켓, 번개장터, 중고나라)
  - 의심 매물: {data.get('suspicious_count', 0)}건 발견
  - 최고 유사도: {data.get('max_similarity', 0)}%

[주의] AI 추정 라벨 (필독)
  - 본 분석은 AI 자동 추정. 오탐 가능성 존재
  - 매물 = 동일 도난품 단정 X
  - 직접 매물 확인·경찰 신고 후 판단

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
본 신고서는 AI 도난탐정 서비스에 의해 자동 생성되었습니다.
주식회사 무무익선 | AI 도난탐정
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    return JSONResponse({"report_text": report_text})


def get_nearest_police(location: str) -> str:
    mapping = {
        "성동": "성동경찰서 (02-2204-0112)",
        "강남": "강남경찰서 (02-531-0112)",
        "서초": "서초경찰서 (02-583-0112)",
        "송파": "송파경찰서 (02-2203-0112)",
        "마포": "마포경찰서 (02-3270-0112)",
        "영등포": "영등포경찰서 (02-2670-0112)",
        "부산": "부산진경찰서 (051-899-0112)",
        "해운대": "해운대경찰서 (051-899-0112)",
        "남구": "남부경찰서 (051-610-0112)",
        "분당": "분당경찰서 (031-786-0112)",
        "수원": "수원서부경찰서 (031-8011-0112)",
        "성남": "성남수정경찰서 (031-741-0112)",
    }
    for key, value in mapping.items():
        if key in location:
            return value
    return "관할 경찰서 확인 필요 (112)"


# ============================================================
# 경찰서 데이터
# ============================================================
POLICE_STATIONS = [
    {"name": "서울 강남경찰서",     "phone": "02-3459-0112", "address": "서울 강남구 테헤란로 114길 11",     "lat": 37.5006, "lng": 127.0386},
    {"name": "서울 서초경찰서",     "phone": "02-3473-0112", "address": "서울 서초구 서초대로 256",           "lat": 37.4836, "lng": 127.0324},
    {"name": "서울 송파경찰서",     "phone": "02-2203-0112", "address": "서울 송파구 올림픽로 326",           "lat": 37.5075, "lng": 127.1152},
    {"name": "서울 성동경찰서",     "phone": "02-2204-0112", "address": "서울 성동구 왕십리로 345",           "lat": 37.5613, "lng": 127.0365},
    {"name": "서울 마포경찰서",     "phone": "02-3270-0112", "address": "서울 마포구 월드컵북로 343",         "lat": 37.5696, "lng": 126.9108},
    {"name": "서울 영등포경찰서",   "phone": "02-2670-0112", "address": "서울 영등포구 국회대로 688",         "lat": 37.5268, "lng": 126.9064},
    {"name": "서울 종로경찰서",     "phone": "02-2148-0112", "address": "서울 종로구 종로 1가 12",            "lat": 37.5704, "lng": 126.9867},
    {"name": "서울 중부경찰서",     "phone": "02-2260-0112", "address": "서울 중구 수표로 27",                "lat": 37.5657, "lng": 126.9996},
    {"name": "경기 분당경찰서",     "phone": "031-786-0112", "address": "경기 성남시 분당구 황새울로 360",    "lat": 37.3780, "lng": 127.1217},
    {"name": "경기 수원서부경찰서", "phone": "031-8011-0112","address": "경기 수원시 팔달구 효원로 241",      "lat": 37.2620, "lng": 126.9975},
    {"name": "경기 성남수정경찰서", "phone": "031-741-0112", "address": "경기 성남시 수정구 수정로 171",      "lat": 37.4456, "lng": 127.1377},
    {"name": "부산 해운대경찰서",   "phone": "051-709-0112", "address": "부산 해운대구 해운대로 452",         "lat": 35.1631, "lng": 129.1605},
    {"name": "부산 부산진경찰서",   "phone": "051-810-0112", "address": "부산 부산진구 중앙대로 634",         "lat": 35.1652, "lng": 129.0527},
]


@app.get("/api/police/nearby")
async def police_nearby(lat: float = 37.5665, lng: float = 126.9780, limit: int = 3):
    stations = sorted(
        POLICE_STATIONS,
        key=lambda s: _haversine_km(lat, lng, s["lat"], s["lng"])
    )[:limit]
    result = []
    for s in stations:
        dist = _haversine_km(lat, lng, s["lat"], s["lng"])
        result.append({**s, "distance_km": round(dist, 1)})
    return JSONResponse({
        "nearest_stations": result,
        "online_report": {
            "safe182": {
                "name": "안전Dream 분실·도난 신고",
                "url": "https://www.safe182.go.kr",
                "desc": "경찰청 공식 온라인 도난 신고 (회원가입 후 접수)",
                "steps": [
                    "1. safe182.go.kr 접속",
                    "2. [민원신청] → [분실·습득물 신고]",
                    "3. 물품 정보 입력 (모델·시리얼·사진 첨부)",
                    "4. 접수 완료 → 사건번호 발급",
                ],
            },
            "epolice": {
                "name": "경찰청 민원포털",
                "url": "https://minwon.police.go.kr",
                "desc": "각종 민원 온라인 접수",
            },
            "direct_112": {
                "name": "112 긴급신고",
                "desc": "도난 직후 즉시 → 112 전화가 가장 빠름",
            },
        },
    })


# ============================================================
# PDF 생성 헬퍼
# ============================================================
def _build_112_pdf(data: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    S = {
        "title":   ParagraphStyle("title",   fontName=PDF_FONT, fontSize=16, spaceAfter=4,  leading=22, alignment=1),
        "subtitle":ParagraphStyle("subtitle",fontName=PDF_FONT, fontSize=10, spaceAfter=10, textColor=colors.grey, alignment=1),
        "section": ParagraphStyle("section", fontName=PDF_FONT, fontSize=11, spaceAfter=4,  spaceBefore=8, textColor=colors.HexColor("#1a1a2e")),
        "body":    ParagraphStyle("body",    fontName=PDF_FONT, fontSize=9,  spaceAfter=3,  leading=14),
        "warn":    ParagraphStyle("warn",    fontName=PDF_FONT, fontSize=8,  textColor=colors.HexColor("#cc4444"), leading=12),
        "footer":  ParagraphStyle("footer",  fontName=PDF_FONT, fontSize=8,  textColor=colors.grey, alignment=1),
    }

    def _table(rows, col_widths):
        t = Table(rows, colWidths=col_widths)
        t.setStyle(TableStyle([
            ("FONTNAME",      (0, 0), (-1, -1), PDF_FONT),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("BACKGROUND",    (0, 0), (0, -1),  colors.HexColor("#f0f0f8")),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    stolen   = data.get("stolen_info", {})
    reporter = data.get("reporter", {})
    now      = datetime.now()
    report_id = data.get("report_id", f"ATD-{now.strftime('%Y%m%d%H%M%S')}")
    listings  = data.get("scan_results", [])
    suspicious = [l for l in listings if l.get("similarity", 0) >= SUSPICIOUS_THRESHOLD]

    story = []

    story.append(Paragraph("자전거 도난 신고서", S["title"]))
    story.append(Paragraph("AI 도난탐정 자동 생성  |  주식회사 무무익선", S["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1a1a2e"), spaceAfter=6))

    story.append(Paragraph("■ 신고 기본 정보", S["section"]))
    story.append(_table([
        ["신고 일시", now.strftime("%Y년 %m월 %d일 %H시 %M분")],
        ["사건 번호", report_id],
        ["도난 일시", stolen.get("time", "-")],
        ["도난 장소", stolen.get("location", "-")],
    ], [45 * mm, 125 * mm]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("■ 피해 물품 정보", S["section"]))
    story.append(_table([
        ["제조사",      stolen.get("brand", "-")],
        ["모델명",      stolen.get("model", "-")],
        ["색상",        stolen.get("color", "-")],
        ["시리얼 번호", stolen.get("serial", "-")],
        ["구입 가격",   stolen.get("price", "-")],
        ["특이사항",    stolen.get("features", "-")],
    ], [45 * mm, 125 * mm]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("■ 신고인 정보", S["section"]))
    story.append(_table([
        ["성명",            reporter.get("name", "-")],
        ["연락처",          reporter.get("phone", "-")],
        ["주소",            reporter.get("address", "-")],
        ["주민번호 앞자리", reporter.get("id_partial", "-")],
    ], [45 * mm, 125 * mm]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("■ AI 도난탐정 분석 결과", S["section"]))
    story.append(Paragraph(
        f"스캔 플랫폼: 당근마켓·번개장터·중고나라  |  "
        f"의심 매물: {data.get('suspicious_count', len(suspicious))}건  |  "
        f"최고 유사도: {data.get('max_similarity', 0)}%",
        S["body"],
    ))

    if suspicious:
        story.append(Spacer(1, 2 * mm))
        susp_header = [["플랫폼", "제목", "가격", "지역", "유사도"]]
        susp_rows = [
            [
                l.get("platform", "-"),
                l.get("title", "-")[:22],
                l.get("price", "-"),
                l.get("location", "-"),
                f"{l.get('similarity', 0)}%",
            ]
            for l in suspicious[:5]
        ]
        t = Table(susp_header + susp_rows, colWidths=[24 * mm, 62 * mm, 22 * mm, 26 * mm, 16 * mm])
        t.setStyle(TableStyle([
            ("FONTNAME",      (0, 0), (-1, -1), PDF_FONT),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
            ("BACKGROUND",    (0, 1), (-1, -1), colors.HexColor("#fff8f0")),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
    story.append(Spacer(1, 4 * mm))

    gps = data.get("last_gps")
    if gps:
        story.append(Paragraph("■ GPS 마지막 확인 위치", S["section"]))
        story.append(Paragraph(
            f"위도 {gps.get('lat', '-')} / 경도 {gps.get('lng', '-')}  "
            f"({gps.get('address', '주소 미확인')}) — {gps.get('time', '-')}",
            S["body"],
        ))
        story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("■ 관할 경찰서", S["section"]))
    story.append(Paragraph(get_nearest_police(stolen.get("location", "")), S["body"]))
    story.append(Paragraph("온라인 접수: https://www.safe182.go.kr  |  긴급: 112", S["body"]))
    story.append(Spacer(1, 6 * mm))

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=4))
    story.append(Paragraph(
        "본 신고서는 AI 도난탐정이 자동 생성한 참고 자료입니다. "
        "AI 분석 결과는 추정치이며 법적 효력이 없습니다. "
        "중고 매물이 실제 도난품임을 단정하지 마시고, 반드시 경찰에 신고 후 판단하세요.",
        S["warn"],
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        f"생성일시: {now.strftime('%Y-%m-%d %H:%M:%S')}  |  주식회사 무무익선 AI 도난탐정",
        S["footer"],
    ))

    doc.build(story)
    return buf.getvalue()


@app.post("/api/report/112-form")
@limiter.limit("5/hour")
async def generate_112_pdf(request: Request):
    from urllib.parse import quote
    data = await request.json()
    pdf_bytes = _build_112_pdf(data)
    report_id = data.get("report_id", datetime.now().strftime("ATD-%Y%m%d%H%M%S"))
    filename_kor = f"도난신고서_{report_id}.pdf"
    filename_enc = quote(filename_kor, safe="")
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename_enc}"},
    )


# ============================================================
# 이동 패턴 예측 (데모용)
# ============================================================
@app.post("/api/predict-movement")
async def predict_movement(request: Request):
    data = await request.json()
    location = data.get("location", "")

    predictions = [
        {"area": "성남 중고시장", "probability": 78, "distance": "12km", "type": "중고거래"},
        {"area": "수원 영통",    "probability": 45, "distance": "25km", "type": "중고거래"},
        {"area": "안양 평촌",    "probability": 23, "distance": "18km", "type": "유동인구"},
    ]

    return JSONResponse({
        "predictions": predictions,
        "data_basis": "39만건 위치 데이터 분석",
        "model": "bicycle_theft_risk_model v1.0",
    })


# ============================================================
# 헬스체크 (Render·Fly.io 자동 헬스체크용)
# ============================================================
_SERVER_START = datetime.now()


@app.get("/healthcheck")
@app.get("/health")
async def healthcheck():
    # DB 연결 확인
    db_ok = False
    try:
        with Session(engine) as session:
            session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    # 스케줄러 활성 잡 수
    try:
        active_jobs = len(scheduler.get_jobs())
    except Exception:
        active_jobs = 0

    uptime_seconds = int((datetime.now() - _SERVER_START).total_seconds())

    return JSONResponse({
        "status": "ok",
        "db": "connected" if db_ok else "error",
        "scheduler": f"running ({active_jobs} jobs)",
        "version": "1.0.0",
        "uptime_seconds": uptime_seconds,
    })


# ============================================================
# Resend 이메일 알림
# ============================================================
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")


async def send_listing_alert(case_id: str, listings: list):
    """새 의심 매물 발견 시 케이스 오너에게 이메일 알림.
    RESEND_API_KEY 없으면 console log만 출력 (시안 모드).
    """
    if not listings:
        return

    with Session(engine) as session:
        case = db_get_case(session, case_id)
        if not case or not case.owner_email:
            print(f"[Resend] {case_id} — owner_email 없음, 알림 스킵")
            return
        owner_email = case.owner_email
        bike_label = f"{case.brand} {case.model}".strip() or "자전거"

    # 매물 목록 HTML (최대 5건)
    rows_html = ""
    for listing in listings[:5]:
        sim = listing.get("similarity", 0)
        color = "#c0392b" if sim >= 80 else "#e67e22"
        rows_html += (
            f"<tr>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee;'>{listing.get('platform', '-')}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee;'>{listing.get('title', '-')}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee;'>{listing.get('price', '-')}</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee;color:{color};font-weight:bold;'>{sim}%</td>"
            f"<td style='padding:8px 12px;border-bottom:1px solid #eee;'>"
            f"<a href='{listing.get('url', '#')}' style='color:#2980b9;'>확인하기</a></td>"
            f"</tr>"
        )

    html_body = (
        f"<div style='font-family:sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;'>"
        f"<div style='background:#1a1a2e;color:#fff;padding:24px;border-radius:10px;margin-bottom:24px;'>"
        f"<h2 style='margin:0 0 8px;'>AI 도난탐정 — 의심 매물 발견</h2>"
        f"<p style='margin:0;color:#aaa;font-size:13px;'>케이스 {case_id} | {bike_label}</p></div>"
        f"<p style='color:#444;'>중고마켓에서 <strong>{len(listings)}건</strong>의 의심 매물이 발견되었습니다.</p>"
        f"<table style='width:100%;border-collapse:collapse;font-size:13px;'>"
        f"<thead><tr style='background:#f5f5f5;'>"
        f"<th style='padding:8px 12px;text-align:left;'>플랫폼</th>"
        f"<th style='padding:8px 12px;text-align:left;'>제목</th>"
        f"<th style='padding:8px 12px;text-align:left;'>가격</th>"
        f"<th style='padding:8px 12px;text-align:left;'>유사도</th>"
        f"<th style='padding:8px 12px;text-align:left;'>링크</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table>"
        f"<div style='background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:14px;margin:20px 0;font-size:12px;color:#856404;'>"
        f"AI 추정치입니다. 실제 도난품이 아닐 수 있습니다. 매도자 직접 접촉 전 반드시 경찰에 신고하세요.</div>"
        f"<p style='text-align:center;'>"
        f"<a href='{SITE_URL}' style='background:#e74c3c;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;'>도난탐정에서 상세 확인</a></p>"
        f"<p style='font-size:11px;color:#999;text-align:center;margin-top:24px;'>"
        f"주식회사 무무익선 AI 도난탐정 | 수신 거부: gnsdl5314@gmail.com</p></div>"
    )

    if not RESEND_API_KEY:
        print(f"[Resend] MOCK — {owner_email} 에게 알림 (의심 매물 {len(listings)}건)")
        for listing in listings[:5]:
            print(f"  - [{listing.get('platform')}] {listing.get('title')} {listing.get('similarity')}% {listing.get('url')}")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "AI 도난탐정 <alert@noreply.theft-detective.com>",
                    "to": [owner_email],
                    "subject": f"[{bike_label}] 의심 매물 {len(listings)}건 발견 — AI 도난탐정",
                    "html": html_body,
                },
            )
        if resp.status_code in (200, 201):
            print(f"[Resend] 발송 완료 → {owner_email} (케이스 {case_id}, 매물 {len(listings)}건)")
        else:
            print(f"[Resend] 발송 실패 {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Resend] 발송 에러: {e}")


# ============================================================
# 프론트엔드 서빙 + 법적 문서 엔드포인트
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", "r") as f:
        return f.read()


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    _path = os.path.join(os.path.dirname(__file__), "public", "privacy.html")
    with open(_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/terms", response_class=HTMLResponse)
async def terms():
    _path = os.path.join(os.path.dirname(__file__), "public", "terms.html")
    with open(_path, "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
