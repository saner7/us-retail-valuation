"""Tests for dcf.py with small numbers you can check by hand (or in Excel)."""

import math

import numpy as np
import pandas as pd
import pytest

import dcf


def flat_assumptions(**changes):
    a = {"revenue_growth": 0.0, "ebitda_margin": 0.20, "da_pct": 0.05, "capex_pct": 0.05,
         "nwc_pct": 0.0, "tax_rate": 0.25}
    a.update(changes)
    return a


def test_fcff_formula():
    # Revenue 1000 -> EBITDA 200, D&A 50, EBIT 150, tax 37.5, NOPAT 112.5
    # FCFF = 112.5 + 50 (D&A) - 50 (capex) - 10 (NWC) = 102.5
    p = dcf.project(1000.0, flat_assumptions(nwc_pct=0.01), years=1)
    assert p.loc["ebit", "Year 1"] == 150.0
    assert p.loc["nopat", "Year 1"] == 112.5
    assert p.loc["fcff", "Year 1"] == pytest.approx(102.5)


def test_revenue_compounds_and_per_year_assumptions():
    p = dcf.project(100.0, flat_assumptions(revenue_growth=[0.10, 0.20]), years=2)
    assert p.loc["revenue", "Year 1"] == pytest.approx(110.0)
    assert p.loc["revenue", "Year 2"] == pytest.approx(132.0)   # 110 x 1.2


def test_wacc():
    # Cost of equity = 4% + 1.2 x 5% + 1% = 11%; after-tax Kd = 6% x 0.75 = 4.5%
    # WACC = 0.6 x 11% + 0.4 x 4.5% = 6.6% + 1.8% = 8.4%
    w = dcf.wacc(risk_free=0.04, equity_risk_premium=0.05, beta=1.2, country_risk_premium=0.01,
                 pre_tax_cost_of_debt=0.06, tax_rate=0.25, equity_value=600, debt_value=400)
    assert w["cost_of_equity"] == pytest.approx(0.11)
    assert w["after_tax_cost_of_debt"] == pytest.approx(0.045)
    assert w["wacc"] == pytest.approx(0.084)


def test_discounting_end_and_mid_year():
    assert list(dcf.discount_periods(3, mid_year=False)) == [1, 2, 3]
    assert list(dcf.discount_periods(3, mid_year=True)) == [0.5, 1.5, 2.5]
    f = dcf.discount_factors(0.10, [1, 2])
    assert f[0] == pytest.approx(1 / 1.1)
    assert f[1] == pytest.approx(1 / 1.21)


def test_terminal_values():
    # Gordon: 100 x 1.02 / (0.10 - 0.02) = 1275
    assert dcf.gordon_terminal_value(100.0, 0.10, 0.02) == pytest.approx(1275.0)
    assert math.isnan(dcf.gordon_terminal_value(100.0, 0.05, 0.05))   # g >= WACC: undefined
    assert dcf.exit_multiple_terminal_value(50.0, 8.0) == 400.0
    # Implied growth turns the exit TV back into a Gordon g
    g = dcf.implied_growth(1275.0, 100.0, 0.10)
    assert g == pytest.approx(0.02)


def test_full_dcf_end_of_year_by_hand():
    # Flat business: revenue 1000, EBIT 150, D&A = capex, no NWC.
    # Choose the tax rate so FCFF is exactly 100: 150 x (1 - t) = 100 -> t = 1/3
    a = flat_assumptions(tax_rate=1 / 3)
    r = dcf.run_dcf(base_revenue=1000.0, assumptions=a, wacc_rate=0.10, terminal_growth=0.0,
                    exit_multiple=5.0, net_debt=100.0, shares=10.0, price=10.0, mid_year=False)
    proj = r["projection"]
    assert list(proj.loc["fcff"].round(6)) == [100.0] * 5
    # PV of 5 x 100 at 10% = 379.0787 (annuity factor 3.790787)
    assert r["gordon"]["sum_pv_fcff"] == pytest.approx(379.0787, rel=1e-6)
    # Gordon with g=0: TV = 100 / 0.10 = 1000, PV = 1000 / 1.1^5 = 620.9213
    # EV = 379.0787 + 620.9213 = 1000 (a flat perpetuity is worth FCFF / WACC)
    assert r["gordon"]["enterprise_value"] == pytest.approx(1000.0)
    assert r["gordon"]["equity_value"] == pytest.approx(900.0)
    assert r["gordon"]["value_per_share"] == pytest.approx(90.0)
    assert r["gordon"]["upside"] == pytest.approx(8.0)
    # Exit: TV = EBITDA 200 x 5 = 1000, discounted at year 5 -> same as Gordon here
    assert r["exit"]["terminal_value"] == pytest.approx(1000.0)
    assert r["exit"]["enterprise_value"] == pytest.approx(1000.0)


def test_mid_year_gordon_tv_discounted_at_n_minus_half_exit_at_n():
    a = flat_assumptions(tax_rate=1 / 3)
    r = dcf.run_dcf(base_revenue=1000.0, assumptions=a, wacc_rate=0.10, terminal_growth=0.0,
                    exit_multiple=5.0, net_debt=0.0, shares=1.0, price=1.0, mid_year=True)
    assert r["gordon"]["tv_discount_period"] == 4.5
    assert r["exit"]["tv_discount_period"] == 5.0
    assert r["gordon"]["pv_terminal_value"] == pytest.approx(1000 / 1.1 ** 4.5)
    assert r["exit"]["pv_terminal_value"] == pytest.approx(1000 / 1.1 ** 5)


