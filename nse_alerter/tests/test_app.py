"""End-to-end flow with fakes: gate -> fetch -> cross -> dedupe -> send."""

from __future__ import annotations

import logging

import pytest

from nse_alerts import app
from nse_alerts.app import build_providers as real_build_providers  # pre-rig ref
from nse_alerts.providers.base import ProviderError
from nse_alerts.state import StateStore
from tests.conftest import MONDAY, SATURDAY, falling, make_candles, make_config, rising


def test_log_filter_redacts_telegram_bot_token(monkeypatch):
    token = "12345678:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    record = logging.LogRecord(
        "urllib3.connectionpool", logging.DEBUG, "test.py", 1,
        f"GET https://api.telegram.org/bot{token}/getUpdates", (), None)
    assert app._CredentialRedactionFilter().filter(record)
    assert token not in record.getMessage()
    assert "/bot[REDACTED]/getUpdates" in record.getMessage()


class FakeProvider:
    name = "fake"

    def __init__(self, df=None, error: str | None = None):
        self.df = df
        self.error = error
        self.calls = 0

    def fetch(self, symbol, interval, lookback):
        self.calls += 1
        if self.error:
            raise ProviderError(self.error)
        return self.df.copy()


class Recorder:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []
        self.markups: list[dict | None] = []

    def __call__(self, token, chat_id, text, **kw):
        self.messages.append((token, chat_id, text))
        self.markups.append(kw.get("reply_markup"))


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """Fake clock (Monday 13:30 IST), fake provider, recorded sends.

    Command polling defaults to 'no updates' so the whole suite stays offline.
    """
    sent = Recorder()
    provider = FakeProvider(make_candles(falling(26)))   # 09:15..11:20, all completed
    monkeypatch.setattr(app, "now_ist", lambda: MONDAY)
    monkeypatch.setattr(app, "build_providers",
                        lambda cfg, watch=None: [provider])
    monkeypatch.setattr(app, "send_telegram", sent)
    from nse_alerts import control
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])
    cfg = make_config(tmp_path)
    return cfg, provider, sent


def test_first_run_records_baseline_and_sends_liveness_heartbeat(rig):
    cfg, provider, sent = rig
    assert app.run(cfg) == 0
    plain = [m for m in sent.messages
             if not m[2].startswith(("⚔️", "🔴"))]         # tripwires filtered
    assert len(plain) == 1                          # heartbeat, NOT an alert
    text = plain[0][2]
    assert "monitoring live" in text and "side=DOWN" in text
    assert "BUY" not in text and "SELL" not in text
    assert sent.markups[0] is None                          # menu buttons paused
    state = StateStore(cfg.state_file).get(cfg.symbol)
    assert state is not None and state.last_side == "DOWN"
    assert state.last_seen_date == "2026-09-28"


def test_heartbeat_fires_only_once_per_trading_day(rig, monkeypatch):
    from datetime import datetime
    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    assert app.run(cfg) == 0                                # day 1 -> heartbeat
    assert app.run(cfg) == 0                                # same day -> silent
    plain = [t for _, _, t in sent.messages
             if not t.startswith(("⚔️", "🔴"))]             # tripwires filtered
    assert len(plain) == 1

    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 29, 13, 30, tzinfo=IST))  # Tuesday
    assert app.run(cfg) == 0                                # new day -> heartbeat
    plain = [t for _, _, t in sent.messages
             if not t.startswith(("⚔️", "🔴"))]
    assert len(plain) == 2
    assert "29 Sep 2026" in plain[1]


def test_cross_sends_exactly_once_then_dedupes(rig):
    cfg, provider, sent = rig
    provider.df = make_candles([100.0] * 10 + [99.5] * 30)  # <0.8%: calm
    assert app.run(cfg) == 0                             # baseline: DOWN + heartbeat
    plain = [m for m in sent.messages
             if not m[2].startswith(("⚔️", "🔴"))]         # tripwires filtered
    assert len(plain) == 1
    assert "monitoring live" in plain[0][2]

    provider.df = make_candles([100.0] * 10 + [99.5] * 30 + [100.7] * 12)  # UP
    assert app.run(cfg) == 0
    plain = [m for m in sent.messages if not m[2].startswith(("⚔️", "🔴"))]
    assert len(plain) == 2                            # heartbeat + BUY
    token, chat_id, text = plain[1]
    assert token == "test-token" and chat_id == "42"
    assert "BUY" in text and "EMA20" in text

    assert app.run(cfg) == 0                             # same data again
    plain = [m for m in sent.messages if not m[2].startswith(("⚔️", "🔴"))]
    assert len(plain) == 2                            # still just the two
    state = StateStore(cfg.state_file).get(cfg.symbol)
    assert state.last_side == "UP"
    assert state.last_event_side == "BUY"


def test_confirmed_flip_marks_the_earlier_prep(rig):
    from dataclasses import replace

    cfg, provider, sent = rig
    cfg = replace(cfg, strategy="ema20")
    provider.df = make_candles([100.0] * 10 + [99.5] * 42)
    assert app.run(cfg) == 0
    state = StateStore(cfg.state_file).get(cfg.symbol)
    state.prep_fired = True
    state.prep_bar = "2026-09-28T13:30:00"
    StateStore(cfg.state_file).put(cfg.symbol, state)

    provider.df = make_candles([100.0] * 10 + [99.5] * 30 + [100.7] * 12)
    assert app.run(cfg) == 0
    flip = next(text for _, _, text in sent.messages if text.startswith("BUY "))
    assert "EMA20 cross · pre-flip confirmed" in flip
    state = StateStore(cfg.state_file).get(cfg.symbol)
    assert state.prep_fired is False and state.prep_bar == ""


def test_dry_run_never_sends_or_writes_state(rig):
    cfg, provider, sent = rig
    assert app.run(cfg) == 0                             # baseline (heartbeat sent)
    sent_count = len(sent.messages)
    before = cfg.state_file.read_text(encoding="utf-8")

    provider.df = make_candles(falling(26) + rising(26))  # would cross
    assert app.run(cfg, dry_run=True) == 0
    assert len(sent.messages) == sent_count                 # dry-run: zero new sends
    assert cfg.state_file.read_text(encoding="utf-8") == before


def test_missing_telegram_credentials_fail_fast_before_fetch(rig):
    cfg, provider, sent = rig
    cfg = make_config(cfg.state_file.parent, telegram_token=None, telegram_chat_id=None)
    assert app.run(cfg) == 1
    assert provider.calls == 0                           # never touched the feed
    assert sent.messages == []


def test_outside_session_is_a_noop(rig, monkeypatch):
    cfg, provider, _ = rig
    monkeypatch.setattr(app, "now_ist", lambda: SATURDAY)
    assert app.run(cfg) == 0
    assert provider.calls == 0


def test_holiday_is_a_noop(rig, monkeypatch):
    from dataclasses import replace
    cfg, provider, _ = rig
    cfg = replace(cfg, holidays=frozenset({MONDAY.date()}))
    assert app.run(cfg) == 0
    assert provider.calls == 0


def test_all_providers_down_skips_run(rig):
    cfg, provider, _ = rig
    provider.error = "network down"
    assert app.run(cfg) == 0


