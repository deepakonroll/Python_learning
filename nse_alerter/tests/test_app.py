"""End-to-end flow with fakes: gate -> fetch -> cross -> dedupe -> send."""

from __future__ import annotations

import pytest

from nse_alerts import app
from nse_alerts.app import build_providers as real_build_providers  # pre-rig ref
from nse_alerts.providers.base import ProviderError
from nse_alerts.state import StateStore
from tests.conftest import MONDAY, SATURDAY, falling, make_candles, make_config, rising


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
    assert app.run(cfg) == 0                             # baseline: DOWN + heartbeat
    plain = [m for m in sent.messages
             if not m[2].startswith(("⚔️", "🔴"))]         # tripwires filtered
    assert len(plain) == 1
    assert "monitoring live" in plain[0][2]

    provider.df = make_candles(falling(26) + rising(26))  # side flips to UP
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


def test_all_providers_down_returns_3(rig):
    cfg, provider, _ = rig
    provider.error = "network down"
    assert app.run(cfg) == 3


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


def test_qqe_mode_ignores_ema_data_length_requirements(rig):
    from tests.conftest import make_candles

    cfg, provider, _ = rig
    cfg = __import__("dataclasses").replace(cfg, strategy="qqe")
    provider.df = make_candles(WAVE_FALL[:40])              # below QQE warm-up
    assert app.run(cfg) == 3                                # clear data error


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


def test_plan_card_once_per_day_and_no_manual_note(rig, monkeypatch):
    """First run in 08:45-09:10 = plan card (not the manual note), once/day."""
    from datetime import datetime

    from nse_alerts import control
    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 28, 8, 46, tzinfo=IST))  # Mon
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])

    assert app.run(cfg) == 0
    assert provider.calls == 0                       # sessions still shut
    texts = [t for _, _, t in sent.messages]
    assert len(texts) == 1                           # card ONLY - no note
    assert texts[0].startswith("📋 PLAN") and "NIFTY" in texts[0]
    assert "premium DOUBLES" in texts[0] and "15:15" in texts[0]
    assert "1 lot" in texts[0] and "BLUE cross" in texts[0]
    assert "Envelope pilot" in texts[0] and "NIFTY ONLY" in texts[0]
    assert "-10k = EXIT" in texts[0]

    assert app.run(cfg) == 0                         # next tick, same day
    assert len(sent.messages) == 1                   # deduped

    # CI-startup grace: a dispatch TRIGGERED at 08:59 that EXECUTES at 09:03
    # (next Monday here) must still card; the manual note must stay quiet.
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 10, 5, 9, 3, tzinfo=IST))
    assert app.run(cfg) == 0
    assert len(sent.messages) == 2
    assert sent.messages[-1][2].startswith("📋 PLAN")


def test_plan_card_weekday_rotation_and_expiry_line(rig, monkeypatch):
    """v4 rotation cards: Tue = NIFTY strangle, Wed = envelope pilot,
    Thu = SENSEX strangle - each with its own discipline rules."""
    from datetime import datetime

    from nse_alerts import control
    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])

    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 29, 8, 50, tzinfo=IST))  # Tue
    assert app.run(cfg) == 0
    text = sent.messages[-1][2]
    assert "NIFTY 0DTE STRANGLE" in text
    assert "09:45 alert" in text and "2×" in text and "15:15" in text
    assert "Envelope ON" in text                     # envelope runs Tue/Thu too

    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 30, 8, 50, tzinfo=IST))  # Wed
    assert app.run(cfg) == 0
    text = sent.messages[-1][2]
    assert "Envelope pilot" in text and "Tue = NIFTY strangle" in text
    assert "STRANGLE day" not in text                # pilot day, not strangle

    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 10, 1, 8, 50, tzinfo=IST))  # Thu
    assert app.run(cfg) == 0
    text = sent.messages[-1][2]
    assert "SENSEX 0DTE STRANGLE" in text
    assert "step 100" in text and "lot 20" in text   # Sensex grid + size rule


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
    assert "NO re-entry" in trend[0]


