"""QQE port: golden behavior on deterministic synthetic series."""

import math

import pandas as pd
import pytest

from nse_alerts.qqe import MIN_QQE_BARS, qqe_side, wilders_rsi
from nse_alerts.signals import SignalError, evaluate
from tests.conftest import make_candles

WAVE = [23000 + 120 * math.sin(i / 6.0) + 0.5 * i for i in range(200)]
FLIP_INDEXES = [53, 72, 91, 110, 129, 148, 167, 185]   # wave half-periods


def _flips(series: pd.Series) -> list[int]:
    return [i for i in range(1, len(series)) if series.iloc[i] != series.iloc[i - 1]]


def test_wilders_rsi_bounds_and_neutral_flat():
    rsi = wilders_rsi(pd.Series(WAVE), 14)
    assert rsi.dropna().between(0, 100).all()
    flat = wilders_rsi(pd.Series([23000.0] * 50), 14)
    assert (flat == 50.0).all()                       # neutral when no movement


def test_wave_series_flips_exactly_at_inflections():
    sides = qqe_side(pd.Series(WAVE))
    assert set(sides.unique()) <= {1.0, -1.0}         # only +1/-1, no NaN after fill
    assert _flips(sides) == FLIP_INDEXES              # golden: sine inflections


def test_sharp_crash_flips_to_short():
    rise = [80 + 0.8 * i for i in range(70)]
    crash = [136 - 3.0 * (i - 70) for i in range(70, 140)]
    sides = qqe_side(pd.Series(rise + crash))
    assert int(sides.iloc[-1]) == -1                  # ends SHORT after the crash
    assert _flips(sides) == [71]                      # flips right at the reversal


def test_constant_series_has_no_valid_regime():
    sides = qqe_side(pd.Series([23000.0] * 100))
    assert sides.isna().all()                          # caller turns this into an error


def test_evaluate_qqe_baseline_then_flip():
    rises = [80 + 0.8 * i for i in range(70)]
    crash = [136 - 3.0 * (i - 70) for i in range(70, 140)]
    candles = make_candles(rises + crash)

    event, side = evaluate(candles, symbol="NIFTY1!", ema_len=20,
                            prev_side=None, source="fake", strategy="qqe")
    assert event is None and side == "DOWN"            # baseline = final side

    event, side = evaluate(candles, symbol="NIFTY1!", ema_len=20,
                            prev_side="UP", source="fake", strategy="qqe")
    assert event is not None and event.side == "SELL"
    assert event.strategy == "qqe"
    assert "QQE flipped SHORT" in event.rule and "RSI scale" in event.rule
    assert "QQE signals" in event.message()


def test_evaluate_qqe_needs_warmup_bars():
    with pytest.raises(SignalError, match="need >= "):
        evaluate(make_candles(WAVE[:40]), symbol="X", ema_len=20,
                 prev_side=None, source="fake", strategy="qqe")


def test_ema20_mode_unchanged_by_qqe_port():
    from nse_alerts.signals import side_series
    closes = pd.Series([100 - 0.3 * i for i in range(60)])
    assert side_series(closes, 20).iloc[-1] == -1      # original rule untouched
    assert MIN_QQE_BARS == 72
