"""Zerodha Kite Connect provider - ready to switch on later (paid plan only).

Free "Personal" Kite plans have NO market data - this provider raises a clear
ProviderError in that case, and DATA_PROVIDER=auto never selects it. Enable by
setting DATA_PROVIDER=kite + KITE_API_KEY + KITE_ACCESS_TOKEN (see README,
"Enabling Zerodha Kite later"): the front-month NIFTY future is picked
automatically from the instruments file, so expiry rolls need no maintenance.
"""

from __future__ import annotations

from datetime import datetime

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


def pick_front_month(instruments: list[dict], base_symbol: str, today: datetime) -> dict:
    """Smallest NFO futures expiry >= today for e.g. base_symbol='NIFTY'.

    Java: streams + min(comparing(expiry)) with a filter on segment/prefix.
    """
    candidates = []
    for row in instruments:
        tradingsymbol = row.get("tradingsymbol", "")
        if row.get("segment") != "NFO-FUT":
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
        raise ProviderError(f"no NFO futures found for {base_symbol}")
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


class KiteProvider:
    name = "kite"

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

    def fetch(self, symbol: str, interval: str, lookback: int) -> pd.DataFrame:
        kite = self._connect()
        now = datetime.now(IST)
        try:
            base = symbol.rstrip("1!") or "NIFTY"      # "NIFTY1!" -> "NIFTY"
            contract = pick_front_month(kite.instruments(), base, now)
            days_back = 7 if _KITE_INTERVAL[interval] != "day" else lookback + 10
            raw = kite.historical(
                contract["instrument_token"],
                (now - pd.Timedelta(days=days_back)).strftime("%Y-%m-%d %H:%M:%S"),
                now.strftime("%Y-%m-%d %H:%M:%S"),
                _KITE_INTERVAL[interval],
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"kite historical failed: {exc}") from exc

        if raw is None or len(raw) == 0:
            raise ProviderError("kite returned no candles")
        candles = normalize_candles(pd.DataFrame(raw))
        return candles.tail(max(lookback, 30))    # session filter: app.fetch_candles
