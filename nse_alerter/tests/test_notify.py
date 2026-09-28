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
    text = ping_message("NIFTY1!", "5m", 20)
    assert "NIFTY1!" in text and "EMA20" in text and "5m" in text


def test_reply_markup_included_when_provided():
    captured = {}

    def poster(url, json=None, timeout=None):
        captured.update(json or {})
        return FakeResponse()

    send_telegram("t", "42", "x", poster=poster, reply_markup={"keyboard": []})
    assert captured["reply_markup"] == {"keyboard": []}     # tappable command keys
