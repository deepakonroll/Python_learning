"""Magic Envelope port: band math, break-side machine, evaluate() integration."""

import pandas as pd
import pytest

from nse_alerts.envelope import envelope_lines, envelope_side
from nse_alerts.signals import STRATEGY_LABELS, SignalError, evaluate
from tests.conftest import make_candles


def test_bands_are_percent_around_basis():
    c = make_candles([100.0 + i for i in range(30)])["Close"]
    basis, upper, lower = envelope_lines(c, length=5, percent=0.2)
    valid = basis.dropna()
    assert (upper.loc[valid.index] / valid - 1.002).abs().max() < 1e-12
    assert (lower.loc[valid.index] / valid - 0.998).abs().max() < 1e-12


def test_golden_side_flips_at_break_bars():
    """Hand-computed: length=3, percent=1.0, flat bars (H=L=C)."""
    cdf = make_candles([100, 110, 120, 130, 80, 70, 60])
    sides = envelope_side(cdf["Close"], cdf["High"], cdf["Low"],
                          length=3, percent=1.0)
    got = list(sides)
    assert pd.isna(got[0]) and pd.isna(got[1])   # no basis yet -> no side
    assert got[2:] == [1.0, 1.0, -1.0, -1.0, -1.0]   # break bars set, then carry


def test_flat_market_never_breaks_no_side():
    cdf = make_candles([100.0] * 40)
    sides = envelope_side(cdf["Close"], cdf["High"], cdf["Low"],
                          length=5, percent=0.2)
    assert sides.isna().all()


def test_exponential_basis_has_no_warmup_gap():
    c = make_candles([100.0 + i for i in range(30)])["Close"]
    basis, upper, lower = envelope_lines(c, length=5, percent=0.2,
                                         exponential=True)
    assert basis.notna().all()
    assert (upper > lower).all()


def test_evaluate_env_emits_alert_with_label():
    cdf = make_candles([100, 110, 120, 130, 80, 70, 60])
    event, side = evaluate(cdf, symbol="T", ema_len=20, prev_side="UP",
                           source="test", strategy="env",
                           envelope_len=3, envelope_percent=1.0)
    assert side == "DOWN"
    assert event is not None and event.side == "SELL"
    text = event.message()
    assert "Env flipped SHORT" in text
    assert "Magic Envelope" in text
    assert "±1.0%" in text
    assert STRATEGY_LABELS["env"] == "Magic Envelope"


def test_evaluate_env_never_broken_raises():
    cdf = make_candles([100.0] * 40)
    with pytest.raises(SignalError):
        evaluate(cdf, symbol="T", ema_len=20, prev_side=None, source="test",
                 strategy="env", envelope_len=5, envelope_percent=0.2)


def test_prep_flags_pending_flip_only():
    from nse_alerts.envelope import envelope_prep

    cdf = make_candles([100, 110, 120, 130, 80, 70, 60])
    closes = cdf["Close"]
    _basis, upper, lower = envelope_lines(closes, 3, 1.0)
    up, lo = float(upper.iloc[-1]), float(lower.iloc[-1])
    # live pierce above the upper band while side is DOWN -> pending BLUE
    assert envelope_prep(closes, up * 1.02, "DOWN", 3, 1.0) == ("UP", pytest.approx(up))
    # live break below the lower band while side is UP -> pending RED
    assert envelope_prep(closes, lo * 0.98, "UP", 3, 1.0) == ("DOWN", pytest.approx(lo))
    # inside the band - nothing pending
    assert envelope_prep(closes, (up + lo) / 2, "DOWN", 3, 1.0) is None
    # beyond the band but ALREADY on that side = continuation, silent
    assert envelope_prep(closes, up * 1.02, "UP", 3, 1.0) is None
    assert envelope_prep(closes, lo * 0.98, "DOWN", 3, 1.0) is None
    # warm-up: band is NaN -> nothing to watch
    assert envelope_prep(pd.Series([100.0] * 3), 101.0, "DOWN",
                         length=40, percent=0.2) is None


def test_prep_message_reads_like_the_alert():
    from datetime import datetime

    from nse_alerts.signals import prep_message

    text = prep_message("MCX:CRUDEOIL", "UP", 99.10, 99.05, 0.2,
                        datetime(2026, 10, 7, 21, 10), "tv", "DOWN")
    assert "⚠️ ENV pre-flip · MCX:CRUDEOIL" in text
    assert "live 99.10 > upper 99.05" in text
    assert "BLUE flip PENDING · side DOWN" in text
    assert "07 Oct 21:10 IST (forming)" in text
    assert "src=tv" in text and "CLOSES fully >" in text