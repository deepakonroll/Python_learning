"""Consensus backtest: last N trading days for CRUDE ~both (EMA20+QQE).

Computes side series once (vectorized EMA20, single-pass QQE), then
detects consensus flips — no bar-by-bar evaluate() loop.

Run from nse_alerter/:  python tools/consensus_backtest_crude.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import yfinance as yf
from nse_alerts.signals import side_series
from nse_alerts.qqe import qqe_side

DATA_DIR = Path(__file__).parent / "_data"
DATA_DIR.mkdir(exist_ok=True)
SYMBOL = "BZ=F"
CACHE = DATA_DIR / "CRUDE_5m_consensus.pkl"
EMA_LEN, QQE_RSI, QQE_SF, QQE_F = 20, 14, 5, 4.238
EVAL_DAYS = 20
HORIZONS = {"5m": 1, "15m": 3, "30m": 6, "1h": 12}
MCX_O, MCX_C = 17*60, 22*60  # 17:00-22:00 IST

def _bm(t):
    return t.hour * 60 + t.minute


def fetch_data():
    if CACHE.exists():
        age = (pd.Timestamp.now() - pd.Timestamp(
            CACHE.stat().st_mtime, unit="s")).total_seconds() / 3600
        if age < 24:
            df = pd.read_pickle(CACHE)
            print(f"cached {len(df)} bars ({age:.1f}h old)")
            return df
    print(f"fetching {SYMBOL} 5m ...")
    df = yf.download(SYMBOL, period="60d", interval="5m",
                     progress=False, auto_adjust=True)
    if df.empty:
        raise RuntimeError(f"no data for {SYMBOL}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.to_pickle(CACHE)
    print(f"fetched {len(df)} bars -> {CACHE}")
    return df


def run_backtest():
    candles = fetch_data()
    mask = candles.index.map(lambda t: MCX_O <= _bm(t) <= MCX_C)
    candles = candles[mask].copy()
    tdays = sorted(set(candles.index.date))[-EVAL_DAYS:]
    print(f"\n{'='*80}")
    print(f"CRUDE ~both consensus backtest - last {EVAL_DAYS} trading days")
    print(f"Proxy: {SYMBOL} (Brent USD, directionally reliable)")
    print(f"Engines: EMA20(len={EMA_LEN}) + QQE(RSI={QQE_RSI},SF={QQE_SF},F={QQE_F})")
    print(f"Session: 17:00-22:00 IST")
    print(f"{'='*80}\n")

    closes = candles["Close"]
    # compute both side series at once (fast)
    ema_sides = side_series(closes, EMA_LEN)       # +1/-1
    qqe_sides = qqe_side(closes, QQE_RSI, QQE_SF, QQE_F)  # +1/-1

    # detect consensus flips
    first_date = tdays[0]
    signals = []
    cprev = None

    for i in range(len(candles)):
        if candles.index[i].date() < first_date:
            ema_v = int(ema_sides.iloc[i])
            qqe_v = int(qqe_sides.iloc[i])
            if ema_v == qqe_v:
                cprev = "UP" if ema_v == 1 else "DOWN"
            continue

        ema_v = int(ema_sides.iloc[i])
        qqe_v = int(qqe_sides.iloc[i])
        if ema_v == qqe_v:
            agreed = "UP" if ema_v == 1 else "DOWN"
            if cprev is not None and agreed != cprev:
                alert = "BUY" if agreed == "UP" else "SELL"
                cl = float(closes.iloc[i])
                signals.append(dict(date=candles.index[i].date(),
                                    time=candles.index[i],
                                    side=alert, direction=agreed,
                                    close=cl, bar_idx=i))
            cprev = agreed

    if not signals:
        print("\nNo consensus signals fired!")
        return


    ebars = candles[candles.index.date >= first_date]
    print(f"Total consensus signals: {len(signals)}\n")
    hdr = f"{'Date':>10} {'Time':>6} {'Side':>5} {'Close':>10}"
    for h in HORIZONS:
        hdr += f" {'+ '+h:>7}"
    print(hdr)
    print("-" * len(hdr))
    stats = {h: {"correct": 0, "wrong": 0, "na": 0} for h in HORIZONS}

    for sig in signals:
        line = f"{sig['date']!s:>10} {sig['time'].strftime('%H:%M'):>6} {sig['side']:>5} {sig['close']:>10.2f}"
        for hn, hb in HORIZONS.items():
            fi = sig["bar_idx"] + hb
            if fi < len(candles):
                fc = float(candles["Close"].iloc[fi])
                ch = fc - sig["close"]
                ok = (ch > 0) if sig["direction"] == "UP" else (ch < 0)
                pct = (ch / sig["close"]) * 100
                if ok:
                    mark = f"+{pct:.2f}%"
                    stats[hn]["correct"] += 1
                else:
                    mark = f"-{abs(pct):.2f}%"
                    stats[hn]["wrong"] += 1
                line += f" {mark:>7}"
            else:
                line += f" {'n/a':>7}"
                stats[hn]["na"] += 1
        print(line)

    print(f"\n{'='*80}")
    print("DIRECTION ACCURACY BY HORIZON")
    print(f"{'='*80}")
    for hn in HORIZONS:
        c, w = stats[hn]["correct"], stats[hn]["wrong"]
        v = c + w
        pct = (c / v * 100) if v else 0
        print(f"  +{hn:>3}:  {c}/{v} correct  ({pct:.0f}%)  |  {w} wrong")

    print(f"\n{'='*80}")
    print("SAME-DAY: signal close vs session close")
    print(f"{'='*80}")
    for sig in signals:
        day = sig["date"]
        day_mask = candles.index.date == day
        last_close = float(candles.loc[day_mask, "Close"].iloc[-1])
        ch = last_close - sig["close"]
        ok = (ch > 0) if sig["direction"] == "UP" else (ch < 0)
        pct = (ch / sig["close"]) * 100
        mark = "CORRECT" if ok else "WRONG"
        tstr = sig["time"].strftime("%H:%M")
        print(f"  {day} {tstr} {sig['side']:>4}  sig@{sig['close']:,.2f}  "
              f"eod@{last_close:,.2f}  {ch:+,.2f} ({pct:+.2f}%)  {mark}")

    print(f"\n{'='*80}")
    print("BY SIGNAL TYPE")
    print(f"{'='*80}")
    for side in ("BUY", "SELL"):
        ss2 = [s for s in signals if s["side"] == side]
        if not ss2:
            continue
        print(f"\n  {side} signals: {len(ss2)}")
        for hn in HORIZONS:
            hb = HORIZONS[hn]
            c = w = 0
            for s in ss2:
                fi = s["bar_idx"] + hb
                if fi >= len(candles):
                    continue
                fc = float(candles["Close"].iloc[fi])
                ch = fc - s["close"]
                if ((s["direction"] == "UP" and ch > 0)
                        or (s["direction"] == "DOWN" and ch < 0)):
                    c += 1
                else:
                    w += 1
            v = c + w
            pct = (c / v * 100) if v else 0
            print(f"    +{hn:>3}:  {c}/{v} correct  ({pct:.0f}%)")

if __name__ == "__main__":
    run_backtest()
