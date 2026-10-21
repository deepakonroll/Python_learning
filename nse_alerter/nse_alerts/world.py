"""09:05 world-markets snapshot: index closes vs prev day, GIFT Nifty, FII/DII.

Sent once per trading day at ~09:05 IST (5-9 min before the NSE open) so the
owner sees overnight global cues + prior-session institutional flows at a
glance. Every component is best-effort - the 7 Yahoo indices are the backbone
and a missing GIFT-Nifty or FII/DII row just omits that block, never the whole
message. build_message() is pure, so the formatter is fully offline-testable.

Sources:
- Yahoo (yfinance, existing provider): Nasdaq ^IXIC, Dow futures YM=F,
  FTSE ^FTSE, CAC ^FCHI, DAX ^GDAXI, Nikkei ^N225, Hang Seng ^HSI.
- TradingView (tvdatafeed, existing provider): GIFT Nifty NSEIX:NIFTY1!
  (NSE IFSC - the overnight Nifty cue; falls back to NSE:GIFTNIFTY spelling).
- FII/DII: chirag127/fii-dii-activity-api static JSON on raw.githubusercontent
  (GitHub warehouse keeps latest.json - prior-session equity cash flows, ₹ cr).

Java equivalent: a QuoteService (3 feeds behind one facade) + a template.
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from .providers.base import normalize_candles
from .providers.tv import TvProvider
from .providers.yahoo import YahooProvider

log = logging.getLogger("nse_alerts")

# region -> [(display label, yahoo ticker)] in message order
GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("🇺🇸 US", [("Nasdaq", "^IXIC"), ("Dow Fut", "YM=F")]),
    ("🇪🇺 Europe", [("FTSE 100", "^FTSE"), ("CAC 40", "^FCHI"),
                    ("DAX", "^GDAXI")]),
    ("🌏 Asia", [("Nikkei 225", "^N225"), ("Hang Seng", "^HSI")]),
]

# GIFT Nifty: TradingView symbol (NSE IFSC), then the NSE-spelling fallback.
_GIFT_TRIES = [("NSEIX", "NIFTY1!"), ("NSE", "GIFTNIFTY")]

# chirag127/fii-dii-activity-api - static JSON, updated ~18:30 IST each session
FII_DII_URL = ("https://raw.githubusercontent.com/chirag127/fii-dii-activity-api/"
               "main/data/latest.json")
_TIMEOUT = 12.0
_USER_AGENT = "nse-alerter/1.0"


# ---------------------------------------------------------------- formatter

def _arrow(value: float) -> str:
    """Green up, red down, grey flat - sign of the change, not of the mood."""
    return "🟢" if value > 0 else "🔴" if value < 0 else "⚪"


def _flow_word(net: float) -> str:
    """FII/DII cash flow wording: negative net = "net SOLD", else BOUGHT."""
    return "SOLD" if net < 0 else "BOUGHT"


def _flow_amount(net: float) -> str:
    """₹ crore with sign, always explicit: -₹5,286 cr / +₹3,319 cr."""
    return f"-₹{abs(net):,.0f} cr" if net < 0 else f"+₹{abs(net):,.0f} cr"


def _fmt_price(n: float) -> str:
    """23,084 or 23,084.5 (index value): whole when possible, else 1 decimal."""
    return f"{n:,.0f}" if abs(n - round(n)) < 1e-9 else f"{n:,.1f}"


def _fmt_points(n: float) -> str:
    """Signed point change: +96 / +96.5 / -42."""
    if abs(n - round(n)) < 1e-9:
        return f"{n:+,.0f}"
    return f"{n:+,.1f}"


def _fmt_date(iso: str | None) -> str:
    """'2026-10-15' -> '15 Oct' (the session the flows refer to)."""
    try:
        return datetime.strptime(str(iso)[:10], "%Y-%m-%d").strftime("%d %b")
    except (TypeError, ValueError):
        return "prior session"


def build_message(rows: dict[str, tuple[float, float]],
                  gift: tuple[float, float] | None = None,
                  fiidii: dict | None = None) -> str | None:
    """Compose the morning snapshot. rows = ticker -> (last, prev) closes from
    the Yahoo groups; gift = (last, prev) GIFT Nifty; fiidii = dict with
    date/fii/dii. None when every Yahoo row failed (nothing worth sending).
    """
    if not rows:
        return None
    out = ["⛅ Good Morning Deepak · 📊 World Markets Update",
           "────────────────────────────────"]
    for header, items in GROUPS:
        present = [(label, rows[sym]) for label, sym in items if sym in rows]
        if not present:
            continue
        out.append(header)
        for label, (last, prev) in present:
            if not prev:
                continue
            pct = (last / float(prev) - 1.0) * 100.0
            out.append(f"• {label:<10} {_arrow(pct)} {pct:+.2f}%")
    if gift:
        last, pts = gift[0], gift[0] - gift[1]
        out += ["", "🎯 GIFT Nifty",
                f"• {_fmt_price(last)}  {_arrow(pts)} {_fmt_points(pts)} pts"]
    if fiidii and fiidii.get("fii") is not None and fiidii.get("dii") is not None:
        fii, dii = fiidii["fii"], fiidii["dii"]
        out += ["", f"💼 FII/DII · {_fmt_date(fiidii.get('date'))}",
                f"• FII  {_arrow(fii)} net {_flow_word(fii)} {_flow_amount(fii)}",
                f"• DII  {_arrow(dii)} net {_flow_word(dii)} {_flow_amount(dii)}"]
    return "\n".join(out)
# ---------------------------------------------------------------- fetchers

def fetch_yahoo_rows() -> dict[str, tuple[float, float]]:
    """ticker -> (last, prev) daily close for every index group. One dead
    ticker is skipped (logged), never raised - the update can't be eaten by
    a single feed hiccup."""
    out: dict[str, tuple[float, float]] = {}
    provider = YahooProvider()
    for _, items in GROUPS:
        for label, sym in items:
            try:
                candles = provider.fetch(sym, "1d", 5)
                closes = candles["Close"].astype(float)
                if len(closes) >= 2:
                    out[sym] = (float(closes.iloc[-1]), float(closes.iloc[-2]))
            except Exception as exc:
                log.warning("world: %s (%s) unavailable: %s", label, sym, exc)
    return out


def fetch_gift() -> tuple[float, float] | None:
    """(last, prev) for GIFT Nifty daily bars via TradingView; None if the
    feed is down so the message simply omits the row."""
    try:
        from tvDatafeed import Interval
        client = TvProvider(attempts=2)._connect()
        for exchange, symbol in _GIFT_TRIES:
            try:
                raw = client.get_hist(symbol=symbol, exchange=exchange,
                                      interval=Interval.in_daily, n_bars=4)
                if raw is None or raw.empty:
                    continue
                closes = normalize_candles(raw)["Close"].astype(float)
                if len(closes) < 2:
                    continue
                return float(closes.iloc[-1]), float(closes.iloc[-2])
            except Exception:
                continue                        # try the next symbol spelling
    except Exception as exc:
        log.warning("world: GIFT Nifty unavailable: %s", exc)
    return None


def fetch_fii_dii() -> dict | None:
    """Prior-session FII/DII equity cash flows (₹ cr): date/fii/dii. None when
    the static JSON is unreachable or malformed."""
    try:
        resp = requests.get(FII_DII_URL, timeout=_TIMEOUT,
                            headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        data = resp.json()
        equity = data.get("equity") or {}

        fii, dii = equity.get("fii_net"), equity.get("dii_net")
        date = data.get("date")
        if fii is None or dii is None or not date:
            log.warning("world: FII/DII payload missing net fields: %s", data)
            return None
        return {"date": str(date)[:10], "fii": float(fii), "dii": float(dii)}
    except Exception as exc:
        log.warning("world: FII/DII unavailable: %s", exc)
        return None