def test_test_notify_works_outside_session(rig, monkeypatch):
    cfg, provider, sent = rig
    monkeypatch.setattr(app, "now_ist", lambda: SATURDAY)
    assert app.run(cfg, test_notify=True) == 0
    assert len(sent.messages) == 1
    assert "test" in sent.messages[0][2].lower()
    assert provider.calls == 0                           # no market data needed


def test_replay_reports_crosses_without_sending_or_writing(rig):
    import pandas as pd

    from tests.conftest import falling, make_candles, rising

    cfg, provider, sent = rig
    # Friday's session: prev day declines (enters DOWN), Friday rallies -> one BUY
    provider.df = pd.concat([
        make_candles(falling(60), start="2026-09-24 09:15"),
        make_candles(rising(60), start="2026-09-25 09:15"),
    ])
    events = app.replay(cfg, __import__("datetime").date(2026, 9, 25))
    assert [e.side for e in events] == ["BUY"]
    assert events[0].bar_time.date().isoformat() == "2026-09-25"
    assert sent.messages == []                           # replay never notifies
    assert not cfg.state_file.exists()                   # replay never writes state


def test_replay_obeys_custom_watch_window_and_first_scan_bar(rig):
    from dataclasses import replace
    import pandas as pd

    from nse_alerts.config import Watch
    from nse_alerts.market_hours import parse_time_window
    from tests.conftest import falling, make_candles

    cfg, provider, sent = rig
    watch = Watch(key="MCX:CRUDEOIL", label="CRUDEOIL", exchange="MCX",
                  tv_symbol="MCX:CRUDEOIL", yahoo_symbol="BZ=F",
                  strategy="ema20", session=parse_time_window("17:00-18:00"))
    cfg = replace(cfg, watches=(watch,), strategy="ema20")

    prior = make_candles(falling(108, start=200, step=0.2),
                         start="2026-09-28 09:00")
    target_closes = (
        [100.0 - 0.5 * i for i in range(20)]
        + [90.0 + 4.0 * i for i in range(30)]
        + [210.0 - 4.0 * i for i in range(20)]
        + [130.0 + 5.0 * i for i in range(38)]
    )
    target = make_candles(target_closes, start="2026-09-29 09:00")
    provider.df = pd.concat([prior, target])

    events = app.replay(cfg, __import__("datetime").date(2026, 9, 29))

    assert [(event.side, event.bar_time.strftime("%H:%M")) for event in events] \
        == [("BUY", "16:55")]
    assert sent.messages == []
    assert not cfg.state_file.exists()


def test_replay_on_day_without_bars_returns_empty(rig):
    import datetime as dt

    cfg, provider, _ = rig
    provider.df = make_candles(falling(60), start="2026-09-24 09:15")
    assert app.replay(cfg, dt.date(2026, 9, 26)) == []  # Saturday: no bars


# ── Telegram commands: /disable /enable /status (mobile control) ────────


def _updates(text: str, chat: int = 42):
    return [{"message": {"chat": {"id": chat}, "text": text}}]


def test_disable_stops_fetching_and_enable_resumes(rig, monkeypatch):
    from nse_alerts import control
    from nse_alerts.state import StateStore

    cfg, provider, _ = rig
    replies: list[str] = []
    monkeypatch.setattr(control, "send_telegram",
                        lambda token, chat_id, text, **kw: replies.append(text))

    assert app.run(cfg) == 0                                # baseline (fetch #1)
    assert provider.calls == 1

    monkeypatch.setattr(control, "fetch_updates",
                        lambda token: _updates("/disable"))
    assert app.run(cfg) == 0
    assert provider.calls == 1                              # no market fetch
    assert StateStore(cfg.state_file).get_control()["enabled"] is False
    assert any("/disable" in r for r in replies)            # explicit acknowledgement

    monkeypatch.setattr(control, "fetch_updates", lambda token: [])
    assert app.run(cfg) == 0                                # stays off silently
    assert provider.calls == 1

    monkeypatch.setattr(control, "fetch_updates",
                        lambda token: _updates("/enable"))
    assert app.run(cfg) == 0
    assert provider.calls == 2                              # evaluated right away
    assert StateStore(cfg.state_file).get_control()["enabled"] is True
    assert any("/enable" in r for r in replies)             # explicit acknowledgement


def test_status_replies_but_keeps_running(rig, monkeypatch):
    from nse_alerts import control

    cfg, provider, _ = rig
    assert app.run(cfg) == 0
    replies: list[str] = []
    monkeypatch.setattr(control, "send_telegram",
                        lambda token, chat_id, text, **kw: replies.append(text))
    monkeypatch.setattr(control, "fetch_updates",
                        lambda token: _updates("/status"))
    assert app.run(cfg) == 0
    assert provider.calls == 2                              # still evaluated
    assert replies and "Alerts" in replies[0]


def test_command_from_another_chat_is_ignored(rig, monkeypatch):
    from nse_alerts import control

    cfg, provider, _ = rig
    assert app.run(cfg) == 0
    monkeypatch.setattr(control, "fetch_updates",
                        lambda token: _updates("/disable", chat=999))
    assert app.run(cfg) == 0
    assert provider.calls == 2                              # not owner -> ignored


def test_dry_run_never_polls_updates(rig, monkeypatch):
    from nse_alerts import control

    cfg, provider, _ = rig

    def boom(token):                                        # pragma: no cover
        raise AssertionError("dry-run must not consume updates")

    monkeypatch.setattr(control, "fetch_updates", boom)
    assert app.run(cfg, dry_run=True) == 0
    assert provider.calls == 1                              # fetch ok, no send


# ── Strategy toggle (STRATEGY=qqe | ema20 | both) ────────────────────────

WAVE_FALL = [23000 - 60 + i * 0.9 for i in range(75)]       # warm-up day


def test_qqe_strategy_uses_dedicated_state_key(rig):
    from tests.conftest import make_candles

    cfg, provider, sent = rig
    cfg = __import__("dataclasses").replace(cfg, strategy="qqe")
    provider.df = make_candles(WAVE_FALL, start="2026-09-26 09:15")
    assert app.run(cfg) == 0
    states = StateStore(cfg.state_file).load()
    assert "NIFTY1!#qqe" in states                          # dedicated baseline
    assert "NIFTY1!" not in states                          # ema key untouched
    assert len(sent.messages) == 1                          # one daily heartbeat
    assert "rule=qqe" in sent.messages[0][2]


def test_both_strategies_baseline_with_single_heartbeat(rig):
    from tests.conftest import make_candles

    cfg, provider, sent = rig
    cfg = __import__("dataclasses").replace(cfg, strategy="both")
    provider.df = make_candles(WAVE_FALL, start="2026-09-26 09:15")
    assert app.run(cfg) == 0
    states = StateStore(cfg.state_file).load()
    assert "NIFTY1!" in states and "NIFTY1!#qqe" in states   # both baselines
    assert len(sent.messages) == 1                          # heartbeat only for primary
    assert app.run(cfg) == 0                                # same day again
    assert len(sent.messages) == 1                          # still one


