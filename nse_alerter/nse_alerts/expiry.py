"""0DTE expiry-day playbook: the approved rotation + the 09:45 entry alert.

Approved rotation (owner, 2026-10-03):
    Tue = NIFTY 0DTE strangle   Thu = SENSEX 0DTE strangle
    Mon/Wed/Fri = Magic Envelope pilot (NIFTY + crude)

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

# weekday -> instrument (Mon=0 ... Sun=6); only these days trade strangles
EXPIRY_ROTATION: dict[int, str] = {1: "NIFTY", 3: "SENSEX"}
EXPIRY_DAYS = frozenset(EXPIRY_ROTATION)

# strike step, lot size, provider symbols and display extras per instrument
EXPIRY_SPECS: dict[str, dict] = {
    "NIFTY":  {"step": 50,  "lot": 65, "exchange": "NSE",
               "tv": "NSE:NIFTY1!", "yahoo": "^NSEI"},
    "SENSEX": {"step": 100, "lot": 20, "exchange": "BSE",
               "tv": "BSE:SENSEX",  "yahoo": "^BSESN"},
}

RISK_BUDGET = 10_000.0     # rupees per strangle (owner's sizing rule)
MAX_LOTS = 2               # ... which lands at 1-2 lots in practice
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


def size_lots(premium_sum: float, lot: int) -> int:
    """Rs 10k budget / (strangle premium x lot), clamped to 1..2 lots."""
    if premium_sum <= 0:
        return MAX_LOTS
    lots = int(RISK_BUDGET // (premium_sum * lot))
    return max(1, min(MAX_LOTS, lots))


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
    """The 09:45 message: strikes, indicative premiums, 2x stops, size, rules."""
    spec = EXPIRY_SPECS[instrument]
    step, lot = spec["step"], spec["lot"]
    atm, ce_k, pe_k = atm_and_otm(spot, step)
    # 0DTE time value: minutes left until the 15:30 IST close of the same day
    minutes_left = max(
        (now.replace(hour=15, minute=30, second=0, microsecond=0) - now)
        .total_seconds() / 60.0,
        1.0,
    )
    t_years = minutes_left / _MINUTES_PER_YEAR
    ce_prem = bs_price(spot, ce_k, t_years, iv, "CE")
    pe_prem = bs_price(spot, pe_k, t_years, iv, "PE")
    lots = size_lots(ce_prem + pe_prem, lot)
    return (
        f"🎯 09:45 EXPIRY ENTRY · {now:%a %d %b} · {instrument} 0DTE strangle\n"
        f"spot {spot:,.0f} · ATM {atm:,} · bar {bar_time:%H:%M} IST · "
        f"src={source} · IV {iv * 100:.1f}% ({iv_src})\n"
        f"SELL {ce_k:,} CE ~ ₹{ce_prem:,.0f} · "
        f"BUY STOP ₹{2 * ce_prem:,.0f} (2×)\n"
        f"SELL {pe_k:,} PE ~ ₹{pe_prem:,.0f} · "
        f"BUY STOP ₹{2 * pe_prem:,.0f} (2×)\n"
        f"size ₹10k ÷ (prem × {lot}) → {lots} lot{'s' if lots > 1 else ''} · "
        f"ONE entry · NO adds\n"
        "⏰ square off ALL by 15:15 · no re-entry · journal the trade\n"
        "storm rule: 2× on EITHER leg → exit BOTH · premiums are "
        "BS-indicative — stops go at 2× your actual fill"
    )
