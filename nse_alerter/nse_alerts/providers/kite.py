"""Zerodha Kite Connect provider - the paid feed that leads when enabled.

DATA_PROVIDER=kite puts this provider FIRST for every watch, with the free
TradingView/yahoo chain still behind it (app.build_providers), so a stale
KITE_ACCESS_TOKEN (it dies every trading day - refresh with kite_login.py)
degrades to the free feeds instead of darkening the alerts. Works on the paid
Kite Connect plan only; the free "Personal" plan has NO market data.

The instrument master (https://api.kite.trade/instruments) is downloaded once
per 6-hour cache window, and the front-month FUTURES contract for the watch's
base symbol is picked automatically across NFO-FUT (NIFTY, BANKNIFTY...),
MCX-FUT (CRUDEOIL...) and BFO-FUT (SENSEX, BANKEX...), so expiry rolls need
no maintenance. Candles arrive as ISO +0530 timestamps: the kiteconnect SDK
returns dicts with a "date" key, the raw HTTP API returns arrays -
candles_frame() accepts both.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pandas as pd

from ..market_hours import IST
from .base import ProviderError, normalize_candles

_KITE_INTERVAL = {
    "1m": "minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "60minute",
    "1d": "day",
}

_HIST_COLS = ["datetime", "open", "high", "low", "close", "volume", "oi"]


def parse_base_symbol(symbol: str) -> str:
    """Watch symbol -> bare instrument base name.

    "NIFTY1!" -> "NIFTY" (TradingView continuous-futures suffix),
    "MCX:CRUDEOIL" -> "CRUDEOIL" (exchange prefix). Unknown shapes pass
    through unchanged and then match no futures -> clean ProviderError ->
    the free chain behind this provider takes over.
    """
    base = symbol.split(":", 1)[-1]
    base = re.sub(r"\d+!$", "", base)
    return base or "NIFTY"


def candles_frame(raw: list) -> pd.DataFrame:
    """Kite candle rows -> datetime-indexed frame for normalize_candles().

    SDK dicts ({date, open, ..., oi}) and raw HTTP arrays
    ([timestamp, o, h, l, c, vol(, oi)]) both accepted; the ISO "+0530"
    timestamps are parsed as UTC here and converted to naive IST wall time
    by normalize_candles().
    """
    first = raw[0]
    if isinstance(first, dict):
        df = pd.DataFrame(raw).rename(columns={"date": "datetime"})
    else:
        df = pd.DataFrame(raw, columns=_HIST_COLS[: len(first)])
    if "datetime" not in df:
        raise ProviderError("kite rows have no timestamp column")
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    return df.set_index("datetime")


def pick_front_month(instruments: list[dict], base_symbol: str, today: datetime) -> dict:
    """Smallest FUTURES expiry >= today for e.g. base_symbol='NIFTY'.

    Matches the segment suffix "-FUT", which covers every exchange's futures
    segment - NFO-FUT (index), MCX-FUT (commodity), BFO-FUT (BSE index) -
    so NIFTY, crude and SENSEX all use the same picker.

    Java: streams + min(comparing(expiry)) with a filter on segment/prefix.
    """
    candidates = []
    for row in instruments:
        tradingsymbol = row.get("tradingsymbol", "")
        if not str(row.get("segment", "")).endswith("-FUT"):
            continue
        if not tradingsymbol.startswith(base_symbol):
            continue
        # excludes look-alikes such as NIFTYNXT50... (next char must be a digit)
        rest = tradingsymbol[len(base_symbol):]
        if not rest[:1].isdigit():
            continue
        expiry = row.get("expiry")
        if not expiry:
            continue
        expiry_day = pd.Timestamp(expiry).date()
        if expiry_day >= today.date():
            candidates.append((expiry_day, row))
    if not candidates:
        raise ProviderError(f"no futures found for {base_symbol}")
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


class KiteProvider:
    name = "kite"
    INSTRUMENTS_TTL = timedelta(hours=6)      # a fresh dump lands ~once a day

    _instruments_cache: tuple[datetime, list[dict]] | None = None

    def __init__(self, api_key: str | None, access_token: str | None, client=None):
        self.api_key = api_key
        self.access_token = access_token
        self._client = client                        # injected in tests

    def _connect(self):
        if self._client is not None:
            return self._client
        if not self.api_key or not self.access_token:
            raise ProviderError(
                "KITE_API_KEY / KITE_ACCESS_TOKEN not set (paid Kite Connect plan "
                "required for market data - see README)"
            )
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:                    # pragma: no cover - env issue
            raise ProviderError(f"kiteconnect not installed: {exc}") from exc
        self._client = KiteConnect(api_key=self.api_key)
        self._client.set_access_token(self.access_token)
        return self._client

    def _instruments(self, kite) -> list[dict]:
        """Instrument master, cached process-wide (one download per 6 h).

        The dump is account-independent, so every watch shares it - re-fetching
        ~1 MB per watch per cron tick would be pure waste (and rate-limit
        risk); the front-month picker still re-runs on every fetch, so expiry
        rolls are picked up between refreshes.
        """
        now = datetime.now(IST)
        cached = KiteProvider._instruments_cache
        if cached and now - cached[0] < self.INSTRUMENTS_TTL:
            return cached[1]
        rows = kite.instruments()
        KiteProvider._instruments_cache = (now, rows)
        return rows

    def fetch(self, symbol: str, interval: str, lookback: int) -> pd.DataFrame:
        kite = self._connect()
        now = datetime.now(IST)
        try:
            contract = pick_front_month(self._instruments(kite),
                                        parse_base_symbol(symbol), now)
            days_back = 7 if _KITE_INTERVAL[interval] != "day" else lookback + 10
            raw = kite.historical(
                contract["instrument_token"],
                (now - pd.Timedelta(days=days_back)).strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d %H:%M:%S"),
                _KITE_INTERVAL[interval],
            )
            if raw is None or len(raw) == 0:
                raise ProviderError("kite returned no candles")
            candles = normalize_candles(candles_frame(raw))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"kite historical failed: {exc}") from exc
        return candles.tail(max(lookback, 30))    # session filter: app.fetch_candles
