"""Telegram command parsing/polling (mobile enable/disable)."""

from nse_alerts.control import extract_commands, fetch_updates
from nse_alerts.state import StateStore, SymbolState


def test_owner_command_extracted():
    updates = [{"message": {"chat": {"id": 42}, "text": "/disable"}}]
    assert extract_commands(updates, "42") == ["/disable"]


def test_all_commands_kept_in_chronological_order():
    updates = [{"message": {"chat": {"id": 42}, "text": "/disable"}},
               {"message": {"chat": {"id": 42}, "text": "hello"}},
               {"message": {"chat": {"id": 42}, "text": "/enable"}}]
    assert extract_commands(updates, 42) == ["/disable", "/enable"]  # int ok too


def test_strategy_command_keeps_its_argument():
    updates = [{"message": {"chat": {"id": 42}, "text": "/Strategy Both"}}]
    assert extract_commands(updates, "42") == ["/strategy both"]


def test_apply_strategy_sets_and_clears_override(tmp_path):
    from nse_alerts.control import _apply_strategy, effective_strategy
    from tests.conftest import make_config

    store = StateStore(tmp_path / "state.json")
    cfg = make_config(tmp_path)
    assert "applied" in _apply_strategy(store, "/strategy both")
    assert store.get_control()["strategy"] == "both"
    assert effective_strategy(cfg, store) == "both"

    assert "restored" in _apply_strategy(store, "/strategy default")
    assert "strategy" not in store.get_control()
    assert effective_strategy(cfg, store) == cfg.strategy


def test_invalid_strategy_value_gets_usage_message(tmp_path):
    from nse_alerts.control import _apply_strategy

    store = StateStore(tmp_path / "state.json")
    msg = _apply_strategy(store, "/strategy macd")
    assert msg.startswith("❌") and "qqe" in msg
    assert "strategy" not in store.get_control()


def test_disable_merge_preserves_strategy_override(tmp_path):
    from nse_alerts.control import _apply_strategy

    store = StateStore(tmp_path / "state.json")
    _apply_strategy(store, "/strategy qqe")
    store.set_control({**store.get_control(), "enabled": False})   # /disable path
    control = store.get_control()
    assert control["strategy"] == "qqe" and control["enabled"] is False


def test_other_chat_and_noise_ignored():
    updates = [{"message": {"chat": {"id": 999}, "text": "/disable"}},
               {"message": {"chat": {"id": 42}, "text": "hello"}}]
    assert extract_commands(updates, "42") == []


def test_fetch_updates_network_failure_is_safe():
    def getter(url, timeout=None):
        raise ConnectionError("offline")

    assert fetch_updates("tok", getter=getter) == []


def test_fetch_updates_confirms_batch_with_offset():
    """Telegram clears updates only when offset > their update_id is passed."""
    calls: list[str] = []
    responses = [
        {"ok": True, "result": [{"update_id": 100},
                                {"update_id": 101}]},
        {"ok": True, "result": []},              # confirmation read -> empty
    ]

    class Resp:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def getter(url, timeout=None):
        calls.append(url)
        return Resp(responses.pop(0))

    out = fetch_updates("tok", getter=getter)
    assert len(out) == 2                         # both collected, chronological
    assert "offset=102" in calls[1]              # 101 + 1 confirms BOTH
    assert len(calls) == 2                       # stop after empty read


def test_fetch_updates_rejection_is_safe():
    class Resp:
        def json(self):
            return {"ok": False, "description": "Unauthorized"}

    assert fetch_updates("tok", getter=lambda url, timeout=None: Resp()) == []


