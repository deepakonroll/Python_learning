"""VIX-ladder sweep for the intraday strangle playbook (research tool).

Rebuilt 2026-10-10 (the original `_vix_strangle.py` was a scratch script,
run and deleted). Method matches the 2026-10-09 sweep that approved the
rotation, with the axes widened per the owner's hybrid question:
  - dist  : 0.50 / 0.75 / 0.95 % OTM  ("come closer on expiry day?")
  - stop  : -0.5C / -1.0C whole-position stop
  - lots  : 10 / 20
  - IV    : fixed ladder 11..18 % (pricing IV, not the realised day VIX)
  - slots : NIFTY Fri 2.92 / Mon 1.92 / Tue 0.92 sess, SENSEX Wed 1.92 /
            Thu 0.92 sess (Thu in the GRID only - the live card keeps
            NO ENTRY until a cell proves positive at every VIX)

Day model (daily OHLC only - documented limitations):
  entry at the day OPEN (proxy for the 09:45 alert fill)
  credit = BS CE + BS PE (r=0, session-time T) - floor 18/40 pts skips
  stop   = reprice the strangle at the day's High and Low with T-1h;
           either side >= credit*(1+stop_frac) -> whole position exits
           at the stop (pts = -stop_frac*credit)
  exit   = reprice at the Close with T = (sessions-1) full sessions
           (0-DTE -> intrinsic at close)
  no 2x per-leg stops, no intrabar path order, no slippage/impact.

Run from nse_alerter/:  python tools/vix_strangle_sweep.py
Data caches 24h in tools/_data/ (gitignored).
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from nse_alerts.expiry import (CREDIT_FLOOR, EXPIRY_SPECS, TRADING_DAYS,
                               bs_price)

DATA_DIR = Path(__file__).parent / "_data"
# (instrument, weekday Mon=0, sessions left at the 09:45 entry)
SLOTS = [("NIFTY", 4, 2.92), ("NIFTY", 0, 1.92), ("NIFTY", 1, 0.92),
         ("SENSEX", 2, 1.92), ("SENSEX", 3, 0.92)]
DISTS = [0.0050, 0.0075, 0.0095]
STOPS = [0.5, 1.0]
VIX_LADDER = [11.0, 12.0, 13.0, 14.0, 16.0, 18.0]
WD_NAME = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def _ohlc(ticker: str, cache: str) -> pd.DataFrame:
    """Daily OHLC frame, cached 24h in tools/_data/."""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{cache}.csv"
    if (path.exists() and pd.Timestamp.now().timestamp()
            - path.stat().st_mtime < 86400):
        return pd.read_csv(path, index_col=0, parse_dates=True)
    import yfinance as yf
    df = yf.download(ticker, period="3y", interval="1d",
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    out = df[["Open", "High", "Low", "Close"]].dropna()
    out.to_csv(path)
    return out


def _t_years(sessions: float) -> float:
    """Session-time T (trading hours only / 252) - matches expiry.strike_rule.

    Calendar annualisation would shrink T 5.6x and drop every premium under
    the credit floor; production prices intraday positions without gaps.
    """
    return max(sessions, 0.0) / TRADING_DAYS


def _strikes(spot: float, dist: float, step: int) -> tuple[int, int]:
    """(ce, pe) at dist OTM, rounded to the instrument's grid."""
    return (round(spot * (1 + dist) / step) * step,
            round(spot * (1 - dist) / step) * step)


def simulate_slot(ohlc: pd.DataFrame, weekday: int, sessions: float,
                  dist: float, stop_frac: float, iv: float,
                  step: int, lot_qty: int, floor: float):
    """One slot's per-day rupee P&L (per lot) + monthly totals, at fixed IV."""
    days = ohlc[ohlc.index.weekday == weekday]
    pnl: list[float] = []
    month: dict[str, float] = {}
    stopped = skipped = 0
    ivd = iv / 100.0
    for ts, row in days.iterrows():
        o, h, l, c = row.Open, row.High, row.Low, row.Close
        ce, pe = _strikes(o, dist, step)
        credit = (bs_price(o, ce, _t_years(sessions), ivd, "CE")
                  + bs_price(o, pe, _t_years(sessions), ivd, "PE"))
        if credit < floor:
            pnl.append(0.0)
            skipped += 1
            continue
        t_hl = _t_years(sessions - 0.16)     # ~1h (60/375 sess) of decay by the extremes
        at_high = (bs_price(h, ce, t_hl, ivd, "CE")
                   + bs_price(h, pe, t_hl, ivd, "PE"))
        at_low = (bs_price(l, ce, t_hl, ivd, "CE")
                  + bs_price(l, pe, t_hl, ivd, "PE"))
        if max(at_high, at_low) >= credit * (1 + stop_frac):
            pts = -stop_frac * credit
            stopped += 1
        else:
            pts = credit - (bs_price(c, ce, _t_years(max(sessions - 1.0, 0.0)),
                                     ivd, "CE")
                            + bs_price(c, pe, _t_years(max(sessions - 1.0, 0.0)),
                                       ivd, "PE"))
        rs = pts * lot_qty
        pnl.append(rs)
        key = f"{ts:%Y-%m}"
        month[key] = month.get(key, 0.0) + rs
    return pnl, month, stopped, skipped





