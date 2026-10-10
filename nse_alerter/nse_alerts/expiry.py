"""Intraday strangle playbook: the approved rotation + the 09:45 entry alert.

Approved rotation (owner, 2026-10-09, VIX-ladder sweep - intraday only,
flat by 15:15, NO overnight positions): every weekday is an entry day -
    NIFTY (exp Tue): Fri 2.9 sess · Mon 1.9 sess · Tue 0.92 sess (0-DTE)
    SENSEX (exp Thu): Wed 1.9 sess · Thu 0.92 sess (0-DTE = NO ENTRY:
    measured E < 0 at every VIX on 3y of daily paths, sweep 2026-10-09)
Strike rule (both legs): closer 0.95% OTM, credit floor 18/40 pts, 20 lots
fixed, whole-position stop -0.5C (-1.0C on NIFTY 0-DTE), target = session
decay to 15:15. Expected ~Rs 6.7k/day at 20 lots (VIX 11-14 average).
The Mon/Wed/Fri envelope pilot card keeps its own rules (envelope runs daily).

SENSEX data note - verified 2026-10-02: TradingView `BSE:SENSEX` and Yahoo
`^BSESN` both print the true index (Yahoo's 2026-10-01 close 71,909.70 =
press close 71,909) and match the owner's own broker strikes (74,300-75,300
traded for 2026-09-10 expiry vs Yahoo 74,902.60 that day). The earlier
"15% below broker" reading (synthetic NIFTY x 3.72) was a misdiagnosis.

Premiums in the alert are Black-Scholes *indicatives* (r = 0, India VIX) -
the discipline rule stays: stops sit at 2x the ACTUAL fill, placed as orders
at entry. Java equivalent: a small pricing service + a message template.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, time
from statistics import NormalDist

log = logging.getLogger("nse_alerts")

# weekday -> (instrument, sessions left at the 09:45 entry); Mon=0 ... Sun=6.
# NIFTY expires Tuesday, SENSEX Thursday - entries are the days before them.
ENTRY_SESSIONS: dict[int, tuple[str, float]] = {
    0: ("NIFTY", 1.92), 1: ("NIFTY", 0.92), 2: ("SENSEX", 1.92),
    3: ("SENSEX", 0.92), 4: ("NIFTY", 2.92),
}
EXPIRY_ROTATION: dict[int, str] = {wd: inst
                                   for wd, (inst, _) in ENTRY_SESSIONS.items()}
EXPIRY_DAYS = frozenset(EXPIRY_ROTATION)
EXPIRY_WEEKDAY = {"NIFTY": "Tue", "SENSEX": "Thu"}   # the contract's expiry day

# strike step, lot size, provider symbols and display extras per instrument
EXPIRY_SPECS: dict[str, dict] = {
    "NIFTY":  {"step": 50,  "lot": 65, "exchange": "NSE",
               "tv": "NSE:NIFTY1!", "yahoo": "^NSEI"},
    "SENSEX": {"step": 100, "lot": 20, "exchange": "BSE",
               "tv": "BSE:SENSEX",  "yahoo": "^BSESN"},
}

CLOSER_DIST = 0.0095           # both legs, 0.95% (the 0.9-1.0% measured band)
CREDIT_FLOOR = {"NIFTY": 18.0, "SENSEX": 40.0}  # min credit, both legs, pts
FIXED_LOTS = 20                # owner-locked size (2026-10-09)
NO_ENTRY_DAYS = {3: "SENSEX 0-DTE loses at every VIX (3y sweep 2026-10-09)"}
THETA_MINUTES = 330.0          # 09:45 -> 15:15 target window
SESSION_MINUTES = 375.0        # 09:15 -> 15:30 full session
TRADING_DAYS = 252.0           # session-time annualisation (intraday = no gap)
DEFAULT_IV = 13.0          # India VIX fallback (percent) when the feed fails
ALERT_FROM = time(9, 45)   # entry-alert window; cron ticks every 5 min inside
ALERT_UNTIL = time(10, 15) # a failed send retries each tick until this

_N = NormalDist().cdf
_MINUTES_PER_YEAR = 365 * 24 * 60


def bs_price(spot: float, strike: float, t_years: float, iv: float,
             kind: str) -> float:
    """Black-Scholes premium, r = 0 (European - fine for 0DTE levels)."""
    if t_years <= 0 or iv <= 0:                     # expiry: intrinsic only
        intrinsic = spot - strike if kind == "CE" else strike - spot
        return max(intrinsic, 0.0)
    vol = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t_years) / vol
    d2 = d1 - vol
    if kind == "CE":
        return spot * _N(d1) - strike * _N(d2)
    return strike * _N(-d2) - spot * _N(-d1)


def atm_and_otm(spot: float, step: int) -> tuple[int, int, int]:
    """(ATM, 1-strike-OTM CE, 1-strike-OTM PE) on the instrument's grid."""
    atm = int(round(spot / step)) * step
    return atm, atm + step, atm - step