def test_default_crude_watch_alerts_qqe_and_ema_without_env(rig, monkeypatch):
    """Default SYMBOLS: NIFTY~qqe, crude~qqe, crude~both.
    Each crude flip produces TWO alerts: one QQE-only (~qqe watch) and
    one QQE+EMA20 consensus (~both watch).  Zero env artifacts."""
    from dataclasses import replace
    from datetime import datetime

    from nse_alerts.config import SYMBOLS_DEFAULT, parse_watches
    from nse_alerts.market_hours import IST
    from tests.conftest import make_candles, rising

    cfg, provider, sent = rig
    cfg = replace(cfg, watches=parse_watches(SYMBOLS_DEFAULT))

    def run_at(when, df):
        monkeypatch.setattr(app, "now_ist", lambda: when)
        provider.df = df
        assert app.run(cfg) == 0

    # crude window open -> silent baseline on a rising path (QQE needs 72+)
    run_at(datetime(2026, 9, 28, 18, 0, tzinfo=IST),
           make_candles(rising(150), start="2026-09-28 09:15", minutes=1))
    states = StateStore(cfg.state_file).load()
    assert "MCX:CRUDEOIL" in states                 # ema20 key (base, from ~both)
    assert "MCX:CRUDEOIL#qqe" in states             # dedicated QQE key (shared)
    assert "MCX:CRUDEOIL#both" in states            # consensus key
    assert "MCX:CRUDEOIL#env" not in states         # env engine never runs
    assert not [t for _, _, t in sent.messages
                if t.startswith(("BUY MCX", "SELL MCX")) or "pre-flip" in t]

    # same day: rise THEN crash - both engines flip DOWN, two SELL alerts fire:
    #   1) QQE-only from the ~qqe watch
    #   2) EMA20+QQE consensus from the ~both watch
    peak = [100.0 + 0.5 * i for i in range(80)]
    crash = [peak[-1] - 0.8 * i for i in range(1, 61)]
    run_at(datetime(2026, 9, 28, 18, 5, tzinfo=IST),
           make_candles(peak + crash, start="2026-09-28 12:00", minutes=1))
    crude = [t for _, _, t in sent.messages if "MCX:CRUDEOIL" in t]
    flips = [t for t in crude if t.startswith(("BUY MCX", "SELL MCX"))]
    assert len(flips) == 2                          # qqe-only + consensus
    qqe_only = [t for t in flips if "QQE signals" in t and "consensus" not in t]
    consensus = [t for t in flips if "EMA20+QQE consensus" in t]
    assert len(qqe_only) == 1
    assert len(consensus) == 1
    assert qqe_only[0].startswith("SELL MCX:CRUDEOIL")
    assert consensus[0].startswith("SELL MCX:CRUDEOIL")
    assert "EMA20 cross" in consensus[0]            # includes EMA20 detail
    assert "QQE signals" in consensus[0]            # includes QQE detail
    assert not [t for t in crude if "ENV pre-flip" in t
                or "Magic Envelope" in t]            # env fully off for crude


def test_both_consensus_no_alert_when_engines_disagree(rig, monkeypatch):
    """~both consensus mode: when engines disagree, no alert fires and
    the consensus side stays unchanged."""
    from dataclasses import replace
    from datetime import datetime

    from nse_alerts.config import Watch
    from nse_alerts.market_hours import IST, parse_time_window
    from nse_alerts.signals import evaluate
    from tests.conftest import make_candles, rising, falling

    cfg, provider, sent = rig
    # Build a ~both watch manually
    crude = Watch(key="MCX:CRUDEOIL", label="CRUDEOIL", exchange="MCX",
                  tv_symbol="MCX:CRUDEOIL", yahoo_symbol="BZ=F",
                  strategy="both",
                  session=parse_time_window("17:00-22:00"))
    cfg = replace(cfg, watches=(crude,), strategy="both")

    # First run: rising path -> both engines UP -> consensus baseline UP
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 18, 0, tzinfo=IST))
    provider.df = make_candles(rising(150), start="2026-09-28 09:15",
                               minutes=1)
    assert app.run(cfg) == 0
    states = StateStore(cfg.state_file).load()
    assert states["MCX:CRUDEOIL#both"].last_side == "UP"

    # Now feed a path where EMA20 flips DOWN but QQE stays UP.
    # We can't easily force QQE to stay UP while EMA20 goes DOWN with
    # synthetic data, so instead we verify the consensus key logic:
    # if both sides are stored as different, no alert fires.
    # We test this by directly manipulating state to simulate disagreement.
    raw = StateStore(cfg.state_file)._load_raw()
    raw["MCX:CRUDEOIL"]["last_side"] = "DOWN"     # ema20 flipped DOWN
    raw["MCX:CRUDEOIL#qqe"]["last_side"] = "UP"   # qqe still UP
    raw["MCX:CRUDEOIL#both"]["last_side"] = "UP"   # consensus was UP
    tmp = cfg.state_file.with_suffix(".tmp")
    tmp.write_text(__import__("json").dumps(raw, indent=2), encoding="utf-8")
    __import__("os").replace(tmp, cfg.state_file)

    # Run again with same data -> engines re-evaluate to their stored sides.
    # The consensus sees disagreement -> no alert, side stays UP.
    before_count = len([t for _, _, t in sent.messages
                        if t.startswith(("BUY MCX", "SELL MCX"))])
    assert app.run(cfg) == 0
    after_count = len([t for _, _, t in sent.messages
                       if t.startswith(("BUY MCX", "SELL MCX"))])
    assert after_count == before_count  # no new alert on disagreement
    states = StateStore(cfg.state_file).load()
    assert states["MCX:CRUDEOIL#both"].last_side == "UP"  # consensus unchanged


def test_consensus_send_failure_does_not_advance_state(rig, monkeypatch):
    from nse_alerts.notify import NotifyError
    from nse_alerts.state import SymbolState

    cfg, provider, _ = rig
    watch = cfg.watches[0]
    store = StateStore(cfg.state_file)
    previous = SymbolState(last_side="UP",
                           last_processed_bar="2026-09-28T11:15:00",
                           last_seen_date="2026-09-28")
    store.put("NIFTY1!#both", previous)
    provider.df = make_candles(falling(26))

    def fail_send(*args, **kwargs):
        raise NotifyError("temporary Telegram failure")

    monkeypatch.setattr(app, "send_telegram", fail_send)
    with pytest.raises(app._RetrySignal):
        app._consensus_decide(
            watch, ["ema20", "qqe"],
            {"ema20": "DOWN", "qqe": "DOWN"},
            {"ema20": None, "qqe": None}, provider.df, "fake",
            provider.df.index[-1].isoformat(), "2026-09-28", False,
            store, {}, cfg, previous, "UP", "NIFTY1!#both", True,
            now=MONDAY, forming=None)

    persisted = store.get("NIFTY1!#both")
    assert persisted is not None
    assert persisted.last_side == "UP"
    assert persisted.last_processed_bar == "2026-09-28T11:15:00"
    assert persisted.last_event_side == ""


