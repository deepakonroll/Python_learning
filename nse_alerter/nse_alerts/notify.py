"""Telegram notifications (the only channel in v1).

Plain HTTP POST with `requests` - no SDK needed. Email/ntfy can be added as
sibling functions later without touching the app flow (see README roadmap).

Java equivalent: RestTemplate/WebClient POST of a JSON body.
"""

from __future__ import annotations

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 10

# Pop-up menu button - an INLINE keyboard attached to every message the bot
# sends (alerts, heartbeats, replies). Tapping it opens the ☰ menu, which
# then edits ITSELF in place (submenus replace the message text).
MENU_KEYBOARD = {
    "inline_keyboard": [[{"text": "☰ Menu", "callback_data": "m:main"}]],
}

# Inline taps arrive as callback_query updates; they must be acknowledged via
# answerCallbackQuery (otherwise Telegram shows a loading spinner forever) and
# menus are updated via editMessageText (the "pop up / replace" behaviour).
ANSWER_API = "https://api.telegram.org/bot{token}/answerCallbackQuery"
EDIT_API = "https://api.telegram.org/bot{token}/editMessageText"


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


def _post(url: str, payload: dict, poster) -> dict:
    """Shared POST + ok-check for the small Telegram methods."""
    try:
        resp = poster(url, json=payload, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise NotifyError(f"telegram unreachable: {exc}") from exc
    try:
        body = resp.json()
    except ValueError as exc:
        raise NotifyError(f"telegram HTTP {resp.status_code}: non-JSON response") from exc
    if not body.get("ok"):
        raise NotifyError(f"telegram rejected: {body.get('description', 'unknown error')}")
    return body


def answer_callback(token: str, callback_query_id: str, poster=requests.post) -> dict:
    """Acknowledge an inline-button tap (dismiss Telegram's loading spinner).

    Must be called for EVERY callback_query received - even ignored ones -
    or the user's tap appears stuck.
    """
    return _post(ANSWER_API.format(token=token),
                 {"callback_query_id": callback_query_id}, poster)


def edit_message(token: str, chat_id: str, message_id: int, text: str,
                 reply_markup: dict | None = None, poster=requests.post) -> dict:
    """Edit an existing message in place - the menu's 'pop up / replace' effect."""
    payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post(EDIT_API.format(token=token), payload, poster)


def ping_message(symbol: str, interval: str, strategy: str) -> str:
    """Credential check payload (named to avoid pytest collecting it)."""
    return (
        f"✅ NSE alerter test OK\n"
        f"Monitoring {symbol} · {interval} · rule={strategy} (cross alerts only)"
    )