def test_expiry_entry_alert_tuesday_0945_fires_once(rig, monkeypatch):
    """Tue 09:45 -> the NIFTY strangle entry alert exactly once; the next
    tick falls through to the normal scan (no second alert)."""
    from datetime import datetime

    from nse_alerts import control, expiry
    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])
    monkeypatch.setattr(expiry, "fetch_india_vix",
                        lambda *a, **k: (0.14, "test"))
    # Oct 5 (Mon) bars for warm-up + Oct 6 morning bars for the live spot
    closes = [22445.0] * 288 + [22445.5] * 12
    provider.df = make_candles(closes, start="2026-10-05 09:15")
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 10, 6, 9, 45, tzinfo=IST))  # Tue

    assert app.run(cfg) == 0
    alerts = [t for _, _, t in sent.messages if t.startswith("🎯")]
    assert len(alerts) == 1
    text = alerts[0]
    assert "NIFTY 0DTE strangle" in text
    assert "SELL 22,500 CE" in text and "SELL 22,400 PE" in text  # spot -> ATM 22,450
    assert "2×" in text and "15:15" in text and "EITHER leg" in text
    assert "ONE entry" in text and "NO adds" in text
    ctrl = StateStore(cfg.state_file).get_control()
    assert ctrl.get("expiry_alert_date") == "2026-10-06"

    assert app.run(cfg) == 0                         # next tick, same day
    assert len([t for _, _, t in sent.messages
                if t.startswith("🎯")]) == 1         # deduped


def test_expiry_entry_alert_retries_after_provider_failure(rig, monkeypatch):
    """A dead feed never consumes the once-per-day key: 09:45 fails quietly,
    09:50 succeeds."""
    from datetime import datetime

    from nse_alerts import control, expiry
    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])
    monkeypatch.setattr(expiry, "fetch_india_vix",
                        lambda *a, **k: (0.14, "test"))
    provider.error = "feed down"
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 10, 6, 9, 45, tzinfo=IST))
    assert app.run(cfg) == 0                          # failure swallowed
    assert not [t for _, _, t in sent.messages if t.startswith("🎯")]
    assert StateStore(cfg.state_file).get_control().get(
        "expiry_alert_date") is None                  # key NOT consumed

    provider.error = None
    provider.df = make_candles([22445.5] * 12, start="2026-10-06 09:15")
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 10, 6, 9, 50, tzinfo=IST))
    assert app.run(cfg) == 0
    alerts = [t for _, _, t in sent.messages if t.startswith("🎯")]
    assert len(alerts) == 1 and "NIFTY 0DTE" in alerts[0]
    assert StateStore(cfg.state_file).get_control().get(
        "expiry_alert_date") == "2026-10-06"


def test_expiry_entry_alert_thursday_uses_sensex_grid(rig, monkeypatch):
    """Thu 09:45 -> SENSEX alert on the 100-pt strike grid (step/lot 100/20)."""
    from datetime import datetime

    from nse_alerts import control, expiry
    from nse_alerts.market_hours import IST

    cfg, provider, sent = rig
    monkeypatch.setattr(control, "fetch_updates", lambda token: [])
    monkeypatch.setattr(expiry, "fetch_india_vix",
                        lambda *a, **k: (0.14, "test"))
    provider.df = make_candles([71905.0] * 12, start="2026-10-08 09:15")
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 10, 8, 9, 45, tzinfo=IST))  # Thu

    assert app.run(cfg) == 0
    alerts = [t for _, _, t in sent.messages if t.startswith("🎯")]
    assert len(alerts) == 1
    text = alerts[0]
    assert "SENSEX 0DTE strangle" in text
    assert "SELL 72,000 CE" in text and "SELL 71,800 PE" in text  # ATM 71,900
    assert "15:15" in text


def test_envelope_alerts_fire_on_strangle_days_too(rig, monkeypatch):
    """No pause: Tue/Thu ADD the 09:45 strangle alert but the envelope keeps
    evaluating and alerting every day of the rotation."""
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

    # Monday 13:30: baseline, no alert
    provider.df = make_candles(falling(60))
    assert app.run(cfg) == 0
    assert not envelope_alerts()
    assert StateStore(cfg.state_file).get("NIFTY1!#env").last_side == "DOWN"

    # Tuesday 11:30 (strangle day): the cross STILL alerts - no pause
    provider.df = make_candles(rising(60), start="2026-09-29 09:15")
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 29, 11, 30, tzinfo=IST))
    assert app.run(cfg) == 0
    assert [t for t in envelope_alerts()
            if t.startswith("BUY NIFTY1!")]          # DOWN -> UP fired on Tue
    assert StateStore(cfg.state_file).get("NIFTY1!#env").last_side == "UP"
    assert [t for _, _, t in sent.messages if t.startswith("⚔️")]  # tripwires

    # Wednesday 11:30: pilot day unchanged
    provider.df = make_candles(falling(60), start="2026-09-30 09:15")
    monkeypatch.setattr(app, "now_ist",
                        lambda: datetime(2026, 9, 30, 11, 30, tzinfo=IST))
    assert app.run(cfg) == 0
    assert [t for t in envelope_alerts()
            if t.startswith("SELL NIFTY1!")]         # UP -> DOWN fired