def test_terminal_value_share_warning():
    a = flat_assumptions(tax_rate=1 / 3)
    r = dcf.run_dcf(base_revenue=1000.0, assumptions=a, wacc_rate=0.10, terminal_growth=0.0,
                    exit_multiple=20.0, net_debt=0.0, shares=1.0, price=1.0, mid_year=False)
    assert r["exit"]["tv_share_of_ev"] > 0.75
    assert any("Exit multiple" in w for w in r["warnings"])


def test_sensitivity_grid_shape_and_center():
    a = flat_assumptions(tax_rate=1 / 3)
    kw = dict(base_revenue=1000.0, assumptions=a, wacc_rate=0.10, terminal_growth=0.0,
              exit_multiple=5.0, net_debt=100.0, shares=10.0, price=10.0, mid_year=False)
    t = dcf.sensitivity(kw, "gordon", [0.09, 0.10, 0.11], [-0.005, 0.0, 0.005])
    assert t.shape == (3, 3)
    assert t.loc[0.10, 0.0] == pytest.approx(90.0)          # same as the base case
    assert t.loc[0.09, 0.0] > t.loc[0.11, 0.0]              # lower WACC -> higher value
    assert dcf._grid(0.10, 0.02, 0.005)[0] == 0.08 and len(dcf._grid(0.10, 0.02, 0.005)) == 9


def sample_history():
    # 4 fiscal years; the oldest one is an outlier (margin 5%, capex 10%)
    return pd.DataFrame({
        "revenue": [100.0, 100.0, 110.0, 121.0], "ebitda": [5.0, 10.0, 11.0, 12.1],
        "d_and_a": [2.0, 2.0, 2.2, 2.42], "capex": [-10.0, -3.0, -3.3, -3.63],
        "change_in_wc": [-5.0, -1.0, -1.1, -1.21], "tax_provision": [1.0, 2.0, 2.5, 3.0],
        "pretax_income": [10.0, 10.0, 10.0, 10.0],
    }, index=pd.to_datetime(["2023-01-31", "2024-01-31", "2025-01-31", "2026-01-31"]))


def test_default_assumptions_use_last_three_years():
    d = dcf.default_assumptions(sample_history(), revenue_ttm=127.05, terminal_growth=0.02, n_years=3)
    assert d["ebitda_margin"] == pytest.approx(0.10)      # no TTM EBITDA given: flat 3-year average
    assert d["capex_pct"] == pytest.approx(0.03)          # capex sign flipped to a cost; 10% year left out
    assert d["tax_rate"] == pytest.approx(0.25)           # (20% + 25% + 30%) / 3
    assert d["nwc_pct"] == 0.0                            # config default, not the volatile history


def test_revenue_growth_fades_from_ttm_growth_to_terminal_growth():
    # TTM 127.05 vs last fiscal year 121 = +5%; straight line to 2% over 5 years
    d = dcf.default_assumptions(sample_history(), revenue_ttm=127.05, terminal_growth=0.02, n_years=3)
    assert d["revenue_growth"] == pytest.approx([0.05, 0.0425, 0.035, 0.0275, 0.02])
    # Without TTM revenue, Year 1 falls back to the CAGR (100 -> 121 over 3 years)
    d = dcf.default_assumptions(sample_history(), terminal_growth=0.02)
    assert d["revenue_growth"][0] == pytest.approx(1.21 ** (1 / 3) - 1)
    assert d["revenue_growth"][-1] == pytest.approx(0.02)


def test_assumption_reference_shows_all_bases():
    ref = dcf.assumption_reference(sample_history(), {"ebitda_ttm": 9.0, "revenue_ttm": 120.0}, n_years=3)
    assert ref.loc["ebitda_margin", "all_years_avg"] == pytest.approx(0.0875)   # (5+10+10+10)/4 %
    assert ref.loc["ebitda_margin", "last_3y_avg"] == pytest.approx(0.10)
    assert ref.loc["ebitda_margin", "ttm"] == pytest.approx(0.075)              # 9 / 120
    assert ref.loc["revenue_growth", "all_years_avg"] == pytest.approx(1.21 ** (1 / 3) - 1)  # CAGR


def test_implied_by_price_reverses_the_dcf():
    # The by-hand case above is worth 90 per share at WACC 10%; ask which WACC gives 90
    a = flat_assumptions(tax_rate=1 / 3)
    kw = dict(base_revenue=1000.0, assumptions=a, wacc_rate=0.12, terminal_growth=0.0,
              exit_multiple=5.0, net_debt=100.0, shares=10.0, price=90.0, mid_year=False)
    assert dcf.implied_by_price(kw, "wacc_rate", "gordon", 0.05, 0.20) == pytest.approx(0.10, abs=1e-6)
    # A price the model cannot reach in the range -> NaN
    assert math.isnan(dcf.implied_by_price(kw, "wacc_rate", "gordon", 0.05, 0.20, target_price=1e6))


def test_ebitda_margin_moves_from_ttm_to_three_year_average():
    # TTM margin 9.0 / 127.05 = 7.08%; 3-year average 10% (outlier year 5% left out)
    d = dcf.default_assumptions(sample_history(), revenue_ttm=127.05, terminal_growth=0.02, n_years=3,
                                ebitda_ttm=9.0)
    start = 9.0 / 127.05
    assert d["ebitda_margin"][0] == pytest.approx(start)
    assert d["ebitda_margin"][-1] == pytest.approx(0.10)
    assert d["ebitda_margin"][2] == pytest.approx((start + 0.10) / 2)   # straight line: middle year
