"""Telegram notifications (the only channel in v1).

Plain HTTP POST with `requests` - no SDK needed. Email/ntfy can be added as
sibling functions later without touching the app flow (see README roadmap).

Java equivalent: RestTemplate/WebClient POST of a JSON body.
"""

from __future__ import annotations

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 10


class NotifyError(RuntimeError):
    """Delivery failed - the app keeps state unchanged so the send retries."""


def send_telegram(
    token: str,
    chat_id: str,
    text: str,
    poster=requests.post,
) -> dict:
    url = TELEGRAM_API.format(token=token)
    payload = {"chat_id": chat_id, "text": text}
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


def ping_message(symbol: str, interval: str, ema_len: int) -> str:
    """Credential check payload (named to avoid pytest collecting it)."""
    return (
        f"✅ NSE alerter test OK\n"
        f"Monitoring {symbol} · {interval} close vs EMA{ema_len} (cross alerts only)"
    )
