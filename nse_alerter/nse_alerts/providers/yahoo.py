"""yfinance fallback - NSE *spot* proxy (Yahoo has no NFO futures contracts).

The proxy tracks futures closely enough (basis is a few points on ~23,000)
that an EMA20 cross on spot almost always coincides with the futures cross;
alerts show src=yahoo so you always know which feed fired.
"""

from __future__ import annotations

import pandas as pd

from .base import ProviderError, normalize_candles

# interval -> yfinance period (Yahoo caps intraday history: 5m/15m ~60d, 1m ~7d)
_PERIOD = {"1m": "5d", "5m": "10d", "15m": "10d", "30m": "60d", "1h": "60d", "1d": "1y"}


class YahooProvider:
    name = "yahoo"

    def fetch(self, symbol: str, interval: str, lookback: int) -> pd.DataFrame:
        try:
            import yfinance as yf
        except ImportError as exc:                    # pragma: no cover - env issue
            raise ProviderError(f"yfinance not installed: {exc}") from exc

        try:
            raw = yf.download(
                symbol,
                period=_PERIOD[interval],
                interval=interval,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception as exc:
            raise ProviderError(f"yfinance download failed: {exc}") from exc

        if raw is None or raw.empty:
            raise ProviderError(f"yfinance returned no rows for {symbol}")

        candles = normalize_candles(raw)
        return candles.tail(max(lookback, 30))
