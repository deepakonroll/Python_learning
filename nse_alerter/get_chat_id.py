"""Find your Telegram chat id automatically (no browser/json digging needed).

Usage:
    1. Put the BotFather token into .env as TELEGRAM_BOT_TOKEN=...
    2. In Telegram: open YOUR bot → press START (or send "hi")
    3. python get_chat_id.py        -> prints the id and fills .env for you
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN is empty in .env")
        print("First paste the token BotFather gave you, then run this again.")
        return 1

    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                            timeout=10)
        data = resp.json()
    except Exception as exc:
        print(f"Could not reach Telegram: {exc}")
        return 1

    if not data.get("ok"):
        print(f"Telegram rejected the token: {data.get('description', 'unknown error')}")
        print("Re-copy it from @BotFather (it looks like 123456789:AA...)")
        return 1

    ids: list[str] = []
    for update in data.get("result") or []:
        message = update.get("message") or update.get("edited_message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id and chat_id not in ids:
            ids.append(chat_id)

    if not ids:
        print("Token works, but no messages reached your bot yet. Do this:")
        print('  1. In Telegram, search YOUR bot\'s username (e.g. "xyz_bot")')
        print("  2. Open it and press the big START button, or send: hi")
        print("  3. Run:  python get_chat_id.py")
        return 2

    chat_id = ids[-1]                                 # most recent conversation
    print(f"TELEGRAM_CHAT_ID = {chat_id}")

    env_path = BASE_DIR / ".env"
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
        match = re.search(r"^TELEGRAM_CHAT_ID=(.*)$", text, re.MULTILINE)
        if match and not match.group(1).strip():
            text = text[:match.start()] + f"TELEGRAM_CHAT_ID={chat_id}" + text[match.end():]
            env_path.write_text(text, encoding="utf-8")
            print("Saved into .env - you're done. Verify with:")
            print("  python main.py --test-notify")
        elif match and match.group(1).strip() != chat_id:
            print(f".env already has TELEGRAM_CHAT_ID={match.group(1).strip()} "
                  f"(different from {chat_id}) - keep whichever bot you use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
