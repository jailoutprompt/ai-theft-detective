"""
신고 준비서용 정적 지도 이미지

도난 지점(빨간 점) · CCTV 반경(원) · 주변 CCTV(파란 점)를 PNG 한 장으로 그린다.
배경은 OpenStreetMap 타일(키 불필요). 타일을 못 받으면 배경 없이 점·반경만 그린
지도를 돌려주고 note 에 그 사실을 적는다 (조용히 실패하지 않는다).

OSM 타일 이용 정책: 식별 가능한 User-Agent, 저빈도 요청, 출처 표기.
"""
import io
import math
from collections import OrderedDict

import httpx
from PIL import Image, ImageDraw, ImageFont

TILE = 256
UA = "AI-Doonan-Tamjeong/1.0 (+https://ai-theft-detective.onrender.com)"
_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()
_CACHE_MAX = 256


def _px(lat, lng, z):
    n = TILE * (2 ** z)
    x = (lng + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def _m_per_px(lat, z):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def _tile(client, z, x, y):
    key = (z, x, y)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    r = client.get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png")
    r.raise_for_status()
    _CACHE[key] = r.content
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return r.content


def render(lat: float, lng: float, radius_m: float, cctvs: list,
           width: int = 760, height: int = 560, font_path: str = None) -> dict:
    """반환: {"png": bytes, "basemap": bool, "note": str}"""
    # 반경 원이 이미지 짧은 변의 약 60%가 되도록 줌 선택
    z = 17
    while z > 12 and (2 * radius_m / _m_per_px(lat, z)) > min(width, height) * 0.6:
        z -= 1
    cx, cy = _px(lat, lng, z)
    left, top = cx - width / 2, cy - height / 2

    img = Image.new("RGB", (width, height), (238, 240, 244))
    basemap, note = False, ""
    try:
        with httpx.Client(timeout=4.0, headers={"User-Agent": UA}) as client:
            for tx in range(int(left // TILE), int((left + width) // TILE) + 1):
                for ty in range(int(top // TILE), int((top + height) // TILE) + 1):
                    t = Image.open(io.BytesIO(_tile(client, z, tx, ty))).convert("RGB")
                    img.paste(t, (int(tx * TILE - left), int(ty * TILE - top)))
        basemap = True
        # 배경을 살짝 옅게 해 점이 잘 보이게
        img = Image.blend(img, Image.new("RGB", img.size, (255, 255, 255)), 0.25)
    except Exception as e:  # noqa: BLE001 — 실패 사실을 note 로 남긴다
        note = f"지도 배경 불러오기 실패({type(e).__name__}) — 위치 점만 표시"

    d = ImageDraw.Draw(img, "RGBA")
    rpx = radius_m / _m_per_px(lat, z)
    W2, H2 = width / 2, height / 2
    d.ellipse([W2 - rpx, H2 - rpx, W2 + rpx, H2 + rpx], fill=(79, 70, 229, 28),
              outline=(79, 70, 229, 200), width=3)
    for c in cctvs:
        x, y = _px(c["lat"], c["lng"], z)
        x, y = x - left, y - top
        if -10 < x < width + 10 and -10 < y < height + 10:
            col = (37, 99, 235, 255) if c.get("priority") else (96, 165, 250, 255)  # 방범용 진한 파랑, 그 외 연한 파랑
            d.ellipse([x - 7, y - 7, x + 7, y + 7], fill=col, outline=(255, 255, 255, 255), width=2)
    d.ellipse([W2 - 13, H2 - 13, W2 + 13, H2 + 13], fill=(220, 38, 38, 255),
              outline=(255, 255, 255, 255), width=4)

    if basemap:
        try:
            f = ImageFont.truetype(font_path, 15) if font_path else ImageFont.load_default()
        except Exception:  # noqa: BLE001
            f = ImageFont.load_default()
        txt = "© OpenStreetMap contributors"
        tw = d.textlength(txt, font=f)
        d.rectangle([width - tw - 14, height - 24, width, height], fill=(255, 255, 255, 210))
        d.text((width - tw - 7, height - 21), txt, font=f, fill=(60, 60, 60, 255))

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return {"png": out.getvalue(), "basemap": basemap, "note": note, "zoom": z}
