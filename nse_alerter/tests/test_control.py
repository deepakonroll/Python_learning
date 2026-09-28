"""Telegram command parsing/polling (mobile enable/disable)."""

from nse_alerts.control import extract_command, fetch_updates
from nse_alerts.state import StateStore, SymbolState


def test_owner_command_extracted():
    updates = [{"message": {"chat": {"id": 42}, "text": "/disable"}}]
    assert extract_command(updates, "42") == "/disable"


def test_last_command_wins():
    updates = [{"message": {"chat": {"id": 42}, "text": "/disable"}},
               {"message": {"chat": {"id": 42}, "text": "/enable"}}]
    assert extract_command(updates, 42) == "/enable"        # int chat id ok


def test_other_chat_and_noise_ignored():
    updates = [{"message": {"chat": {"id": 999}, "text": "/disable"}},
               {"message": {"chat": {"id": 42}, "text": "hello"}}]
    assert extract_command(updates, "42") is None


def test_fetch_updates_network_failure_is_safe():
    def getter(url, timeout=None):
        raise ConnectionError("offline")

    assert fetch_updates("tok", getter=getter) == []


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