def test_consensus_waits_for_first_agreement_instead_of_guessing_up(rig):
    cfg, provider, sent = rig
    watch = cfg.watches[0]
    store = StateStore(cfg.state_file)
    provider.df = make_candles(falling(26))
    bar_iso = provider.df.index[-1].isoformat()

    app._consensus_decide(
        watch, ["ema20", "qqe"], {"ema20": "DOWN", "qqe": "UP"},
        {"ema20": None, "qqe": None}, provider.df, "fake", bar_iso,
        "2026-09-28", False, store, {}, cfg, None, None,
        "NIFTY1!#both", True, now=MONDAY, forming=None)
    assert store.get("NIFTY1!#both") is None

    app._consensus_decide(
        watch, ["ema20", "qqe"], {"ema20": "DOWN", "qqe": "DOWN"},
        {"ema20": None, "qqe": None}, provider.df, "fake", bar_iso,
        "2026-09-28", False, store, {}, cfg, None, None,
        "NIFTY1!#both", True, now=MONDAY, forming=None)
    baseline = store.get("NIFTY1!#both")
    assert baseline is not None and baseline.last_side == "DOWN"
    assert not [text for _, _, text in sent.messages
                if text.startswith(("BUY NIFTY1!", "SELL NIFTY1!"))]


def test_both_consensus_alert_when_both_engines_flip_same_direction(rig, monkeypatch):
    """~both consensus: when both engines flip from UP->DOWN on the same
    bar, crude produces TWO alerts: one QQE-only (~qqe watch) and one
    EMA20+QQE consensus (~both watch)."""
    from dataclasses import replace
    from datetime import datetime

    from nse_alerts.config import SYMBOLS_DEFAULT, parse_watches
    from nse_alerts.market_hours import IST
    from tests.conftest import make_candles, rising

    cfg, provider, sent = rig
    cfg = replace(cfg, watches=parse_watches(SYMBOLS_DEFAULT))

    # Baseline: rising -> both UP
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 18, 0, tzinfo=IST))
    provider.df = make_candles(rising(150), start="2026-09-28 09:15",
                               minutes=1)
    assert app.run(cfg) == 0

    # Crash: both flip DOWN -> qqe-only alert + consensus SELL
    peak = [100.0 + 0.5 * i for i in range(80)]
    crash = [peak[-1] - 0.8 * i for i in range(1, 61)]
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 18, 5, tzinfo=IST))
    provider.df = make_candles(peak + crash, start="2026-09-28 12:00",
                               minutes=1)
    assert app.run(cfg) == 0

    crude_alerts = [t for _, _, t in sent.messages
                    if "MCX:CRUDEOIL" in t
                    and t.startswith(("BUY MCX", "SELL MCX"))]
    assert len(crude_alerts) == 2                   # qqe-only + consensus
    qqe_only = [t for t in crude_alerts if "QQE signals" in t and "consensus" not in t]
    consensus = [t for t in crude_alerts if "EMA20+QQE consensus" in t]
    assert len(qqe_only) == 1
    assert len(consensus) == 1
    assert qqe_only[0].startswith("SELL MCX:CRUDEOIL")
    assert consensus[0].startswith("SELL MCX:CRUDEOIL")
    assert "EMA20+QQE consensus" in consensus[0]

    # Verify state: consensus key recorded the event
    states = StateStore(cfg.state_file).load()
    both_state = states["MCX:CRUDEOIL#both"]
    assert both_state.last_side == "DOWN"
    assert both_state.last_event_side == "SELL"


def test_qqe_mode_ignores_ema_data_length_requirements(rig):
    from tests.conftest import make_candles

    cfg, provider, _ = rig
    cfg = __import__("dataclasses").replace(cfg, strategy="qqe")
    provider.df = make_candles(WAVE_FALL[:40])              # below QQE warm-up
    assert app.run(cfg) == 0                                # skipped, retries next run


def test_telegram_strategy_command_switches_this_run(rig, monkeypatch):
    from nse_alerts import control
    from tests.conftest import make_candles

    cfg, provider, sent = rig                               # cfg strategy = ema20
    provider.df = make_candles(WAVE_FALL, start="2026-09-26 09:15")
    replies: list[str] = []
    monkeypatch.setattr(control, "send_telegram",
                        lambda token, chat_id, text, **kw: replies.append(text))
    monkeypatch.setattr(control, "fetch_updates",
                        lambda token: [{"message": {"chat": {"id": 42},
                                                    "text": "/strategy qqe"}}])
    assert app.run(cfg) == 0
    assert any("applied" in r for r in replies)
    assert any("qqe" in r and "Telegram override" in r for r in replies)
    states = StateStore(cfg.state_file).load()
    assert "NIFTY1!#qqe" in states and "NIFTY1!" not in states  # switched THIS run
    assert any("rule=qqe" in text for _, _, text in sent.messages)  # heartbeat label


def test_control_override_beats_config_default(rig):
    from tests.conftest import make_candles

    cfg, provider, sent = rig                              # cfg strategy = ema20
    StateStore(cfg.state_file).set_control(
        {"enabled": True, "strategy": "qqe"})
    provider.df = make_candles(WAVE_FALL, start="2026-09-26 09:15")
    assert app.run(cfg) == 0
    states = StateStore(cfg.state_file).load()
    assert "NIFTY1!#qqe" in states                          # override applied
    assert "rule=qqe" in sent.messages[0][2]                # heartbeat labels it


def test_watches_gated_by_their_own_exchange_sessions(monkeypatch, tmp_path):
    """Monday 20:00 IST: NSE is closed but MCX energy trades - only crude runs."""
    from datetime import datetime

    from nse_alerts import control
    from nse_alerts.config import Watch
    from nse_alerts.market_hours import IST

    sent = Recorder()
    provider = FakeProvider(make_candles(falling(26)))
    nifty = Watch(key="NIFTY1!", label="NIFTY1!", exchange="NSE",
                  tv_symbol="NIFTY1!", yahoo_symbol="^NSEI")
    crude = Watch(key="MCX:CRUDEOIL", label="CRUDEOIL", exchange="MCX",
                  tv_symbol="MCX:CRUDEOIL", yahoo_symbol="BZ=F")
    cfg = make_config(tmp_path, watches=(nifty, crude))
    evening = datetime(2026, 9, 28, 20, 0, tzinfo=IST)
    monkeypatch.setattr(app, "now_ist", lambda: evening)
    monkeypatch.setattr(app, "build_providers", lambda cfg, watch=None: [provider])
    monkeypatch.setattr(app, "send_telegram", sent)
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])

    assert app.run(cfg) == 0
    assert provider.calls == 1                                # MCX watch only
    states = StateStore(cfg.state_file).load()
    assert "MCX:CRUDEOIL" in states
    assert "NIFTY1!" not in states                            # never fetched
    heartbeats = [t for _, _, t in sent.messages if "monitoring live" in t]
    assert len(heartbeats) == 1 and "MCX:CRUDEOIL" in heartbeats[0]


