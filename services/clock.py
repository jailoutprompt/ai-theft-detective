"""
앱 기준 시각 = 한국 시간(KST, UTC+9)

Render 서버는 UTC로 돈다. 사용자가 입력하는 도난·신고 시각은 한국 시간이라
datetime.now()(UTC)와 9시간 어긋나 경과일·열람 잔여일·기록 시각이 틀어졌다.
프로세스 시간대(TZ)를 바꾸면 APScheduler(tzlocal)가 기동에 실패하므로,
앱 코드에서 쓰는 datetime.now() 만 KST naive 값을 돌려주도록 감싼다.

사용: from services.clock import datetime   (기존 datetime 과 동일하게 사용)
"""
from datetime import datetime as _dt
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")


class datetime(_dt):  # noqa: N801 — 표준 datetime 자리를 그대로 대체
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return super().now(tz)
        return super().now(KST).replace(tzinfo=None)
