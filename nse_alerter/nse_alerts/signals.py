"""The signal engine: side flips on completed 5m bars -> alert exactly once.

Two interchangeable strategies (config STRATEGY = qqe | ema20 | both):

  ema20 (original rule):          qqe (ported "QQE signals" Pine script):
    close > EMA20 -> side UP        FastAtrRsiTL < RSIndex -> Long  (+1)
    close < EMA20 -> side DOWN      FastAtrRsiTL > RSIndex -> Short (-1)

Both produce a +1/-1 side series; an alert fires when the side of the last
*completed* bar differs from the side stored in state (below->above = BUY,
above->below = SELL). Comparing against stored state (not just adjacent bars)
means a flip that happened while the app was down is still reported exactly
once. Ties never flip a signal (they carry forward).

Bar-completion filtering lives in providers/base.py (completed_bars).

Java equivalent: a small state machine fed one event at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .qqe import MIN_QQE_BARS, qqe_lines, qqe_side

STRATEGY_LABELS = {"ema20": "EMA20 cross", "qqe": "QQE signals"}


class SignalError(RuntimeError):
    """Not enough / unusable candle data to evaluate the rule."""


@dataclass(frozen=True)
class CrossEvent:
    symbol: str
    side: str            # "BUY" | "SELL"
    prev_side: str       # "UP" | "DOWN" before the flip
    bar_time: datetime   # start of the completed bar (naive IST)
    close: float
    ema: float           # EMA20 value, or the QQE trailing line for qqe
    source: str          # provider that produced the candles
    strategy: str = "ema20"
    rule: str = ""       # human-readable condition line

    def message(self) -> str:
        arrow = "UP->DOWN" if self.side == "SELL" else "DOWN->UP"
        label = STRATEGY_LABELS.get(self.strategy, self.strategy)
        rule = self.rule or (
            f"5m close {self.close:,.2f} "
            f"{'>' if self.side == 'BUY' else '<'} EMA20 {self.ema:,.2f}")
        return (
            f"{'SELL' if self.side == 'SELL' else 'BUY'} {self.symbol} "
            f"({arrow}) · {label}\n"
            f"{rule}\n"
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
    strategy: str = "ema20",
    rsi_period: int = 14,
    sf: int = 5,
    factor: float = 4.238,
) -> tuple[CrossEvent | None, str]:
    """Evaluate the selected strategy on the last completed bar.

    Returns (event_or_None, current_side). prev_side=None means 'first run':
    record a baseline silently instead of alerting.
    """
    if candles is None:
        raise SignalError("no candle data")
    needed = ema_len + 2 if strategy == "ema20" else MIN_QQE_BARS
    if len(candles) < needed:
        raise SignalError(f"need >= {needed} bars for {strategy}, got {len(candles)}")

    closes = candles["Close"]
    rsi_val = float("nan")
    if strategy == "qqe":
        sides = qqe_side(closes, rsi_period, sf, factor)
        if sides.isna().all():
            raise SignalError("QQE regime never warmed up on this data")
        lines = qqe_lines(closes, rsi_period, sf, factor)
        level = float(lines["FastAtrRsiTL"].iloc[-1])
        rsi_val = float(lines["RSIndex"].iloc[-1])
    else:
        sides = side_series(closes, ema_len)
        level = float(ema(closes, ema_len).iloc[-1])
    if sides.isna().all():
        raise SignalError("strategy produced no usable side data")

    cur = "UP" if int(sides.iloc[-1]) == 1 else "DOWN"

    if prev_side is None:
        return None, cur                      # baseline: no alert on startup
    if cur == prev_side:
        return None, cur                      # still on the same side: silent

    # side flipped relative to stored state -> alert
    close = float(closes.iloc[-1])
    bar_time = candles.index[-1].to_pydatetime()
    if strategy == "qqe":
        rule = (f"QQE flipped {'LONG' if cur == 'UP' else 'SHORT'} · "
                f"close {close:,.2f} · trailing {level:.1f} vs "
                f"RSI-ma {rsi_val:.1f} (RSI scale)")
    else:
        rule = (f"5m close {close:,.2f} "
                f"{'>' if cur == 'UP' else '<'} EMA{ema_len} {level:,.2f}")
    event = CrossEvent(
        symbol=symbol,
        side="BUY" if cur == "UP" else "SELL",
        prev_side=prev_side,
        bar_time=bar_time,
        close=close,
        ema=level,
        source=source,
        strategy=strategy,
        rule=rule,
    )
    return event, cur
