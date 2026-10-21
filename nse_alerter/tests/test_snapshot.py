"""09:20 opening-levels snapshot: pure formatter + runner.

build_message() runs fully offline on synthetic levels; the runner stubs the
three fetch functions so no test touches Yahoo/TradingView.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from nse_alerts import app, snapshot
from nse_alerts.expiry import (DEFAULT_IV, ENTRY_SESSIONS, bs_price,
                               atm_and_otm)
from nse_alerts.market_hours import IST
from nse_alerts.providers.base import ProviderError
from tests.conftest import make_config

MONDAY = datetime(2026, 9, 28, 9, 20, tzinfo=IST)   # Mon - NIFTY 1.92 sess


def levels_full() -> dict:
    return {"index": {"NIFTY": (23089.0, 23346.0),     # -257 pts, -1.10%
                      "SENSEX": (76500.0, 76100.0)},   # +400 pts, +0.53%
            "vix": (13.5, 13.2),                       # +0.3, +2.27%
            "gift": (23024.0, 22928.0)}                # +96 pts overnight


def _straddle(instrument, spot, iv, weekday):
    step = {"NIFTY": 50, "SENSEX": 100}[instrument]
    strike = atm_and_otm(spot, step)[0]
    t = snapshot.SESSIONS_LEFT[instrument][weekday] / snapshot.TRADING_DAYS
    ce = bs_price(spot, strike, t, iv, "CE")
    pe = bs_price(spot, strike, t, iv, "PE")
    credit = ce + pe
    return (f"🧶 {instrument} ATM STRADDLE · strike {strike:,}",
            f"• CE ₹{ce:,.0f} · PE ₹{pe:,.0f} · total ₹{credit:,.0f} "
            f"(~{credit / spot * 100:.1f}% of spot)")


# ------------------------------------------------------------- formatter

def test_message_full_snapshot_with_straddles():
    msg = snapshot.build_message(levels_full(), MONDAY)
    assert msg is not None
    assert "📊 OPENING LEVELS · Mon 28 Sep · 09:20 · exp Tue · 1.9 sess" in msg
    assert "23,089" in msg and "🔴 -257 (-1.10%)" in msg
    assert "13.5" in msg and "🟢 +0.3 (+2.27%)" in msg
    assert "23,024" in msg and "🟢 +96 pts (overnight)" in msg
    assert "76,500" in msg and "🟢 +400 (+0.53%)" in msg
    nifty = _straddle("NIFTY", 23089.0, 0.135, 0)
    sensex = _straddle("SENSEX", 76500.0, 0.135, 0)
    assert nifty[0] in msg and nifty[1] in msg
    assert sensex[0] in msg and sensex[1] in msg
    assert "🛡️ GUARD RAILS" in msg
    assert "−₹7k → WARN · −₹10k → EXIT, session over" in msg
    assert "flat ALL by 15:15" in msg and msg.rstrip().endswith("15:15")


def test_message_red_day_and_nifty_strike_grid():
    levels = levels_full()
    levels["index"]["NIFTY"] = (23089.0, 23346.0)     # down
    levels["index"]["SENSEX"] = (75820.0, 76100.0)    # down: -280
    msg = snapshot.build_message(levels, MONDAY)
    assert msg is not None
    assert "🔴 -257 (-1.10%)" in msg
    assert "SENSEX ATM STRADDLE · strike 75,800" in msg   # round to nearest 100


def test_message_omits_missing_vix_gift_blocks():
    levels = levels_full()
    levels["vix"] = None
    levels["gift"] = None
    msg = snapshot.build_message(levels, MONDAY)
    assert msg is not None
    assert "GIFT" not in msg and "VIX" not in msg


def test_message_uses_default_vix_when_missing():
    levels = levels_full()
    levels["vix"] = None
    msg = snapshot.build_message(levels, MONDAY)
    nifty = _straddle("NIFTY", 23089.0, DEFAULT_IV / 100.0, 0)
    assert msg is not None
    assert nifty[1] in msg                          # priced on the 13% fallback


def test_message_sensex_only_when_nifty_feed_dead():
    levels = {"index": {"SENSEX": (76100.0, 75800.0)}, "vix": None, "gift": None}
    msg = snapshot.build_message(levels, MONDAY)
    assert msg is not None
    assert "NIFTY" not in msg
    assert "SENSEX ATM STRADDLE" in msg


def test_message_none_when_no_indian_index_at_all():
    assert snapshot.build_message({"index": {}}, MONDAY) is None


def test_sessions_left_consistent_with_entry_playbook():
    """Entry-day rows must match expiry.ENTRY_SESSIONS exactly (the 09:45
    alert and the 09:20 straddle must agree on T for the same day)."""
    for wd, (inst, sess) in ENTRY_SESSIONS.items():
        assert snapshot.SESSIONS_LEFT[inst][wd] == pytest.approx(sess)


def test_next_expiry_follows_playbook_rotation():
    """Mon/Tue -> NIFTY (exp Tue), Wed/Thu -> SENSEX (exp Thu), Fri -> NIFTY."""
    expect = {0: ("NIFTY", 1.92), 1: ("NIFTY", 0.92), 2: ("SENSEX", 1.92),
              3: ("SENSEX", 0.92), 4: ("NIFTY", 2.92)}
    for wd, (inst, sess) in expect.items():
        got_inst, got_sess = snapshot._next_expiry(wd)
        assert got_inst == inst and got_sess == pytest.approx(sess)


# ------------------------------------------------------------- runner

class _Recorder:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []

    def __call__(self, token, chat_id, text, **kw):
        self.messages.append((token, chat_id, text))


class _FakeProvider:
    name = "fake"

    def fetch(self, symbol, interval, lookback):
        raise ProviderError("should not be reached on snapshot ticks")


@pytest.fixture
def rig(monkeypatch, tmp_path):
    sent = _Recorder()
    from nse_alerts import control
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])
    monkeypatch.setattr(app, "send_telegram", sent)
    monkeypatch.setattr(app, "build_providers",
                        lambda cfg, watch=None: [_FakeProvider()])
    monkeypatch.setattr(snapshot, "fetch_levels", levels_full)
    cfg = make_config(tmp_path)
    return cfg, sent


def test_levels_sends_once_then_dedupes(rig, monkeypatch):
    cfg, sent = rig
    monkeypatch.setattr(app, "now_ist", lambda: MONDAY)
    assert app.run(cfg) == 0
    assert len(sent.messages) == 1
    text = sent.messages[0][2]
    assert "📊 OPENING LEVELS" in text and "NIFTY ATM STRADDLE" in text
    app.run(cfg)                                  # same-day rerun: gate already
    assert len(sent.messages) == 1                # consumed (levels_date dedupe)


def test_levels_silent_outside_window(rig, monkeypatch):
    cfg, sent = rig
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 9, 10, tzinfo=IST))
    app.run(cfg)                                      # before 09:17
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 9, 30, tzinfo=IST))
    app.run(cfg)                                      # after 09:26
    assert not any("OPENING LEVELS" in t for _, _, t in sent.messages)


def test_levels_nothing_sent_when_spots_dead_then_recovered(rig, monkeypatch):
    """All-spot-failed -> no send, levels_date stays unset, so a later tick
    with data still delivers (no silent gap)."""
    cfg, sent = rig
    monkeypatch.setattr(app, "now_ist", lambda: MONDAY)
    monkeypatch.setattr(snapshot, "fetch_levels", lambda: {"index": {}})
    assert app.run(cfg) == 0
    assert not sent.messages
    monkeypatch.setattr(snapshot, "fetch_levels", levels_full)   # feed recovers
    assert app.run(cfg) == 0
    assert "OPENING LEVELS" in sent.messages[-1][2]
    """Entry-day rows must match expiry.ENTRY_SESSIONS exactly (the 09:45
    alert and the 09:20 straddle must agree on T for the same day)."""
    for wd, (inst, sess) in ENTRY_SESSIONS.items():
        assert snapshot.SESSIONS_LEFT[inst][wd] == pytest.approx(sess)