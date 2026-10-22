"""09:20 opening-levels snapshot: NIFTY/SENSEX spot, India VIX, ATM straddles.

Sent once per trading day ~20 min after the NSE open (09:15) so the owner sees
actual opening levels and what the ATM straddles are worth while the session is
still young. Premiums reuse the expiry.py pricing kernels (r = 0, India VIX,
session-time T - whole 09:15-15:30 sessions to that contract's expiry, extended
across non-entry days).

Best-effort, like world.py: any missing row (spot, VIX, GIFT) omits its block,
never the whole message. build_message() is pure (offline-testable) and
fetch_levels() is the only I/O.

Java equivalent: a tiny quotation service (3 quote fetches + a BS calculator)
behind a facade + a message template.
"""

from __future__ import annotations

import logging

from .expiry import (DEFAULT_IV, EXPIRY_SPECS, EXPIRY_WEEKDAY, TRADING_DAYS,
                     atm_and_otm, bs_price)
from .providers.yahoo import YahooProvider
from .world import _arrow, _fmt_points, _fmt_price

log = logging.getLogger("nse_alerts")

# whole 09:15-15:30 sessions left until that contract expires, by weekday
# (Mon=0 ... Fri=4). Derived from expiry.ENTRY_SESSIONS (Mon NIFTY 1.92 =
# Mon + 0.92 of Tue expiry) and extended across the non-entry days:
#   NIFTY  (exp Tue): Mon 1.92 · Tue 0.92 · Wed 4.92 · Thu 3.92 · Fri 2.92
#   SENSEX (exp Thu): Mon 3.92 · Tue 2.92 · Wed 1.92 · Thu 0.92 · Fri 4.92
SESSIONS_LEFT = {
    "NIFTY":  {0: 1.92, 1: 0.92, 2: 4.92, 3: 3.92, 4: 2.92},
    "SENSEX": {0: 3.92, 1: 2.92, 2: 1.92, 3: 0.92, 4: 4.92},
}


def _next_expiry(weekday: int) -> tuple[str, float]:
    """(instrument, sessions) of the next expiring contract - the min across
    both tables, which reproduces the playbook rotation exactly: Mon/Tue =
    NIFTY (exp Tue), Wed/Thu = SENSEX (exp Thu), Fri = NIFTY."""
    best: tuple[str, float] = ("NIFTY", SESSIONS_LEFT["NIFTY"][weekday])
    for other in ("SENSEX",):
        sess = SESSIONS_LEFT[other][weekday]
        if sess < best[1]:
            best = (other, sess)
    return best


def _level_line(label: str, row: tuple[float, float]) -> list[str]:
    """'🇮🇳 NIFTY  23,089  🔴 −257 (−1.10%)' - level, signed points, pct.
    Small levels (VIX ~13) quote points to 1 decimal so +0.3 reads, not +0."""
    last, prev = row
    if not prev:
        return [f"{label:<9}  {_fmt_price(last)}"]
    pts = last - prev
    pct = pts / prev * 100.0
    dec = 1 if last < 1000 else 0
    return [f"{label:<9}  {_fmt_price(last):>9}  {_arrow(pts)} "
            f"{pts:+,.{dec}f} ({pct:+.2f}%)"]


def _straddle_line(instrument: str, spot: float, iv: float,
                   weekday: int) -> list[str]:
    """The ATM straddle block: strike + BS CE/PE premiums + total + % of spot."""
    step = EXPIRY_SPECS[instrument]["step"]
    strike = atm_and_otm(spot, step)[0]
    t = SESSIONS_LEFT[instrument][weekday] / TRADING_DAYS
    ce = bs_price(spot, strike, t, iv, "CE")
    pe = bs_price(spot, strike, t, iv, "PE")
    credit = ce + pe
    return [f"🧶 {instrument} ATM STRADDLE · strike {strike:,}",
            f"• CE ₹{ce:,.0f} · PE ₹{pe:,.0f} · total ₹{credit:,.0f} "
            f"(~{credit / spot * 100:.1f}% of spot)"]


def build_message(levels: dict, now) -> str | None:
    """Compose the snapshot. levels = dict from fetch_levels(): index -> inst
    -> (last, prev), vix -> (last, prev)|None, gift -> (last, prev)|None.
    None when neither Indian index was fetched (nothing worth sending)."""
    index = levels.get("index", {})
    vix = levels.get("vix")
    gift = levels.get("gift")
    if not index:
        return None
    iv = (vix[0] / 100.0 if vix and vix[0] else DEFAULT_IV / 100.0)
    weekday = now.weekday()
    inst, sessions = _next_expiry(weekday)
    out = [f"📊 OPENING LEVELS · {now:%a %d %b} · 09:20 · "
           f"exp {EXPIRY_WEEKDAY[inst]} · {sessions:.1f} sess",
           "──────────────────────────────────"]
    if "NIFTY" in index:
        out += _level_line("🇮🇳 NIFTY", index["NIFTY"])
    if vix:
        out += _level_line("😨 VIX", vix)
    if gift and gift[1]:
        last, prev = gift
        pts = last - prev
        out.append(f"{'🎯 GIFT':<7}  {_fmt_price(last):>9}  {_arrow(pts)} "
                   f"{_fmt_points(pts)} pts (overnight)")
    if "NIFTY" in index:
        out += ["", *_straddle_line("NIFTY", index["NIFTY"][0], iv, weekday)]
    if "SENSEX" in index:
        out += [""] + _level_line("🇮🇳 SENSEX", index["SENSEX"])
        out += ["", *_straddle_line("SENSEX", index["SENSEX"][0], iv, weekday)]
    out += ["", "🛡️ GUARD RAILS",
            "• −₹7k → WARN · −₹10k → EXIT, session over · flat ALL by 15:15"]
    return "\n".join(out)


def fetch_levels() -> dict:
    """Best-effort spot levels: index -> inst -> (last, prev) daily closes,
    vix -> (last, prev)|None, gift -> (last, prev)|None. One dead feed is
    logged and skipped, never raised."""
    result: dict = {"index": {}, "vix": None, "gift": None}
    provider = YahooProvider()
    for inst in ("NIFTY", "SENSEX"):
        sym = EXPIRY_SPECS[inst]["yahoo"]
        try:
            candles = provider.fetch(sym, "1d", 5)
            closes = candles["Close"].astype(float)
            if len(closes) >= 2:
                result["index"][inst] = (float(closes.iloc[-1]),
                                         float(closes.iloc[-2]))
        except Exception as exc:
            log.warning("snapshot: %s spot (%s) unavailable: %s", inst, sym, exc)
    try:
        candles = provider.fetch("^INDIAVIX", "1d", 5)
        closes = candles["Close"].astype(float)
        if len(closes) >= 2:
            result["vix"] = (float(closes.iloc[-1]), float(closes.iloc[-2]))
    except Exception as exc:
        log.warning("snapshot: India VIX unavailable: %s", exc)
    try:
        from .world import fetch_gift
        result["gift"] = fetch_gift()
    except Exception as exc:
        log.warning("snapshot: GIFT Nifty unavailable: %s", exc)
    return result