def test_both_sessions_open_evaluate_every_watch(rig):
    """Monday 13:30: both exchanges open -> fetch per watch, heartbeat per watch."""
    from dataclasses import replace

    from nse_alerts.config import Watch

    cfg, provider, sent = rig
    nifty = Watch(key="NIFTY1!", label="NIFTY1!", exchange="NSE",
                  tv_symbol="NIFTY1!", yahoo_symbol="^NSEI")
    crude = Watch(key="MCX:CRUDEOIL", label="CRUDEOIL", exchange="MCX",
                  tv_symbol="MCX:CRUDEOIL", yahoo_symbol="BZ=F")
    cfg = replace(cfg, watches=(nifty, crude))

    assert app.run(cfg) == 0
    assert provider.calls == 2                                # one fetch per watch
    states = StateStore(cfg.state_file).load()
    assert "NIFTY1!" in states and "MCX:CRUDEOIL" in states
    heartbeats = [t for _, _, t in sent.messages if "monitoring live" in t]
    assert len(heartbeats) == 2                               # once per watch/day


def test_status_lists_every_watch(rig, monkeypatch):
    from nse_alerts import control

    cfg, provider, sent = rig
    replies: list[str] = []
    monkeypatch.setattr(control, "send_telegram",
                        lambda token, chat_id, text, **kw: replies.append(text))
    monkeypatch.setattr(control, "fetch_updates",
                        lambda token: [{"message": {"chat": {"id": 42},
                                                    "text": "/status"}}])
    assert app.run(cfg) == 0
    assert replies and "🔭 Watching: NIFTY1!" in replies[0]


def test_status_hides_unwatched_symbols_stale_state(rig, monkeypatch):
    """NG removed from SYMBOLS -> its cached state must not appear in /status."""
    from nse_alerts import control
    from nse_alerts.state import SymbolState

    cfg, provider, sent = rig                                # watches only NIFTY1!
    store = StateStore(cfg.state_file)
    store.put("NIFTY1!", SymbolState(last_side="UP",
                                     last_processed_bar="2026-09-29T15:25:00"))
    store.put("MCX:NATURALGAS#qqe", SymbolState(last_side="DOWN",
                                                last_processed_bar="2026-09-29T23:20:00"))
    replies: list[str] = []
    monkeypatch.setattr(control, "send_telegram",
                        lambda token, chat_id, text, **kw: replies.append(text))
    monkeypatch.setattr(control, "fetch_updates",
                        lambda token: [{"message": {"chat": {"id": 42},
                                                    "text": "/status"}}])
    assert app.run(cfg) == 0
    assert replies and "NIFTY1!" in replies[0]
    assert "NATURALGAS" not in replies[0]                   # hidden as unwatched


def test_per_watch_strategy_suffix_runs_own_engine(rig):
    """'MCX:CRUDEOIL~env' runs envelope while NIFTY keeps the global engine."""
    from dataclasses import replace

    from nse_alerts.config import Watch

    cfg, provider, sent = rig                                # global strategy=ema20
    nifty = Watch(key="NIFTY1!", label="NIFTY1!", exchange="NSE",
                  tv_symbol="NIFTY1!", yahoo_symbol="^NSEI")
    crude = Watch(key="MCX:CRUDEOIL", label="CRUDEOIL", exchange="MCX",
                  tv_symbol="MCX:CRUDEOIL", yahoo_symbol="BZ=F",
                  strategy="env")
    cfg = replace(cfg, watches=(nifty, crude))

    assert app.run(cfg) == 0
    assert provider.calls == 2                                # one fetch per watch
    states = StateStore(cfg.state_file).load()
    assert "NIFTY1!" in states                # global ema20 -> base key
    assert "MCX:CRUDEOIL#env" in states       # suffix -> envelope key
    assert "MCX:CRUDEOIL" not in states       # crude NOT on the global engine


def test_global_strategy_override_beats_watch_suffix(rig):
    """/strategy qqe from Telegram steers EVERY watch, even '~env' ones."""
    from dataclasses import replace

    from nse_alerts.config import Watch

    cfg, provider, sent = rig
    provider.df = make_candles(falling(150), start="2026-09-26 09:15")  # QQE needs 72+
    nifty = Watch(key="NIFTY1!", label="NIFTY1!", exchange="NSE",
                  tv_symbol="NIFTY1!", yahoo_symbol="^NSEI")
    crude = Watch(key="MCX:CRUDEOIL", label="CRUDEOIL", exchange="MCX",
                  tv_symbol="MCX:CRUDEOIL", yahoo_symbol="BZ=F",
                  strategy="env")
    cfg = replace(cfg, watches=(nifty, crude))
    StateStore(cfg.state_file).set_control({"enabled": True, "strategy": "qqe"})

    assert app.run(cfg) == 0
    states = StateStore(cfg.state_file).load()
    assert "NIFTY1!#qqe" in states
    assert "MCX:CRUDEOIL#qqe" in states       # override wins over the suffix
    assert "MCX:CRUDEOIL#env" not in states


def test_status_shows_suffix_and_hides_stale_strategy_keys(rig, monkeypatch):
    from dataclasses import replace

    from nse_alerts import control
    from nse_alerts.config import Watch
    from nse_alerts.state import SymbolState

    cfg, provider, sent = rig
    nifty = Watch(key="NIFTY1!", label="NIFTY1!", exchange="NSE",
                  tv_symbol="NIFTY1!", yahoo_symbol="^NSEI")
    crude = Watch(key="MCX:CRUDEOIL", label="CRUDEOIL", exchange="MCX",
                  tv_symbol="MCX:CRUDEOIL", yahoo_symbol="BZ=F",
                  strategy="env")
    cfg = replace(cfg, watches=(nifty, crude))
    store = StateStore(cfg.state_file)
    store.put("NIFTY1!", SymbolState(last_side="UP",
                                     last_processed_bar="2026-09-29T15:25:00"))
    store.put("MCX:CRUDEOIL#env", SymbolState(
        last_side="DOWN", last_processed_bar="2026-09-29T23:20:00"))
    store.put("MCX:CRUDEOIL#qqe", SymbolState(          # stale from the qqe era
        last_side="UP", last_processed_bar="2026-09-29T21:05:00"))
    replies: list[str] = []
    monkeypatch.setattr(control, "send_telegram",
                        lambda token, chat_id, text, **kw: replies.append(text))
    monkeypatch.setattr(control, "fetch_updates",
                        lambda token: [{"message": {"chat": {"id": 42},
                                                    "text": "/status"}}])
    assert app.run(cfg) == 0
    text = replies[0]
    assert "MCX:CRUDEOIL~env" in text                       # suffix visible
    assert "MCX:CRUDEOIL#env" in text                       # current key shown
    assert "MCX:CRUDEOIL#qqe" not in text                   # stale switch hidden


def test_manual_dispatch_out_of_session_replies_with_note(rig, monkeypatch):
    """Pressing 'Run workflow' pre-market must prove the pipeline is alive."""
    from datetime import datetime

    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 8, 0, tzinfo=IST))  # pre-open
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")

    assert app.run(cfg) == 0
    assert provider.calls == 0                                # gates still closed
    assert len(sent.messages) == 1                            # but the note arrived
    text = sent.messages[0][2]
    assert "manual run" in text and "out of session" in text
    assert sent.markups[0] is None                            # menu buttons paused


def test_scheduled_or_local_run_out_of_session_stays_silent(rig, monkeypatch):
    """Scheduled cron runs (and local runs) must never add chat noise."""
    from datetime import datetime

    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 8, 0, tzinfo=IST))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    assert app.run(cfg) == 0
    assert sent.messages == []

    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)    # local run
    assert app.run(cfg) == 0
    assert sent.messages == []


