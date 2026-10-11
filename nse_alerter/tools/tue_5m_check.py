"""5m path check for the NIFTY Tuesday 0-DTE strangle (research tool).

The vix_strangle_sweep prices each slot from DAILY OHLC: entry at the open,
stop = strangle repriced at the day's High/Low with T-1h, exit = intrinsic
at the close (0-DTE). Fine for multi-day slots, but for the 0.92-session
Tuesday slot the intrabar stop ORDER is the whole story and daily bars
cannot see it. This tool re-runs the exact same rules on real 5m paths and
reports both models side by side on the SAME Tuesdays and the SAME fixed
IV ladder (11-14), so the only difference is path granularity - it answers
"does the grid's cell for dist 0.50/0.75/0.95% survive real paths?".

5m model (mirrors the card + sweep conventions):
  entry  : first 5m bar ENDING >= 09:50 (09:45 cron + 1 bar), strikes at
           dist from that spot, credit = BS CE+PE (r=0, session-time T =
           minutes left to 15:30 / 375 / 252)
  stop   : each later bar, reprice the strangle at the bar's High and at
           its Low with T = time left to 15:30; either >= credit*(1+1.0C)
           -> whole position out at the stop (pts = -1.0C * credit)
  exit   : flat by 15:15 (last bar ENDING <= 15:15), mark = reprice at the
           bar close with the tiny time left
  credit < Rs18 -> skip (Rs0 - still counts in the EV denominator, same
           as simulate_slot's floor skip)
No per-leg backstop or take-profit leg: the strangle card has neither
(session decay to 15:15 only).

Run from nse_alerter/:  python tools/tue_5m_check.py
Data reuses envelope_ev's 5m cache (tools/_data/, 24h, gitignored).
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # .../nse_alerter/tools
_ROOT = _HERE.parent                              # .../nse_alerter
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

import pandas as pd

from nse_alerts.expiry import (CREDIT_FLOOR, SESSION_MINUTES, TRADING_DAYS,
                               bs_price)
from envelope_ev import _load_5m
from vix_strangle_sweep import _ohlc, _strikes, simulate_slot

TICKER, CACHE5 = "^NSEI", "NIFTY_5m"
INST = "NIFTY"
WEEKDAY = 1                                  # Tuesday
SESSIONS = 0.92                              # left at the 09:45 entry
STOP_FRAC = 1.0                              # whole-position stop, 0-DTE rule
DISTS = [0.0095, 0.0075, 0.0050]             # the sweep's three distances
IVS = [11.0, 12.0, 13.0, 14.0]               # the headline VIX band
LOTS = 10
STEP = 50
LOT_QTY = 65
FLOOR = CREDIT_FLOOR[INST]
ENTRY_END_H, ENTRY_END_M = 9, 50             # first bar ending >= 09:50
SQUARE_H, SQUARE_M = 15, 15                  # last bar ending <= 15:15



def _sess_left(end_ts: pd.Timestamp) -> float:
    """Fraction of the trading session still ahead at a bar's END stamp."""
    day = end_ts.date()
    close_t = (pd.Timestamp(day.year, day.month, day.day, 15, 30)
               .tz_localize(end_ts.tzinfo))
    mins = (close_t - end_ts).total_seconds() / 60.0
    return max(min(SESSION_MINUTES, mins), 0.0) / SESSION_MINUTES


def _strangle(spot: float, ce_k: float, pe_k: float, t: float,
              ivd: float) -> float:
    return (bs_price(spot, ce_k, t, ivd, "CE")
            + bs_price(spot, pe_k, t, ivd, "PE"))


def _path_day(day_bars: pd.DataFrame, ei: int, xi: int, dist: float,
              iv: float) -> dict:
    """One Tuesday on its real 5m path (Rs per lot)."""
    idx = day_bars.index
    ivd = iv / 100.0
    spot_e = float(day_bars["Close"].iloc[ei])
    ce_k, pe_k = _strikes(spot_e, dist, STEP)
    t_e = _sess_left(idx[ei] + pd.Timedelta(minutes=5))
    credit = _strangle(spot_e, ce_k, pe_k, t_e / TRADING_DAYS, ivd)
    if credit < FLOOR:
        return dict(pts=0.0, reason="skip", credit=credit)
    for k in range(ei + 1, xi + 1):
        t_k = _sess_left(idx[k] + pd.Timedelta(minutes=5)) / TRADING_DAYS
        bar = day_bars.iloc[k]
        worst = max(_strangle(float(bar["High"]), ce_k, pe_k, t_k, ivd),
                    _strangle(float(bar["Low"]), ce_k, pe_k, t_k, ivd))
        if worst >= credit * (1.0 + STOP_FRAC):
            return dict(pts=-STOP_FRAC * credit, reason="stop", credit=credit)
    t_x = _sess_left(idx[xi] + pd.Timedelta(minutes=5)) / TRADING_DAYS
    mark = _strangle(float(day_bars["Close"].iloc[xi]), ce_k, pe_k, t_x, ivd)
    return dict(pts=credit - mark, reason="time", credit=credit)


