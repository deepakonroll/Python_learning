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
- FII/DII: NSE's public FII/DII endpoint, with a static GitHub JSON fallback;
  both are accepted only when dated to the previous NSE session (₹ cr).

Java equivalent: a QuoteService (3 feeds behind one facade) + a template.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import requests

from .providers.base import normalize_candles
from .providers.tv import TvProvider
from .providers.yahoo import YahooProvider
from .market_hours import is_trading_day, now_ist

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

# NSE's public FII/DII endpoint is primary. Keep the static GitHub JSON as a
# fallback, but accept either source only when it matches the prior session.
NSE_FII_DII_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_HOME_URL = "https://www.nseindia.com/"
FII_DII_URL = ("https://raw.githubusercontent.com/chirag127/fii-dii-activity-api/"
               "main/data/latest.json")
_TIMEOUT = 12.0
_USER_AGENT = "nse-alerter/1.0"
_NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/130 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/reports/fii-dii",
}


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
    out = ["Good Morning Deepak",
           "📊 World Markets Update",
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


def _previous_nse_session(as_of: date,
                           holidays: frozenset[date]) -> date:
    candidate = as_of - timedelta(days=1)
    while not is_trading_day(candidate, holidays):
        candidate -= timedelta(days=1)
    return candidate


def _parse_fii_dii(data) -> dict | None:
    """Normalize NSE's list response or the fallback provider's JSON object."""
    if isinstance(data, list):
        flows: dict[str, tuple[str, float]] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            category = str(row.get("category", "")).strip().upper()
            key = ("dii" if category == "DII" else
                   "fii" if category in {"FII", "FII/FPI", "FPI"} else None)
            if key:
                raw_date, net = row.get("date"), row.get("netValue")
                if raw_date and net is not None:
                    flows[key] = (str(raw_date), float(net))
        if "fii" not in flows or "dii" not in flows:
            return None
        fii_date, fii = flows["fii"]
        dii_date, dii = flows["dii"]
        if fii_date != dii_date:
            return None
        try:
            parsed_date = datetime.strptime(fii_date, "%d-%b-%Y").date()
        except ValueError:
            return None
        return {"date": parsed_date.isoformat(), "fii": fii, "dii": dii}

    if not isinstance(data, dict):
        return None
    equity = data.get("equity") or {}
    fii, dii, raw_date = (equity.get("fii_net"), equity.get("dii_net"),
                          data.get("date"))
    if fii is None or dii is None or not raw_date:
        return None
    try:
        return {"date": str(raw_date)[:10], "fii": float(fii),
                "dii": float(dii)}
    except (TypeError, ValueError):
        return None


def _fetch_nse_fii_dii():
    """Fetch NSE's report with a session so its homepage cookies are retained."""
    with requests.Session() as session:
        try:
            session.get(NSE_HOME_URL, headers=_NSE_HEADERS, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            log.warning("world: NSE FII/DII cookie warm-up failed: %s", exc)
        response = session.get(NSE_FII_DII_URL, headers=_NSE_HEADERS,
                               timeout=_TIMEOUT)
        response.raise_for_status()
        return response.json()


def fetch_fii_dii(as_of: datetime | date | None = None,
                  holidays: frozenset[date] = frozenset()) -> dict | None:
    """Get prior-session equity cash flows (₹ cr), rejecting stale payloads.

    NSE is primary; a static JSON mirror is fallback. In the production
    morning snapshot, ``as_of`` and the configured holidays are passed so a
    source is used only when its date equals the previous NSE trading session.
    """
    as_of_date = (as_of.date() if isinstance(as_of, datetime) else as_of)
    expected = _previous_nse_session(as_of_date or now_ist().date(), holidays)

    sources = ("nse", "mirror")
    for name in sources:
        try:
            if name == "nse":
                data = _fetch_nse_fii_dii()
            else:
                resp = requests.get(
                    FII_DII_URL, timeout=_TIMEOUT,
                    headers={"User-Agent": _USER_AGENT,
                             "Cache-Control": "no-cache"})
                resp.raise_for_status()
                data = resp.json()
            result = _parse_fii_dii(data)
        except Exception as exc:
            log.warning("world: FII/DII %s source unavailable: %s", name, exc)
            continue
        if result is None:
            log.warning("world: FII/DII %s payload missing valid net fields",
                        name)
            continue
        if result["date"] != expected.isoformat():
            log.warning("world: FII/DII %s data is stale (%s; expected %s) - "
                        "omitting", name, result["date"], expected.isoformat())
            continue
        log.info("world: FII/DII source=%s date=%s", name, result["date"])
        return result
    return None
