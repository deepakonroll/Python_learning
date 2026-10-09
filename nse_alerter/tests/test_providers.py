"""Candle normalization/shape handling + Kite front-month selection."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from nse_alerts.market_hours import IST
from nse_alerts.providers.base import (
    ProviderError,
    completed_bars,
    filter_session,
    normalize_candles,
)
from nse_alerts.providers.kite import (KiteProvider, candles_frame,
                                       parse_base_symbol, pick_front_month)
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


# ── Kite provider: base symbols, all futures segments, cache, candle shapes ─

MCX_INSTRUMENTS = [
    {"tradingsymbol": "CRUDEOILM26OCTFUT", "segment": "MCX-FUT",
     "expiry": "2026-10-19", "instrument_token": 11},   # mini: shares the prefix
    {"tradingsymbol": "CRUDEOIL26OCTFUT", "segment": "MCX-FUT",
     "expiry": "2026-10-19", "instrument_token": 22},   # front month
    {"tradingsymbol": "CRUDEOIL26NOVFUT", "segment": "MCX-FUT",
     "expiry": "2026-11-20", "instrument_token": 33},
    {"tradingsymbol": "CRUDEOIL26OCTOPT", "segment": "MCX-OPT",
     "expiry": "2026-10-19", "instrument_token": 44},   # options: excluded
]

# 2099 expiries so the wall-clock "today" inside KiteProvider.fetch never
# invalidates these fixtures (pick_front_month above takes `today` explicitly)
FUT_2099 = [
    {"tradingsymbol": "CRUDEOIL26OCTFUT", "segment": "MCX-FUT",
     "expiry": "2099-01-15", "instrument_token": 22},
    {"tradingsymbol": "CRUDEOIL26NOVFUT", "segment": "MCX-FUT",
     "expiry": "2099-02-19", "instrument_token": 33},
]

SDK_ROWS = [
    {"date": "2026-10-09T09:15:00+0530", "open": 100.0, "high": 101.0,
     "low": 99.5, "close": 100.5, "volume": 10, "oi": 5},
    {"date": "2026-10-09T09:20:00+0530", "open": 100.5, "high": 101.5,
     "low": 100.0, "close": 101.0, "volume": 12, "oi": 6},
]

ARRAY_ROWS = [
    ["2026-10-09T09:15:00+0530", 100.0, 101.0, 99.5, 100.5, 10],
    ["2026-10-09T09:20:00+0530", 100.5, 101.5, 100.0, 101.0, 12],
]


class FakeKite:
    """Injected KiteConnect stand-in (same surface the provider uses)."""

    def __init__(self, instruments=None, raw=None):
        self._rows = instruments or []
        self._raw = raw if raw is not None else []
        self.instruments_calls = 0
        self.historical_calls = 0
        self.last = None

    def instruments(self):
        self.instruments_calls += 1
        return self._rows

    def historical(self, token, from_, to, interval):
        self.historical_calls += 1
        self.last = (token, from_, to, interval)
        return self._raw


@pytest.fixture(autouse=True)
def _reset_kite_instruments_cache():
    KiteProvider._instruments_cache = None
    yield
    KiteProvider._instruments_cache = None


@pytest.mark.parametrize("symbol,base", [
    ("NIFTY1!", "NIFTY"),
    ("MCX:CRUDEOIL", "CRUDEOIL"),
    ("BSE:SENSEX1!", "SENSEX"),
    ("TVC:UKOIL", "UKOIL"),              # matches no futures -> clean fallback
])
def test_parse_base_symbol(symbol, base):
    assert parse_base_symbol(symbol) == base


def test_pick_front_month_works_for_mcx_futures():
    chosen = pick_front_month(MCX_INSTRUMENTS, "CRUDEOIL", datetime(2026, 10, 9, 10, 0))
    assert chosen["instrument_token"] == 22     # CRUDEOILM (mini) excluded


def test_pick_front_month_works_for_bfo_sensex():
    rows = [{"tradingsymbol": "SENSEX26OCTFUT", "segment": "BFO-FUT",
             "expiry": "2026-10-27", "instrument_token": 7},
            {"tradingsymbol": "SENSEX26NOVFUT", "segment": "BFO-FUT",
             "expiry": "2026-11-24", "instrument_token": 8}]
    chosen = pick_front_month(rows, "SENSEX", datetime(2026, 10, 9, 10, 0))
    assert chosen["instrument_token"] == 7


def test_fetch_sdk_dict_rows_become_naive_ist_index():
    """kiteconnect returns dicts with a 'date' key - the old pd.DataFrame(raw)
    path indexed by RangeIndex, which normalized to 1970 stamps (every row then
    died in the session filter); rows must land on real IST bar stamps."""
    fake = FakeKite(instruments=FUT_2099, raw=SDK_ROWS)
    got = KiteProvider("k", "t", client=fake).fetch("MCX:CRUDEOIL", "5m", 30)
    assert list(got.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert got.index.tz is None
    assert got.index[0] == pd.Timestamp("2026-10-09 09:15")   # +0530 -> IST
    assert fake.last[0] == 22                                 # front-month token


def test_fetch_raw_http_array_rows_accepted():
    fake = FakeKite(instruments=FUT_2099, raw=ARRAY_ROWS)
    got = KiteProvider("k", "t", client=fake).fetch("MCX:CRUDEOIL", "5m", 30)
    assert list(got.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert got.index[0] == pd.Timestamp("2026-10-09 09:15")


def test_instruments_master_downloaded_once_per_ttl_window():
    fake = FakeKite(instruments=FUT_2099, raw=ARRAY_ROWS)
    kite = KiteProvider("k", "t", client=fake)
    kite.fetch("MCX:CRUDEOIL", "5m", 30)
    kite.fetch("MCX:CRUDEOIL", "5m", 30)
    assert fake.instruments_calls == 1                      # cached, not re-fetched
    assert fake.historical_calls == 2


def test_instruments_cache_expires_after_ttl(monkeypatch):
    monkeypatch.setattr(KiteProvider, "INSTRUMENTS_TTL", timedelta(0))
    fake = FakeKite(instruments=FUT_2099, raw=ARRAY_ROWS)
    kite = KiteProvider("k", "t", client=fake)
    kite.fetch("MCX:CRUDEOIL", "5m", 30)
    kite.fetch("MCX:CRUDEOIL", "5m", 30)
    assert fake.instruments_calls == 2


def test_missing_credentials_raise_clear_error():
    with pytest.raises(ProviderError, match="KITE_API_KEY"):
        KiteProvider(None, None).fetch("NIFTY1!", "5m", 30)


def test_candles_frame_rejects_rows_without_timestamp():
    with pytest.raises(ProviderError):
        candles_frame([{"open": 1.0, "close": 1.5}])