def strike_rule(instrument: str, weekday: int, spot: float,
                iv: float) -> dict:
    """The measured entry rule: closer 0.95% strikes + floor + stop/target.

    Pure decision (no I/O): returns the full ladder row for this moment -
    strikes, credit, session-decay target, whole-position stop, qty - or
    skip != None when the day must NOT be traded. T is session-time
    (trading hours only) because positions are intraday, never overnight.
    """
    spec = EXPIRY_SPECS[instrument]
    step, lot = spec["step"], spec["lot"]
    sessions = ENTRY_SESSIONS[weekday][1]
    ce_k = int(round(spot * (1.0 + CLOSER_DIST) / step)) * step
    pe_k = int(round(spot * (1.0 - CLOSER_DIST) / step)) * step
    t = sessions / TRADING_DAYS
    ce = bs_price(spot, ce_k, t, iv, "CE")
    pe = bs_price(spot, pe_k, t, iv, "PE")
    credit = ce + pe
    t_close = max(t - THETA_MINUTES / SESSION_MINUTES / TRADING_DAYS, 0.0)
    decay = credit - (bs_price(spot, ce_k, t_close, iv, "CE")
                      + bs_price(spot, pe_k, t_close, iv, "PE"))
    floor = CREDIT_FLOOR[instrument]
    zero_dte = sessions < 1.2
    # 0-DTE NIFTY needs the wide stop: -0.5C is tripped by daily noise
    # (measured p_stop 0.73-0.80 -> negative E; -1.0C turns it positive)
    stop_frac = 1.0 if (zero_dte and instrument == "NIFTY") else 0.5
    skip = None
    if weekday in NO_ENTRY_DAYS:
        skip = NO_ENTRY_DAYS[weekday]
    elif credit < floor:
        skip = f"credit ₹{credit:,.0f} < floor ₹{floor:,.0f} - no edge here"
    qty = FIXED_LOTS * lot
    return {
        "instrument": instrument, "weekday": weekday, "sessions": sessions,
        "zero_dte": zero_dte, "dist": CLOSER_DIST, "ce": ce_k, "pe": pe_k,
        "ce_prem": ce, "pe_prem": pe, "credit": credit, "floor": floor,
        "decay": decay, "stop_frac": stop_frac,
        "stop_pts": stop_frac * credit, "stop_rs": stop_frac * credit * qty,
        "target_rs": decay * qty, "lots": FIXED_LOTS, "qty": qty,
        "skip": skip,
    }


def fetch_india_vix(default: float = DEFAULT_IV) -> tuple[float, str]:
    """(iv as decimal, source label) - latest India VIX close, range-checked."""
    try:
        from .providers.yahoo import YahooProvider
        candles = YahooProvider().fetch("^INDIAVIX", "1d", 5)
        iv = float(candles["Close"].iloc[-1]) / 100.0   # prints 14.2 -> 0.142
        if not 0.05 < iv < 0.60:
            raise ValueError(f"implausible VIX {iv * 100:.1f}")
        return iv, "india-vix"
    except Exception as exc:                            # feed/parse failure
        log.warning("India VIX unavailable (%s) - using default %.1f%%",
                    exc, default)
        return default / 100.0, "default"


def build_alert(instrument: str, spot: float, bar_time: datetime,
                now: datetime, iv: float, iv_src: str, source: str) -> str:
    """The 09:45 message: strike_rule() decision + premiums + stops + rules."""
    rule = strike_rule(instrument, now.weekday(), spot, iv)
    tag = (f"{instrument} 0DTE strangle" if rule["zero_dte"]
           else f"{instrument} strangle · exp {EXPIRY_WEEKDAY[instrument]}")
    head = f"🎯 09:45 EXPIRY ENTRY · {now:%a %d %b} · {tag}\n"
    if rule["skip"]:
        return (
            head
            + f"⛔ NO ENTRY · {rule['skip']}\n"
            f"levels: {rule['ce']:,} CE / {rule['pe']:,} PE @ "
            f"{rule['dist'] * 100:.2f}% · spot {spot:,.0f} · "
            f"IV {iv * 100:.1f}% ({iv_src})\n"
            f"{rule['sessions']:.1f} sessions left · credit would be "
            f"₹{rule['credit']:,.0f} (floor ₹{rule['floor']:,.0f}) · "
            f"{rule['lots']} lots = {rule['qty']:,} qty\n"
            "stay FLAT · envelope signals still valid · flat ALL by 15:15"
        )
    return (
        head
        + f"rule CLOSER {rule['dist'] * 100:.2f}% · {rule['sessions']:.1f} sess"
        f" · spot {spot:,.0f} · IV {iv * 100:.1f}% ({iv_src})\n"
        f"SELL {rule['ce']:,} CE ~ ₹{rule['ce_prem']:,.0f} · "
        f"BUY STOP ₹{2 * rule['ce_prem']:,.0f} (2×)\n"
        f"SELL {rule['pe']:,} PE ~ ₹{rule['pe_prem']:,.0f} · "
        f"BUY STOP ₹{2 * rule['pe_prem']:,.0f} (2×)\n"
        f"credit ₹{rule['credit']:,.0f} · portfolio stop −{rule['stop_frac']:.1f}C"
        f" = −{rule['stop_pts']:,.0f} pts (−₹{rule['stop_rs']:,.0f}) · "
        f"target +{rule['decay']:,.0f} pts decay (+₹{rule['target_rs']:,.0f})\n"
        f"size {rule['lots']} lots ({rule['qty']:,} qty) · ONE entry · NO adds\n"
        "⏰ square off ALL by 15:15 · no re-entry · journal the trade\n"
        "storm rule: 2× on EITHER leg → exit BOTH · premiums are "
        "BS-indicative — stops go at 2× your actual fill"
    )
