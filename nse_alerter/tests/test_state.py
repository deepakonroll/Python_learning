"""State persistence: round-trip, corruption recovery, per-symbol keys."""

import json

from nse_alerts.state import StateStore, SymbolState


def test_missing_file_returns_empty(tmp_path):
    assert StateStore(tmp_path / "state.json").load() == {}


def test_round_trip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = SymbolState(last_side="DOWN", last_processed_bar="2026-09-28T09:20")
    store.put("NIFTY1!", state)

    loaded = StateStore(tmp_path / "state.json").get("NIFTY1!")
    assert loaded is not None
    assert loaded.last_side == "DOWN"
    assert loaded.updated_at                           # stamped on save
    assert StateStore(tmp_path / "state.json").get("OTHER") is None


def test_event_history_is_recorded_and_capped(tmp_path):
    store = StateStore(tmp_path / "state.json")
    state = SymbolState(last_side="UP")
    for i in range(60):
        state.record_event("BUY" if i % 2 else "SELL", f"bar-{i}")
    store.put("NIFTY1!", state)
    loaded = store.get("NIFTY1!")
    assert len(loaded.history) == 50                   # rolling tail
    assert loaded.last_event_bar == "bar-59"


def test_corrupt_file_recovers_to_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    store = StateStore(path)
    assert store.load() == {}
    store.put("NIFTY1!", SymbolState(last_side="UP"))  # and we can write again
    assert store.get("NIFTY1!").last_side == "UP"


def test_unknown_keys_in_entry_are_tolerated(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"NIFTY1!": {"last_side": "UP", "future_field": 1}}),
                    encoding="utf-8")
    assert StateStore(path).get("NIFTY1!") is None     # TypeError -> skipped entry
