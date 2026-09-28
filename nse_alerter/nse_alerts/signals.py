"""The signal rule: 5m close vs EMA(20), alert only on a *cross*.

    close > EMA20  -> side = UP     (candidate BUY)
    close < EMA20  -> side = DOWN   (candidate SELL)
    close == EMA20 -> side carries forward (ties never flip a signal)

An alert fires when the side of the last *completed* bar differs from the
side we last recorded in state (below->above = BUY, above->below = SELL).
Comparing against stored state (not just adjacent bars) means a cross that
happened while the app was down is still reported exactly once.

Bar-completion filtering lives in providers/base.py (completed_bars) so the
providers and the app share one definition of a "closed" candle.

Java equivalent: a small state machine fed one event at a time - the pandas
ewm() is the vectorized form of a loop you'd write as ema = ema + (x - ema)*k.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


class SignalError(RuntimeError):
    """Not enough / unusable candle data to evaluate the rule."""


@dataclass(frozen=True)
class CrossEvent:
    symbol: str
    side: str            # "BUY" | "SELL"
    prev_side: str       # "UP" | "DOWN" before the flip
    bar_time: datetime   # start of the completed bar (naive IST)
    close: float
    ema: float
    source: str          # provider that produced the candles

    def message(self) -> str:
        arrow = "UP->DOWN" if self.side == "SELL" else "DOWN->UP"
        return (
            f"{'SELL' if self.side == 'SELL' else 'BUY'} {self.symbol} "
            f"({arrow})\n"
            f"5m close {self.close:,.2f} "
            f"{'>' if self.side == 'BUY' else '<'} EMA20 {self.ema:,.2f}\n"
            f"bar {self.bar_time:%d %b %H:%M} IST - src={self.source}"
        )


def ema(closes: pd.Series, length: int) -> pd.Series:
    """Exponential moving average (span form - matches TradingView's EMA)."""
    return closes.ewm(span=length, adjust=False).mean()


def side_series(closes: pd.Series, length: int) -> pd.Series:
    """+1 while close is above the EMA, -1 below; exact ties carry forward."""
    diff = closes - ema(closes, length)
    side = pd.Series(float("nan"), index=closes.index)
    side[diff > 0] = 1.0
    side[diff < 0] = -1.0
    side = side.ffill().bfill()          # ties inherit neighbours
    if side.isna().all():
        raise SignalError("close never differs from EMA - cannot form a signal")
    return side.astype(int)


def evaluate(
    candles: pd.DataFrame,
    *,
    symbol: str,
    ema_len: int,
    prev_side: str | None,
    source: str,
) -> tuple[CrossEvent | None, str]:
    """Evaluate the rule on the last completed bar.

    Returns (event_or_None, current_side). prev_side=None means 'first run':
    record a baseline silently instead of alerting.
    """
    needed = ema_len + 2
    if candles is None or len(candles) < needed:
        raise SignalError(f"need >= {needed} bars for EMA{ema_len}, got {0 if candles is None else len(candles)}")

    closes = candles["Close"]
    sides = side_series(closes, ema_len)
    cur = "UP" if int(sides.iloc[-1]) == 1 else "DOWN"

    if prev_side is None:
        return None, cur                      # baseline: no alert on startup
    if cur == prev_side:
        return None, cur                      # still on the same side: silent

    # side flipped relative to stored state -> alert
    close = float(closes.iloc[-1])
    level = float(ema(closes, ema_len).iloc[-1])
    bar_time = candles.index[-1].to_pydatetime()
    event = CrossEvent(
        symbol=symbol,
        side="BUY" if cur == "UP" else "SELL",
        prev_side=prev_side,
        bar_time=bar_time,
        close=close,
        ema=level,
        source=source,
    )
    return event, cur