# --- P0 fresh-crude feed + P1 env prep alerts --------------------------------

def test_crude_auto_chain_leads_with_fresh_tv_override(rig):
    """TVC:UKOIL (real-time) leads for crude; delayed BZ=F is only the
    fallback; other MCX symbols (NG) keep the old yahoo-first order."""
    from nse_alerts.config import parse_watches

    cfg, _, _ = rig
    crude = parse_watches("MCX:CRUDEOIL~env@17:00-22:00")[0]
    assert [p.name for p in real_build_providers(cfg, crude)] == ["tv", "yahoo"]
    ng = parse_watches("MCX:NATURALGAS")[0]
    assert [p.name for p in real_build_providers(cfg, ng)] == ["yahoo", "tv"]
    assert [p.name for p in real_build_providers(cfg, cfg.watches[0])] \
        == ["tv", "yahoo"]


def test_kite_provider_leads_with_free_chain_behind(rig):
    """DATA_PROVIDER=kite: paid feed first, free TV/yahoo chain still behind
    it - a stale daily token degrades, it never darkens the alerts."""
    from dataclasses import replace

    from nse_alerts.config import parse_watches

    cfg, _, _ = rig
    cfg = replace(cfg, data_provider="kite",
                  kite_api_key="key", kite_access_token="tok")
    crude = parse_watches("MCX:CRUDEOIL~env@17:00-22:00")[0]
    ng = parse_watches("MCX:NATURALGAS")[0]
    assert [p.name for p in real_build_providers(cfg, cfg.watches[0])] \
        == ["kite", "tv", "yahoo"]
    assert [p.name for p in real_build_providers(cfg, crude)] \
        == ["kite", "tv", "yahoo"]
    assert [p.name for p in real_build_providers(cfg, ng)] \
        == ["kite", "yahoo", "tv"]


def test_stale_kite_token_falls_back_to_free_provider(rig):
    """A dead kite (expired token, credits gone...) must keep alerts alive on
    the next provider; kite reads the RAW watch symbol - the TV override
    (TVC:UKOIL) is TV-only."""
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
    assert seen == ["MCX:CRUDEOIL", "TVC:UKOIL"]    # kite raw, tv override
    assert src == "tv" and not candles.empty

    seen: list[str] = []

    class RecorderProvider:
        name = "tv"

        def fetch(self, symbol, interval, lookback):
            seen.append(symbol)
            return make_candles(falling(26))

    candles, src = app.fetch_candles(cfg, [RecorderProvider()], MONDAY, crude,
                                     min_bars=1)
    assert seen == ["TVC:UKOIL"]                 # crude fetches the fresh name
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

    provider.df = _prep_frame(completed_close=85.75)   # close back inside band
    assert app.run(cfg) == 0                        # re-arms AND preps again
    texts = [t for _, _, t in sent.messages
             if not t.startswith(("⚔️", "🔴"))]
    preps = [t for t in texts if t.startswith("⚠️")]
    assert len(preps) == 2                          # one per approach episode
    assert StateStore(cfg.state_file).get("NIFTY1!#env").prep_fired is True

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


def test_split_forming_separates_live_row():
    import pandas as pd

    df = make_candles([100.0] * 52)                  # last row starts 13:30
    done, forming = app.split_forming(df, 5, MONDAY)
    assert forming is not None and len(forming) == 1
    assert forming.index[-1] == pd.Timestamp("2026-09-28 13:30")
    assert done.index[-1] == pd.Timestamp("2026-09-28 13:25")
    done2, forming2 = app.split_forming(done, 5, MONDAY)
    assert forming2 is None and done2 is done

