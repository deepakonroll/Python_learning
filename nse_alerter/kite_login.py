"""Daily Zerodha Kite access_token helper (the token dies every trading day).

Usage:
    python kite_login.py                # browser mode (recommended): prints and
                                        #   opens the Kite login page, you enter
                                        #   password+TOTP, the post-login redirect
                                        #   to http://localhost:3000 is caught by
                                        #   a tiny local server, and the resulting
                                        #   KITE_ACCESS_TOKEN is saved into .env
    python kite_login.py --url "<redirect url copied from the address bar>"
    python kite_login.py --token <request_token>
    python kite_login.py --auto         # headless: needs KITE_USER_ID /
                                        #   KITE_PASSWORD / KITE_TOTP_SECRET in
                                        #   .env (unofficial form flow - browser
                                        #   mode always works if it breaks)

Needs KITE_API_KEY + KITE_API_SECRET in .env (app page at developers.kite.trade;
the redirect_url registered on the app must be http://localhost:3000 for the
browser mode's local catch). Token expires ~6 AM IST next day - re-run every
trading morning (an 08:55 Task Scheduler job is ideal). If the token is stale
the bot is fine anyway: DATA_PROVIDER=kite falls back to the free
TradingView/yahoo chain automatically (README section 6).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

KITE_LOGIN = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
KITE_TOKEN_URL = "https://api.kite.trade/session/token"
ENV_PATH = Path(__file__).resolve().parent / ".env"

# hidden form fields that chain the login steps together (the order of the
# name= / value= attributes varies in the HTML)
_HIDDEN = re.compile(r'name="(request_id|csrf_token)"\s+value="([^"]+)"')
_HIDDEN_FLIP = re.compile(r'value="([^"]+)"\s+name="(request_id|csrf_token)"')


class LoginError(RuntimeError):
    """Kite login/exchange failure with a message safe to print."""


def login_url(api_key: str) -> str:
    """Official v3 login URL (docs: kite.trade/docs/connect/v3)."""
    return KITE_LOGIN.format(api_key=api_key)


def maybe_request_token(value: str) -> str | None:
    """request_token inside a URL/query string, else None."""
    if not value or "request_token=" not in value:
        return None
    text = value if "://" in value else "https://kite.local" + (
        value if value.startswith("/") else "/" + value)
    tokens = parse_qs(urlparse(text).query).get("request_token")
    return tokens[0] if tokens else None


def extract_request_token(value: str) -> str:
    """Bare request_token, or the full redirect URL containing one."""
    token = maybe_request_token(value.strip())
    return token if token else value.strip()


def exchange_access_token(api_key: str, api_secret: str, request_token: str) -> str:
    """request_token -> access_token via POST /session/token (official flow):
    checksum = sha256(api_key + request_token + api_secret)."""
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()
    resp = requests.post(KITE_TOKEN_URL, timeout=15, data={
        "api_key": api_key, "request_token": request_token, "checksum": checksum,
    })
    try:
        payload = resp.json()
    except ValueError as exc:
        raise LoginError(
            f"kite session/token returned non-JSON (HTTP {resp.status_code})") from exc
    data = payload.get("data") or {}
    if payload.get("status") != "success" or not data.get("access_token"):
        raise LoginError(f"kite token exchange failed: "
                         f"{payload.get('message') or payload}")
    return data["access_token"]


def save_env_token(access_token: str, env_path: Path = ENV_PATH) -> None:
    """Overwrite KITE_ACCESS_TOKEN in .env (always replace - it rotates daily)."""
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    if re.search(r"^KITE_ACCESS_TOKEN=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^KITE_ACCESS_TOKEN=.*$", f"KITE_ACCESS_TOKEN={access_token}",
                      text, flags=re.MULTILINE)
    else:
        text = (text.rstrip("\n")
                + "\n\n# written by kite_login.py (expires ~6 AM IST next day)\n"
                + f"KITE_ACCESS_TOKEN={access_token}\n")
    env_path.write_text(text, encoding="utf-8")


def _hidden_fields(html: str) -> dict:
    fields = dict(_HIDDEN.findall(html))
    for value, name in _HIDDEN_FLIP.findall(html):
        fields.setdefault(name, value)
    return fields


def _follow_to_token(session, location: str) -> str:
    """Location header -> request_token. Never connects to the redirect host:
    the localhost redirect is read straight from the header (no listener
    needed), and Kite's /connect/finish hop is re-requested with
    skip_session=true per the community-tested flow."""
    if not location:
        raise LoginError("kite login: empty redirect (wrong password/TOTP?)")
    if location.startswith("/"):
        location = "https://kite.zerodha.com" + location
    token = maybe_request_token(location)
    if token:
        return token
    if "/connect/finish" in location:
        sep = "&" if "?" in location else "?"
        resp = session.get(location + sep + "skip_session=true",
                           allow_redirects=False, timeout=15)
        nxt = resp.headers.get("Location", "")
        token = maybe_request_token(nxt)
        if token:
            return token
        raise LoginError(f"kite login: no request_token in {nxt!r} - TOTP "
                         "rejected or login rate-limited (retry in a minute; "
                         "browser mode always works)")
    raise LoginError(f"kite login: unrecognised redirect {location!r}")


def auto_request_token(api_key: str, user_id: str, password: str, totp: str,
                       session=None) -> str:
    """Headless login (UNOFFICIAL - Zerodha can change the form any day; the
    browser mode above always works). Three chained POSTs - user_id ->
    password -> TOTP pin - carrying the form's csrf_token/request_id."""
    session = session or requests.Session()
    url = login_url(api_key)
    resp = session.get(url, timeout=15)
    hidden = _hidden_fields(resp.text)
    csrf, request_id = hidden.get("csrf_token", ""), ""
    for payload in ({"user_id": user_id}, {"password": password}, {"pin": totp}):
        data = {"csrf_token": csrf, **payload}
        if request_id:
            data["request_id"] = request_id
        resp = session.post(url, data=data, allow_redirects=False, timeout=15)
        if resp.status_code in (301, 302, 303):
            return _follow_to_token(session, resp.headers.get("Location", ""))
        hidden = _hidden_fields(resp.text)
        csrf = hidden.get("csrf_token", csrf)
        request_id = hidden.get("request_id", request_id)
    raise LoginError("kite headless login did not redirect after the TOTP step "
                     "(form may have changed - use browser mode: "
                     "python kite_login.py)")


