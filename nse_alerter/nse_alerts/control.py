"""Telegram command polling - enable/disable the alerts from your phone.

GitHub's mobile app cannot toggle workflows, but the cloud cron polls every
5 minutes anyway, so each live run FIRST checks the bot chat for commands
sent by the owner (TELEGRAM_CHAT_ID only):

    /disable  -> stop evaluating & sending until /enable
    /enable   -> resume immediately
    /status   -> report on/off state, current side, last event

The on/off flag lives inside state.json (already persisted by the GitHub
Actions cache), so it survives across cloud runs. Dry runs never poll -
getUpdates is a consuming read, so only the live scheduler may touch it.
"""

from __future__ import annotations

import logging

import requests

from .config import Config
from .notify import NotifyError, send_telegram
from .state import StateStore

log = logging.getLogger("nse_alerts")

CONTROL_KEY = "__control__"           # reserved key inside state.json
COMMANDS = ("/disable", "/enable", "/status")

KEYBOARD = {
    "keyboard": [[{"text": "/disable"}, {"text": "/enable"}, {"text": "/status"}]],
    "is_persistent": True,
    "resize_keyboard": True,
}


def fetch_updates(token: str, getter=requests.get) -> list[dict]:
    """Consume pending bot updates; network problems never break the run."""
    try:
        resp = getter(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
        data = resp.json()
    except Exception as exc:
        log.warning("command poll failed: %s", exc)
        return []
    if not data.get("ok"):
        log.warning("command poll rejected: %s", data.get("description"))
        return []
    return data.get("result") or []


def extract_command(updates: list[dict], owner_chat_id: str) -> str | None:
    """Last valid command from the owner's chat; others are ignored."""
    found = None
    for update in updates:
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != str(owner_chat_id):
            continue
        text = (message.get("text") or "").strip().split(" ")[0].lower()
        if text in COMMANDS:
            found = text
    return found


def is_enabled(store: StateStore) -> bool:
    return bool(store.get_control().get("enabled", True))


def _reply(cfg: Config, text: str) -> None:
    try:
        send_telegram(cfg.telegram_token or "", cfg.telegram_chat_id or "",
                      text, reply_markup=KEYBOARD)
    except NotifyError as exc:               # a failed reply must never kill alerts
        log.warning("command reply failed: %s", exc)


def _status_text(store: StateStore) -> str:
    symbol = _symbol_hint(store)
    state = store.load().get(symbol)
    enabled = is_enabled(store)
    lines = [(f"✅ Alerts: ON" if enabled else f"🛑 Alerts: OFF")]
    if state:
        lines.append(f"📊 {symbol} side={state.last_side} · "
                     f"last bar {state.last_processed_bar or 'n/a'}")
        if state.last_event_side:
            lines.append(f"🔔 last event: {state.last_event_side} @ "
                         f"{state.last_event_bar or '?'}")
    lines.append("Commands: /disable · /enable · /status")
    return "\n".join(lines)


def _symbol_hint(store: StateStore) -> str:
    for key in store.load():
        if key != CONTROL_KEY:
            return key
    return "NIFTY1!"


def process_commands(cfg: Config, store: StateStore) -> bool:
    """Poll for owner commands; returns True when alerts should run."""
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        return True
    cmd = extract_command(fetch_updates(cfg.telegram_token), cfg.telegram_chat_id)

    if cmd == "/disable":
        store.set_control({"enabled": False})
        _reply(cfg, "🛑 Alerts disabled — cloud runs stay idle.\n"
                    "Tap /enable to resume.")
        return False
    if cmd == "/enable":
        store.set_control({"enabled": True})
        _reply(cfg, "✅ Alerts enabled — evaluating from this run.")
        return True
    if cmd == "/status":
        _reply(cfg, _status_text(store))
    return is_enabled(store)
