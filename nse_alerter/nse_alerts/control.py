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

from .config import STRATEGIES, Config, normalize_strategy
from .notify import NotifyError, answer_callback, edit_message, send_telegram
from .state import StateStore

log = logging.getLogger("nse_alerts")

CONTROL_KEY = "__control__"           # reserved key inside state.json
COMMANDS = ("/disable", "/enable", "/status", "/strategy")

# Pop-up menu: inline callback_data namespace ('m:...') and the text sentinel
# that marks a message AS a menu (so taps edit it in place, while ☰ under an
# alert/heartbeat sends a fresh menu instead of destroying the alert text).
# PAUSED: buttons are no longer attached to messages (tap latency without an
# always-on server); taps on stale buttons are only acked. One flag to resume.
CALLBACK_PREFIX = "m:"
MENU_PREFIX = "🎛 Menu"
MENU_ENABLED = False


def fetch_updates(token: str, getter=requests.get) -> list[dict]:
    """Read AND confirm pending updates (Telegram only clears updates when a
    later getUpdates call passes their max update_id + 1 as `offset`).

    Loop: read batch -> next call carries offset (confirms it) and picks up
    anything that arrived meanwhile -> until a read comes back empty.
    Network problems never break the caller (returns what was collected).
    """
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    collected: list[dict] = []
    offset: int | None = None
    for _ in range(5):                          # bounded - no infinite loops
        target = url if offset is None else f"{url}?offset={offset}&timeout=0"
        try:
            resp = getter(target, timeout=10)
            data = resp.json()
        except Exception as exc:
            log.warning("command poll failed: %s", exc)
            break
        if not data.get("ok"):
            log.warning("command poll rejected: %s", data.get("description"))
            break
        batch = data.get("result") or []
        if not batch:
            break                               # empty read also confirms prior batch
        collected.extend(batch)
        offset = max(u.get("update_id", 0) for u in batch) + 1   # confirm on next call
    return collected


def extract_commands(updates: list[dict], owner_chat_id: str) -> list[str]:
    """All valid commands from the owner's chat, in chronological order.

    '/strategy <x>' keeps its argument; other commands are single tokens.
    """
    found: list[str] = []
    for update in updates:
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != str(owner_chat_id):
            continue
        text = (message.get("text") or "").strip().lower()
        if not text:
            continue
        first = text.split(" ")[0]
        if first == "/strategy":
            found.append(text)                 # keep the argument: "/strategy both"
        elif first in COMMANDS:
            found.append(first)
    return found


def is_enabled(store: StateStore) -> bool:
    return bool(store.get_control().get("enabled", True))


def effective_strategy(cfg: Config, store: StateStore) -> str:
    """Telegram override (stored in state.json) wins over the env/default.

    This is what makes strategy switching mobile-first: /strategy writes here,
    every later run reads it - no rebuild, no repo settings, no computer.
    """
    override = store.get_control().get("strategy")
    if isinstance(override, str):
        canonical = normalize_strategy(override)
        if canonical:
            return canonical
    return cfg.strategy


def _reply(cfg: Config, text: str) -> None:
    try:
        send_telegram(cfg.telegram_token or "", cfg.telegram_chat_id or "", text)
    except NotifyError as exc:               # a failed reply must never kill alerts
        log.warning("command reply failed: %s", exc)


# --- pop-up menu (inline callbacks) -----------------------------------------


def extract_callbacks(updates: list[dict], owner_chat_id: str) -> list[dict]:
    """Every inline-button tap, in arrival order.

    Each entry: {id, chat_id, message_id, text, data, from_owner}.
    """
    found: list[dict] = []
    for update in updates:
        cb = update.get("callback_query")
        if not cb:
            continue
        message = cb.get("message") or {}
        found.append({
            "id": str(cb.get("id") or ""),
            "chat_id": str((message.get("chat") or {}).get("id", "")),
            "message_id": message.get("message_id"),
            "text": str(message.get("text") or ""),
            "data": str(cb.get("data") or ""),
            "from_owner": str((cb.get("from") or {}).get("id", ""))
                          == str(owner_chat_id),
        })
    return found


def _menu_main(cfg: Config, store: StateStore) -> tuple[str, dict]:
    enabled = is_enabled(store)
    text = (f"{MENU_PREFIX}\n"
            f"{'✅ Alerts: ON' if enabled else '🛑 Alerts: OFF'} · "
            f"Strategy: {effective_strategy(cfg, store)}\n"
            f"Tap a button:")
    keyboard = {"inline_keyboard": [
        [{"text": "📊 Status", "callback_data": "m:status"}],
        [{"text": "🎯 Strategy", "callback_data": "m:strategy"}],
        [{"text": "🛑 Disable" if enabled else "✅ Enable",
          "callback_data": "m:toggle"}],
    ]}
    return text, keyboard


def _menu_status(cfg: Config, store: StateStore) -> tuple[str, dict]:
    text = f"{MENU_PREFIX}\n{_status_text(store, cfg)}"
    keyboard = {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": "m:status"},
         {"text": "🎯 Strategy", "callback_data": "m:strategy"}],
        [{"text": "⬅️ Back", "callback_data": "m:main"}],
    ]}
    return text, keyboard


def _menu_strategy(cfg: Config, store: StateStore) -> tuple[str, dict]:
    override = store.get_control().get("strategy")
    eff = effective_strategy(cfg, store)
    source = "Telegram override" if override else "config default"
    text = (f"{MENU_PREFIX}\n🎯 Strategy: {eff} ({source})\n"
            f"Pick a strategy (applies this run):")
    keyboard = {"inline_keyboard": [
        [{"text": "qqe", "callback_data": "m:strategy:qqe"},
         {"text": "ema20", "callback_data": "m:strategy:ema20"}],
        [{"text": "both", "callback_data": "m:strategy:both"},
         {"text": "default", "callback_data": "m:strategy:default"}],
        [{"text": "⬅️ Back", "callback_data": "m:main"}],
    ]}
    return text, keyboard


