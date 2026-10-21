"""09:05 world-markets update: pure formatter + best-effort fetchers + runner.

The formatter runs fully offline (synthetic last/prev closes, canned FII/DII
JSON). The runner path stubs the three network fetchers, so no test touches
Yahoo/TradingView/GitHub.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from nse_alerts import app, world
from nse_alerts.market_hours import IST
from nse_alerts.providers.base import ProviderError
from tests.conftest import make_config, make_candles, falling


# the exact percentages from the owner's sample card: 0.08/0.04/1.07/0.78/
# 0.33/1.35/0.88%
def up_rows() -> dict[str, tuple[float, float]]:
    return {"^IXIC": (10008.0, 10000.0), "YM=F": (50020.0, 50000.0),
            "^FTSE": (10107.0, 10000.0), "^FCHI": (10078.0, 10000.0),
            "^GDAXI": (10033.0, 10000.0), "^N225": (10135.0, 10000.0),
            "^HSI": (10088.0, 10000.0)}


def down_rows() -> dict[str, tuple[float, float]]:
    return {sym: (prev * 0.997, prev) for sym, (_, prev) in up_rows().items()}


GIFT_UP = (23084.0, 22988.0)          # +96 points
FII_DII = {"date": "2026-10-15", "fii": -5285.87, "dii": 3319.39}


# ------------------------------------------------------------- formatter

def test_message_full_green_day_matches_sample():
    msg = world.build_message(up_rows(), gift=GIFT_UP, fiidii=FII_DII)
    assert msg is not None
    assert "Good Morning Deepak" in msg
    assert f"• {'Nasdaq':<10} 🟢 +0.08%" in msg
    assert f"• {'Dow Fut':<10} 🟢 +0.04%" in msg
    assert f"• {'FTSE 100':<10} 🟢 +1.07%" in msg
    assert f"• {'CAC 40':<10} 🟢 +0.78%" in msg
    assert f"• {'DAX':<10} 🟢 +0.33%" in msg
    assert f"• {'Nikkei 225':<10} 🟢 +1.35%" in msg
    assert f"• {'Hang Seng':<10} 🟢 +0.88%" in msg
    assert "GIFT Nifty" in msg and "23,084" in msg and "🟢 +96 pts" in msg
    assert "FII/DII · 15 Oct" in msg
    assert "FII  🔴 net SOLD -₹5,286 cr" in msg
    assert "DII  🟢 net BOUGHT +₹3,319 cr" in msg


def test_message_red_day_flips_signs_and_flow_colors():
    msg = world.build_message(down_rows(), gift=(23000.0, 23100.0),
                              fiidii={"date": "2026-10-15", "fii": 500.0,
                                      "dii": -300.0})
    assert msg is not None
    assert f"• {'Nasdaq':<10} 🔴 -0.30%" in msg
    assert "GIFT Nifty" in msg and "🔴 -100 pts" in msg
    assert "FII  🟢 net BOUGHT +₹500 cr" in msg
    assert "DII  🔴 net SOLD -₹300 cr" in msg


def test_message_flat_row_uses_neutral_grey():
    rows = {"^IXIC": (10000.0, 10000.0)}
    msg = world.build_message(rows)
    assert msg is not None and "⚪ +0.00%" in msg


def test_message_omits_missing_gift_and_fiidii_blocks():
    msg = world.build_message(up_rows())
    assert msg is not None
    assert "GIFT" not in msg and "FII/DII" not in msg


def test_message_omits_single_dead_ticker_keeps_rest():
    rows = dict(up_rows())
    del rows["^FTSE"]
    msg = world.build_message(rows)
    assert msg is not None
    assert "Nasdaq" in msg and "FTSE" not in msg


def test_message_none_when_no_yahoo_rows_at_all():
    assert world.build_message({}) is None


def test_message_fractional_gift_level_and_points():
    msg = world.build_message(up_rows(), gift=(23084.5, 22988.0))
    assert msg is not None
    assert "23,084.5" in msg and "+96.5 pts" in msg


# ------------------------------------------------------------- fetch parsers

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_fii_dii_parses_equity_net_flows(monkeypatch):
    monkeypatch.setattr(
        world.requests, "get",
        lambda *a, **k: _FakeResp({"date": "2026-10-15", "source": "groww",
                                   "equity": {"fii_net": -5285.87,
                                              "dii_net": 3319.39},
                                   "derivative": {}}))
    assert world.fetch_fii_dii() == {"date": "2026-10-15",
                                     "fii": -5285.87, "dii": 3319.39}


def test_fetch_fii_dii_returns_none_on_error_or_bad_shape(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net down")
    monkeypatch.setattr(world.requests, "get", boom)
    assert world.fetch_fii_dii() is None

    monkeypatch.setattr(world.requests, "get",
                        lambda *a, **k: _FakeResp({"equity": {}}))
    assert world.fetch_fii_dii() is None
# ------------------------------------------------------------- runner

class _Recorder:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []

    def __call__(self, token, chat_id, text, **kw):
        self.messages.append((token, chat_id, text))


class _FakeProvider:
    name = "fake"

    def fetch(self, symbol, interval, lookback):
        raise ProviderError("should not be reached on card/world ticks")


@pytest.fixture
def rig(monkeypatch, tmp_path):
    sent = _Recorder()
    from nse_alerts import control
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])
    monkeypatch.setattr(app, "send_telegram", sent)
    monkeypatch.setattr(app, "build_providers",
                        lambda cfg, watch=None: [_FakeProvider()])
    monkeypatch.setattr(world, "fetch_yahoo_rows", up_rows)
    monkeypatch.setattr(world, "fetch_gift", lambda: GIFT_UP)
    monkeypatch.setattr(world, "fetch_fii_dii", lambda: dict(FII_DII))
    cfg = make_config(tmp_path)
    return cfg, sent


def test_world_updates_on_prime_run_and_dedupes(rig, monkeypatch):
    """A 09:05 run sends the world snapshot directly (no 08:45 card anymore);
    re-running the same day does not resend."""
    cfg, sent = rig
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 9, 5, tzinfo=IST))  # Mon
    assert app.run(cfg) == 0
    assert len(sent.messages) == 1
    assert "World Markets Update" in sent.messages[-1][2]
    assert "🟢 +96 pts" in sent.messages[-1][2]

    n = len(sent.messages)
    assert app.run(cfg) == 0
    assert len(sent.messages) == n                         # dedupe world_date


def test_world_silent_at_0845_then_updates_at_0905(rig, monkeypatch):
    """Pre-open dispatches are silent now (the card retired with it: nothing
    fires before 09:02); the snapshot still lands on its 09:05 tick."""
    cfg, sent = rig
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 8, 46, tzinfo=IST))
    assert app.run(cfg) == 0
    assert not sent.messages                          # pre-open quiet window

    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 9, 5, tzinfo=IST))
    assert app.run(cfg) == 0
    assert len(sent.messages) == 1
    assert "World Markets Update" in sent.messages[-1][2]


def test_world_skipped_outside_window(rig, monkeypatch):
    """09:30 is past the snapshot window - the update must NOT fire (the flow
    just proceeds to normal session monitoring, heartbeat is fine)."""
    cfg, sent = rig
    class OkProvider:
        name = "fake"
        def fetch(self, symbol, interval, lookback):
            return make_candles(falling(26)).copy()
    monkeypatch.setattr(app, "build_providers",
                        lambda cfg, watch=None: [OkProvider()])
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 9, 30, tzinfo=IST))
    app.run(cfg)                                     # rc N/A: only 3 bars exist
                                                     # by 09:30 (warm-up needs 22)
    assert not any("World Markets" in text for _, _, text in sent.messages)


def test_world_nothing_sent_when_all_fetchers_dead(rig, monkeypatch):
    """All-Yahoo-failed -> no send, and world_date stays unset so a later
    tick with data still delivers (no silent gap)."""
    cfg, sent = rig
    monkeypatch.setattr(world, "fetch_yahoo_rows", lambda: {})
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 9, 5, tzinfo=IST))
    assert app.run(cfg) == 0
    assert not sent.messages

    monkeypatch.setattr(world, "fetch_yahoo_rows", up_rows)   # feed recovers
    assert app.run(cfg) == 0
    assert "World Markets Update" in sent.messages[-1][2]