"""The core rule: 5m close vs EMA20, alert only on a side flip."""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from nse_alerts.signals import (
    CrossEvent,
    SignalError,
    ema,
    evaluate,
    prep_flip,
    prep_flip_message,
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


def test_prep_flip_ema20_pending_up_when_live_crosses():
    """A live bar already above the completed-bar EMA20 warns a BUY is
    pending (even though the full bar hasn't closed yet)."""
    candles = make_candles([100.0] * 40 + [99.5] * 20)   # completed side DOWN
    level = f"{ema(candles['Close'], 20).iloc[-1]:,.2f}"
    assert prep_flip(candles, 100.4, "DOWN", "ema20", ema_len=20) \
        == ("UP", f"live 100.40 > EMA20 {level} + 0.02% buffer")
    # same live price but still on the current side -> not a flip
    assert prep_flip(candles, 100.4, "UP", "ema20", ema_len=20) is None
    # live still below the EMA -> nothing pending
    assert prep_flip(candles, 99.2, "DOWN", "ema20", ema_len=20) is None


def test_prep_flip_both_requires_engines_to_agree():
    # series ends side DOWN (close 99.9 < EMA20 99.91), QQE also DOWN
    candles = make_candles([100.0] * 40 + [99.9] * 20)
    # a live close back above the EMA flips BOTH engines UP -> preview fires
    got = prep_flip(candles, 100.2, "DOWN", "both", ema_len=20,
                    rsi_period=14, sf=5, factor=4.238)
    assert got is not None and got[0] == "UP"
    assert "live 100.20 > EMA20" in got[1] and "QQE gap" in got[1]
    # live still below the EMA -> EMA engine has nothing pending, so the
    # consensus preview must NOT fire (both engines must agree)
    assert prep_flip(candles, 99.5, "DOWN", "both", ema_len=20,
                     rsi_period=14, sf=5, factor=4.238) is None


def test_ema_prep_requires_configured_clearance():
    candles = make_candles([100.0] * 40 + [99.5] * 20)
    level = float(ema(candles["Close"], 20).iloc[-1])
    assert prep_flip(candles, level + 0.01, "DOWN", "ema20") is None
    got = prep_flip(candles, level * 1.0003, "DOWN", "ema20")
    assert got is not None and got[0] == "UP"
    assert "+ 0.02% buffer" in got[1]


def test_qqe_prep_requires_gap_buffer(monkeypatch):
    import nse_alerts.signals as signals

    candles = make_candles([100.0] * 40)
    monkeypatch.setattr(signals, "qqe_side",
                        lambda *args: pd.Series([-1, 1]))
    lines = {"FastAtrRsiTL": pd.Series([50.0]),
             "RSIndex": pd.Series([50.5])}
    monkeypatch.setattr(signals, "qqe_lines", lambda *args: pd.DataFrame(lines))
    assert prep_flip(candles, 100.0, "DOWN", "qqe") is None
    lines["RSIndex"] = pd.Series([51.5])
    got = prep_flip(candles, 100.0, "DOWN", "qqe")
    assert got is not None and got[0] == "UP"
    assert "QQE gap 1.5 RSI pts (min 1)" in got[1]


def test_prep_flip_message_mentions_pending_and_confirmation_rule():
    from nse_alerts.signals import prep_flip_message
    bar = datetime(2026, 9, 28, 11, 30)
    text = prep_flip_message("NIFTY1!", "UP", 23000.0,
                             "live 23000.00 > EMA20 22990.00",
                             bar, "tv", "DOWN", "ema20")
    assert text.startswith("⚠️ EMA20 cross flip PENDING")
    assert "23000.00" in text and "confirms only if the bar CLOSES" in text
    assert "11:30 IST (forming)" in text


def test_message_stamps_bar_age_when_late():
    bar = datetime(2026, 9, 28, 10, 5)                   # 10:05 -> 10:10 close
    ev = CrossEvent(symbol="NIFTY1!", side="SELL", prev_side="UP",
                    bar_time=bar, close=22992.6, ema=23004.39,
                    source="tv", strategy="ema20")
    now = bar + timedelta(minutes=20)                      # sent 10:25
    text = ev.message(now=now, interval_minutes=5)
    assert "bar 28 Sep 10:05 IST - src=tv" in text
    assert "bar closed 10:10" in text and "sent 10:25" in text
    assert "3 bar(s) late" in text                          # 10:25 - 10:10 = 3
    # without `now` the footer stays the old terse form (replay/dry-run)
    assert "sent" not in ev.message()


def test_message_same_bar_no_lag():
    bar = datetime(2026, 9, 28, 10, 5)
    ev = CrossEvent(symbol="NIFTY1!", side="SELL", prev_side="UP",
                    bar_time=bar, close=22992.6, ema=23004.39,
                    source="tv", strategy="ema20")
    text = ev.message(now=bar + timedelta(minutes=5), interval_minutes=5)
    assert "just closed" in text and "bar(s) late" not in text
