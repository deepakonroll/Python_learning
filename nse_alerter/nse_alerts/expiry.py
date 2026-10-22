"""Intraday expiry/rotation data + Black-Scholes pricing helpers.

Holds the contract rotation and the BS price kernels shared by the 09:20
opening-levels snapshot (nse_alerts/snapshot.py) and the research tools
(tools/). The 09:45 entry alert that sat on top of this playbook was
**retired** (2026-10-22, owner call).

Java equivalent: a small pricing service + a constant catalogue.
"""

from __future__ import annotations

import math
from statistics import NormalDist

# weekday -> (instrument, sessions left to that contract's expiry); Mon=0.
# Used by the 09:20 snapshot header countdown and the research tools.
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

CREDIT_FLOOR = {"NIFTY": 18.0, "SENSEX": 40.0}  # min credit, both legs, pts
SESSION_MINUTES = 375.0        # 09:15 -> 15:30 full session
TRADING_DAYS = 252.0           # session-time annualisation (intraday = no gap)
DEFAULT_IV = 13.0          # India VIX fallback (percent) when the feed fails

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
