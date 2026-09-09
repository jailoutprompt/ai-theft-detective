#!/usr/bin/env python3
"""
CCTV 조회용 SQLite 생성

기본 입력은 저장소에 포함된 data_src/cctv_kr.csv.gz (공공데이터 가공본, UTF-8).
원본 공공데이터 CSV(CP949)를 직접 넣어도 동작한다.

사용:
    python3 scripts/build_cctv_db.py                 # 저장소 기본 데이터
    python3 scripts/build_cctv_db.py <csv|csv.gz>    # 임의 파일
"""
import csv
import gzip
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "cctv.db")
DEFAULT_SRC = os.path.join(BASE_DIR, "data_src", "cctv_kr.csv.gz")

GRID = 0.01  # 0.01도 ≈ 1.1km

# 가공본(영문 헤더) / 공공데이터 원본(한글 헤더) 양쪽 지원
FIELD_MAP = {
    "agency": ("agency", "관리기관명"),
    "addr": ("addr", "소재지도로명주소", "소재지지번주소"),
    "purpose": ("purpose", "설치목적구분"),
    "cams": ("cams", "카메라대수"),
    "keep": ("keep", "보관일수"),
    "tel": ("tel", "관리기관전화번호"),
    "lat": ("lat", "WGS84위도"),
    "lng": ("lng", "WGS84경도"),
}


def pick(row: dict, key: str) -> str:
    for name in FIELD_MAP[key]:
        v = row.get(name)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def open_csv(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    try:
        f = open(path, encoding="utf-8")
        f.readline()
        f.seek(0)
        return f
    except UnicodeDecodeError:
        return open(path, encoding="cp949", errors="ignore")


def build(src: str = DEFAULT_SRC):
    if not os.path.exists(src):
        raise SystemExit(f"데이터 파일 없음: {src}")

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE cctv (
            id        INTEGER PRIMARY KEY,
            agency    TEXT,
            addr      TEXT,
            purpose   TEXT,
            cams      INTEGER,
            keep_days INTEGER,
            tel       TEXT,
            lat       REAL,
            lng       REAL,
            gx        INTEGER,
            gy        INTEGER
        )
    """)

    rows, total, kept = [], 0, 0
    with open_csv(src) as f:
        for row in csv.DictReader(f):
            total += 1
            try:
                lat = float(pick(row, "lat") or 0)
                lng = float(pick(row, "lng") or 0)
            except ValueError:
                continue
            if not (33.0 < lat < 39.5 and 124.0 < lng < 132.0):
                continue

            def as_int(key, default=0):
                try:
                    return int(float(pick(row, key) or default))
                except ValueError:
                    return default

            rows.append((
                pick(row, "agency"), pick(row, "addr"), pick(row, "purpose"),
                as_int("cams"), as_int("keep", 30), pick(row, "tel"),
                lat, lng, int(lat / GRID), int(lng / GRID),
            ))
            kept += 1
            if len(rows) >= 20000:
                cur.executemany(
                    "INSERT INTO cctv (agency,addr,purpose,cams,keep_days,tel,lat,lng,gx,gy)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
                rows.clear()

    if rows:
        cur.executemany(
            "INSERT INTO cctv (agency,addr,purpose,cams,keep_days,tel,lat,lng,gx,gy)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

    cur.execute("CREATE INDEX idx_grid ON cctv(gx, gy)")
    cur.execute("CREATE INDEX idx_purpose ON cctv(purpose)")
    con.commit()
    cur.execute("SELECT COUNT(*) FROM cctv")
    n = cur.fetchone()[0]
    con.close()

    print(f"[CCTV DB] {os.path.basename(src)} -> {n:,}건 적재 "
          f"(입력 {total:,} / 제외 {total - kept:,}), "
          f"{os.path.getsize(DB_PATH)/1024/1024:.1f} MB")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC)
