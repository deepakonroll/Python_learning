"""TradingView candles via the unofficial tvdatafeed library.

Provides the actual NIFTY *futures* contract (NSE:NIFTY1! = continuous
front-month, rolls automatically at expiry). Free for non-professional users
(real-time NSE F&O per TradingView's data FAQ), but unofficial - so we retry
a few times and let the caller fall back to the yfinance spot proxy.

Timestamps from the library are true UTC -> normalize_candles() converts to IST.
"""

from __future__ import annotations

import time as _time

import pandas as pd

from ..config import INTERVAL_MINUTES
from .base import ProviderError, filter_session, normalize_candles

# our interval key -> tvDatafeed Interval member name
_TV_INTERVAL = {
    "1m": "in_1_minute",
    "5m": "in_5_minute",
    "15m": "in_15_minute",
    "30m": "in_30_minute",
    "1h": "in_1_hour",
    "1d": "in_daily",
}

ATTEMPTS = 3
SLEEP_SECONDS = 1.5


class TvProvider:
    name = "tv"

    def __init__(self, attempts: int = ATTEMPTS, sleep=_time.sleep):
        self.attempts = attempts
        self._sleep = sleep
        self._client = None

    def _connect(self):
        if self._client is None:
            try:
                from tvDatafeed import TvDatafeed
            except ImportError as exc:                # pragma: no cover - env issue
                raise ProviderError(f"tvdatafeed not installed: {exc}") from exc
            self._client = TvDatafeed()               # anonymous access
        return self._client

    def fetch(self, symbol: str, interval: str, lookback: int) -> pd.DataFrame:
        try:
            from tvDatafeed import Interval
            tv_interval = Interval[_TV_INTERVAL[interval]]
        except (ImportError, KeyError) as exc:
            raise ProviderError(f"tv interval mapping failed for {interval!r}: {exc}") from exc

        exchange = "NSE"
        if ":" in symbol:                             # allow "NSE:NIFTY1!" spelling
            exchange, symbol = symbol.split(":", 1)

        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                raw = self._connect().get_hist(
                    symbol=symbol,
                    exchange=exchange,
                    interval=tv_interval,
                    n_bars=lookback,
                )
                if raw is None or raw.empty:
                    raise ProviderError("tvDatafeed returned no data")
                candles = normalize_candles(raw)
                candles = filter_session(candles)
                if len(candles) < INTERVAL_MINUTES[interval]:
                    raise ProviderError(f"only {len(candles)} session bars returned")
                return candles
            except ProviderError as exc:
                last_error = exc
            except Exception as exc:                  # websocket drops, parse errors...
                last_error = exc
                self._client = None                   # force reconnect next try
            if attempt < self.attempts:
                self._sleep(SLEEP_SECONDS)

        raise ProviderError(f"tv fetch failed after {self.attempts} attempts: {last_error}")