def test_control_flag_survives_state_writes(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.set_control({"enabled": False})
    store.put("NIFTY1!", SymbolState(last_side="UP"))        # signal save keeps flag
    assert store.get_control()["enabled"] is False
    assert store.get("NIFTY1!").last_side == "UP"
    assert "__control__" not in store.load()                 # hidden from symbols


# --- pop-up menu (inline callbacks) ------------------------------------------


def _cb(data, text="BUY NIFTY1! · alert text", from_id="42", mid=7):
    """A callback_query update as Telegram delivers it after an inline tap."""
    return {"callback_query": {"id": "cb-1", "data": data,
                               "from": {"id": from_id},
                               "message": {"message_id": mid,
                                           "chat": {"id": 42},
                                           "text": text}}}


def _menu_rig(monkeypatch, tmp_path, updates):
    from nse_alerts import control
    from tests.conftest import make_config

    cfg = make_config(tmp_path)
    store = StateStore(cfg.state_file)
    events = {"acks": [], "edits": [], "sends": []}
    monkeypatch.setattr(control, "fetch_updates", lambda token: list(updates))
    monkeypatch.setattr(control, "answer_callback",
                        lambda token, cb_id, **kw: events["acks"].append(cb_id))
    monkeypatch.setattr(control, "edit_message",
                        lambda token, chat, mid, text, reply_markup=None, **kw:
                        events["edits"].append((mid, text, reply_markup)))
    monkeypatch.setattr(control, "send_telegram",
                        lambda token, chat, text, reply_markup=None, **kw:
                        events["sends"].append((text, reply_markup)))
    return control, cfg, store, events


def test_extract_callbacks_owner_filter():
    from nse_alerts.control import extract_callbacks

    cbs = extract_callbacks([_cb("m:main"),
                             _cb("m:toggle", from_id=99, mid=8)], "42")
    assert [c["from_owner"] for c in cbs] == [True, False]
    assert cbs[0]["data"] == "m:main" and cbs[0]["message_id"] == 7


def test_menu_tap_under_alert_sends_fresh_menu(monkeypatch, tmp_path):
    """☰ under an alert must SEND a new menu - never edit the alert away."""
    control, cfg, store, events = _menu_rig(monkeypatch, tmp_path, [_cb("m:main")])
    assert control.process_commands(cfg, store) is True
    assert events["acks"] == ["cb-1"]                      # spinner dismissed
    assert not events["edits"]                             # alert untouched
    text, kb = events["sends"][0]
    assert text.startswith(control.MENU_PREFIX)
    assert kb["inline_keyboard"][0][0]["callback_data"] == "m:status"


def test_menu_tap_edits_in_place(monkeypatch, tmp_path):
    from nse_alerts.control import MENU_PREFIX

    updates = [_cb("m:status", text=f"{MENU_PREFIX}\nold menu body")]
    control, cfg, store, events = _menu_rig(monkeypatch, tmp_path, updates)
    assert control.process_commands(cfg, store) is True
    assert not events["sends"]                             # pop-up = edit itself
    mid, text, kb = events["edits"][0]
    assert mid == 7 and text.startswith(MENU_PREFIX) and "Alerts" in text
    flat = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    assert "m:status" in flat and "m:main" in flat


def test_toggle_via_menu_applies_and_gates(monkeypatch, tmp_path):
    control, cfg, store, events = _menu_rig(monkeypatch, tmp_path, [_cb("m:toggle")])
    assert control.process_commands(cfg, store) is False   # disabled -> run stops
    assert store.get_control()["enabled"] is False
    text, kb = events["sends"][0]
    assert "disabled" in text and "🛑" in text
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "✅ Enable" in labels                            # button flips over


def test_strategy_via_menu_tap_stores_override(monkeypatch, tmp_path):
    from nse_alerts.control import MENU_PREFIX

    updates = [_cb("m:strategy:qqe", text=f"{MENU_PREFIX}\nmain body")]
    control, cfg, store, events = _menu_rig(monkeypatch, tmp_path, updates)
    control.process_commands(cfg, store)
    assert store.get_control()["strategy"] == "qqe"
    _mid, text, _kb = events["edits"][0]
    assert "qqe" in text and "applied" in text


def test_foreign_tap_is_acked_but_ignored(monkeypatch, tmp_path):
    control, cfg, store, events = _menu_rig(monkeypatch, tmp_path,
                                            [_cb("m:toggle", from_id=99)])
    assert control.process_commands(cfg, store) is True    # state untouched
    assert events["acks"] == ["cb-1"]                      # their spinner still dies
    assert not events["edits"] and not events["sends"]
    assert control.is_enabled(store)


def test_menu_tap_and_typed_command_in_same_poll(monkeypatch, tmp_path):
    updates = [_cb("m:main"),
               {"message": {"chat": {"id": 42}, "text": "/status"}}]
    control, cfg, store, events = _menu_rig(monkeypatch, tmp_path, updates)
    assert control.process_commands(cfg, store) is True
    assert events["acks"] == ["cb-1"]
    assert len(events["sends"]) == 2                       # menu + combined reply
    assert "report below" in events["sends"][1][0]         # typed command honoured