def run() -> None:
    data = {inst: _ohlc(ticker, f"{inst}_d") for inst, ticker
            in [("NIFTY", "^NSEI"), ("SENSEX", "^BSESN")]}

    print("=" * 110)
    print("STANGLE SWEEP - 3y daily OHLC · entry at open · fixed IV ladder "
          "· rupees at 10 lots")
    print("=" * 110)

    # --- headline: the live rule at 10 and 20 lots, per slot, VIX 11-14 avg
    for inst, wd, sess in SLOTS:
        step = EXPIRY_SPECS[inst]["step"]
        lot_qty = EXPIRY_SPECS[inst]["lot"]
        floor = CREDIT_FLOOR[inst]
        stop = 1.0 if (inst == "NIFTY" and sess < 1) else 0.5
        rows = []
        for iv in (11.0, 12.0, 13.0, 14.0):
            pnl, months, st, sk = simulate_slot(
                data[inst], wd, sess, 0.0095, stop, iv, step, lot_qty, floor)
            rows.append((pnl, months, st, sk))
        ev10 = statistics.fmean([statistics.fmean(r[0]) for r in rows]) * 10
        allp = [x * 10 for r in rows for x in r[0]]
        print(f"{inst} {WD_NAME[wd]} {sess:.2f}sess stop-{stop:.1f}C "
              f"dist-0.95%: EV/10L Rs{ev10:>8,.0f}  worst-day Rs{min(allp):>9,.0f}  "
              f"win {sum(1 for x in allp if x > 0) / len(allp):5.1%}")

    # --- the widened grid on the 0-DTE slots (the "come closer" question)
    print()
    print("-" * 110)
    print("0-DTE GRID (dist x stop x IV, EV per 10 lots Rs; VIX 11-18):")
    for inst, wd, sess in SLOTS:
        if sess >= 1:
            continue                      # 0-DTE slots only
        step = EXPIRY_SPECS[inst]["step"]
        lot_qty = EXPIRY_SPECS[inst]["lot"]
        floor = CREDIT_FLOOR[inst]
        print(f"\n  {inst} {WD_NAME[wd]} 0.92 sess:")
        print(f"    {'dist':>6s} {'stop':>6s} " +
              " ".join(f"V{int(v):>2d}" for v in VIX_LADDER) +
              "   avg    win%  stop%")
        for dist in DISTS:
            for stop in STOPS:
                cells, wins, stops_l = [], [], []
                for iv in VIX_LADDER:
                    pnl, _m, st, sk = simulate_slot(
                        data[inst], wd, sess, dist, stop, iv, step,
                        lot_qty, floor)
                    cells.append(statistics.fmean(pnl) * 10)
                    wins.append(sum(1 for x in pnl if x > 0) / max(len(pnl), 1))
                    stops_l.append(st / max(len(pnl) - sk, 1))
                avg = statistics.fmean(cells[:4])   # VIX 11-14 avg for the rank
                print(f"    {dist * 100:5.2f}% {stop:5.1f}C " +
                      " ".join(f"{c:>5,.0f}" for c in cells) +
                      f"  {avg:>6,.0f}  {statistics.fmean(wins):5.1%}  "
                      f"{statistics.fmean(stops_l):5.1%}")

    # --- rotation total at 10 lots (live rule, VIX 11-14): every weekday
    print()
    print("-" * 110)
    day_ev: dict[str, float] = {}
    for inst, wd, sess in SLOTS:
        if inst == "SENSEX" and wd == 3:
            continue                      # Thu stays NO ENTRY in the headline
        step = EXPIRY_SPECS[inst]["step"]
        lot_qty = EXPIRY_SPECS[inst]["lot"]
        floor = CREDIT_FLOOR[inst]
        stop = 1.0 if (inst == "NIFTY" and sess < 1) else 0.5
        evs = [statistics.fmean(simulate_slot(
            data[inst], wd, sess, 0.0095, stop, iv, step, lot_qty,
            floor)[0]) * 10 for iv in (11.0, 12.0, 13.0, 14.0)]
        day_ev[f"{inst} {WD_NAME[wd]}"] = statistics.fmean(evs)
    total = sum(day_ev.values())
    for label, ev in day_ev.items():
        print(f"  {label:12s} Rs{ev:>8,.0f}/entry-day")
    print(f"  {'TOTAL':12s} Rs{total:>8,.0f}/week (4 entry days/week)")


if __name__ == "__main__":
    run()