def test_dispatch_note_only_before_open_no_nightly_spam(rig, monkeypatch):
    """Post-close dispatch ticks (external cron!) must stay silent - the
    23:35-23:55 double-notes happened because cron dispatches are also
    workflow_dispatch events."""
    from datetime import datetime

    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 23, 45, tzinfo=IST))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")

    assert app.run(cfg) == 0
    assert provider.calls == 0                                # all sessions shut
    assert sent.messages == []                                # but no note at night


def test_stale_window_never_fabricates_flips(rig):
    """A feed returning a window OLDER than stored state must be skipped -
    today's false BUY was stamped with yesterday's bar (29 Sep 15:25)."""
    from nse_alerts.state import SymbolState

    cfg, provider, sent = rig                                # candles end 11:20
    StateStore(cfg.state_file).put(
        cfg.symbol,
        SymbolState(last_side="UP",                         # data says DOWN
                    last_processed_bar="2026-09-28T14:00:00",   # ahead of window
                    last_seen_date="2026-09-28"))

    assert app.run(cfg) == 0
    assert provider.calls == 1                                # fetch happened
    signals = [t for _, _, t in sent.messages
               if not t.startswith(("⚔️", "🔴"))]             # tripwires filtered
    assert signals == []                                     # no alert, no heartbeat
    state = StateStore(cfg.state_file).get(cfg.symbol)
    assert state.last_processed_bar == "2026-09-28T14:00:00"  # untouched
    assert state.last_side == "UP"


def test_watch_session_window_overrides_exchange_hours(monkeypatch, tmp_path):
    """'@17:00-22:00' gate: crude must NOT run at 12:00 (MCX open) but MUST
    at 18:00 - each watch keeps its own clock, NIFTY still follows NSE."""
    from datetime import datetime

    from nse_alerts import control
    from nse_alerts.config import Watch
    from nse_alerts.market_hours import IST, parse_time_window

    def run_at(when, tag):
        sent = Recorder()
        provider = FakeProvider(make_candles(falling(26)))
        nifty = Watch(key="NIFTY1!", label="NIFTY1!", exchange="NSE",
                      tv_symbol="NIFTY1!", yahoo_symbol="^NSEI")
        crude = Watch(key="MCX:CRUDEOIL", label="CRUDEOIL", exchange="MCX",
                      tv_symbol="MCX:CRUDEOIL", yahoo_symbol="BZ=F",
                      session=parse_time_window("17:00-22:00"))
        cfg = make_config(tmp_path, watches=(nifty, crude),
                          state_file=tmp_path / f"{tag}.json")
        monkeypatch.setattr(app, "now_ist", lambda: when)
        monkeypatch.setattr(app, "build_providers", lambda c, watch=None: [provider])
        monkeypatch.setattr(app, "send_telegram", sent)
        monkeypatch.setattr(control, "fetch_updates", lambda token: [])
        assert app.run(cfg) == 0
        return provider, cfg

    provider, cfg = run_at(datetime(2026, 9, 28, 12, 0, tzinfo=IST), "noon")
    assert provider.calls == 1                               # NIFTY ran, crude gated
    states = StateStore(cfg.state_file).load()
    assert "NIFTY1!" in states and "MCX:CRUDEOIL" not in states

    provider, cfg = run_at(datetime(2026, 9, 28, 18, 0, tzinfo=IST), "eve")
    assert provider.calls == 1                               # NSE shut, crude window
    states = StateStore(cfg.state_file).load()
    assert "MCX:CRUDEOIL" in states and "NIFTY1!" not in states


def test_trend_tripwires_zone_then_trend_fire_once(monkeypatch, tmp_path):
    """Nifty +0.60% -> ⚔️ only; same day no repeat; next day +0.96% -> both."""
    from datetime import datetime

    from nse_alerts import control
    from nse_alerts.config import Watch
    from nse_alerts.market_hours import IST

    nifty = Watch(key="NIFTY1!", label="NIFTY1!", exchange="NSE",
                  tv_symbol="NIFTY1!", yahoo_symbol="^NSEI")

    def run_at(closes, when, tag):
        sent = Recorder()
        provider = FakeProvider(
            make_candles(closes, start=f"{when:%Y-%m-%d} 09:15"))
        cfg = make_config(tmp_path, watches=(nifty,),
                          state_file=tmp_path / f"{tag}.json")
        monkeypatch.setattr(app, "now_ist", lambda: when)
        monkeypatch.setattr(app, "build_providers",
                            lambda c, watch=None: [provider])
        monkeypatch.setattr(app, "send_telegram", sent)
        monkeypatch.setattr(control, "fetch_updates", lambda token: [])
        assert app.run(cfg) == 0
        return sent

    monday = datetime(2026, 9, 28, 11, 30, tzinfo=IST)
    sent = run_at([100 + i * 0.024 for i in range(26)], monday, "t")
    zone = [t for _, _, t in sent.messages if t.startswith("⚔️")]
    assert len(zone) == 1 and "NO averaging" in zone[0]
    assert not [t for _, _, t in sent.messages if t.startswith("🔴")]

    sent = run_at([100 + i * 0.024 for i in range(26)], monday, "t")
    assert not [t for _, _, t in sent.messages if "⚔️" in t]   # deduped

    tuesday = datetime(2026, 9, 29, 11, 30, tzinfo=IST)
    sent = run_at([100 + i * 0.04 for i in range(26)], tuesday, "t")
    zone = [t for _, _, t in sent.messages if t.startswith("⚔️")]
    trend = [t for _, _, t in sent.messages if t.startswith("🔴")]
    assert len(zone) == 1 and len(trend) == 1
    assert "stops live" in trend[0]


def test_envelope_alerts_fire_every_day_on_calm_days(rig, monkeypatch):
    """The envelope keeps evaluating and alerting daily. Calm days (<0.45%
    from open) cross normally; a TREND DAY is information only - every
    consensus/envelope signal is still delivered (the 09:45 strangle alert
    is retired)."""
    from dataclasses import replace
    from datetime import datetime

    from nse_alerts import control
    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    cfg = replace(cfg, strategy="env")
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])

    def envelope_alerts():
        return [t for _, _, t in sent.messages
                if t.startswith(("BUY ", "SELL "))]

    # Monday 13:30: baseline, no alert (day stays inside the ±0.45% zone)
    provider.df = make_candles([100.0] * 40 + [99.4] * 20)
    assert app.run(cfg) == 0
    assert not envelope_alerts()
    assert StateStore(cfg.state_file).get("NIFTY1!#env").last_side == "DOWN"

    # Tuesday 11:30 (calm day): the DOWN -> UP cross STILL alerts.
    # 8 bars flat then the level step - so the completed bars at 11:30
    # (~27) already contain the break, while keeping the day within ±0.6%.
    provider.df = make_candles([100.0] * 8 + [100.6] * 52,
                               start="2026-09-29 09:15")
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 29, 11, 30, tzinfo=IST))
    assert app.run(cfg) == 0
    assert [t for t in envelope_alerts()
            if t.startswith("BUY NIFTY1!")]          # DOWN -> UP fired on Tue
    assert StateStore(cfg.state_file).get("NIFTY1!#env").last_side == "UP"

    # Wednesday 11:30 (calm day): UP -> DOWN alerts too
    provider.df = make_candles([100.6] * 8 + [100.0] * 52,
                               start="2026-09-30 09:15")
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 30, 11, 30, tzinfo=IST))
    assert app.run(cfg) == 0
    assert [t for t in envelope_alerts()
            if t.startswith("SELL NIFTY1!")]         # UP -> DOWN fired


