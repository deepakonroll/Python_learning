"""Telegram notifications (the only channel in v1).

Plain HTTP POST with `requests` - no SDK needed. Email/ntfy can be added as
sibling functions later without touching the app flow (see README roadmap).

Java equivalent: RestTemplate/WebClient POST of a JSON body.
"""

from __future__ import annotations

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 10

# Tappable command keyboard - attached to every message the bot sends, so the
# buttons are always visible after the first reply/heartbeat/alert.
KEYBOARD = {
    "keyboard": [[{"text": "/disable"}, {"text": "/enable"}, {"text": "/status"}],
                 [{"text": "/strategy both"}, {"text": "/strategy qqe"},
                  {"text": "/strategy ema20"}]],
    "is_persistent": True,
    "resize_keyboard": True,
}


class NotifyError(RuntimeError):
    """Delivery failed - the app keeps state unchanged so the send retries."""


def send_telegram(
    token: str,
    chat_id: str,
    text: str,
    poster=requests.post,
    reply_markup: dict | None = None,
) -> dict:
    url = TELEGRAM_API.format(token=token)
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup    # e.g. tappable command keyboard
    try:
        resp = poster(url, json=payload, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise NotifyError(f"telegram unreachable: {exc}") from exc

    try:
        body = resp.json()
    except ValueError as exc:
        raise NotifyError(f"telegram HTTP {resp.status_code}: non-JSON response") from exc
    if not body.get("ok"):
        raise NotifyError(f"telegram rejected message: {body.get('description', 'unknown error')}")
    return body


def ping_message(symbol: str, interval: str, strategy: str) -> str:
    """Credential check payload (named to avoid pytest collecting it)."""
    return (
        f"✅ NSE alerter test OK\n"
        f"Monitoring {symbol} · {interval} · rule={strategy} (cross alerts only)"
    )
