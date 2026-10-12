"""Tests for the data layer. They use small made-up numbers and never touch
the internet, so you can check every result by hand."""

from datetime import datetime

import math

import numpy as np
import pandas as pd

import data_loader as dl


def sample_snapshot():
    return {
        "price": 10.0,
        "market_cap": 1000.0,          # 100 shares x 10
        "total_debt": 300.0,
        "cash": 100.0,
        "revenue_ttm": 5000.0,
        "ebitda_ttm": 400.0,
        "net_income_ttm": 150.0,
        "shares_outstanding": 100.0,
        "book_value_per_share": 4.0,
        "low_52w": 8.0,
        "high_52w": 12.0,
    }


def no_overrides():
    return pd.DataFrame(columns=["ticker", "field", "value", "note"])


def build(snapshot, overrides=None):
    values, sources = dl.apply_overrides(snapshot, "TEST.IS", overrides if overrides is not None else no_overrides())
    return dl.compute_derived(values, sources)


def sample_history(last_year=2025):
    return pd.DataFrame({"revenue": [1.0, 2.0]}, index=[last_year - 1, last_year])


def test_net_debt_ev_and_equity():
    values, sources = build(sample_snapshot())
    assert values["net_debt"] == 200.0             # 300 - 100
    assert values["enterprise_value"] == 1200.0    # 1000 + 200
    assert values["total_equity"] == 400.0         # 4 x 100
    assert sources["enterprise_value"] == "derived"
    assert sources["price"] == "auto"


def test_override_replaces_auto_value_and_flows_into_derived():
    overrides = pd.DataFrame([{"ticker": "TEST.IS", "field": "cash", "value": 250.0, "note": "annual report"},
                              {"ticker": "OTHER.IS", "field": "cash", "value": 999.0, "note": "other company"}])
    values, sources = build(sample_snapshot(), overrides)
    assert values["cash"] == 250.0
    assert sources["cash"] == "manual"
    assert values["net_debt"] == 50.0              # 300 - 250, uses the override
    assert values["enterprise_value"] == 1050.0


def test_manual_override_of_derived_field_is_kept():
    overrides = pd.DataFrame([{"ticker": "TEST.IS", "field": "total_equity", "value": 777.0, "note": "KAP"}])
    values, sources = build(sample_snapshot(), overrides)
    assert values["total_equity"] == 777.0
    assert sources["total_equity"] == "manual"


def test_missing_input_gives_nan_not_a_guess():
    snap = sample_snapshot()
    snap["cash"] = None
    values, sources = build(snap)
    assert sources["cash"] == "missing"
    assert math.isnan(values["net_debt"])
    assert math.isnan(values["enterprise_value"])
    assert sources["enterprise_value"] == "missing"


def run_checks(snapshot, history=None, meta=None):
    values, sources = build(snapshot)
    meta = meta or {"financial_currency": "TRY", "yahoo_enterprise_value": 1200.0}
    history = sample_history() if history is None else history
    q = dl.quality_checks("TEST.IS", values, sources, meta, history, today=datetime(2026, 6, 30))
    return q.set_index("field")["status"]


def test_clean_data_has_no_issues():
    status = run_checks(sample_snapshot())
    assert (status == "OK").all()


def test_negative_ebitda_and_loss_are_flagged():
    snap = sample_snapshot()
    snap["ebitda_ttm"] = -50.0
    snap["net_income_ttm"] = -20.0
    status = run_checks(snap)
    assert status["ebitda_ttm"] == "Suspicious"
    assert status["net_income_ttm"] == "Note"


def test_negative_equity_and_price_outside_range():
    snap = sample_snapshot()
    snap["book_value_per_share"] = -1.0
    snap["low_52w"] = 11.0                         # price 10 is below the low
    status = run_checks(snap)
    assert status["total_equity"] == "Suspicious"
    assert status["price"] == "Suspicious"


def test_ev_mismatch_and_wrong_currency():
    status = run_checks(sample_snapshot(), meta={"financial_currency": "USD",
                                                 "yahoo_enterprise_value": 1500.0})  # ours is 1200 -> -20%
    assert status["enterprise_value"] == "Suspicious"
    assert status["financial_currency"] == "Suspicious"


def test_stale_statements():
    # FY2024 ends 2024-12-31; on 2026-06-30 that is 18 months old (> 15)
    assert run_checks(sample_snapshot(), history=sample_history(2024))["annual_statements"] == "Suspicious"
    assert run_checks(sample_snapshot(), history=sample_history(2025))["annual_statements"] == "OK"


def test_usd_conversion():
    values, _ = build(sample_snapshot())
    usd = dl.to_usd(values, usdtry=40.0)
    assert usd["market_cap"] == 25.0               # 1000 / 40
    assert usd["shares_outstanding"] == 100.0      # share count is not money


def test_history_to_usd_uses_yearly_average():
    hist = pd.DataFrame({"revenue": [300.0, 400.0]}, index=[2024, 2025])
    usd = dl.history_to_usd(hist, {"2024": 30.0, "2025": 40.0})
    assert usd.loc[2024, "revenue"] == 10.0
    assert usd.loc[2025, "revenue"] == 10.0
    no_rate = dl.history_to_usd(hist, {"2025": 40.0})
    assert np.isnan(no_rate.loc[2024, "revenue"])


def quarterly(dates, ebitda):
    cols = pd.to_datetime(dates)
    return pd.DataFrame([[100.0] * len(dates), ebitda, [5.0] * len(dates)], columns=cols,
                        index=["Total Revenue", "EBITDA", "Net Income Common Stockholders"])


FOUR_QUARTERS = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]


def test_ttm_is_sum_of_last_four_quarters():
    q = quarterly(FOUR_QUARTERS + ["2025-06-30"], [10.0, 20.0, 30.0, 40.0, 999.0])
    ttm = dl.ttm_from_quarters(q)
    assert ttm["ebitda_ttm"] == 100.0              # 10+20+30+40, the 5th (oldest) quarter is ignored
    assert ttm["revenue_ttm"] == 400.0


def test_ttm_is_nan_if_a_quarter_is_missing_or_gap():
    assert math.isnan(dl.ttm_from_quarters(quarterly(FOUR_QUARTERS, [10.0, np.nan, 30.0, 40.0]))["ebitda_ttm"])
    gap = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-06-30"]   # Q3 2025 is missing
    assert math.isnan(dl.ttm_from_quarters(quarterly(gap, [1.0, 1.0, 1.0, 1.0]))["ebitda_ttm"])


def test_replace_ttm_overrides_yahoo_and_explains_why():
    snap = sample_snapshot()
    snap["ebitda_ttm"] = -50.0                     # Yahoo's (wrong) summary value
    new, notes = dl.replace_ttm(snap, quarterly(FOUR_QUARTERS, [10.0, 20.0, 30.0, 40.0]))
    assert new["ebitda_ttm"] == 100.0
    assert "differs" in notes["ebitda_ttm"]
    # Not enough quarters -> keep Yahoo's value but say so
    kept, notes = dl.replace_ttm(snap, pd.DataFrame())
    assert kept["ebitda_ttm"] == -50.0
    assert "Yahoo's summary" in notes["ebitda_ttm"]
