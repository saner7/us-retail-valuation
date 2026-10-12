"""Tests for summary.py: football field rows, user choices -> model, reverse DCF."""

import pytest

import config
import dcf
import summary
from fake_data import make_data


@pytest.fixture(scope="module")
def data():
    return make_data()


def test_football_field_rows_in_configured_order_without_pb(data):
    model = summary.build_model(data, summary.default_choices(data))
    ff = summary.football_field_data(data["companies"], model["run_kwargs"])
    assert list(ff["key"]) == ["52w", "ev_ebitda", "pe", "ev_sales", "dcf_exit", "dcf_gordon"]
    assert "pb" not in set(ff["key"])                      # P/B stays in the comps tables only
    for _, r in ff.iterrows():
        assert r["low"] <= r["high"]
        if r["key"] != "52w":
            assert r["low"] <= r["mid"] <= r["high"]


def test_dcf_range_is_3x3_grid_around_base(data):
    kw = summary.build_model(data, summary.default_choices(data))["run_kwargs"]
    r = summary.dcf_range(kw, "gordon")
    w, g, dw, dg = kw["wacc_rate"], kw["terminal_growth"], config.FF_WACC_DELTA, config.FF_GROWTH_DELTA
    # Highest value: lowest WACC, highest growth; lowest value: the opposite corner
    assert r["high"] == pytest.approx(dcf.run_dcf(**{**kw, "wacc_rate": w - dw, "terminal_growth": g + dg})
                                      ["gordon"]["value_per_share"])
    assert r["low"] == pytest.approx(dcf.run_dcf(**{**kw, "wacc_rate": w + dw, "terminal_growth": g - dg})
                                     ["gordon"]["value_per_share"])
    assert r["mid"] == pytest.approx(dcf.run_dcf(**kw)["gordon"]["value_per_share"])


def test_default_choices_reproduce_default_dcf(data):
    model = summary.build_model(data, summary.default_choices(data))
    ref = dcf.default_inputs(data)["run_kwargs"]
    assert model["run_kwargs"]["wacc_rate"] == pytest.approx(ref["wacc_rate"])
    ours = dcf.run_dcf(**model["run_kwargs"])["gordon"]["value_per_share"]
    assert ours == pytest.approx(dcf.run_dcf(**ref)["gordon"]["value_per_share"])


def test_paths_and_per_year_table():
    choices = {"growth_start": 0.05, "terminal_growth": 0.01, "margin_start": 0.06, "margin_end": 0.10,
               "da_pct": 0.03, "capex_pct": 0.04, "nwc_pct": 0.0, "tax_rate": 0.25, "per_year": None}
    a = summary.assumptions_from_choices(choices, years=5)
    assert a["revenue_growth"] == pytest.approx([0.05, 0.04, 0.03, 0.02, 0.01])
    assert a["ebitda_margin"] == pytest.approx([0.06, 0.07, 0.08, 0.09, 0.10])
    assert a["capex_pct"] == [0.04] * 5
    a = summary.assumptions_from_choices({**choices, "per_year": {"capex_pct": [0.1, 0.1, 0.05, 0.05, 0.05]}},
                                         years=5)
    assert a["capex_pct"] == [0.1, 0.1, 0.05, 0.05, 0.05]


def test_reverse_dcf_hits_the_price(data):
    kw = summary.build_model(data, summary.default_choices(data))["run_kwargs"]
    rev = summary.reverse_dcf(kw)
    for key, method in [("wacc", "gordon"), ("terminal_growth", "gordon"), ("exit_multiple", "exit")]:
        arg = {"wacc": "wacc_rate"}.get(key, key)
        value = dcf.run_dcf(**{**kw, arg: rev[key]})[method]["value_per_share"]
        assert value == pytest.approx(kw["price"], rel=1e-6)