def serve_redirect_url(api_key: str, port: int = 3000, timeout: int = 240) -> str:
    """Open the Kite login page and catch the post-login redirect locally.

    The app's redirect_url is http://localhost:3000; after TOTP the browser
    lands here with ?request_token=... in the query string.
    """
    import webbrowser
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    done: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                # noqa: N802 (stdlib name)
            if "request_token" in self.path:
                done["url"] = f"http://localhost:{port}{self.path}"
                body = b"OK - request token captured. You can close this tab."
            else:
                body = b"Waiting for the Kite login redirect..."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):                    # quiet
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise LoginError(f"cannot listen on port {port} ({exc}) - free the port "
                         "or use --url '<redirect url>'") from exc
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = login_url(api_key)
        webbrowser.open(url)
        print(f"Opened {url}")
        print(f"Waiting up to {timeout}s for the redirect on "
              f"http://localhost:{port} - enter password + TOTP in the browser...")
        deadline = time.monotonic() + timeout
        while "url" not in done and time.monotonic() < deadline:
            time.sleep(0.25)
    finally:
        server.shutdown()
        server.server_close()
    if "url" not in done:
        raise LoginError("timed out waiting for the Kite login redirect "
                         "(is port 3000 free? or use --url '<redirect url>')")
    return done["url"]


def main(argv=None) -> int:
    load_dotenv(ENV_PATH)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="full redirect URL (or bare request_token)")
    parser.add_argument("--token", help="bare request_token")
    parser.add_argument("--auto", action="store_true",
                        help="headless login via KITE_USER_ID/KITE_PASSWORD/"
                             "KITE_TOTP_SECRET (unofficial flow)")
    parser.add_argument("--port", type=int, default=3000,
                        help="localhost port for browser-mode redirect catch")
    args = parser.parse_args(argv)

    api_key = os.environ.get("KITE_API_KEY", "")
    api_secret = os.environ.get("KITE_API_SECRET", "")
    if not api_key or not api_secret:
        print("ERROR: KITE_API_KEY and KITE_API_SECRET must be set in .env "
              "(developers.kite.trade -> My Apps)")
        return 1
    try:
        if args.url or args.token:
            request_token = extract_request_token(args.url or args.token or "")
        elif args.auto:
            missing = [name for name in ("KITE_USER_ID", "KITE_PASSWORD",
                                         "KITE_TOTP_SECRET")
                       if not os.environ.get(name)]
            if missing:
                print(f"ERROR: --auto needs {', '.join(missing)} in .env")
                return 1
            import pyotp
            request_token = auto_request_token(
                api_key,
                os.environ["KITE_USER_ID"],
                os.environ["KITE_PASSWORD"],
                pyotp.TOTP(os.environ["KITE_TOTP_SECRET"]).now(),
            )
        else:
            request_token = extract_request_token(
                serve_redirect_url(api_key, port=args.port))
        access_token = exchange_access_token(api_key, api_secret, request_token)
        save_env_token(access_token)
    except LoginError as exc:
        print(f"ERROR: {exc}")
        return 1
    print("OK - KITE_ACCESS_TOKEN saved to .env (valid until ~6 AM IST tomorrow)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())