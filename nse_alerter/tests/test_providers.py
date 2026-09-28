"""Candle normalization/shape handling + Kite front-month selection."""

from datetime import datetime

import pandas as pd
import pytest

from nse_alerts.market_hours import IST
from nse_alerts.providers.base import (
    ProviderError,
    completed_bars,
    filter_session,
    normalize_candles,
)
from nse_alerts.providers.kite import pick_front_month
from tests.conftest import falling, make_candles


def test_lowercase_tv_columns_and_utc_index_are_normalized():
    # tvDatafeed shape: lowercase cols, true-UTC index (03:45 UTC = 09:15 IST)
    raw = pd.DataFrame(
        {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10.0]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-09-28 03:45", tz="UTC")]),
    )
    out = normalize_candles(raw)
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert out.index.tz is None
    assert out.index[0] == pd.Timestamp("2026-09-28 09:15")     # IST wall time


def test_multiindex_yahoo_columns_are_flattened():
    names = ["Open", "High", "Low", "Close", "Volume"]
    cols = pd.MultiIndex.from_product([names, ["^NSEI"]])
    raw = pd.DataFrame([[1.0, 2.0, 0.5, 1.5, 10.0]] * 2, columns=cols,
                       index=pd.date_range("2026-09-28 09:15", periods=2, freq="5min"))
    out = normalize_candles(raw)
    assert list(out.columns) == names
    assert "^NSEI" not in out.columns


def test_naive_index_is_assumed_ist():
    raw = make_candles(falling(5))
    out = normalize_candles(raw)
    assert out.index[0] == pd.Timestamp("2026-09-28 09:15")


def test_unsorted_and_duplicate_rows_are_cleaned():
    raw = make_candles(falling(5)).iloc[::-1]           # reversed
    raw = pd.concat([raw, raw.iloc[[0, 1]]])            # + duplicates
    out = normalize_candles(raw)
    assert out.index.is_monotonic_increasing
    assert not out.index.has_duplicates


def test_empty_frame_raises():
    with pytest.raises(ProviderError):
        normalize_candles(pd.DataFrame())


def test_session_filter_drops_post_close_stamps():
    candles = make_candles([1.0] * 3 + [1.0, 1.0],
                           start="2026-09-28 15:20")    # 15:20, 15:25, 15:30, 15:35
    kept = filter_session(candles)
    assert list(kept.index.strftime("%H:%M")) == ["15:20", "15:25"]


def test_completed_bars_drops_forming_bar():
    candles = make_candles(falling(30))                 # 09:15 .. 11:40
    now = datetime(2026, 9, 28, 11, 30, tzinfo=IST).replace(tzinfo=None)
    done = completed_bars(candles, 5, now)
    assert done.index[-1] == pd.Timestamp("2026-09-28 11:25")
    assert len(done) == len(candles) - 3                # 11:30, 11:35, 11:40 not closed


# ── Kite front-month selection ──────────────────────────────────────────

INSTRUMENTS = [
    {"tradingsymbol": "NIFTY26SEP26FUT", "segment": "NFO-FUT",
     "expiry": "2026-09-25", "instrument_token": 111},      # already expired
    {"tradingsymbol": "NIFTY26OCT26FUT", "segment": "NFO-FUT",
     "expiry": "2026-10-01", "instrument_token": 222},      # next available
    {"tradingsymbol": "NIFTY26NOV26FUT", "segment": "NFO-FUT",
     "expiry": "2026-10-29", "instrument_token": 333},
    {"tradingsymbol": "NIFTYNXT5026OCTFUT", "segment": "NFO-FUT",
     "expiry": "2026-10-01", "instrument_token": 444},      # look-alike: excluded
    {"tradingsymbol": "NIFTY26OCT26OPT", "segment": "NFO-OPT",
     "expiry": "2026-10-01", "instrument_token": 555},      # options: excluded
]


def test_pick_front_month_takes_nearest_future_expiry():
    chosen = pick_front_month(INSTRUMENTS, "NIFTY", datetime(2026, 9, 28, 10, 0))
    assert chosen["instrument_token"] == 222


def test_pick_front_month_on_expiry_day_still_works():
    chosen = pick_front_month(INSTRUMENTS, "NIFTY", datetime(2026, 10, 1, 9, 0))
    assert chosen["instrument_token"] == 222             # 2026-10-01 counts (>= today)


def test_pick_front_month_no_match_raises():
    with pytest.raises(ProviderError):
        pick_front_month([{"tradingsymbol": "BANKNIFTY26OCTFUT", "segment": "NFO-FUT",
                           "expiry": "2026-10-01", "instrument_token": 9}],
                         "NIFTY", datetime(2026, 9, 28, 10, 0))
