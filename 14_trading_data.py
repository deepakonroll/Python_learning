"""
Lesson 14 - Market data + analysis: yfinance & pandas, from a Java perspective.

Run:  python 14_trading_data.py

A side-quest next to ROADMAP.md's Phase 1-2 (NumPy/pandas): real market data
instead of toy CSVs, and a taste of what the data stack feels like.

Needs:  pip install yfinance   (pandas + numpy arrive with it)

Java -> Python highlights:
  * REST client + Jackson     -> yfinance.download() -> pandas DataFrame
  * ResultSet / List<Map>     -> DataFrame (typed columns + DatetimeIndex)
  * for-loop over rows        -> vectorized ops: close.pct_change().mean()
  * SQL GROUP BY date_trunc   -> df.resample("ME").last()
  * SQL window function       -> df.rolling(21).std()
  * Stream.filter             -> boolean mask:  close[close > sma200]
  * checked IOException       -> try/except (EAFP) around the fetch
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

TICKER = "SPY"
YEARS = 10
TRADING_DAYS = 252  # sessions in a trading year (business-calendar constant)


def fetch_prices(ticker: str = TICKER, years: int = YEARS) -> tuple[pd.DataFrame, str]:
    """Download daily OHLCV bars; fall back to synthetic data when offline.

    Java: this try/except is lesson 09's EAFP idiom - here it also absorbs
    Yahoo's rate limits and API churn (nobody forces us to catch anything).
    """
    try:
        df = yf.download(
            ticker,
            period=f"{years}y",      # e.g. 10 years of daily bars
            auto_adjust=True,        # Close adjusted for splits/dividends
            progress=False,          # keep the output clean
        )
        if df is None or df.empty:
            raise RuntimeError("empty response")
        if len(df) < 200:
            raise RuntimeError(f"only {len(df)} rows (need >= 200 for SMA200)")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)  # single ticker -> flat
        df.columns.name = None                          # cosmetic: drop "Price" label
        return df, f"live data from Yahoo Finance ({len(df)} rows)"
    except Exception as exc:  # rate limit, offline, API change...
        return (
            synthetic_prices(ticker, years),
            f"offline fallback ({type(exc).__name__}: {exc})",
        )


def synthetic_prices(ticker: str = TICKER, years: int = YEARS) -> pd.DataFrame:
    """Deterministic fake OHLCV (numpy geometric random walk).

    Java: a test fixture factory - same shape as production data, so every
    section below runs with or without network access.
    """
    n = years * TRADING_DAYS
    rng = np.random.default_rng(42)              # seeded -> reproducible
    daily = rng.normal(0.0004, 0.011, n)         # ~10% drift, ~17% vol/yr
    close = 100.0 * np.exp(np.cumsum(daily))     # GBM prices, zero loops
    open_ = np.concatenate(([close[0]], close[:-1])) * (
        1 + rng.normal(0, 0.002, n)
    )
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n)),
            "Low": np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n)),
            "Close": close,
            "Volume": rng.integers(10_000_000, 90_000_000, n),
        },
        index=pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n),
    )


def main() -> None:
    # ------------------------------------------------------------------
    # 0) Fetch - one call where Java needs a client + Jackson + DTO classes
    # ------------------------------------------------------------------
    print(f"=== 0) fetch {TICKER} ({YEARS}y of daily bars) ===")
    df, provenance = fetch_prices()
    print(provenance)

    # ------------------------------------------------------------------
    # 1) DataFrame anatomy - your ResultSet, but with labeled, typed
    #    columns and a sorted DatetimeIndex instead of a row cursor.
    # ------------------------------------------------------------------
    print("\n=== 1) DataFrame anatomy ===")
    print(df.tail(3))
    print("shape (rows, cols):", df.shape)   # ResultSet.getRowCount() x column count
    print("columns:", list(df.columns))
    print(df.dtypes)                         # per-column types, no rs.getObject(i)

    close: pd.Series = df["Close"]           # one column -> a Series (vector)

    # ------------------------------------------------------------------
    # 2) Vectorized math - the whole point of pandas/numpy (ROADMAP Phase 1).
    #    Java equivalent is a for-loop over ~2500 rows:
    #        for (int i = 1; i < close.size(); i++)
    #            ret[i] = close.get(i) / close.get(i - 1) - 1.0;
    # ------------------------------------------------------------------
    print("\n=== 2) vectorized returns (no loops) ===")
    daily = close.pct_change(fill_method=None)   # elementwise, executed in C
    print(daily.tail(3).round(4))
    print(
        f"mean day {daily.mean():+.5f} | worst {daily.min():+.4f} "
        f"on {daily.idxmin().date()} | best {daily.max():+.4f} "
        f"on {daily.idxmax().date()}"
    )
    # numpy underneath: log returns = diff of logs, still one expression
    log_ret = np.log(close / close.shift(1))
    print(f"log-return mean {log_ret.mean():+.5f} (NumPy, zero loops)")

    # ------------------------------------------------------------------
    # 3) Boolean masks - Stream.filter, but vectorized over the column.
    # ------------------------------------------------------------------
    print("\n=== 3) boolean masks (golden-cross screen) ===")
    sma_fast = close.rolling(50).mean()      # 50-day moving average
    sma_slow = close.rolling(200).mean()     # SQL: AVG OVER 200-ROW WINDOW
    above = close > sma_slow                 # one bool per row, no if/else
    print(
        f"days above SMA200: {int(above.sum())} of {len(above)} "
        f"| right now: {bool(above.iloc[-1])}"
    )
    bull = sma_fast > sma_slow
    # pandas has a nullable "boolean" dtype too - the Boolean vs boolean
    # distinction you know from Java (shift on plain bool would inject NaN).
    prev_bull = bull.astype("boolean").shift(1, fill_value=False)
    cross = bull & ~prev_bull                # False -> True transitions
    print(
        "last golden cross:",
        cross[cross].index[-1].date() if cross.any() else "none in window",
    )
    print(close[above].tail(2))              # rows surviving the filter

    # ------------------------------------------------------------------
    # 4) resample + rolling - SQL GROUP BY / window functions, as methods.
    # ------------------------------------------------------------------
    print("\n=== 4) resample (GROUP BY month) + rolling volatility ===")
    monthly = close.resample("ME").last()        # month-end close, pandas >= 2.2
    print(monthly.tail(3))
    monthly_ret = monthly.pct_change(fill_method=None).dropna()
    print(
        "best month:",
        monthly_ret.idxmax().date(),
        f"{monthly_ret.max():+.2%}",
    )
    ann_vol = daily.rolling(21).std() * np.sqrt(TRADING_DAYS)  # 1-month window
    print(f"annualized 21d volatility now: {ann_vol.iloc[-1]:.1%}")

    # ------------------------------------------------------------------
    # 5) Mini backtest - 4 vectorized lines; the Java version is a mutable
    #    double + branch per row in a for-loop. shift(1) = act on TODAY's
    #    close using YESTERDAY's signal (no look-ahead cheating).
    # ------------------------------------------------------------------
    print("\n=== 5) SMA200 strategy vs buy & hold ===")
    signal = (close > sma_slow).astype(float)    # 1.0 = invest, 0.0 = cash
    position = signal.shift(1).fillna(0.0)       # decide on yesterday's bar
    strat_ret = position * daily.fillna(0.0)
    equity = (1 + strat_ret).cumprod()           # compound a starting $1
    buy_hold = (1 + daily.fillna(0.0)).cumprod()
    print(
        f"buy & hold ${buy_hold.iloc[-1]:.2f} | SMA200 ${equity.iloc[-1]:.2f}"
        f" | days invested: {int(position.sum())}"
    )
    print("(toy model: no fees/slippage - a real backtest needs those)")

    # ------------------------------------------------------------------
    # 6) Export - DataFrame -> CSV (Jackson-dataformat-CSV vibes).
    # ------------------------------------------------------------------
    print("\n=== 6) round-trip to CSV ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{TICKER}.csv"
        df.tail(30).to_csv(path)                 # index included by default
        lines = path.read_text(encoding="utf-8").splitlines()
        print(f"wrote {len(lines) - 1} rows + header: {lines[0]}")

    # ------------------------------------------------------------------
    # 7) Charts - roadmap Phase 3 territory. A 2x2 report figure; in Java
    #    this is JFreeChart plumbing - here it is ~25 declarative lines.
    # ------------------------------------------------------------------
    print("\n=== 7) charts ===")
    try:
        import matplotlib
    except ImportError:
        print("[skip] matplotlib not installed - roadmap Phase 3 says hi")
    else:
        matplotlib.use("Agg")                    # headless backend, no GUI
        import matplotlib.pyplot as plt

        out_dir = Path(__file__).resolve().parent / "charts"
        out_dir.mkdir(exist_ok=True)             # gitignored output folder
        out = out_dir / f"{TICKER}_report.png"

        norm = close / close.iloc[0]             # start every series at 1.0
        sma_norm = sma_slow / close.iloc[0]
        drawdown = buy_hold / buy_hold.cummax() - 1.0

        fig, axes = plt.subplots(2, 2, figsize=(13, 8))
        axes[0, 0].plot(norm.index, norm.values, label=TICKER)
        axes[0, 0].plot(sma_norm.index, sma_norm.values, label="SMA200")
        axes[0, 0].set_title("Normalized price (base = 1.0)")
        axes[0, 0].legend()

        axes[0, 1].fill_between(drawdown.index, drawdown.values, 0.0,
                                color="firebrick", alpha=0.6)
        axes[0, 1].set_title("Buy & hold drawdown")
        axes[0, 1].set_ylabel("drawdown")

        axes[1, 0].bar(monthly_ret.index, monthly_ret.values,
                       width=20, color="seagreen")
        axes[1, 0].axhline(0, color="black", linewidth=0.8)
        axes[1, 0].set_title("Monthly returns")

        axes[1, 1].plot(ann_vol.index, ann_vol.values, color="darkorange")
        axes[1, 1].set_title("21-day annualized volatility")
        axes[1, 1].set_ylabel("volatility")

        fig.tight_layout()
        fig.savefig(out, dpi=110)
        print(f"[chart] wrote {out}")
        try:
            import os
            os.startfile(out)                    # Windows: pop it open
        except (AttributeError, OSError):
            pass                                 # other OS / no GUI: file stays

    print("\nNext: ROADMAP.md Phase 1 (NumPy) and Phase 2 (pandas).")


if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
