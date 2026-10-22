"""Unit tests for the expiry/rotation data + BS pricing helpers.

The 09:45 entry-alert tests (strike_rule/build_alert/fetch_india_vix) were
removed with the alert itself (retired 2026-10-22).
"""

from __future__ import annotations

import pytest

from nse_alerts import expiry


def test_rotation_is_every_weekday_with_a_sessions_map():
    assert expiry.EXPIRY_ROTATION == {0: "NIFTY", 1: "NIFTY", 2: "SENSEX",
                                      3: "SENSEX", 4: "NIFTY"}
    assert expiry.EXPIRY_DAYS == {0, 1, 2, 3, 4}
    # sessions left to the contract's expiry: NIFTY expires Tue, SENSEX Thu
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
