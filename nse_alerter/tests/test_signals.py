"""The core rule: 5m close vs EMA20, alert only on a side flip."""

import pandas as pd
import pytest

from nse_alerts.signals import (
    SignalError,
    ema,
    evaluate,
    side_series,
)
from tests.conftest import falling, make_candles, rising


def test_ema_matches_pandas_reference():
    closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    pd.testing.assert_series_equal(ema(closes, 3), closes.ewm(span=3, adjust=False).mean())


def test_side_is_down_in_a_decline(candles_down):
    assert side_series(candles_down["Close"], 20).iloc[-1] == -1


def test_side_is_up_in_a_rally(candles_up):
    assert side_series(candles_up["Close"], 20).iloc[-1] == 1


def test_baseline_when_no_previous_side(candles_down):
    event, side = evaluate(candles_down, symbol="NIFTY1!", ema_len=20,
                            prev_side=None, source="fake")
    assert event is None and side == "DOWN"


def test_same_side_is_silent(candles_down):
    event, side = evaluate(candles_down, symbol="NIFTY1!", ema_len=20,
                            prev_side="DOWN", source="fake")
    assert event is None and side == "DOWN"


def test_below_to_above_cross_fires_buy():
    candles = make_candles(falling(60) + rising(60))
    event, side = evaluate(candles, symbol="NIFTY1!", ema_len=20,
                            prev_side="DOWN", source="tv")
    assert side == "UP"
    assert event is not None and event.side == "BUY"
    assert event.prev_side == "DOWN"
    assert event.close > event.ema                      # that's what BUY means
    assert event.source == "tv"
    assert event.bar_time == candles.index[-1].to_pydatetime()
    assert "BUY" in event.message() and "EMA20" in event.message()


def test_above_to_below_cross_fires_sell(candles_down):
    event, side = evaluate(candles_down, symbol="NIFTY1!", ema_len=20,
                            prev_side="UP", source="tv")
    assert side == "DOWN"
    assert event is not None and event.side == "SELL"
    assert event.close < event.ema


def test_exact_tie_carries_previous_side():
    closes = falling(60)
    candles = make_candles(closes)
    # append a bar whose close is mathematically identical to its EMA20:
    # ema_t = ema_(t-1)*(1-a) + close_t*a  ->  choosing close_t = ema_(t-1) keeps ema flat
    level = float(ema(candles["Close"], 20).iloc[-1])
    tied = make_candles(closes + [level])
    event, side = evaluate(tied, symbol="NIFTY1!", ema_len=20,
                            prev_side="DOWN", source="fake")
    assert side == "DOWN" and event is None             # tie never flips a signal


def test_walking_the_series_fires_exactly_one_buy_then_one_sell():
    candles = make_candles(falling(60) + rising(60) + falling(60))
    events, side = [], None
    for i in range(30, len(candles)):
        event, side = evaluate(candles.iloc[:i + 1], symbol="X", ema_len=20,
                               prev_side=side, source="fake")
        if event:
            events.append(event.side)
    assert events == ["BUY", "SELL"]                    # one flip each way, no spam


def test_insufficient_data_raises():
    with pytest.raises(SignalError):
        evaluate(make_candles([100.0, 101.0]), symbol="X", ema_len=20,
                 prev_side=None, source="fake")
