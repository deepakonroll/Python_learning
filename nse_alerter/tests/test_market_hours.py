"""NSE session gate: weekdays 09:15-15:35 IST incl. holiday list."""

from datetime import datetime

from nse_alerts.market_hours import IST, in_session, is_trading_day, now_ist


def dt(*args) -> datetime:
    return datetime(*args, tzinfo=IST)


def test_weekday_inside_session():
    assert in_session(dt(2026, 9, 28, 10, 0))           # Monday 10:00


def test_open_boundary_inclusive():
    assert in_session(dt(2026, 9, 28, 9, 15))
    assert not in_session(dt(2026, 9, 28, 9, 14))


def test_close_and_grace():
    assert in_session(dt(2026, 9, 28, 15, 30))          # final bar closes now
    assert in_session(dt(2026, 9, 28, 15, 35))          # grace window
    assert not in_session(dt(2026, 9, 28, 15, 36))
    assert not in_session(dt(2026, 9, 28, 20, 0))       # evening


def test_weekend_closed():
    assert not in_session(dt(2026, 9, 26, 10, 0))       # Saturday
    assert not in_session(dt(2026, 9, 27, 10, 0))       # Sunday


def test_holiday_list_skips_trading_day():
    holidays = frozenset({dt(2026, 9, 28).date()})
    assert not in_session(dt(2026, 9, 28, 10, 0), holidays)
    assert not is_trading_day(dt(2026, 9, 28).date(), holidays)


def test_naive_datetime_is_treated_as_ist():
    assert in_session(datetime(2026, 9, 28, 10, 0))     # no tzinfo supplied


def test_now_ist_is_aware_and_in_ist_zone():
    now = now_ist()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 5.5 * 3600