def test_trend_day_no_longer_suppresses_entries(rig, monkeypatch):
    """🔴 TREND DAY (>= 0.8% from open) is INFORMATION ONLY (owner call
    2026-10-26 - today's 11:10 SELL and 12:30 BUY consensus signals were
    wrongly eaten by the old gate): a fresh BUY/SELL flip is DELIVERED even
    after the day is declared, and state advances normally."""
    from nse_alerts.state import StateStore as _S

    cfg, provider, sent = rig
    store = _S(cfg.state_file)

    # Baseline DOWN on a calm day (no trend declared).
    provider.df = make_candles([100.0] * 40 + [99.6] * 20)
    assert app.run(cfg) == 0
    assert not [t for _, _, t in sent.messages
                if t.startswith(("BUY NIFTY1!", "SELL NIFTY1!"))]

    # Declare today a trend day (what the 🔴 tripwire would have done).
    store.set_control({"enabled": True, "trend_date": "2026-09-28",
                       "trend_sent": True})

    # A rising path that flips EMA20 DOWN -> UP (fresh BUY entry).
    provider.df = make_candles([100.0] * 40 + [100.7] * 20)
    assert app.run(cfg) == 0
    flips = [t for _, _, t in sent.messages
             if t.startswith(("BUY NIFTY1!", "SELL NIFTY1!"))]
    assert len(flips) == 1                                # entry DELIVERED
    state = store.get(cfg.symbol)
    assert state.last_side == "UP"                        # state advanced
    assert state.last_event_side == "BUY"                 # event recorded


def test_stale_feed_guard_skips_run_no_alert(rig, monkeypatch):
    """FRESHNESS_GUARD on: a feed whose last bar is still forming upstream
    (more than a full interval behind now) is REJECTED - the run can never
    alert on partial-bar data. This is the 26 Oct bug (alert quoted the 09:55
    close as 22,638.90; the real close was 22,724.70)."""
    from dataclasses import replace

    cfg, provider, sent = rig
    cfg = replace(cfg, freshness_guard=True)
    # now = Monday 13:30, candles end 11:20 -> feed hours behind -> reject
    assert app.run(cfg) == 0                    # skipped, retries next run
    assert not sent.messages                     # nothing delivered
    assert StateStore(cfg.state_file).load() == {}   # state untouched


def test_stale_feed_forming_bar_passes_guard(rig, monkeypatch):
    """A current feed's last row IS the forming bar - the guard must not
    reject it; the normal split/complete handling applies."""
    from dataclasses import replace

    cfg, provider, sent = rig
    cfg = replace(cfg, freshness_guard=True)
    # 52 bars 09:15..13:30; at now=13:30 the last row is the forming bar
    provider.df = make_candles(falling(52), start="2026-09-28 09:15")
    assert app.run(cfg) == 0                     # fresh: normal baseline run
    assert any("monitoring live" in t for *_, t in sent.messages)


def test_stale_feed_just_closed_bar_passes_guard(rig, monkeypatch):
    """The ambiguous boundary case: the feed's last row is the bar that just
    closed at the current boundary - its data IS final, so it passes."""
    from dataclasses import replace

    cfg, provider, sent = rig
    cfg = replace(cfg, freshness_guard=True)
    # last row 13:25 just closed at 13:30 (== boundary - interval)
    provider.df = make_candles(falling(51), start="2026-09-28 09:15")
    assert app.run(cfg) == 0
    assert any("monitoring live" in t for *_, t in sent.messages)



def test_0945_tick_runs_normal_scan_after_entry_alert_retired(rig, monkeypatch):
    """The 🎯 09:45 EXPIRY ENTRY strangle alert is RETIRED (2026-10-22,
    owner call): its Tuesday 09:45 slot must behave like any other scan tick,
    and none of the retired card's vocabulary may ever come back. This locks
    the removal that the '_tmp_0945.py' scratch verified by hand."""
    from datetime import datetime

    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    # Tuesday 09:45 - the old expiry-entry slot (NIFTY 0.92 sessions left).
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 29, 9, 45, tzinfo=IST))
    # A live fetch returns lookback_bars of history (incl. prior sessions),
    # so the engine has enough completed bars even at 09:45 to warm up.
    provider.df = make_candles([100.0 - 0.05 * i for i in range(40)],
                               start="2026-09-25 09:15")

    assert app.run(cfg) == 0

    dead = {"EXPIRY ENTRY", "strangle", "square off", "0DTE"}
    for _, _, text in sent.messages:
        assert not any(m in text for m in dead), \
            f"retired 09:45 card resurrected: {text!r}"

# --- P0 fresh-crude feed + P1 env prep alerts --------------------------------

def test_crude_auto_chain_leads_with_fresh_tv_override(rig):
    """NYMEX:BZ1! (continuous Brent futures - real-time) leads for crude;
    delayed BZ=F is only the fallback; other MCX symbols (NG) keep the old
    yahoo-first order."""
    from nse_alerts.config import parse_watches

    cfg, _, _ = rig
    crude = parse_watches("MCX:CRUDEOIL~env@17:00-22:00")[0]
    assert [p.name for p in real_build_providers(cfg, crude)] == ["tv", "yahoo"]
    ng = parse_watches("MCX:NATURALGAS")[0]
    assert [p.name for p in real_build_providers(cfg, ng)] == ["yahoo", "tv"]
    assert [p.name for p in real_build_providers(cfg, cfg.watches[0])] \
        == ["tv", "yahoo"]


def test_kite_provider_leads_with_yahoo_fallback(rig):
    """DATA_PROVIDER=kite: paid feed first, Yahoo proxy is the only fallback."""
    from dataclasses import replace

    from nse_alerts.config import parse_watches

    cfg, _, _ = rig
    cfg = replace(cfg, data_provider="kite",
                  kite_api_key="key", kite_access_token="tok")
    crude = parse_watches("MCX:CRUDEOIL~env@17:00-22:00")[0]
    ng = parse_watches("MCX:NATURALGAS")[0]
    assert [p.name for p in real_build_providers(cfg, cfg.watches[0])] \
        == ["kite", "yahoo"]
    assert [p.name for p in real_build_providers(cfg, crude)] \
        == ["kite", "yahoo"]
    assert [p.name for p in real_build_providers(cfg, ng)] \
        == ["kite", "yahoo"]


