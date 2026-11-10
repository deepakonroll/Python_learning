"""Exchange a Kite request token and rotate the GitHub Actions secret.

Required environment variables are supplied by the owner-only refresh workflow.
Never print request/access tokens or API responses: both tokens are credentials.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sys

import requests
from nacl.public import PublicKey, SealedBox

KITE_TOKEN_URL = "https://api.kite.trade/session/token"
GITHUB_API = "https://api.github.com"
SECRET_NAME = "KITE_ACCESS_TOKEN"


def _mask(value: str) -> None:
    """Tell the Actions runner to redact a value from subsequent log output."""
    if value and "\n" not in value and "\r" not in value:
        print(f"::add-mask::{value}", flush=True)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Required Actions secret/input is missing: {name}")
    return value


def main() -> int:
    request_token = os.environ.get("KITE_REQUEST_TOKEN", "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", request_token):
        _mask(request_token)
    else:
        print("::error::Invalid request token. Paste only the request_token value.")
        return 2

    try:
        api_key = _required("KITE_API_KEY")
        api_secret = _required("KITE_API_SECRET")
        github_token = _required("GITHUB_SECRET_UPDATE_TOKEN")
        repository = _required("GITHUB_REPOSITORY")
    except ValueError as exc:
        print(f"::error::{exc}")
        return 2

    checksum = hashlib.sha256(
        f"{api_key}{request_token}{api_secret}".encode("utf-8")
    ).hexdigest()
    try:
        response = requests.post(
            KITE_TOKEN_URL,
            data={"api_key": api_key, "request_token": request_token,
                  "checksum": checksum},
            timeout=20,
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        print("::error::Kite token exchange failed. Check the login and retry.")
        return 1

    access_token = (payload.get("data") or {}).get("access_token")
    if response.status_code != 200 or payload.get("status") != "success" or not access_token:
        message = payload.get("message") if isinstance(payload, dict) else None
        detail = f": {message}" if message else ""
        print(f"::error::Kite rejected the request token (HTTP "
              f"{response.status_code}){detail}")
        return 1
    _mask(access_token)

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_url = f"{GITHUB_API}/repos/{repository}/actions/secrets"
    try:
        key_response = requests.get(f"{base_url}/public-key", headers=headers,
                                   timeout=20)
        key_response.raise_for_status()
        public_key_data = key_response.json()
        public_key = PublicKey(base64.b64decode(public_key_data["key"]))
        encrypted = SealedBox(public_key).encrypt(access_token.encode("utf-8"))
        update_response = requests.put(
            f"{base_url}/{SECRET_NAME}",
            headers=headers,
            json={"encrypted_value": base64.b64encode(encrypted).decode("ascii"),
                  "key_id": public_key_data["key_id"]},
            timeout=20,
        )
    except (requests.RequestException, ValueError, KeyError, TypeError):
        print("::error::Kite exchange succeeded, but GitHub secret update failed. "
              "Check KITE_SECRET_UPDATE_TOKEN permissions; log in again before retrying.")
        return 1

    if update_response.status_code not in (201, 204):
        print("::error::Kite exchange succeeded, but GitHub secret update failed. "
              "Check KITE_SECRET_UPDATE_TOKEN permissions; log in again before retrying.")
        return 1

    print("KITE_ACCESS_TOKEN was updated in repository Actions secrets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
