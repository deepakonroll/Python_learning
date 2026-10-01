"""Shared fixtures: synthetic 5m candles + a ready-to-use Config."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from nse_alerts.config import Config
from nse_alerts.market_hours import IST

# 2026-09-28 is a Monday; 2026-09-26 a Saturday (verified against Yahoo data)
MONDAY = datetime(2026, 9, 28, 13, 30, tzinfo=IST)    # in-session "now" (late enough
                                                       # for 52 x 5m bars to be completed)
SATURDAY = datetime(2026, 9, 26, 10, 0, tzinfo=IST)


def make_candles(closes: list[float], start: str = "2026-09-28 09:15",
                 minutes: int = 5) -> pd.DataFrame:
    """Canonical naive-IST OHLC frame (Close == Open == High == Low)."""
    idx = pd.date_range(start, periods=len(closes), freq=f"{minutes}min")
    n = len(closes)
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": [100.0] * n},
        index=idx,
    )


def falling(n: int = 60, start: float = 100.0, step: float = 0.3) -> list[float]:
    """A decline long enough that close < EMA20 (side = DOWN)."""
    return [start - step * i for i in range(n)]


def rising(n: int = 60, start: float = 82.0, step: float = 1.2) -> list[float]:
    """A rally long enough that close > EMA20 (side = UP)."""
    return [start + step * i for i in range(n)]


def make_config(tmp_path, **overrides) -> Config:
    from nse_alerts.config import Watch
    nifty = Watch(key="NIFTY1!", label="NIFTY1!", exchange="NSE",
                  tv_symbol="NIFTY1!", yahoo_symbol="^NSEI")
    values: dict = dict(
        symbol="NIFTY1!",
        watches=(nifty,),
        yahoo_symbol="^NSEI",
        interval="5m",
        ema_len=20,
        lookback_bars=300,
        data_provider="auto",
        strategy="ema20",                        # legacy rule keeps old tests stable
        qqe_rsi_period=14,
        qqe_sf=5,
        qqe_factor=4.238,
        envelope_len=20,
        envelope_percent=0.2,
        envelope_exponential=False,
        telegram_token="test-token",
        telegram_chat_id="42",
        kite_api_key=None,
        kite_access_token=None,
        state_file=tmp_path / "state.json",
        holidays=frozenset(),
        log_file=None,
    )
    values.update(overrides)
    return Config(**values)


@pytest.fixture
def candles_down() -> pd.DataFrame:
    return make_candles(falling())


@pytest.fixture
def candles_up() -> pd.DataFrame:
    return make_candles(rising())
