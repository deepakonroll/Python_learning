"""Telegram sender: payload shape, API rejection, network failure."""

import requests
import pytest

from nse_alerts.notify import NotifyError, ping_message, send_telegram


class FakeResponse:
    def __init__(self, ok=True, description=""):
        self._body = {"ok": ok, "description": description}
        self.status_code = 200

    def json(self):
        return self._body


def test_payload_shape():
    captured = {}

    def poster(url, json=None, timeout=None):
        captured.update(url=url, json=json, timeout=timeout)
        return FakeResponse()

    send_telegram("TOKEN123", "42", "hello", poster=poster)
    assert captured["url"].endswith("botTOKEN123/sendMessage")
    assert captured["json"] == {"chat_id": "42", "text": "hello"}
    assert captured["timeout"] == 10


def test_api_rejection_raises():
    def poster(url, json=None, timeout=None):
        return FakeResponse(ok=False, description="chat not found")

    with pytest.raises(NotifyError, match="chat not found"):
        send_telegram("t", "42", "x", poster=poster)


def test_network_error_raises_notify_error():
    def poster(url, json=None, timeout=None):
        raise requests.ConnectionError("boom")

    with pytest.raises(NotifyError, match="unreachable"):
        send_telegram("t", "42", "x", poster=poster)


def test_non_json_response_raises():
    class NoJson:
        status_code = 502

        def json(self):
            raise ValueError("nope")

    def poster(url, json=None, timeout=None):
        return NoJson()

    with pytest.raises(NotifyError, match="502"):
        send_telegram("t", "42", "x", poster=poster)


def test_test_message_mentions_the_rule():
    text = ping_message("NIFTY1!", "5m", "qqe")
    assert "NIFTY1!" in text and "5m" in text and "rule=qqe" in text


def test_reply_markup_included_when_provided():
    captured = {}

    def poster(url, json=None, timeout=None):
        captured.update(json or {})
        return FakeResponse()

    send_telegram("t", "42", "x", poster=poster, reply_markup={"keyboard": []})
    assert captured["reply_markup"] == {"keyboard": []}     # generic markup passthrough


def test_answer_callback_dismisses_spinner():
    from nse_alerts.notify import answer_callback

    captured = {}

    def poster(url, json=None, timeout=None):
        captured.update(url=url, json=json)
        return FakeResponse()

    answer_callback("tok", "cbq-1", poster=poster)
    assert captured["url"].endswith("bottok/answerCallbackQuery")
    assert captured["json"] == {"callback_query_id": "cbq-1"}


def test_edit_message_edits_in_place_with_markup():
    from nse_alerts.notify import edit_message

    captured = {}
    kb = {"inline_keyboard": [[{"text": "⬅️ Back", "callback_data": "m:main"}]]}

    def poster(url, json=None, timeout=None):
        captured.update(url=url, json=json)
        return FakeResponse()

    edit_message("tok", "42", 7, "🎛 Menu", reply_markup=kb, poster=poster)
    assert captured["url"].endswith("bottok/editMessageText")
    assert captured["json"]["chat_id"] == "42"
    assert captured["json"]["message_id"] == 7
    assert captured["json"]["reply_markup"] == kb


def test_menu_keyboard_is_inline_with_menu_button():
    from nse_alerts.notify import MENU_KEYBOARD

    button = MENU_KEYBOARD["inline_keyboard"][0][0]
    assert button["text"] == "☰ Menu"
    assert button["callback_data"] == "m:main"
