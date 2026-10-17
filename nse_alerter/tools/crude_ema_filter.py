"""Crude envelope x HTF EMA trend filter (research tool).

The owner remembers this suggestion from an earlier chat (never built,
never written down until now): only take Magic Envelope CRUDE entries in
the direction of the higher-timeframe EMA trend, so counter-trend flips
are skipped. This tool measures the idea before anything touches live.

Method: envelope_ev's crude simulation VERBATIM - ``_flips_of`` /
``_simulate`` / ``_daily`` are imported, not rewritten (delay+1 entry,
17:00-22:00 IST gate, max 2 trades/day, double -> reverse -> 21:55
square-off, Rs100/pt, 0.5pt cost, IV30 headline + 25/35 ladder) on the
same cached 60d of BZ=F 5m. The filter gates each flip's ENTRY:

    hourly EMA20 vs EMA50 on the SAME 5m closes resampled to 1h
    UP flip   (sell PE, long bias)  needs EMA20 > EMA50
    DOWN flip (sell CE, short bias) needs EMA20 < EMA50

Exactness notes:
  - the gate samples the bias at the ENTRY bar (entry-point mirrored from
    envelope_ev: in-window shift + delay, cf. its lines 155-172 - passes
    identical checks, structural skips flow through unchanged);
  - only COMPLETED hourly bars count (a bar stamped 17:00 covers
    17:00-18:00, unusable at a 17:05 entry);
  - before 50 hourly bars exist the gate PASSES everything (warm-up) -
    identical sample to baseline, no fair-comparison distortion;
  - daily EMA20/50 deliberately NOT tested: 60d holds ~42 daily bars,
    far too few for a valid EMA50 (~90d+ needed - revisit via Kite).

The read: baseline vs filtered EV/day, plus the EV of exactly what the
filter BLOCKS (blocked EV < 0 = filter earns its keep; > 0 = it burns
money). Run from nse_alerter/:  python tools/crude_ema_filter.py
Data reuses envelope_ev's 24h cache (tools/_data/, gitignored).
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # .../nse_alerter/tools
sys.path.insert(0, str(_HERE))                   # envelope_ev + its root path

import pandas as pd

from envelope_ev import (HEAD_DELAY, INSTRUMENTS, _daily, _flips_of,
                         _load_5m, _simulate)
from nse_alerts.envelope import envelope_side

FAST, SLOW = 20, 50            # hourly EMA pair; valid after SLOW bars


def _hourly(closes: pd.Series) -> tuple[pd.DatetimeIndex, pd.Series,
                                        pd.Series]:
    """1h closes from the SAME 5m series -> (index, up?, valid?) bias.
    up = EMA20 > EMA50; valid = EMA50 warmed up (first SLOW-1 bars out)."""
    hourly = closes.resample("1h").last().dropna()
    fast = hourly.ewm(span=FAST, adjust=False).mean()
    slow = hourly.ewm(span=SLOW, adjust=False).mean()
    up = (fast > slow)
    valid = pd.Series(False, index=hourly.index)
    valid.iloc[SLOW - 1:] = True
    return hourly.index, up, valid


def _bias_at(hidx: pd.DatetimeIndex, up: pd.Series, valid: pd.Series,
             ts: pd.Timestamp) -> bool | None:
    """Bias from the last hourly bar COMPLETED at ts (start + 1h <= ts).
    None = no completed bar yet / EMA still warming up -> caller passes."""
    pos = int(hidx.searchsorted(ts - pd.Timedelta(hours=1), side="right"))
    if pos == 0 or not bool(valid.iloc[pos - 1]):
        return None
    return bool(up.iloc[pos - 1])


def _partition(spec: dict, idx: pd.DatetimeIndex, flips: list[int],
               sides: pd.Series, hidx: pd.DatetimeIndex, up: pd.Series,
               valid: pd.Series) -> tuple[list[int], list[int]]:
    """Split flips by the gate at their ENTRY bar (entry-point mirror of
    envelope_ev._simulate lines 155-172: window shift, then + delay).
    Structural skips (what _simulate drops anyway) flow to `kept` - the
    gate decision is moot for them; the 2/day cap stays with _simulate."""
    lo, hi = spec["window"] if spec["window"] else (None, None)
    kept: list[int] = []
    blocked: list[int] = []
    for fi in flips:
        day = idx[fi].date()
        if lo is not None:
            j = fi
            while (j < len(idx) and idx[j].date() == day
                   and not lo <= idx[j].hour < hi):
                j += 1
            if j >= len(idx) or idx[j].date() != day:
                kept.append(fi)
                continue
        else:
            j = fi
        ei = j + HEAD_DELAY
        if (ei >= len(idx) or idx[ei].date() != day
                or (lo is not None and not (lo <= idx[ei].hour < hi))
                or idx[ei].time() >= spec["squareoff"]):
            kept.append(fi)
            continue
        bias = _bias_at(hidx, up, valid, idx[ei])
        want_up = float(sides.iloc[fi]) == 1.0   # UP flip needs up-trend
        if bias is None or bias == want_up:
            kept.append(fi)                      # no info (warm-up) / aligned
        else:
            blocked.append(fi)
    return kept, blocked


def run() -> None:
    spec = INSTRUMENTS["CRUDE"]
    bars = _load_5m(spec["ticker"], spec["cache"])
    closes = bars["Close"]
    idx = closes.index
    sides = envelope_side(closes, bars["High"], bars["Low"])
    flips = _flips_of(bars)
    hidx, up, valid = _hourly(closes)
    kept, blocked = _partition(spec, idx, flips, sides, hidx, up, valid)
    dayset = {ts.date() for ts in idx}
    iv0 = spec["head_iv"] / 100.0               # headline IV30, fixed

    base = _simulate(spec, sides, closes, flips, None, HEAD_DELAY, iv0)
    filt = _simulate(spec, sides, closes, kept, None, HEAD_DELAY, iv0)
    blk = _simulate(spec, sides, closes, blocked, None, HEAD_DELAY, iv0)
    b, f, g = _daily(base, dayset), _daily(filt, dayset), _daily(blk, dayset)

    # sanity: every baseline trade's flip is either kept or blocked (the
    # gate only removes flips; fewer same-day competitors can add trades
    # to filt/blk but never drop a base trade - spot-check that here)
    def key(t: dict) -> tuple:
        return (t["day"], t["side"], t["kind"], round(t["prem_in"], 4))

    missing = ({key(t) for t in base} - {key(t) for t in filt}
               - {key(t) for t in blk})

    days = len(dayset)
    print("=" * 104)
    print("CRUDE ENVELOPE x HTF EMA FILTER - 60d BZ=F 5m, card rules verbatim "
          "(delay+1, 17-22 gate, 2/day,")
    print("double -> reverse -> 21:55 square-off, Rs100/pt, 0.5pt cost) · "
          "gate: hourly EMA20/50, completed bars only")
    print("=" * 104)
    print(f"window {idx[0].date()} .. {idx[-1].date()} · days {days} · flips "
          f"{len(flips)} ({len(flips) / days:.1f}/day) · hourly bars "
          f"{len(hidx)} · structural skips pass through (gate moot)")
    print(f"gate split: kept {len(kept)} flips · blocked {len(blocked)} flips"
          f" · warm-up passes through (bias unknown)")

    print(f"\n  {'variant':26s} {'trades':>6s} {'/day':>5s} "
          f"{'EV/day gross':>12s} {'net/day':>9s} {'win':>5s} "
          f"{'worst day':>10s}")
    for label, st in (("no filter (baseline)", b),
                      ("EMA20>50 aligned only", f),
                      ("BLOCKED (run alone)", g)):
        print(f"  {label:26s} {st['n']:>6d} {st['n'] / days:>5.2f} "
              f"Rs{st['ev_day']:>10,.0f} Rs{st['net_day']:>8,.0f} "
              f"{st['win']:>5.0%} Rs{st['worst']:>10,.0f}")

    # IV robustness: does the gate hold across the crude IV ladder?
    rows = []
    for ivp in spec["iv_ladder"]:
        ivx = ivp / 100.0
        tb = _daily(_simulate(spec, sides, closes, flips, None,
                              HEAD_DELAY, ivx), dayset)
        tf = _daily(_simulate(spec, sides, closes, kept, None,
                              HEAD_DELAY, ivx), dayset)
        rows.append(f"IV{int(ivp)}: Rs{tb['ev_day']:>6,.0f} -> "
                    f"Rs{tf['ev_day']:>6,.0f}")
    print("\n  EV/day by IV (base -> filtered) -> " + "  ".join(rows))

    # what exactly does the gate refuse, by flip direction?
    up_b = [t for t in blk if t["side"] == "BLUE"]   # UP (sell PE) blocked
    dn_b = [t for t in blk if t["side"] == "RED"]     # DOWN (sell CE) blocked
    print(f"  blocked split -> UP {len(up_b)} trades "
          f"Rs{sum(t['rs'] for t in up_b):+,.0f} · DOWN {len(dn_b)} trades "
          f"Rs{sum(t['rs'] for t in dn_b):+,.0f}")

    check = "OK" if not missing else f"MISSING {len(missing)}!"
    print(f"\n  partition check -> {check} (every baseline trade is in the "
          "kept or blocked set)")

    delta = f["ev_day"] - b["ev_day"]
    pct = f" ({delta / b['ev_day']:+.1%})" if b["ev_day"] else ""
    print("-" * 104)
    print(f"VERDICT: filtered Rs{f['ev_day']:,.0f}/day vs baseline "
          f"Rs{b['ev_day']:,.0f}/day -> {delta:+,.0f}{pct} · refused-stream "
          f"EV Rs{g['ev_day']:,.0f}/day (sign only - runs alone, 2/day cap "
          "interplay ignored)")
    print("  refused < 0 = the gate earns its keep; > 0 = it burns money.")
    print("  live wiring (if kept): crude flip alert gains an 'HTF trend "
          "against' line - owner call (README 9).")


if __name__ == "__main__":
    run()

