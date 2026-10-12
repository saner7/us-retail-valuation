"""Tests for comps.py with small numbers you can check by hand."""

import math

import numpy as np
import pandas as pd

import comps


def company(ev=1200.0, mcap=1000.0, revenue=4000.0, ebitda=200.0, ni=50.0, equity=500.0,
            net_debt=200.0, shares=100.0, price=10.0):
    return {"enterprise_value": ev, "market_cap": mcap, "revenue_ttm": revenue,
            "ebitda_ttm": ebitda, "net_income_ttm": ni, "total_equity": equity,
            "net_debt": net_debt, "shares_outstanding": shares, "price": price}


def test_multiples_and_margins():
    r, notes = comps.compute_multiples(company())
    assert r["ev_sales"] == 0.3        # 1200 / 4000
    assert r["ev_ebitda"] == 6.0       # 1200 / 200
    assert r["pe"] == 20.0             # 1000 / 50
    assert r["pb"] == 2.0              # 1000 / 500
    assert r["ebitda_margin"] == 0.05  # 200 / 4000
    assert r["net_margin"] == 0.0125   # 50 / 4000
    assert notes == {}


def test_loss_making_company_pe_is_excluded_with_reason():
    r, notes = comps.compute_multiples(company(ni=-30.0))
    assert math.isnan(r["pe"])
    assert "negative" in notes["pe"]
    assert r["net_margin"] == -0.0075  # negative margin is kept: it is real information


def test_multiple_above_cap_is_not_meaningful():
    r, notes = comps.compute_multiples(company(ni=1.0), caps={"pe": 100.0})  # P/E = 1000x
    assert math.isnan(r["pe"])
    assert "cap" in notes["pe"]


def test_peer_stats_exclude_target_and_nan():
    table = pd.DataFrame({"ev_ebitda": [99.0, 4.0, 6.0, 8.0, 10.0, np.nan]},
                         index=["TARGET", "A", "B", "C", "D", "E"])
    s = comps.peer_stats(table, ["A", "B", "C", "D", "E"])["ev_ebitda"]
    assert s["count"] == 4             # E is NaN, TARGET is not a peer
    assert s["mean"] == 7.0            # (4+6+8+10)/4 - the 99 of the target is ignored
    assert s["median"] == 7.0
    assert s["min"] == 4.0 and s["max"] == 10.0
    assert s["p25"] == 5.5             # linear interpolation: 4 + 0.75 x (6-4)
    assert s["p75"] == 8.5


def test_range_uses_quartiles_or_min_max():
    col = pd.Series({"count": 4, "median": 7.0, "min": 4.0, "max": 10.0, "p25": 5.5, "p75": 8.5})
    assert comps.valuation_range(col, min_peers=4)[:3] == (5.5, 7.0, 8.5)
    small = col.copy()
    small["count"] = 3
    assert comps.valuation_range(small, min_peers=4)[:3] == (4.0, 7.0, 10.0)


def test_bridge_for_ev_multiple():
    # EBITDA 200 x 6.0 = EV 1200; - net debt 200 = equity 1000; / 100 shares = 10.00
    r = comps.implied_value(company(), "ev_ebitda", (6.0,))
    assert r["implied_ev"] == [1200.0]
    assert r["equity_value"] == [1000.0]
    assert r["per_share"] == [10.0]


def test_bridge_for_equity_multiple_skips_net_debt():
    # Net income 50 x P/E 20 = equity 1000 directly; / 100 shares = 10.00
    r = comps.implied_value(company(), "pe", (20.0,))
    assert math.isnan(r["implied_ev"][0])
    assert r["equity_value"] == [1000.0]
    assert r["per_share"] == [10.0]


def test_cannot_apply_multiple_to_negative_target_metric():
    r = comps.implied_value(company(ebitda=-10.0), "ev_ebitda", (6.0,))
    assert math.isnan(r["per_share"][0])
    assert "not positive" in r["note"]
