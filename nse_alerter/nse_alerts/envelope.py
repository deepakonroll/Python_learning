"""Magic Envelope port (TradingView v3 study "Magic Envelope").

    basis  = SMA(close, length)            (EMA when exponential=true)
    upper  = basis * (1 + percent/100)
    lower  = basis * (1 - percent/100)
    break-up bar   (low  > upper)  -> side +1  (LONG)
    break-down bar (high < lower)  -> side -1  (SHORT)
    every other bar carries the previous side forward (ties carry forward).

The Pine original only draws dots/crosses - it has no side and no alerts -
so the "full bar beyond the band" crosses (its c/d markers) are mapped onto
our existing flip-dedupe engine here. Backtest on one month of 5m bars with
percent=0.2: crude (BZ=F, MCX session) = 5.3 flips/day (3-7, no dead days);
NIFTY = 0.5/day - tune percent per instrument.

Java equivalent: rolling mean + two comparison bands + carry-forward state.
"""

from __future__ import annotations

import pandas as pd


def envelope_lines(
    closes: pd.Series,
    length: int = 20,
    percent: float = 0.2,
    exponential: bool = False,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(basis, upper, lower) - percent is in percent units (0.2 = +/-0.2%)."""
    basis = (closes.ewm(span=length, adjust=False).mean() if exponential
             else closes.rolling(length).mean())
    k = percent / 100.0
    return basis, basis * (1 + k), basis * (1 - k)


def envelope_side(
    closes: pd.Series,
    high: pd.Series,
    low: pd.Series,
    length: int = 20,
    percent: float = 0.2,
    exponential: bool = False,
) -> pd.Series:
    """+1 / -1 side series; NaN until the first full-bar break, then
    carry-forward - the same contract as qqe_side()."""
    _basis, upper, lower = envelope_lines(closes, length, percent, exponential)
    side = pd.Series(float("nan"), index=closes.index)
    side[low > upper] = 1.0          # comparisons with NaN basis stay False
    side[high < lower] = -1.0        # (mutually exclusive: low <= high)
    return side.ffill()