def _menu_action(cfg: Config, store: StateStore, cb: dict) -> tuple[str, dict]:
    """Menu state machine: applies store changes, returns what the message
    should now show. Falls back to the main menu for unknown taps."""
    data = cb.get("data", "")
    if data == "m:status":
        return _menu_status(cfg, store)
    if data == "m:strategy":
        return _menu_strategy(cfg, store)
    if data == "m:toggle":
        enable = not is_enabled(store)
        store.set_control({**store.get_control(), "enabled": enable})
        text, keyboard = _menu_main(cfg, store)
        note = "enabled" if enable else "disabled"
        text = text.replace(MENU_PREFIX, f"{MENU_PREFIX} · {note}", 1)
        return text, keyboard
    if data.startswith("m:strategy:"):
        arg = data.split(":", 2)[2]
        result = _apply_strategy(store, f"/strategy {arg}")   # reuse typed-path
        text, keyboard = _menu_main(cfg, store)
        lines = text.split("\n", 1)
        return f"{lines[0]}\n{result}\n{lines[1]}", keyboard
    return _menu_main(cfg, store)


def _status_text(store: StateStore, cfg: Config) -> str:
    states = store.load()
    enabled = is_enabled(store)
    override = store.get_control().get("strategy")
    eff = effective_strategy(cfg, store)
    source = "Telegram override" if override else "config default"
    lines = [(f"✅ Alerts: ON" if enabled else f"🛑 Alerts: OFF")]
    lines.append(f"🎯 Strategy: {eff} ({source})")
    lines.append(f"🔭 Watching: {', '.join(w.key for w in cfg.watches)}")
    for key in sorted(states):
        st = states[key]
        lines.append(f"📊 {key} side={st.last_side} · "
                     f"last bar {st.last_processed_bar or 'n/a'}")
        if st.last_event_side:
            lines.append(f"🔔 {key} last event: {st.last_event_side} @ "
                         f"{st.last_event_bar or '?'}")
    lines.append("Commands: /disable · /enable · /status · "
                 "/strategy qqe|ema20|both|default")
    return "\n".join(lines)


def _symbol_hint(store: StateStore) -> str:
    for key in store.load():
        if key != CONTROL_KEY:
            return key
    return "NIFTY1!"


def process_commands(cfg: Config, store: StateStore) -> bool:
    """Poll for owner input - typed commands AND ☰ menu taps; returns True
    when alerts should run.

    Menu taps (callback_query):
      * EVERY tap is acknowledged first (dismisses Telegram's spinner);
      * owner taps inside a menu EDIT that message in place (the pop-up UX);
      * owner taps under an alert/heartbeat SEND a fresh menu message, so the
        alert text is never destroyed;
      * non-owner taps are acknowledged but ignored.

    Typed commands (unchanged): every queued command is applied in order and
    acknowledged in a single combined reply ending with the current state.
    """
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        return True
    updates = fetch_updates(cfg.telegram_token)

    for cb in extract_callbacks(updates, cfg.telegram_chat_id):
        try:
            answer_callback(cfg.telegram_token, cb["id"])
        except NotifyError as exc:
            log.warning("menu ack failed: %s", exc)
        if not MENU_ENABLED:                 # paused: ack stops the spinner, done
            continue
        if not cb["from_owner"] or not cb["chat_id"] or cb["message_id"] is None:
            continue                            # someone else's tap: ack only
        if not cb["data"].startswith(CALLBACK_PREFIX):
            continue
        try:
            text, keyboard = _menu_action(cfg, store, cb)
            if cb["text"].startswith(MENU_PREFIX):
                edit_message(cfg.telegram_token, cb["chat_id"],
                             cb["message_id"], text, reply_markup=keyboard)
            else:                               # ☰ under an alert -> new message
                send_telegram(cfg.telegram_token, cb["chat_id"], text,
                              reply_markup=keyboard)
        except NotifyError as exc:
            log.warning("menu reply failed: %s", exc)

    cmds = extract_commands(updates, cfg.telegram_chat_id)
    if not cmds:
        return is_enabled(store)

    applied: list[str] = []
    for cmd in cmds:
        if cmd == "/disable":
            store.set_control({**store.get_control(), "enabled": False})
            applied.append("🛑 /disable → applied")
        elif cmd == "/enable":
            store.set_control({**store.get_control(), "enabled": True})
            applied.append("✅ /enable → applied")
        elif cmd.startswith("/strategy"):
            applied.append(_apply_strategy(store, cmd))
        else:                                     # /status - answered by summary
            applied.append("📊 /status → report below")

    _reply(cfg, "\n".join(applied) + "\n" + _status_text(store, cfg))
    return is_enabled(store)


def _apply_strategy(store: StateStore, cmd: str) -> str:
    """/strategy qqe|ema20|both|default -> persist override (keeps 'enabled')."""
    arg = cmd.split(" ", 1)[1] if " " in cmd else ""
    control = dict(store.get_control())
    if arg in ("default", "auto", "env"):
        control.pop("strategy", None)
        store.set_control(control)
        return "🎯 /strategy default → config value restored"
    canonical = normalize_strategy(arg)
    if canonical is None:
        return (f"❌ /strategy {arg or '?'} → use qqe | ema20 | both "
                f"(or 'default' to restore)")
    control["strategy"] = canonical
    store.set_control(control)                    # 'enabled' preserved by dict copy
    return f"🎯 /strategy {canonical} → applied (next scan uses it)"
