"""
Lesson 15 - A browser UI for the lesson-14 analysis: Streamlit.

Run:  streamlit run 15_trading_dashboard.py
      (plain `python 15_trading_dashboard.py` just prints usage)

Java -> Python highlights:
  * Spring MVC + templates -> Streamlit: the Python script IS the UI
  * React components / JSX    -> st.* widgets; no JS, HTML or CSS to write
  * server-side rendering     -> the whole script RERUNS on every interaction
  * @Cacheable / memoization -> @st.cache_data(ttl=...)
  * REST + chart JS library   -> plotly figures, interactive out of the box

Like every lesson this file is runnable on its own: it pulls the data logic
from lesson 14 via importlib (digit-first filenames are not importable the
normal way - see README - same idea as Class.forName at runtime).
"""

import importlib.util
import pathlib

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

TICKER = "SPY"
TRADING_DAYS = 252  # sessions in a trading year (business-calendar constant)


def in_streamlit() -> bool:
    """True only when running under `streamlit run` (or the test harness).

    Java: like checking a request/servlet context before touching
    request-scoped APIs - this file has two valid entry paths.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def _lesson14():
    """Dynamically load the digit-named lesson module (Class.forName vibes)."""
    path = pathlib.Path(__file__).with_name("14_trading_data.py")
    spec = importlib.util.spec_from_file_location("lesson14", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # top-level code runs; its main() is guarded
    return module


@st.cache_data(ttl=3600, show_spinner="Fetching market data...")
def load_prices(ticker: str, years: int) -> tuple[pd.DataFrame, str]:
    """Cached fetch - same ticker/years hits the cache, not Yahoo.

    Java: @Cacheable("marketData") on a service method; ttl = time-to-live.
    """
    lesson = _lesson14()
    return lesson.fetch_prices(ticker, years)  # live data or synthetic fallback


def build_ui() -> None:
    st.set_page_config(page_title="Market Lab - lesson 15", page_icon="📈",
                       layout="wide")
    st.title("📈 Market Lab")
    st.caption("Lessons 14 + 15, now with a UI. Every widget below reruns "
               "this whole script - like a controller action, zero wiring.")

    # ------------------------------------------------------------------
    # Sidebar controls - state survives across reruns (Streamlit does it).
    # ------------------------------------------------------------------
    with st.sidebar:
        st.header("Controls")
        ticker = st.text_input("Ticker", TICKER).strip().upper() or TICKER
        years = st.selectbox("History", [5, 10], index=1,
                             format_func=lambda y: f"{y} years")
        fast = st.number_input("Fast SMA (days)", 5, 100, 50, 5)
        slow = st.number_input("Slow SMA (days)", 50, 400, 200, 10)
        st.caption("Change any widget -> the script reruns, but the\n"
                   "cached fetch is NOT repeated (@st.cache_data).")

    df, provenance = load_prices(ticker, years)
    if "fallback" in provenance:
        st.warning(provenance)
    st.caption(f"{provenance} | {df.shape[0]} rows x {df.shape[1]} cols")

    if slow >= len(df):
        st.error(f"Slow SMA ({slow}) longer than the loaded history.")
        st.stop()

    # --- same math as lesson 14, straight from pandas ------------------
    close = df["Close"]
    daily = close.pct_change(fill_method=None)
    sma_fast = close.rolling(fast).mean()
    sma_slow = close.rolling(slow).mean()
    above = close > sma_slow
    monthly = close.resample("ME").last()
    monthly_ret = monthly.pct_change(fill_method=None).dropna()
    ann_vol = daily.rolling(21).std() * np.sqrt(TRADING_DAYS)

    signal = (close > sma_slow).astype(float)    # 1.0 = invested, 0.0 = cash
    position = signal.shift(1).fillna(0.0)       # no look-ahead (lesson 14)
    equity = (1 + position * daily.fillna(0.0)).cumprod()
    buy_hold = (1 + daily.fillna(0.0)).cumprod()
    drawdown = buy_hold / buy_hold.cummax() - 1.0

    # --- metric cards: the terminal prints, but prettier ----------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Last close", f"${close.iloc[-1]:,.2f}", f"{daily.iloc[-1]:+.2%}")
    c2.metric("21d ann. vol", f"{ann_vol.iloc[-1]:.1%}")
    c3.metric("Buy & hold", f"${buy_hold.iloc[-1]:.2f}")
    c4.metric(f"SMA{slow} strategy", f"${equity.iloc[-1]:.2f}",
              f"{equity.iloc[-1] / buy_hold.iloc[-1] - 1:+.1%} vs B&H")

    tab_price, tab_back, tab_month, tab_data = st.tabs(
        ["Price & SMAs", "Backtest", "Monthly", "Raw data"]
    )

    with tab_price:
        fig = go.Figure()
        fig.add_scatter(x=close.index, y=close, name=ticker,
                        line=dict(width=1.5))
        fig.add_scatter(x=sma_fast.index, y=sma_fast, name=f"SMA{fast}")
        fig.add_scatter(x=sma_slow.index, y=sma_slow, name=f"SMA{slow}")
        fig.update_layout(height=430, margin=dict(l=10, r=10, t=30, b=10),
                          xaxis_title="", yaxis_title="price", legend_title="")
        st.plotly_chart(fig, width="stretch")
        st.write(f"Days above SMA{slow}: **{int(above.sum())}** of "
                 f"{len(above)} | right now: **{bool(above.iloc[-1])}**")

    with tab_back:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07,
            row_heights=[0.65, 0.35],
            subplot_titles=("Equity curves ($1 start)", "Buy & hold drawdown"),
        )
        fig.add_trace(
            go.Scatter(x=buy_hold.index, y=buy_hold, name="buy & hold"),
            row=1, col=1)
        fig.add_trace(
            go.Scatter(x=equity.index, y=equity, name=f"SMA{slow} strategy"),
            row=1, col=1)
        fig.add_trace(
            go.Scatter(x=drawdown.index, y=drawdown, name="drawdown",
                       fill="tozeroy"),
            row=2, col=1)
        fig.update_layout(height=560, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, width="stretch")
        st.info("Toy model: no fees/slippage. shift(1) = no look-ahead "
                "cheating - a real backtest adds transaction costs.")

    with tab_month:
        fig = px.bar(x=monthly_ret.index, y=monthly_ret.values,
                     labels={"x": "", "y": "return"}, title="Monthly returns")
        fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, width="stretch")

        frame = monthly_ret.to_frame("ret")
        frame["year"] = frame.index.year
        frame["month"] = frame.index.month
        pivot = frame.pivot(index="year", columns="month", values="ret")
        fig = px.imshow(pivot, text_auto=".1%", aspect="auto",
                        color_continuous_scale="RdYlGn",
                        labels=dict(color="return"),
                        title="Year x month heatmap")
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, width="stretch")

    with tab_data:
        st.dataframe(df.tail(100).round(3), width="stretch")
        st.download_button("Download full history as CSV",
                           df.to_csv().encode("utf-8"),
                           file_name=f"{ticker}.csv", mime="text/csv")


def main() -> None:
    if not in_streamlit():
        print("This file is a Streamlit app - run it with:\n")
        print("    streamlit run 15_trading_dashboard.py\n")
        print("Plain `python` cannot render the UI. Streamlit reruns the")
        print("script on every widget interaction - no JS/HTML/CSS needed.")
        return
    build_ui()


if __name__ == "__main__":
    main()