def _day_windows(m5: pd.DataFrame, dayset: set) -> dict:
    """Per Tuesday -> (frame, entry_i, exit_i) on the 5m bars; days missing
    either bar are dropped (both models then run on the survivors)."""
    out: dict = {}
    for d in sorted(dayset):
        b = m5[m5.index.date == d]
        if b.empty:
            continue
        ends = b.index + pd.Timedelta(minutes=5)
        ei = xi = None
        for i, e in enumerate(ends):
            if ei is None and (e.hour, e.minute) >= (ENTRY_END_H, ENTRY_END_M):
                ei = i
            if (e.hour, e.minute) <= (SQUARE_H, SQUARE_M):
                xi = i
        if ei is not None and xi is not None and ei < xi:
            out[d] = (b, ei, xi)
    return out


def run() -> None:
    daily = _ohlc(TICKER, f"{INST}_d")
    m5 = _load_5m(TICKER, CACHE5)
    tues = {d for d in m5.index.date if d.weekday() == WEEKDAY}
    wins = _day_windows(m5, tues)
    days = sorted(wins)
    # daily frame restricted to the exact same Tuesdays -> simulate_slot's
    # pnl list aligns 1:1 with `days` (one element per row, skips included)
    daily_f = daily[[ts.date() in set(days) and ts.weekday() == WEEKDAY
                     for ts in daily.index]]
    assert len(daily_f) == len(days), (len(daily_f), len(days))

    print("=" * 104)
    print("TUE 0-DTE PATH CHECK - daily-OHLC model vs real 5m paths, same "
          f"{len(days)} Tuesdays ({days[0]} .. {days[-1]})")
    print(f"5m entry 09:50 bar close · stop -{STOP_FRAC:.1f}C whole position "
          "· flat by 15:15 · IV ladder 11-14 · rupees at 10 lots")
    print("=" * 104)

    detail_at_13: dict[float, list] = {}
    for dist in DISTS:
        d_evs, m_evs, d_stops, m_stops, m_worst = [], [], [], [], []
        for iv in IVS:
            d_pnl, _mo, d_st, d_sk = simulate_slot(
                daily_f, WEEKDAY, SESSIONS, dist, STOP_FRAC, iv,
                STEP, LOT_QTY, FLOOR)
            trades = [_path_day(wins[d][0], wins[d][1], wins[d][2], dist, iv)
                      for d in days]
            m_pnl = [t["pts"] * LOT_QTY for t in trades]
            d_evs.append(statistics.fmean(d_pnl) * LOTS)
            m_evs.append(statistics.fmean(m_pnl) * LOTS)
            d_traded = len(d_pnl) - d_sk
            d_stops.append(d_st / max(d_traded, 1))
            m_stops.append(sum(1 for t in trades
                               if t["reason"] == "stop")
                           / max(sum(1 for t in trades
                                     if t["reason"] != "skip"), 1))
            m_worst.append(min(m_pnl) * LOTS)
            if iv == 13.0:
                detail_at_13[dist] = trades
        print(f"\n  dist {dist * 100:4.2f}% OTM")
        print(f"    daily OHLC : EV/10L Rs{statistics.fmean(d_evs):>8,.0f}"
              f"   stop {statistics.fmean(d_stops):5.1%}")
        print(f"    5m paths   : EV/10L Rs{statistics.fmean(m_evs):>8,.0f}"
              f"   stop {statistics.fmean(m_stops):5.1%}"
              f"   worst-day Rs{min(m_worst):>8,.0f}")

    print("\n  per-day detail @ IV13 (5m model):")
    print(f"    {'date':12s} {'credit':>8s} {'dist0.95 pts':>13s} {'exit':>6s}"
          f" {'dist0.50 pts':>13s} {'exit':>6s}   Rs/10L@0.50")
    for i, d in enumerate(days):
        t95 = detail_at_13[0.0095][i]
        t50 = detail_at_13[0.0050][i]
        print(f"    {str(d):12s} {t95['credit']:>8,.1f} {t95['pts']:>13,.1f}"
              f" {t95['reason']:>6s} {t50['pts']:>13,.1f} {t50['reason']:>6s}"
              f" {t50['pts'] * LOT_QTY * LOTS:>12,.0f}")

    print("\n  read: 5m EV below daily EV = the daily model's stop/exit")
    print("  approximation is too generous; sign flip = do NOT widen.")


if __name__ == "__main__":
    run()
