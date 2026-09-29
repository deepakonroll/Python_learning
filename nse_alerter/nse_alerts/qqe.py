"""QQE ("QQE signals" by colinmck, Pine v4, MPL-2.0) ported to pandas.

Pipeline (exactly as the Pine script):
    RSI(14, Wilder) -> EMA smooth (SF=5) = RSIndex
    AtrRsi   = |RSIndex - RSIndex[1]|
    MaAtrRsi = EMA(AtrRsi, Wilders_Period=27)
    dar      = EMA(MaAtrRsi, 27) * FastQQEFactor(4.238)
    iterative longband/shortband/trend loop  (the [1] recursion forces a
    bar-by-bar loop - vectorization is impossible by construction)
    FastAtrRsiTL = trend == 1 ? longband : shortband   (the trailing line)

Regime / signals:
    FastAtrRsiTL < RSIndex -> +1  (the script's QQExlong == 1  -> "Long")
    FastAtrRsiTL > RSIndex -> -1  (QQExshort == 1 -> "Short"); tie carries.

Flip into +1/-1 equals the Pine Long/Short alerts, so the existing cross-based
dedupe applies unchanged. Warm-up bars stay NaN and are carried from the first
valid regime (mirrors Pine's nz(trend[1], 1) - avoids a spurious "Long" at the
first valid bar of a fresh fetch). The Pine input `ThreshHold` is declared but
never used in the original script, so it is not ported.

Java equivalent: the band loop is a hand-written state machine over the bar
array - exactly what Pine does under the hood.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

QQE_DEFAULTS = {"rsi_period": 14, "sf": 5, "factor": 4.238}
MIN_QQE_BARS = 72        # 14 (RSI) + 5 + 27 + 27 smoothing warm-up (+ slack)


def _rma(x: pd.Series, length: int) -> pd.Series:
    """Wilder's moving average: SMA seed, then alpha = 1/length recursion."""
    out = pd.Series(np.nan, index=x.index, dtype=float)
    vals = x.to_numpy(dtype=float)
    if len(vals) < length:
        return out
    prev = float(np.nanmean(vals[:length]))
    out.iloc[length - 1] = prev
    for i in range(length, len(vals)):
        prev = (prev * (length - 1) + vals[i]) / length
        out.iloc[i] = prev
    return out


def _ema(x: pd.Series, length: int) -> pd.Series:
    """EMA seeded with the SMA of its first `length` values (TV convention)."""
    out = pd.Series(np.nan, index=x.index, dtype=float)
    vals = x.to_numpy(dtype=float)
    if len(vals) < length:
        return out
    alpha = 2.0 / (length + 1)
    prev = float(np.nanmean(vals[:length]))
    out.iloc[length - 1] = prev
    for i in range(length, len(vals)):
        v = vals[i]
        if np.isnan(v) or np.isnan(prev):    # Pine: na propagates through ema
            prev = np.nan
        else:
            prev = v * alpha + prev * (1 - alpha)
        out.iloc[i] = prev
    return out


def wilders_rsi(closes: pd.Series, period: int) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0.0).fillna(0.0)
    loss = (-delta.clip(upper=0.0)).fillna(0.0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = avg_gain / avg_loss                 # 0/0 -> nan, x/0 -> inf (Pine-like)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    return rsi.fillna(50.0)                  # neutral when flat/undefined


def qqe_lines(closes: pd.Series, rsi_period: int = 14, sf: int = 5,
              factor: float = 4.238) -> pd.DataFrame:
    """RSIndex (smoothed RSI), dar and FastAtrRsiTL (the trailing line)."""
    wilders = rsi_period * 2 - 1
    rsi = wilders_rsi(closes, rsi_period)
    rsi_ma = _ema(rsi, sf)
    atr_rsi = (rsi_ma - rsi_ma.shift(1)).abs()
    ma_atr_rsi = _ema(atr_rsi, wilders)
    dar = _ema(ma_atr_rsi, wilders) * factor

    r = rsi_ma.to_numpy(dtype=float)
    d = dar.to_numpy(dtype=float)
    n = len(closes)
    longb = np.full(n, np.nan)
    shortb = np.full(n, np.nan)
    trend = np.full(n, np.nan)
    fast = np.full(n, np.nan)

    def xcross(a1: float, b1: float, a0: float, b0: float) -> bool:
        """Pine cross(): the two series cross (equality tolerated on this bar)."""
        if any(map(np.isnan, (a1, b1, a0, b0))):
            return False
        return (a1 < b1 and a0 >= b0) or (a1 > b1 and a0 <= b0)

    for i in range(n):
        r0 = r[i]
        r_prev = r[i - 1] if i >= 1 else np.nan
        long_prev = longb[i - 1] if i >= 1 else np.nan
        short_prev = shortb[i - 1] if i >= 1 else np.nan
        trend_prev = trend[i - 1] if i >= 1 else np.nan
        long2 = longb[i - 2] if i >= 2 else np.nan
        short2 = shortb[i - 2] if i >= 2 else np.nan

        new_long = r0 - d[i]
        new_short = r0 + d[i]
        if (not np.isnan(r_prev) and not np.isnan(long_prev)
                and r_prev > long_prev and r0 > long_prev):
            long_now = max(long_prev, new_long)
        else:
            long_now = new_long
        if (not np.isnan(r_prev) and not np.isnan(short_prev)
                and r_prev < short_prev and r0 < short_prev):
            short_now = min(short_prev, new_short)
        else:
            short_now = new_short

        # Pine: trend := cross(RSIndex, shortband[1]) ? 1
        #              : cross(longband[1], RSIndex)  ? -1 : nz(trend[1], 1)
        if i >= 2 and xcross(r_prev, short2, r0, short_prev):
            trend_now = 1.0
        elif i >= 2 and xcross(long2, r_prev, long_prev, r0):
            trend_now = -1.0
        else:
            trend_now = trend_prev if not np.isnan(trend_prev) else 1.0

        longb[i], shortb[i], trend[i] = long_now, short_now, trend_now
        fast[i] = long_now if trend_now == 1.0 else short_now

    return pd.DataFrame({"RSIndex": r, "dar": d, "FastAtrRsiTL": fast},
                        index=closes.index)


def qqe_side(closes: pd.Series, rsi_period: int = 14, sf: int = 5,
             factor: float = 4.238) -> pd.Series:
    """+1 while the trailing line is below RSIndex (Long), -1 above (Short).

    Returns an all-NaN series when nothing is valid yet - the caller decides
    (signals.evaluate turns that into a SignalError).
    """
    lines = qqe_lines(closes, rsi_period, sf, factor)
    fast, rsi_index = lines["FastAtrRsiTL"], lines["RSIndex"]
    side = pd.Series(np.nan, index=closes.index)
    side[fast < rsi_index] = 1.0
    side[fast > rsi_index] = -1.0
    side = side.ffill().bfill()              # ties + warm-up carry forward
    return side.astype(float)                # may be all-NaN (too little data)
