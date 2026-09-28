"""NSE trading-session clock (Asia/Kolkata - no DST, unlike the US session).

Java equivalent: java.time.ZonedDateTime.of(..., ZoneId.of("Asia/Kolkata")) -
Python's zoneinfo module is the same JSR-310 design, and tzdata is installed
so Windows gets the IANA database too.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

NSE_OPEN = time(9, 15)          # first 5m bar starts at 09:15
NSE_CLOSE = time(15, 30)        # last 5m bar starts at 15:25, closes 15:30
GRACE_END = time(15, 35)        # keep polling a few minutes past close so the
                                # final 15:25 bar gets evaluated


def now_ist() -> datetime:
    """Current time as a tz-aware Asia/Kolkata datetime."""
    return datetime.now(IST)


def in_session(dt: datetime, holidays: frozenset[date] = frozenset()) -> bool:
    """True when the NSE cash/futures session is open (or in close-grace).

    Naive datetimes are assumed to already be IST (friendly for tests).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    local = dt.time()
    return is_trading_day(dt.date(), holidays) and NSE_OPEN <= local <= GRACE_END


def is_trading_day(day: date, holidays: frozenset[date] = frozenset()) -> bool:
    """Weekday and not a configured NSE holiday."""
    return day.weekday() < 5 and day not in holidays
