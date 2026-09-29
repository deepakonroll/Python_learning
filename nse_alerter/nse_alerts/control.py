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
from .notify import KEYBOARD, NotifyError, send_telegram
from .state import StateStore

log = logging.getLogger("nse_alerts")

CONTROL_KEY = "__control__"           # reserved key inside state.json
COMMANDS = ("/disable", "/enable", "/status", "/strategy")


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
        send_telegram(cfg.telegram_token or "", cfg.telegram_chat_id or "",
                      text, reply_markup=KEYBOARD)
    except NotifyError as exc:               # a failed reply must never kill alerts
        log.warning("command reply failed: %s", exc)


def _status_text(store: StateStore, cfg: Config) -> str:
    symbol = _symbol_hint(store)
    state = store.load().get(symbol)
    enabled = is_enabled(store)
    override = store.get_control().get("strategy")
    eff = effective_strategy(cfg, store)
    source = "Telegram override" if override else "config default"
    lines = [(f"✅ Alerts: ON" if enabled else f"🛑 Alerts: OFF")]
    lines.append(f"🎯 Strategy: {eff} ({source})")
    if state:
        lines.append(f"📊 {symbol} side={state.last_side} · "
                     f"last bar {state.last_processed_bar or 'n/a'}")
        if state.last_event_side:
            lines.append(f"🔔 last event: {state.last_event_side} @ "
                         f"{state.last_event_bar or '?'}")
    lines.append("Commands: /disable · /enable · /status · "
                 "/strategy qqe|ema20|both|default")
    return "\n".join(lines)


def _symbol_hint(store: StateStore) -> str:
    for key in store.load():
        if key != CONTROL_KEY:
            return key
    return "NIFTY1!"


def process_commands(cfg: Config, store: StateStore) -> bool:
    """Poll for owner commands; returns True when alerts should run.

    EVERY queued command is applied in chronological order and acknowledged in
    a single combined reply that always ends with the current state - so you
    always get a definitive 'yes it took effect' answer, never silence.
    """
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        return True
    cmds = extract_commands(fetch_updates(cfg.telegram_token), cfg.telegram_chat_id)
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
