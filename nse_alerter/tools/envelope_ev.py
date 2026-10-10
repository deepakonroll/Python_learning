"""Envelope-pilot EV: the card's second income stream, quantified.

The 2026-10-09 sweep measured ONLY the strangle (the envelope had flip
counts but no rupee EV). This tool prices the pilot rules exactly as the
08:45 card states them (app.py _plan_card v5):

    BLUE cross (side -> UP)  -> sell ATM PE, 1 lot
    RED  cross (side -> DOWN) -> sell ATM CE, 1 lot
    entry <= 2 bars after the confirming close (delay measured)
    exit: premium DOUBLES (backstop) first, else REVERSE cross,
          else flat by 15:15 (NIFTY) / 22:00 window end (crude)
    max 2 envelope trades/day per instrument

Method: yfinance 5m, period=60d (Yahoo's stated hard limit for 5m -
the serve came back as ~60 TRADING days, 16 Jul - 9 Oct 2026; Kite can
deepen the sample later). Signals come straight from
``nse_alerts.envelope.envelope_side`` (SMA20 +-0.2%, full-bar break,
carry-forward) so the backtest and the live alerter cannot drift apart.
Premiums: Black-Scholes r=0 via ``nse_alerts.expiry.bs_price`` with the
production session-time convention (T = sessions/252):
  NIFTY  - sessions to the next Tuesday expiry (0-DTE when entered Tue),
           IV = previous close of ^INDIAVIX (the live feed), qty 65
  CRUDE  - BZ=F proxy (live chain: TVC:UKOIL > BZ=F), T = 5 sessions
           (monthly option, ~1 week - theta is a second-order term),
           IV ladder 25/30/35, qty 100, gated 17:00-22:00 IST

Documented limitations: bar-close fills only (no intrabar path - the
backstop can be missed inside a bar), no slippage in the headline number
(net-after-cost shown separately), BZ=F/Brent proxy for MCX crude, and
the two streams are measured independently (no combined margin model).

Run from nse_alerter/:  python tools/envelope_ev.py
Data caches 24h in tools/_data/ (gitignored).
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from nse_alerts.envelope import envelope_side
from nse_alerts.expiry import TRADING_DAYS, bs_price

DATA_DIR = Path(__file__).parent / "_data"
MAX_TRADES_PER_DAY = 2
ENTRY_DELAYS = (0, 1, 2)          # bars after the confirming close
HEAD_DELAY = 1                    # headline: enter 1 bar after the alert

# window = in-IST alert gate (None = whole NSE session); squareoff = last
# bar START whose close is the "flat by" price (15:10 bar closes at 15:15).
INSTRUMENTS: dict[str, dict] = {
    "NIFTY": dict(ticker="^NSEI", cache="NIFTY_5m", step=50, qty=65,
                  iv_mode="vix", iv_ladder=(12.0, 13.0, 14.0),
                  head_iv=13.0, window=None, squareoff=time(15, 10),
                  cost_pts=1.5, t_sessions=None, expiry_wd=1,   # expiry Tue
                  label="NIFTY  ^NSEI 5m (NSE session)"),
    "CRUDE": dict(ticker="BZ=F", cache="CRUDE_5m", step=100, qty=100,
                  iv_mode="fixed", iv_ladder=(25.0, 30.0, 35.0),
                  head_iv=30.0, window=(17, 22), squareoff=time(21, 55),
                  cost_pts=0.5, t_sessions=5.0, expiry_wd=None,
                  label="CRUDE  BZ=F 5m proxy (17:00-22:00 IST gate)"),
}


def _load_5m(ticker: str, cache: str) -> pd.DataFrame:
    """60d of 5m OHLC, tz-normalised to IST, cached 24h in tools/_data/."""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{cache}.csv"
    if not (path.exists() and pd.Timestamp.now().timestamp()
            - path.stat().st_mtime < 86400):
        import yfinance as yf
        df = yf.download(ticker, period="60d", interval="5m",
                         auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df[["Open", "High", "Low", "Close"]].to_csv(path)
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("Asia/Kolkata")
    else:
        df.index = df.index.tz_convert("Asia/Kolkata")
    return df[["Open", "High", "Low", "Close"]].dropna()


def _vix_by_date() -> pd.Series | None:
    """Previous-close India VIX per date (live feed's IV source), else None."""
    try:
        import yfinance as yf
        df = yf.download("^INDIAVIX", period="60d", interval="1d",
                         auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        close = df["Close"].dropna()
        if len(close) < 5 or float(close.iloc[-1]) <= 0:
            return None
        out = (close / 100.0).copy()
        out.index = [ts.date() for ts in out.index]
        return out.shift(1)         # trade on the PREVIOUS close's IV
    except Exception:
        return None                 # caller falls back to head_iv


def _weekdays_through(expiry_wd: int, d0: date) -> date:
    """Next date with weekday expiry_wd (Mon=0) on/after d0 (expiry day)."""
    d = d0
    while d.weekday() != expiry_wd:
        d += timedelta(days=1)
    return d


def _sessions_left(ts: pd.Timestamp, spec: dict) -> float:
    """Sessions to option expiry at bar time - production convention
    (entry on expiry day = remainder only; else remainder + whole
    weekdays up to and including the expiry date, cf. ENTRY_SESSIONS)."""
    if spec["t_sessions"] is not None:               # crude: fixed pool
        return spec["t_sessions"]
    close = ts.normalize() + pd.Timedelta(hours=15, minutes=30)
    rem = max((close - ts).total_seconds() / 60.0, 0.0) / 375.0
    exp = _weekdays_through(spec["expiry_wd"], ts.date())
    wd = 0
    d = ts.date() + timedelta(days=1)
    while d <= exp:
        if d.weekday() < 5:
            wd += 1
        d += timedelta(days=1)
    return rem + wd


def _prem(spot: float, strike: float, t_sess: float, iv: float,
          kind: str) -> float:
    return bs_price(spot, strike, max(t_sess, 0.0) / TRADING_DAYS, iv, kind)


def _simulate(spec: dict, sides: pd.Series, closes: pd.Series,
              flip_idx: list[int], iv_by_date: pd.Series | None,
              delay: int, iv_fixed: float | None) -> list[dict]:
    """Trade every eligible flip: entry = confirming close + `delay` bars,
    exits = backstop -> reverse cross -> square-off (first match per bar)."""
    idx = closes.index
    out: list[dict] = []
    taken: dict[date, int] = {}
    lo, hi = spec["window"] if spec["window"] else (None, None)
    for fi in flip_idx:
        side_new = float(sides.iloc[fi])
        day = idx[fi].date()
        # crude: off-window flips alert at the first in-window evaluation
        if lo is not None:
            j = fi
            while (j < len(idx) and idx[j].date() == day
                   and not lo <= idx[j].hour < hi):
                j += 1
            if j >= len(idx) or idx[j].date() != day:
                continue
        else:
            j = fi
        ei = j + delay
        if ei >= len(idx) or idx[ei].date() != day:
            continue                                     # flat-by window lost
        if lo is not None and not (idx[ei].hour < hi and idx[ei].hour >= lo):
            continue
        if idx[ei].time() >= spec["squareoff"]:
            continue                                     # no room to trade
        if taken.get(day, 0) >= MAX_TRADES_PER_DAY:
            continue
        spot = float(closes.iloc[ei])
        strike = round(spot / spec["step"]) * spec["step"]
        kind = "PE" if side_new == 1.0 else "CE"
        if iv_fixed is not None:
            iv = iv_fixed
        else:
            got = iv_by_date.get(day) if iv_by_date is not None else None
            iv = float(got) if got == got and got else spec["head_iv"] / 100.0
        if not (0.03 < iv < 0.9):
            iv = spec["head_iv"] / 100.0
        t0 = _sessions_left(idx[ei], spec)
        prem = _prem(spot, strike, t0, iv, kind)
        if prem <= 0.5:
            continue
        # last bar of the day allowed by the square-off rule
        last = ei
        for k in range(ei + 1, len(idx)):
            if idx[k].date() != day:
                break
            if lo is not None and not (idx[k].hour < hi and idx[k].hour >= lo):
                continue
            last = k
            if idx[k].time() >= spec["squareoff"]:
                break
        if idx[last].time() < spec["squareoff"] and last == ei:
            continue                                      # no room to exit
        exit_px, ex_i, reason = None, None, None
        for k in range(ei + 1, last + 1):
            el = (idx[k] - idx[ei]).total_seconds() / 60.0
            t_k = t0 - el / 375.0
            p_k = _prem(float(closes.iloc[k]), strike, t_k, iv, kind)
            if p_k >= 2.0 * prem:                         # backstop first
                exit_px, ex_i, reason = float(closes.iloc[k]), k, "double"
                break
            if float(sides.iloc[k]) != side_new:
                exit_px, ex_i, reason = float(closes.iloc[k]), k, "reverse"
                break
        if exit_px is None:
            exit_px = float(closes.iloc[last])
            ex_i, reason = last, "squareoff"
        el = (idx[ex_i] - idx[ei]).total_seconds() / 60.0
        p_out = _prem(exit_px, strike, t0 - el / 375.0, iv, kind)
        pts = prem - p_out                                # short premium
        qty = spec["qty"]
        taken[day] = taken.get(day, 0) + 1
        out.append(dict(day=day, side="BLUE" if side_new == 1 else "RED",
                        kind=kind, iv=iv, prem_in=prem, prem_out=p_out,
                        pts=pts, rs=pts * qty,
                        net=(pts - spec["cost_pts"]) * qty, reason=reason))
    return out


def _flips_of(bars: pd.DataFrame) -> list[int]:
    """Confirming-bar indexes of every envelope side change (SMA20 +-0.2%),
    excluding the 20-bar warm-up and the silent first-side baseline."""
    sides = envelope_side(bars["Close"], bars["High"], bars["Low"])
    chg = sides.ne(sides.shift(1)) & sides.notna() & sides.shift(1).notna()
    return [i for i, ok in enumerate(chg.tolist()) if ok and i >= 20]


def _daily(trades: list[dict], dayset: set) -> dict:
    """Per-day totals over EVERY trading day in the sample (no-trade days
    count as Rs0 - otherwise EV/day is conditioned on trading and biased)."""
    by_day: dict[date, float] = {d: 0.0 for d in dayset}
    net_day: dict[date, float] = {d: 0.0 for d in dayset}
    for t in trades:
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["rs"]
        net_day[t["day"]] = net_day.get(t["day"], 0.0) + t["net"]
    vals = sorted(by_day.values())
    n = len(trades)
    return {
        "n": n, "days": len(dayset),
        "ev_day": statistics.fmean(vals) if vals else 0.0,
        "ev_trade": statistics.fmean([t["rs"] for t in trades]) if n else 0.0,
        "win": sum(1 for t in trades if t["rs"] > 0) / n if n else 0.0,
        "worst": min(vals) if vals else 0.0,
        "p10": vals[max(int(0.10 * len(vals)) - 1, 0)] if vals else 0.0,
        "p50": statistics.median(vals) if vals else 0.0,
        "p90": vals[min(int(0.90 * len(vals)), len(vals) - 1)] if vals else 0.0,
        "net_day": statistics.fmean(list(net_day.values())) if vals else 0.0,
    }


def run() -> None:
    vix = _vix_by_date()
    iv_src = "prev ^INDIAVIX close" if vix is not None else "default 13%"
    print("=" * 104)
    print("ENVELOPE PILOT EV - yfinance 5m x60d · card rules verbatim "
          "(1 lot, <=2-bar entry, double/reverse/square-off)")
    print(f"IV: NIFTY = {iv_src} · crude = fixed ladder · gross = no "
          "slippage (net subtracts 1.5pt / 0.5rs round trip)")
    print("=" * 104)

    hybrid: dict[str, dict] = {}
    for name, spec in INSTRUMENTS.items():
        bars = _load_5m(spec["ticker"], spec["cache"])
        sides = envelope_side(bars["Close"], bars["High"], bars["Low"])
        flips = _flips_of(bars)
        dayset = {ts.date() for ts in bars.index}
        days = len(dayset)
        print(f"\n{name} - {spec['label']}")
        print(f"  bars {len(bars):,} · trading days {days} · flips "
              f"{len(flips)} ({len(flips) / days:.1f}/day)")

        head_iv = None if spec["iv_mode"] == "vix" else spec["head_iv"] / 100
        head = _simulate(spec, sides, bars["Close"], flips, vix,
                         HEAD_DELAY, head_iv)
        h = _daily(head, dayset)
        hybrid[name] = h
        reasons = {r: sum(1 for t in head if t["reason"] == r)
                   for r in ("double", "reverse", "squareoff")}
        print(f"  headline delay+{HEAD_DELAY}: trades {h['n']} "
              f"({h['n'] / days:.2f}/day) · EV/day Rs{h['ev_day']:>8,.0f} "
              f"(net Rs{h['net_day']:>7,.0f}) · EV/trade "
              f"Rs{h['ev_trade']:>7,.0f} · win {h['win']:.0%}")
        print(f"    worst day Rs{h['worst']:>8,.0f} · daily P10/50/90 "
              f"Rs{h['p10']:>7,.0f}/{h['p50']:>7,.0f}/{h['p90']:>7,.0f} · "
              f"exits double {reasons['double']} reverse "
              f"{reasons['reverse']} squareoff {reasons['squareoff']}")

        row = []
        for dly in ENTRY_DELAYS:
            tr = _simulate(spec, sides, bars["Close"], flips, vix, dly,
                           head_iv)
            row.append(f"delay+{dly}: Rs{_daily(tr, dayset)['ev_day']:>7,.0f}")
        print("  EV/day by entry delay -> " + "  ".join(row))
        row = []
        for ivp in spec["iv_ladder"]:
            tr = _simulate(spec, sides, bars["Close"], flips, vix,
                           HEAD_DELAY, ivp / 100.0)
            row.append(f"IV{int(ivp)}: Rs{_daily(tr, dayset)['ev_day']:>7,.0f}")
        print("  EV/day by IV        -> " + "  ".join(row))

    # hybrid totals vs the Rs 7-10k/day target (strangle sweep = 80d77e6)
    strangle_wk = 20_463          # 10 lots, 4 entry days, VIX 11-14 avg
    env_day = sum(h["ev_day"] for h in hybrid.values())
    env_net = sum(h["net_day"] for h in hybrid.values())
    print()
    print("-" * 104)
    print(f"HYBRID (strangle 10 lots Rs{strangle_wk:,}/wk over 4 entry days "
          f"+ envelope Rs{env_day:,.0f}/day gross):")
    print(f"  envelope/week (5d)      Rs{env_day * 5:>9,.0f} gross  "
          f"Rs{env_net * 5:>9,.0f} net")
    print(f"  strangle/week (10 lots) Rs{strangle_wk:>9,.0f}")
    total_wk = strangle_wk + env_day * 5
    per_day = total_wk / 5
    verdict = ("MET" if per_day >= 10_000 else
               "in band" if per_day >= 7_000 else "short")
    print(f"  HYBRID/week             Rs{total_wk:>9,.0f}  "
          f"= Rs{per_day:>8,.0f}/trading day")
    print(f"  target Rs7,000-10,000/day -> {verdict}; gap Rs"
          f"{max(7_000 - per_day, 0):,.0f}-Rs{max(10_000 - per_day, 0):,.0f}"
          f"/day")


if __name__ == "__main__":
    run()

