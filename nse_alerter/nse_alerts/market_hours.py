"""Exchange trading clocks - NSE & MCX (Asia/Kolkata - no DST).

Each exchange has (open, close, grace_end): the grace window lets a run right
after the close still evaluate the final completed 5m bar.

Java equivalent: java.time.ZonedDateTime + a small enum of session windows.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# exchange -> (open, close, grace_end); all trade Mon-Fri (see is_trading_day)
SESSIONS: dict[str, tuple[time, time, time]] = {
    "NSE": (time(9, 15), time(15, 30), time(15, 35)),   # 1st bar 09:15, last 15:25
    "BSE": (time(9, 15), time(15, 30), time(15, 35)),   # Sensex: same clock as NSE
    "MCX": (time(9, 0), time(23, 30), time(23, 35)),    # energy: 9:00-23:30 IST
}
DEFAULT_EXCHANGE = "NSE"

NSE_OPEN = SESSIONS["NSE"][0]
NSE_CLOSE = SESSIONS["NSE"][1]
GRACE_END = SESSIONS["NSE"][2]


def session_bounds(exchange: str) -> tuple[time, time, time]:
    return SESSIONS.get(exchange.upper(), SESSIONS[DEFAULT_EXCHANGE])


def parse_time_window(raw: str) -> tuple[time, time, time]:
    """'17:00-22:00' -> (open, close, grace_end = close + 5 min).

    Same 5-minute close-grace convention as SESSIONS, so the run right after
    the window still picks up the final completed 5m bar. Anything that is
    not HH:MM-HH:MM with open < close raises ValueError.
    """
    start_s, sep, end_s = raw.partition("-")
    if not sep:
        raise ValueError(f"session window {raw!r} must look like HH:MM-HH:MM")
    try:
        open_t = time.fromisoformat(start_s.strip())
        close_t = time.fromisoformat(end_s.strip())
    except ValueError:
        raise ValueError(
            f"session window {raw!r} must look like HH:MM-HH:MM") from None
    if open_t >= close_t:
        raise ValueError(f"session window {raw!r} must start before it ends")
    grace = (datetime.combine(date(2000, 1, 1), close_t)
             + timedelta(minutes=5)).time()
    return open_t, close_t, grace


def now_ist() -> datetime:
    """Current time as a tz-aware Asia/Kolkata datetime."""
    return datetime.now(IST)


def in_session(dt: datetime, holidays: frozenset[date] = frozenset(),
               exchange: str = DEFAULT_EXCHANGE,
               bounds: tuple[time, time, time] | None = None) -> bool:
    """True when the exchange's session is open (or in its close-grace).

    `bounds` replaces the exchange window for one watch (the SYMBOLS
    '@HH:MM-HH:MM' suffix), so crude can be gated to 17:00-22:00 while MCX
    as a whole trades 09:00-23:35. Naive datetimes are assumed to already
    be IST (friendly for tests).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    open_t, _close, grace_end = bounds or session_bounds(exchange)
    local = dt.time()
    return is_trading_day(dt.date(), holidays) and open_t <= local <= grace_end


def is_trading_day(day: date, holidays: frozenset[date] = frozenset()) -> bool:
    """Weekday and not a configured holiday (NSE & MCX both trade Mon-Fri)."""
    return day.weekday() < 5 and day not in holidays
