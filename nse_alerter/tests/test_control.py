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