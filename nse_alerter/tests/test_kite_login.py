"""kite_login.py: token extraction, .env rotation, headless form flow."""

from __future__ import annotations

import hashlib

import pytest

from kite_login import (LoginError, auto_request_token, exchange_access_token,
                        extract_request_token, login_url, maybe_request_token,
                        save_env_token)


def test_extract_request_token_accepts_bare_token_or_redirect_url():
    assert extract_request_token("abc123") == "abc123"
    url = ("http://localhost:3000/?action=login&request_token=abc123"
           "&api_key=key")
    assert extract_request_token(url) == "abc123"
    # relative Location header straight off the login POST
    assert maybe_request_token("/connect/finish?request_token=xyz") == "xyz"
    assert maybe_request_token("https://kite.zerodha.com/connect/login") is None


def test_login_url_uses_official_v3_format():
    assert login_url("key123") == \
        "https://kite.zerodha.com/connect/login?v=3&api_key=key123"


def test_exchange_posts_documented_checksum(monkeypatch):
    seen: dict = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"status": "success", "data": {"access_token": "AT-1"}}

    def fake_post(url, timeout, data):
        seen["url"], seen["data"] = url, data
        return Resp()

    monkeypatch.setattr("kite_login.requests.post", fake_post)
    token = exchange_access_token("key1", "secret1", "req1")
    assert token == "AT-1"
    assert seen["url"] == "https://api.kite.trade/session/token"
    assert seen["data"]["api_key"] == "key1"
    assert seen["data"]["request_token"] == "req1"
    assert seen["data"]["checksum"] == hashlib.sha256(b"key1req1secret1").hexdigest()


def test_exchange_failure_raises_with_kite_message(monkeypatch):
    class Resp:
        status_code = 400

        def json(self):
            return {"status": "error", "message": "Token is invalid or has expired"}

    monkeypatch.setattr("kite_login.requests.post", lambda *a, **k: Resp())
    with pytest.raises(LoginError, match="invalid"):
        exchange_access_token("k", "s", "r")


def test_save_env_token_rotates_in_place(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=x\nKITE_ACCESS_TOKEN=old\nFOO=1\n",
                   encoding="utf-8")
    save_env_token("new-token", env)
    text = env.read_text(encoding="utf-8")
    assert "KITE_ACCESS_TOKEN=new-token" in text and "old" not in text
    assert "TELEGRAM_BOT_TOKEN=x" in text and "FOO=1" in text


def test_save_env_token_appends_then_rotates(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=x\n", encoding="utf-8")
    save_env_token("tok1", env)
    assert "KITE_ACCESS_TOKEN=tok1" in env.read_text(encoding="utf-8")
    save_env_token("tok2", env)                       # next morning: rotate
    text = env.read_text(encoding="utf-8")
    assert text.count("KITE_ACCESS_TOKEN=") == 1 and "tok2" in text


class FakeResp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code, self.text = status_code, text
        self.headers = headers or {}


class FakeSession:
    """Scripted get/post responses for the headless flow."""

    def __init__(self, posts, gets=None):
        self.posts, self.gets = list(posts), list(gets or [])
        self.post_calls, self.get_calls = [], []

    def get(self, url, **kw):
        self.get_calls.append(url)
        return self.gets.pop(0) if self.gets else FakeResp(text="")

    def post(self, url, data=None, **kw):
        self.post_calls.append(dict(data or {}))
        return self.posts.pop(0)


PAGE = '<input type="hidden" name="csrf_token" value="csrf1">'
STEP1 = ('<input name="request_id" value="rid-1">'
         '<input type="hidden" name="csrf_token" value="csrf2">')
STEP2 = '<input name="request_id" value="rid-2">'


def test_auto_flow_chains_steps_and_reads_token_from_location():
    session = FakeSession(
        posts=[FakeResp(text=STEP1),
               FakeResp(text=STEP2),
               FakeResp(302, headers={
                   "Location": "http://localhost:3000/?action=login"
                               "&request_token=RT-9&api_key=key"})],
        gets=[FakeResp(text=PAGE)],
    )
    token = auto_request_token("key", "USER1", "pw", "123456", session=session)
    assert token == "RT-9"
    first, second, third = session.post_calls
    assert first["user_id"] == "USER1" and first["csrf_token"] == "csrf1"
    assert second["password"] == "pw" and second["request_id"] == "rid-1"
    assert third["pin"] == "123456" and third["request_id"] == "rid-2"
    assert third["csrf_token"] == "csrf2"          # csrf carried forward


def test_auto_flow_follows_finish_redirect_with_skip_session():
    session = FakeSession(
        posts=[FakeResp(text=STEP1),
               FakeResp(text=STEP2),
               FakeResp(302, headers={
                   "Location": "/connect/finish?api_key=key&sess_id=s1"})],
        gets=[FakeResp(text=PAGE),
              FakeResp(302, headers={
                  "Location": "http://localhost:3000/?request_token=RT-FIN"})],
    )
    token = auto_request_token("key", "USER1", "pw", "123456", session=session)
    assert token == "RT-FIN"
    assert session.get_calls[1].endswith("skip_session=true")


def test_auto_flow_fails_loudly_when_form_changes():
    session = FakeSession(
        posts=[FakeResp(text="<html>new layout</html>")] * 3,
        gets=[FakeResp(text=PAGE)],
    )
    with pytest.raises(LoginError, match="browser mode"):
        auto_request_token("key", "USER1", "pw", "123456", session=session)