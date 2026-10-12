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
        "lease_liabilities": 50.0,
    }


def no_overrides():
    return pd.DataFrame(columns=["ticker", "field", "value", "note"])


def build(snapshot, overrides=None):
    values, sources = dl.apply_overrides(snapshot, "TEST.IS", overrides if overrides is not None else no_overrides())
    return dl.compute_derived(values, sources)


def sample_history(last_end="2026-01-31"):
    """Two fiscal years; the index is the fiscal year end date (like Target's late-January year end)."""
    last = pd.Timestamp(last_end)
    return pd.DataFrame({"revenue": [1.0, 2.0]}, index=[last - pd.DateOffset(years=1), last])


def test_net_debt_ev_and_equity():
    values, sources = build(sample_snapshot())     # leases excluded by default
    assert values["debt_for_ev"] == 250.0          # 300 - 50 leases
    assert values["net_debt"] == 150.0             # 250 - 100
    assert values["enterprise_value"] == 1150.0    # 1000 + 150
    assert values["total_equity"] == 400.0         # 4 x 100
    assert sources["enterprise_value"] == "derived"
    assert sources["price"] == "auto"


def test_leases_can_be_kept_in_debt():
    values, sources = dl.apply_overrides(sample_snapshot(), "TEST.IS", no_overrides())
    values, _ = dl.compute_derived(values, sources, exclude_leases=False)
    assert values["debt_for_ev"] == 300.0
    assert values["net_debt"] == 200.0


def test_missing_lease_data_is_not_treated_as_zero():
    snap = sample_snapshot()
    snap["lease_liabilities"] = None
    values, sources = build(snap)
    assert math.isnan(values["net_debt"])
    assert sources["net_debt"] == "missing"


def test_override_replaces_auto_value_and_flows_into_derived():
    overrides = pd.DataFrame([{"ticker": "TEST.IS", "field": "cash", "value": 250.0, "note": "annual report"},
                              {"ticker": "OTHER.IS", "field": "cash", "value": 999.0, "note": "other company"}])
    values, sources = build(sample_snapshot(), overrides)
    assert values["cash"] == 250.0
    assert sources["cash"] == "manual"
    assert values["net_debt"] == 0.0               # (300 - 50) - 250, uses the override
    assert values["enterprise_value"] == 1000.0


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
    meta = meta or {"financial_currency": "USD", "yahoo_enterprise_value": 1200.0}  # incl. leases
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
    status = run_checks(sample_snapshot(), meta={"financial_currency": "EUR",
                                                 "yahoo_enterprise_value": 1500.0})  # ours is 1200 -> -20%
    assert status["enterprise_value"] == "Suspicious"
    assert status["financial_currency"] == "Suspicious"


def test_stale_statements_use_real_fiscal_year_end():
    # "today" in run_checks is 2026-06-30.
    # Year ended 2025-01-31 -> 17 months old (> 15) -> stale
    assert run_checks(sample_snapshot(), history=sample_history("2025-01-31"))["annual_statements"] == "Suspicious"
    # Year ended 2025-08-31 (Costco-style) -> 10 months old -> fine.
    # (The old calendar-year logic would have treated this as FY2025 = Dec 2025.)
    assert run_checks(sample_snapshot(), history=sample_history("2025-08-31"))["annual_statements"] == "OK"


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


def override_rows(**cols):
    row = {"ticker": "TEST.IS", "field": "ebitda_ttm", "value": 90.0, "adjusted_quarter": np.nan,
           "ttm_end": np.nan, "note": "one-off removed"}
    row.update(cols)
    return pd.DataFrame([row])


def test_override_check_ok_while_adjusted_quarter_in_window():
    q = quarterly(FOUR_QUARTERS, [10.0, 20.0, 30.0, 40.0])
    ov = override_rows(adjusted_quarter="2025-12-31", ttm_end="2026-06-30")
    assert dl.override_checks("TEST.IS", ov, q) == []
    assert dl.override_checks("OTHER.IS", ov, q) == []       # other companies' overrides are ignored


def test_override_check_flags_one_off_that_left_the_window():
    # A new quarter (2026-09-30) pushed 2025-09-30 out of the 4-quarter window
    q = quarterly(["2026-09-30"] + FOUR_QUARTERS, [1.0] * 5)
    flags = dl.override_checks("TEST.IS", override_rows(adjusted_quarter="2025-09-30", ttm_end="2026-06-30"), q)
    assert len(flags) == 1
    field, status, comment = flags[0]
    assert (field, status) == ("ebitda_ttm", "Suspicious")
    assert "no longer in the TTM window" in comment


def test_override_check_flags_newer_quarter():
    # The one-off is still in the window, but the value was calculated for an older window
    q = quarterly(["2026-09-30"] + FOUR_QUARTERS, [1.0] * 5)
    flags = dl.override_checks("TEST.IS", override_rows(adjusted_quarter="2026-03-31", ttm_end="2026-06-30"), q)
    assert len(flags) == 1 and "Recalculate" in flags[0][2]


def test_override_flags_show_in_quality_report():
    values, sources = build(sample_snapshot())
    meta = {"financial_currency": "USD", "yahoo_enterprise_value": 1200.0}
    q = dl.quality_checks("TEST.IS", values, sources, meta, sample_history(), today=datetime(2026, 6, 30),
                          flags=[("ebitda_ttm", "Suspicious", "Stale override")])
    row = q.set_index("field").loc["ebitda_ttm"]
    assert row["status"] == "Suspicious" and "Stale override" in row["comment"]


def test_overrides_file_without_optional_columns(tmp_path):
    path = tmp_path / "overrides.csv"
    path.write_text("ticker,field,value,note\nTEST.IS,cash,5,annual report\n")
    ov = dl.load_overrides(path)
    assert set(dl.OVERRIDE_COLUMNS) <= set(ov.columns)
    assert ov.loc[0, "value"] == 5
    assert ov["adjusted_quarter"].isna().all()