def test_stale_kite_token_falls_back_to_free_provider(rig):
    """A dead kite (expired token, credits gone...) must keep alerts alive on
    the next provider; kite reads the RAW watch symbol - the TV override
    (NYMEX:BZ1!) is TV-only."""
    from nse_alerts.config import parse_watches

    cfg, _, _ = rig
    crude = parse_watches("MCX:CRUDEOIL~env@17:00-22:00")[0]
    seen: list[str] = []

    class DeadKite:
        name = "kite"

        def fetch(self, symbol, interval, lookback):
            seen.append(symbol)
            raise ProviderError("Token is invalid or has expired")

    class Recorder:
        name = "tv"

        def fetch(self, symbol, interval, lookback):
            seen.append(symbol)
            return make_candles(falling(26))

    candles, src = app.fetch_candles(cfg, [DeadKite(), Recorder()], MONDAY,
                                     crude, min_bars=1)
    assert seen == ["MCX:CRUDEOIL", "NYMEX:BZ1!"]   # kite raw, tv override
    assert src == "tv" and not candles.empty

    seen: list[str] = []

    class RecorderProvider:
        name = "tv"

        def fetch(self, symbol, interval, lookback):
            seen.append(symbol)
            return make_candles(falling(26))

    candles, src = app.fetch_candles(cfg, [RecorderProvider()], MONDAY, crude,
                                     min_bars=1)
    assert seen == ["NYMEX:BZ1!"]                 # crude fetches the fresh name
    assert src == "tv" and not candles.empty


def _env_cfg(rig):
    from dataclasses import replace

    cfg, provider, sent = rig
    return (replace(cfg, strategy="env", envelope_len=5,
                    envelope_percent=0.3), provider, sent)


def _prep_frame(completed_close: float | None = None, live: float = 90.0):
    """51 completed bars 09:15-13:25 (falling = side DOWN) + a live pierce
    bar at 13:30 (forming at MONDAY 13:30)."""
    closes = falling(51)
    if completed_close is not None:
        closes[50] = completed_close
    return make_candles(closes + [live])


def test_prep_alert_fires_once_rearms_without_touching_side(rig):
    cfg, provider, sent = _env_cfg(rig)
    provider.df = _prep_frame()
    assert app.run(cfg) == 0
    texts = [t for _, _, t in sent.messages
             if not t.startswith(("⚔️", "🔴"))]         # tripwires filtered
    preps = [t for t in texts if t.startswith("⚠️")]
    assert len(texts) == 2 and len(preps) == 1          # prep + heartbeat
    assert "BLUE flip PENDING" in preps[0] and "live 90.00" in preps[0]
    assert "pre-flip · NIFTY1! live" in preps[0]      # display key, no #env
    state = StateStore(cfg.state_file).get("NIFTY1!#env")
    assert state.prep_fired is True
    assert state.last_side == "DOWN"                # prep NEVER moves state
    assert state.last_processed_bar == "2026-09-28T13:25:00"  # not the live row

    assert app.run(cfg) == 0                        # same episode: no repeat
    texts = [t for _, _, t in sent.messages
             if not t.startswith(("⚔️", "🔴"))]
    assert len(texts) == 2                          # still just prep + heartbeat

    provider.df = _prep_frame(completed_close=85.75)   # flicker inside same candle
    assert app.run(cfg) == 0                        # no second heads-up this candle
    texts = [t for _, _, t in sent.messages
             if not t.startswith(("⚔️", "🔴"))]
    preps = [t for t in texts if t.startswith("⚠️")]
    assert len(preps) == 1
    state = StateStore(cfg.state_file).get("NIFTY1!#env")
    assert state.prep_fired is True
    assert state.prep_bar == "2026-09-28T13:30:00"

    before = len(sent.messages)
    assert app.run(cfg, dry_run=True) == 0          # dry: no sends at all
    assert len(sent.messages) == before


def test_prep_alerts_can_be_disabled(rig):
    from dataclasses import replace

    cfg, provider, sent = rig
    cfg = replace(cfg, strategy="env", envelope_len=5, envelope_percent=0.3,
                  prep_alerts=False)
    provider.df = _prep_frame()
    assert app.run(cfg) == 0
    texts = [t for _, _, t in sent.messages
             if not t.startswith(("⚔️", "🔴"))]
    assert len(texts) == 1 and "monitoring live" in texts[0]
    assert StateStore(cfg.state_file).get("NIFTY1!#env").prep_fired is False


def test_prep_send_failure_retries_next_tick(rig, monkeypatch):
    from nse_alerts.notify import NotifyError

    cfg, provider, sent = _env_cfg(rig)
    provider.df = _prep_frame()
    record = sent                                    # the rig's Recorder

    def flaky(token, chat_id, text, **kw):
        if "pre-flip" in text:
            raise NotifyError("network blip")
        record(token, chat_id, text, **kw)

    monkeypatch.setattr(app, "send_telegram", flaky)
    assert app.run(cfg) == 0                         # advisory failure != 2
    assert StateStore(cfg.state_file).get("NIFTY1!#env").prep_fired is False

    monkeypatch.setattr(app, "send_telegram", record)
    assert app.run(cfg) == 0
    assert any("pre-flip" in t for _, _, t in record.messages)   # retried


def test_ema20_prep_fires_via_run_and_rearms(rig):
    """The forming-bar heads-up is not envelope-only: EMA20 also warns while
    the live bar is crossing (side DOWN + live penny above EMA20 -> pending
    UP), then re-arms when the live price pulls back."""
    from dataclasses import replace

    cfg, provider, sent = rig
    cfg = replace(cfg, strategy="ema20")
    # completed bars fall (side DOWN), live bar at 13:30 pierces above EMA20
    provider.df = _prep_frame(completed_close=None, live=110.0)
    assert app.run(cfg) == 0
    texts = [t for _, _, t in sent.messages
             if not t.startswith(("⚔️", "🔴"))]
    preps = [t for t in texts if t.startswith("⚠️")]
    assert len(preps) == 1                              # heads-up + heartbeat
    assert "EMA20 cross flip PENDING" in preps[0]
    assert "live 110.00" in preps[0] and "confirms only if the bar CLOSES" in preps[0]
    state = StateStore(cfg.state_file).get(cfg.symbol)
    assert state.prep_fired is True
    assert state.last_side == "DOWN"                    # prep NEVER moves state

    assert app.run(cfg) == 0                            # same episode: no repeat
    assert len([t for _, _, t in sent.messages
                if t.startswith("⚠️")]) == 1

    provider.df = _prep_frame(completed_close=None, live=85.0)  # below EMA20
    assert app.run(cfg) == 0
    assert len([t for _, _, t in sent.messages
                if t.startswith("⚠️")]) == 1            # same candle: no repeat
    assert StateStore(cfg.state_file).get(cfg.symbol).prep_fired is True

    provider.df = _prep_frame(completed_close=None, live=110.0)  # re-approach
    assert app.run(cfg) == 0
    assert len([t for _, _, t in sent.messages
                if t.startswith("⚠️")]) == 1            # recross in same candle stays quiet
    assert StateStore(cfg.state_file).get(cfg.symbol).prep_fired is True


def test_split_forming_separates_live_row():
    import pandas as pd

    df = make_candles([100.0] * 52)                  # last row starts 13:30
    done, forming = app.split_forming(df, 5, MONDAY)
    assert forming is not None and len(forming) == 1
    assert forming.index[-1] == pd.Timestamp("2026-09-28 13:30")
    assert done.index[-1] == pd.Timestamp("2026-09-28 13:25")
    done2, forming2 = app.split_forming(done, 5, MONDAY)
    assert forming2 is None and done2 is done

