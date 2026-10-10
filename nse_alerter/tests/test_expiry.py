"""Unit tests for the intraday strangle playbook: rotation, strike rule, pricing, text."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from nse_alerts import expiry
from nse_alerts.providers.base import ProviderError


def test_rotation_is_every_weekday_with_a_sessions_map():
    assert expiry.EXPIRY_ROTATION == {0: "NIFTY", 1: "NIFTY", 2: "SENSEX",
                                      3: "SENSEX", 4: "NIFTY"}
    assert expiry.EXPIRY_DAYS == {0, 1, 2, 3, 4}
    # sessions left at the 09:45 entry: NIFTY expires Tue, SENSEX Thu
    assert expiry.ENTRY_SESSIONS[4] == ("NIFTY", 2.92)   # Friday, 2.9 sess
    assert expiry.ENTRY_SESSIONS[1] == ("NIFTY", 0.92)   # Tuesday, 0-DTE
    assert expiry.ENTRY_SESSIONS[3] == ("SENSEX", 0.92)  # Thursday, 0-DTE
    assert set(expiry.EXPIRY_SPECS) == {"NIFTY", "SENSEX"}
    assert expiry.EXPIRY_SPECS["NIFTY"]["step"] == 50
    assert expiry.EXPIRY_SPECS["SENSEX"]["step"] == 100
    assert expiry.EXPIRY_SPECS["SENSEX"]["lot"] == 20


def test_bs_price_put_call_parity_and_expiry_intrinsic():
    spot, iv = 22450.0, 0.14
    t = 300 / expiry._MINUTES_PER_YEAR               # ~5h of 0DTE life left
    ce = expiry.bs_price(spot, 22500.0, t, iv, "CE")
    pe = expiry.bs_price(spot, 22500.0, t, iv, "PE")
    assert ce > 0 and pe > 0                         # both sides carry time
    assert ce - pe == pytest.approx(spot - 22500.0, abs=1e-6)   # r = 0 parity
    # at expiry only intrinsic remains
    assert expiry.bs_price(22550, 22500, 0, iv, "CE") == pytest.approx(50)
    assert expiry.bs_price(22500, 22550, 0, iv, "CE") == 0.0
    assert expiry.bs_price(22400, 22500, 0, iv, "PE") == pytest.approx(100)


def test_atm_and_otm_strikes_on_both_grids():
    assert expiry.atm_and_otm(22445, 50) == (22450, 22500, 22400)
    assert expiry.atm_and_otm(71905, 100) == (71900, 72000, 71800)
    # exactly on a strike -> still one strike OTM on each side
    assert expiry.atm_and_otm(72000, 100) == (72000, 72100, 71900)


def test_strike_rule_sizes_20_lots_fixed():
    nifty = expiry.strike_rule("NIFTY", 0, 22500.0, 0.14)
    assert nifty["lots"] == 20 and nifty["qty"] == 1300   # 20 x 65
    sensex = expiry.strike_rule("SENSEX", 2, 72000.0, 0.14)
    assert sensex["lots"] == 20 and sensex["qty"] == 400   # 20 x 20


def test_build_alert_has_stops_size_and_discipline_lines():
    now = datetime(2026, 10, 6, 9, 45)               # Tuesday, NIFTY 0-DTE
    text = expiry.build_alert("NIFTY", spot=22445.0,
                              bar_time=datetime(2026, 10, 6, 9, 40),
                              now=now, iv=0.145, iv_src="test", source="tv")
    assert "NIFTY 0DTE strangle" in text
    rule = expiry.strike_rule("NIFTY", 1, 22445.0, 0.145)
    assert rule["ce"] == 22650 and rule["pe"] == 22250    # closer 0.95% legs
    assert f"SELL {rule['ce']:,} CE" in text
    assert f"SELL {rule['pe']:,} PE" in text
    # the stop on each leg is exactly 2x that leg's indicative premium
    assert f"₹{2 * rule['ce_prem']:,.0f} (2×)" in text
    assert f"₹{2 * rule['pe_prem']:,.0f} (2×)" in text
    assert "rule CLOSER 0.95%" in text
    assert "−1.0C" in text                               # 0-DTE wide stop
    assert f"−{rule['stop_pts']:,.0f} pts" in text       # portfolio stop
    assert f"+{rule['decay']:,.0f} pts decay" in text    # session-decay target
    assert "20 lots (1,300 qty)" in text
    assert "ONE entry" in text and "NO adds" in text
    assert "15:15" in text and "EITHER leg" in text


def test_strike_rule_sessions_stops_and_skip_paths():
    fri = expiry.strike_rule("NIFTY", 4, 22500.0, 0.14)
    mon = expiry.strike_rule("NIFTY", 0, 22500.0, 0.14)
    tue = expiry.strike_rule("NIFTY", 1, 22500.0, 0.14)
    assert (fri["sessions"], mon["sessions"], tue["sessions"]) == (2.92, 1.92,
                                                                  0.92)
    assert fri["stop_frac"] == mon["stop_frac"] == 0.5
    assert tue["stop_frac"] == 1.0                     # 0-DTE wide stop
    assert 0 < fri["decay"] < fri["credit"]            # theta target is sane
    assert fri["stop_pts"] == pytest.approx(0.5 * fri["credit"])
    assert fri["stop_rs"] == pytest.approx(fri["stop_pts"] * 1300)
    # Thursday SENSEX 0-DTE is a measured loser - never trade it
    thu = expiry.strike_rule("SENSEX", 3, 72000.0, 0.14)
    assert thu["skip"] and "loses at every VIX" in thu["skip"]
    # a deflated IV drops a normal day's credit under the floor
    thin = expiry.strike_rule("NIFTY", 0, 22500.0, 0.05)
    assert thin["skip"] and "floor" in thin["skip"]
    # Thursday's alert carries NO ENTRY plus the 100-pt grid levels
    text = expiry.build_alert("SENSEX", spot=71905.0,
                              bar_time=datetime(2026, 10, 8, 9, 40),
                              now=datetime(2026, 10, 8, 9, 45),
                              iv=0.14, iv_src="test", source="tv")
    assert "NO ENTRY" in text and "SENSEX 0DTE strangle" in text
    assert "72,600 CE" in text and "71,200 PE" in text   # 0.95% on step 100


def test_fetch_india_vix_reads_close(monkeypatch):
    from nse_alerts.providers import yahoo

    def fake_fetch(self, symbol, interval, lookback):
        assert symbol == "^INDIAVIX"
        return pd.DataFrame(
            {"Open": [14.0], "High": [14.5], "Low": [13.9],
             "Close": [14.46], "Volume": [1.0]},
            index=pd.to_datetime(["2026-10-01"]),
        )

    monkeypatch.setattr(yahoo.YahooProvider, "fetch", fake_fetch)
    iv, src = expiry.fetch_india_vix()
    assert iv == pytest.approx(0.1446)
    assert src == "india-vix"


def test_fetch_india_vix_falls_back_when_the_feed_breaks(monkeypatch):
    from nse_alerts.providers import yahoo

    def broken(self, symbol, interval, lookback):
        raise ProviderError("offline")

    monkeypatch.setattr(yahoo.YahooProvider, "fetch", broken)
    iv, src = expiry.fetch_india_vix()
    assert iv == expiry.DEFAULT_IV / 100.0
    assert src == "default"
