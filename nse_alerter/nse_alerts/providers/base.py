"""Data provider protocol + candle normalization shared by every provider.

Java equivalent: an interface (DataProvider) plus a common DTO mapper - each
provider speaks its own dialect (column case, timezone), normalize_candles()
converts everything to the canonical shape: Open/High/Low/Close/Volume with a
sorted, unique, tz-naive Asia/Kolkata DatetimeIndex.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd

from ..market_hours import session_bounds

CANONICAL = ["Open", "High", "Low", "Close", "Volume"]


class ProviderError(RuntimeError):
    """Provider failed (network, auth, rate limit, bad payload)."""


class DataProvider(Protocol):
    name: str

    def fetch(self, symbol: str, interval: str, lookback: int) -> pd.DataFrame:
        """Return normalized candles; raise ProviderError on failure."""
        ...


def normalize_candles(df: pd.DataFrame, *, assume_tz: str = "Asia/Kolkata") -> pd.DataFrame:
    """Canonical form regardless of source (tv lowercase cols / yf MultiIndex...)."""
    if df is None or len(df) == 0:
        raise ProviderError("provider returned no rows")

    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):          # yfinance single-ticker shape
        df.columns = df.columns.get_level_values(0)
    rename = {c: c.strip().capitalize() for c in df.columns if isinstance(c, str)}
    df = df.rename(columns=rename)
    missing = {"Open", "High", "Low", "Close"} - set(df.columns)
    if missing:
        raise ProviderError(f"missing columns {sorted(missing)} (got {list(df.columns)})")
    if "Volume" not in df.columns:
        df["Volume"] = 0.0

    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize(assume_tz)               # naive sources = exchange local
    idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)   # canonical: naive IST

    df.index = idx
    df.index.name = "datetime"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Close"])
    if df.empty:
        raise ProviderError("all rows dropped during normalization")
    return df[CANONICAL]


def filter_session(candles: pd.DataFrame, exchange: str = "NSE") -> pd.DataFrame:
    """Keep bars that start inside the exchange's session (open <= t < close).

    TradingView occasionally returns stamps past the close - dropped here.
    """
    open_t, close_t, _grace = session_bounds(exchange)
    times = candles.index.time
    mask = (times >= open_t) & (times < close_t)
    out = candles[mask]
    return out if not out.empty else candles          # daily bars pass through


def completed_bars(candles: pd.DataFrame, interval_minutes: int, now: datetime) -> pd.DataFrame:
    """Drop the forming bar: keep only bars whose close time has passed."""
    if candles is None or candles.empty:
        return candles
    cutoff = pd.Timestamp(now)
    return candles[candles.index + pd.Timedelta(minutes=interval_minutes) <= cutoff]
