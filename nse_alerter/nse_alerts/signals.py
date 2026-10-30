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
from datetime import datetime, timedelta

import pandas as pd

from .envelope import envelope_lines, envelope_side
from .qqe import MIN_QQE_BARS, qqe_lines, qqe_side

STRATEGY_LABELS = {"ema20": "EMA20 cross", "qqe": "QQE signals",
                   "env": "Magic Envelope", "both": "EMA20+QQE consensus"}


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

    def message(self, now: datetime | None = None,
                interval_minutes: int = 5,
                prep_confirmed: bool = False) -> str:
        arrow = "UP->DOWN" if self.side == "SELL" else "DOWN->UP"
        label = STRATEGY_LABELS.get(self.strategy, self.strategy)
        rule = self.rule or (
            f"5m close {self.close:,.2f} "
            f"{'>' if self.side == 'BUY' else '<'} EMA20 {self.ema:,.2f}")
        footer = f"bar {self.bar_time:%d %b %H:%M} IST - src={self.source}"
        if now is not None:
            naive = now.replace(tzinfo=None) if now.tzinfo else now
            close_t = self.bar_time + timedelta(minutes=interval_minutes)
            lag = max(0, int((naive - close_t).total_seconds()
                             // (interval_minutes * 60)))
            footer += (f"\n⏱️ bar closed {close_t:%H:%M} · "
                       f"{'just closed' if lag == 0 else f'{lag} bar(s) late'}"
                       f" · sent {naive:%H:%M}")
        confirmed = " · pre-flip confirmed" if prep_confirmed else ""
        return (
            f"{'SELL' if self.side == 'SELL' else 'BUY'} {self.symbol} "
            f"({arrow}) · {label}{confirmed}\n"
            f"{rule}\n"
            f"{footer}"
        )


def prep_flip(candles: pd.DataFrame, live_close: float, side: str,
              strategy: str, *, ema_len: int = 20, rsi_period: int = 14,
              sf: int = 5, factor: float = 4.238,
              ema_buffer_pct: float = 0.02,
              qqe_buffer: float = 1.0) -> tuple[str, str] | None:
    """Forming-bar heads-up for the non-envelope engines.

    ('UP' | 'DOWN', detail) when the LIVE bar would flip `side` if it closed
    right now - the preview BEFORE the confirmed full-bar break. `both`
    requires the two engines to predict the SAME new direction. None when
    nothing is pending (or the engines disagree).
    """
    closes = candles["Close"].astype(float).reset_index(drop=True)
    if strategy == "ema20":
        level = float(ema(closes, ema_len).iloc[-1])
        buffer = abs(level) * ema_buffer_pct / 100.0
        if side == "DOWN" and live_close > level + buffer:
            return "UP", (f"live {live_close:,.2f} > EMA{ema_len} "
                           f"{level:,.2f} + {ema_buffer_pct:g}% buffer")
        if side == "UP" and live_close < level - buffer:
            return "DOWN", (f"live {live_close:,.2f} < EMA{ema_len} "
                             f"{level:,.2f} - {ema_buffer_pct:g}% buffer")
        return None
    if strategy == "qqe":
        hyp = pd.concat([closes, pd.Series([float(live_close)])],
                        ignore_index=True)
        want = qqe_side(hyp, rsi_period, sf, factor)
        if want.isna().all():
            return None
        new = "UP" if int(want.iloc[-1]) == 1 else "DOWN"
        if new == side:
            return None
        lines = qqe_lines(hyp, rsi_period, sf, factor)
        trail = float(lines["FastAtrRsiTL"].iloc[-1])
        rsi = float(lines["RSIndex"].iloc[-1])
        gap = rsi - trail if new == "UP" else trail - rsi
        if gap < qqe_buffer:
            return None
        op = "<" if new == "UP" else ">"
        return new, (f"QQE gap {gap:.1f} RSI pts (min {qqe_buffer:g}) | "
                     f"trailing {trail:.1f} {op} RSI-ma {rsi:.1f} "
                     f"(live {live_close:,.2f})")
    if strategy == "both":
        em = prep_flip(candles, live_close, side, "ema20",
                       ema_len=ema_len, ema_buffer_pct=ema_buffer_pct)
        if em is None:
            return None
        qq = prep_flip(candles, live_close, side, "qqe",
                       rsi_period=rsi_period, sf=sf, factor=factor,
                       qqe_buffer=qqe_buffer)
        if qq is None or qq[0] != em[0]:
            return None
        return em[0], f"{em[1]} | {qq[1]}"
    return None


def prep_message(symbol: str, direction: str, live: float, level: float,
                 percent: float, bar_start: datetime, source: str,
                 current_side: str) -> str:
    """⚠️ envelope pre-flip heads-up: the FORMING bar is beyond the band, but
    state does not move - only evaluate() on a completed full-bar break
    confirms the flip (BLUE/RED as in the confirmed alert)."""
    color = "BLUE" if direction == "UP" else "RED"
    op, band = (">", "upper") if direction == "UP" else ("<", "lower")
    return (
        f"⚠️ ENV pre-flip · {symbol} live {live:,.2f} {op} {band} {level:,.2f} "
        f"(±{percent}%)\n"
        f"{color} flip PENDING · side {current_side} · "
        f"bar {bar_start:%d %b %H:%M} IST (forming)\n"
        f"confirms only if the bar CLOSES fully {op} the band · src={source}"
    )


def prep_flip_message(symbol: str, direction: str, live: float, detail: str,
                      bar_start: datetime, source: str,
                      current_side: str, strategy: str) -> str:
    """⚠️ forming-bar heads-up for ema20 / qqe / both - same contract as the
    envelope prep: state never moves, only a completed full-bar break in
    evaluate() confirms the flip. `detail` is the rule text from prep_flip()."""
    label = STRATEGY_LABELS.get(strategy, strategy)
    arrow = "UP (bullish)" if direction == "UP" else "DOWN (bearish)"
    return (
        f"⚠️ {label} flip PENDING · {symbol} live {live:,.2f} → {arrow}\n"
        f"{detail}\n"
        f"side {current_side} · bar {bar_start:%d %b %H:%M} IST (forming) · "
        f"confirms only if the bar CLOSES · src={source}"
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
    envelope_len: int = 20,
    envelope_percent: float = 0.2,
    envelope_exponential: bool = False,
) -> tuple[CrossEvent | None, str]:
    """Evaluate the selected strategy on the last completed bar.

    Returns (event_or_None, current_side). prev_side=None means 'first run':
    record a baseline silently instead of alerting.
    """
    if candles is None:
        raise SignalError("no candle data")
    if strategy == "env":
        needed = envelope_len + 2
    elif strategy == "qqe":
        needed = MIN_QQE_BARS
    else:
        needed = ema_len + 2
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
    elif strategy == "env":
        basis, upper, lower = envelope_lines(closes, envelope_len,
                                             envelope_percent,
                                             envelope_exponential)
        sides = envelope_side(closes, candles["High"], candles["Low"],
                              envelope_len, envelope_percent,
                              envelope_exponential)
        level = float(basis.iloc[-1])
        env_band = (float(lower.iloc[-1]), float(upper.iloc[-1]))
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
    elif strategy == "env":
        lo_v, up_v = env_band
        color = "BLUE" if cur == "UP" else "RED"
        rule = (f"Env flipped {'LONG' if cur == 'UP' else 'SHORT'} "
                f"({color} cross) · "
                f"close {close:,.2f} · band {lo_v:,.2f}–{up_v:,.2f} "
                f"(±{envelope_percent}%)")
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
