"""
모니터링 우선 지역 가중치 빌드 (P4: 이동패턴 데이터 근거화)

기존 movement_service 는 중고거래 거점 14곳과 가중치를 손으로 정했다.
이 스크립트는 후보 지역과 가중치를 전부 공개 데이터에서 산출한다.

  후보 지역 : 경찰청 범죄 통계의 시군구 단위 지역 (외국 제외)
  가중치    : 같은 통계의 2024년 '절도범죄' 발생 건수
  지역 좌표 : 공공데이터포털 「전국 CCTV 표준데이터」(data/cctv.db) 중
              그 시군구 주소를 가진 CCTV 좌표의 중앙값
              (CCTV 는 생활권에 몰려 있어 행정구역 도형 중심보다 생활 중심에 가깝다)

입력
  data_src/police_crime_region_2024.csv  경찰청_범죄 발생 지역별 통계_20241231 (CP949 원본)
  data/cctv.db                           scripts/build_cctv_db.py 결과
출력
  data_src/region_weights.json           movement_service 가 읽는 결과물 (커밋 대상)

실행: python3 scripts/build_region_weights.py
"""
import csv
import io
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data_src", "police_crime_region_2024.csv")
DB = os.path.join(ROOT, "data", "cctv.db")
OUT = os.path.join(ROOT, "data_src", "region_weights.json")

# CCTV 주소의 시도 표기 → 경찰청 통계의 시도 표기
SIDO = {
    "서울특별시": "서울", "서울": "서울",
    "부산광역시": "부산", "부산": "부산",
    "대구광역시": "대구", "대구": "대구",
    "인천광역시": "인천", "인천": "인천",
    "광주광역시": "광주", "광주": "광주",
    "대전광역시": "대전", "대전": "대전",
    "울산광역시": "울산", "울산": "울산",
    "세종특별자치시": "세종", "세종": "세종",
    "경기도": "경기도", "경기": "경기도",
    "강원특별자치도": "강원도", "강원도": "강원도", "강원": "강원도",
    "충청북도": "충북", "충북": "충북",
    "충청남도": "충남", "충남": "충남",
    "전북특별자치도": "전북", "전라북도": "전북", "전북": "전북",
    "전라남도": "전남", "전남": "전남",
    "경상북도": "경북", "경북": "경북",
    "경상남도": "경남", "경남": "경남",
    "제주특별자치도": "제주", "제주": "제주",
    # 2026 행정통합: 광주 5개 구와 전남 시군이 같은 시도명을 쓴다
    "전남광주통합특별시": "광주전남",
}
GWANGJU_GU = ("동구", "서구", "남구", "북구", "광산구")


def load_theft():
    raw = open(SRC, "rb").read()
    text = raw.decode("cp949")
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    row = next(r for r in rows if len(r) > 1 and r[1].strip() == "절도범죄")
    out = {}
    for label, v in zip(head[2:], row[2:]):
        label = label.strip()
        if label.startswith("외국"):
            continue
        out[label] = int(v or 0)
    return out


def split_label(label):
    """'서울 강남구' → ('서울','강남구'), '세종시' → ('세종','')"""
    if label == "세종시":
        return "세종", ""
    sido, _, sgg = label.partition(" ")
    return sido, sgg


def main():
    theft = load_theft()
    by_sido = {}
    for label in theft:
        sido, sgg = split_label(label)
        by_sido.setdefault(sido, []).append((sgg, label))
    # 긴 이름부터 비교 (예: '부산진구'를 '부산'보다 먼저)
    for k in by_sido:
        by_sido[k].sort(key=lambda x: -len(x[0]))

    def resolve(sido_word, rest):
        sido = SIDO.get(sido_word)
        if not sido:
            return None
        if sido == "세종":
            return "세종시"
        if sido == "광주전남":
            sido = "광주" if rest.startswith(GWANGJU_GU) else "전남"
        for sgg, lb in by_sido.get(sido, []):
            if sgg and rest.startswith(sgg):
                return lb
        return None

    sido_keys = sorted(SIDO, key=len, reverse=True)

    def resolve_agency(agency):
        """주소에 시도가 빠진 행은 관리기관명(예: '부산광역시 남구청')으로 판정"""
        a = (agency or "").replace(" ", "")
        for k in sido_keys:
            if a.startswith(k):
                return resolve(k, a[len(k):])
        return None

    pts = {label: [] for label in theft}
    con = sqlite3.connect(DB)
    n_all = n_hit = n_agency = 0
    for addr, agency, lat, lng in con.execute("SELECT addr, agency, lat, lng FROM cctv"):
        n_all += 1
        if lat is None or lng is None:
            continue
        if not (33.0 <= lat <= 39.0 and 124.0 <= lng <= 132.0):
            continue
        tok = (addr or "").split()
        label = resolve(tok[0], tok[1] if len(tok) > 1 else "") if tok else None
        if not label:
            label = resolve_agency(agency)
            if not label:
                continue
            n_agency += 1
        pts[label].append((lat, lng))
        n_hit += 1

    regions = []
    missing = []
    for label, cnt in theft.items():
        p = pts.get(label) or []
        if not p:
            missing.append(label)
            continue
        sido, sgg = split_label(label)
        name = label.replace("경기도 ", "경기 ").replace("강원도 ", "강원 ")
        regions.append({
            "region": name,
            "sido": sido,
            "sigungu": sgg or "세종시",
            "lat": round(statistics.median(x[0] for x in p), 5),
            "lng": round(statistics.median(x[1] for x in p), 5),
            "theft_2024": cnt,
            "cctv_points": len(p),
        })
    regions.sort(key=lambda r: -r["theft_2024"])

    meta = {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "weight": "2024년 시군구별 절도범죄 발생 건수 (경찰청)",
        "weight_source": "경찰청_범죄 발생 지역별 통계_20241231 (공공데이터포털 3074462, 이용허락범위 제한 없음)",
        "weight_source_url": "https://www.data.go.kr/data/3074462/fileData.do",
        "weight_note": "자전거 절도만 분리한 시군구 단위 공개 통계를 찾지 못해 절도범죄 전체 발생 건수를 대리 지표로 사용",
        "coord_source": "공공데이터포털 「전국 CCTV 표준데이터」 좌표의 시군구별 중앙값",
        "regions_in_source": len(theft),
        "regions_used": len(regions),
        "regions_missing": missing,
        "theft_total_used": sum(r["theft_2024"] for r in regions),
        "cctv_rows": n_all,
        "cctv_rows_matched": n_hit,
        "cctv_rows_matched_by_agency": n_agency,
    }
    json.dump({"meta": meta, "regions": regions}, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    print("상위 5:", [(r["region"], r["theft_2024"], r["cctv_points"]) for r in regions[:5]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
