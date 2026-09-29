"""Exchange trading clocks - NSE & MCX (Asia/Kolkata - no DST).

Each exchange has (open, close, grace_end): the grace window lets a run right
after the close still evaluate the final completed 5m bar.

Java equivalent: java.time.ZonedDateTime + a small enum of session windows.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# exchange -> (open, close, grace_end); both trade Mon-Fri (see is_trading_day)
SESSIONS: dict[str, tuple[time, time, time]] = {
    "NSE": (time(9, 15), time(15, 30), time(15, 35)),   # 1st bar 09:15, last 15:25
    "MCX": (time(9, 0), time(23, 30), time(23, 35)),    # energy: 9:00-23:30 IST
}
DEFAULT_EXCHANGE = "NSE"

NSE_OPEN = SESSIONS["NSE"][0]
NSE_CLOSE = SESSIONS["NSE"][1]
GRACE_END = SESSIONS["NSE"][2]


def session_bounds(exchange: str) -> tuple[time, time, time]:
    return SESSIONS.get(exchange.upper(), SESSIONS[DEFAULT_EXCHANGE])


def now_ist() -> datetime:
    """Current time as a tz-aware Asia/Kolkata datetime."""
    return datetime.now(IST)


def in_session(dt: datetime, holidays: frozenset[date] = frozenset(),
               exchange: str = DEFAULT_EXCHANGE) -> bool:
    """True when the exchange's session is open (or in its close-grace).

    Naive datetimes are assumed to already be IST (friendly for tests).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    open_t, _close, grace_end = session_bounds(exchange)
    local = dt.time()
    return is_trading_day(dt.date(), holidays) and open_t <= local <= grace_end


def is_trading_day(day: date, holidays: frozenset[date] = frozenset()) -> bool:
    """Weekday and not a configured holiday (NSE & MCX both trade Mon-Fri)."""
    return day.weekday() < 5 and day not in holidays
