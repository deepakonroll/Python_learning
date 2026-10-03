"""Unit tests for the 0DTE expiry playbook: rotation, pricing, sizing, text."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from nse_alerts import expiry
from nse_alerts.providers.base import ProviderError


def test_rotation_is_tuesday_nifty_thursday_sensex():
    assert expiry.EXPIRY_ROTATION == {1: "NIFTY", 3: "SENSEX"}
    assert expiry.EXPIRY_DAYS == {1, 3}
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


def test_size_lots_clamped_to_the_10k_budget():
    assert expiry.size_lots(120, 65) == 1            # 7,800/lot -> 1 lot
    assert expiry.size_lots(40, 65) == 2             # 2,600/lot -> 3 -> clamp 2
    assert expiry.size_lots(0, 65) == expiry.MAX_LOTS  # degenerate -> max


def test_build_alert_has_stops_size_and_discipline_lines():
    now = datetime(2026, 10, 6, 9, 45)               # Tuesday, NIFTY expiry
    text = expiry.build_alert("NIFTY", spot=22445.0,
                              bar_time=datetime(2026, 10, 6, 9, 40),
                              now=now, iv=0.145, iv_src="test", source="tv")
    assert "NIFTY 0DTE strangle" in text
    assert "SELL 22,500 CE" in text and "SELL 22,400 PE" in text
    # the stop on each leg is exactly 2x that leg's indicative premium
    minutes = 345                                    # 09:45 -> 15:30
    t = minutes / expiry._MINUTES_PER_YEAR
    ce = expiry.bs_price(22445.0, 22500.0, t, 0.145, "CE")
    pe = expiry.bs_price(22445.0, 22400.0, t, 0.145, "PE")
    assert f"₹{2 * ce:,.0f} (2×)" in text
    assert f"₹{2 * pe:,.0f} (2×)" in text
    assert "ONE entry" in text and "NO adds" in text
    assert "15:15" in text and "EITHER leg" in text


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
