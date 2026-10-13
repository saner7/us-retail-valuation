"""LBO tests with small, hand-checkable numbers.

Company: revenue 1,000 flat, EBITDA margin 20% (EBITDA 200), D&A 5%, capex 5%,
no working capital, tax 25%. Price 10, 100 shares, debt 150, cash 50.
"""
import copy

import pytest

import lbo

RUN_KWARGS = {"base_revenue": 1000.0,
              "assumptions": {"revenue_growth": 0.0, "ebitda_margin": 0.20, "da_pct": 0.05,
                              "capex_pct": 0.05, "nwc_pct": 0.0, "tax_rate": 0.25}}
VALUES = {"ebitda_ttm": 200.0, "revenue_ttm": 1000.0, "price": 10.0, "shares_outstanding": 100.0,
          "debt_for_ev": 150.0, "cash": 50.0, "net_debt": 100.0}


def inputs(**kw):
    base = {"premium": 0.25, "entry_multiple": None, "exit_multiple": None, "exit_year": 1,
            "transaction_fee_pct": 0.01, "financing_fee_pct": 0.02, "min_cash_pct": 0.02, "target_irr": 0.20,
            "tranches": [
                {"name": "TLB", "multiple": 3.0, "rate_type": "floating", "base_rate": 0.05, "spread": 0.05,
                 "amortization": 0.01, "maturity": 7, "sweep": True},
                {"name": "Notes", "multiple": 1.0, "rate_type": "fixed", "base_rate": 0.08, "spread": 0.0,
                 "amortization": 0.0, "maturity": 8, "sweep": False}]}
    base.update(kw)
    return base


def test_entry_and_sources_uses():
    r = lbo.run_lbo(RUN_KWARGS, VALUES, inputs())
    e, su = r["entry"], r["sources_uses"]
    # offer 12.5 x 100 = 1,250 equity; + net debt 100 = EV 1,350 = 6.75x EBITDA
    assert e["offer_price"] == pytest.approx(12.5)
    assert e["entry_ev"] == pytest.approx(1350) and e["entry_multiple"] == pytest.approx(6.75)
    # uses: 1,250 + 150 + 1% x 1,350 + 2% x 800 = 1,429.5
    assert su["total"] == pytest.approx(1429.5)
    assert su["sources"]["TLB"] == pytest.approx(600) and su["sources"]["Notes"] == pytest.approx(200)
    # cash 50 - minimum 2% x 1,000 = 30 used; sponsor equity = 1,429.5 - 800 - 30
    assert su["sources"]["Existing cash above minimum"] == pytest.approx(30)
    assert su["sponsor_equity"] == pytest.approx(599.5)
    assert sum(su["sources"].values()) == pytest.approx(sum(su["uses"].values()))


def test_entry_multiple_input_sets_the_price():
    e = lbo.run_lbo(RUN_KWARGS, VALUES, inputs(entry_multiple=8.0))["entry"]
    # EV 1,600 - net debt 100 = 1,500 / 100 shares
    assert e["offer_price"] == pytest.approx(15.0) and e["premium"] == pytest.approx(0.5)


def test_year_one_cash_flow_sweep_and_returns():
    r = lbo.run_lbo(RUN_KWARGS, VALUES, inputs())
    y = r["schedule"]["Year 1"]
    # interest on opening balances: 10% x 600 + 8% x 200 = 76
    assert y["Interest"] == pytest.approx(76)
    # (200 - 50 - 76) = 74 pre-tax, 18.5 tax, 55.5 net income; FCF = 55.5 + 50 - 50
    assert y["Taxes"] == pytest.approx(18.5) and y["Free cash flow (after interest)"] == pytest.approx(55.5)
    # required 1% x 600 = 6; sweep = cash 20 + 55.5 - 6 - minimum 20 = 49.5
    assert y["TLB: required repayment"] == pytest.approx(6) and y["TLB: cash sweep"] == pytest.approx(49.5)
    assert y["TLB: end"] == pytest.approx(544.5) and y["Notes: end"] == pytest.approx(200)
    assert y["Cash, end of year"] == pytest.approx(20)
    assert y["EBITDA / interest"] == pytest.approx(200 / 76)
    # exit: 6.75 x 200 = 1,350 - net debt (744.5 - 20) = 625.5; MOIC = IRR + 1 after one year
    x, ret = r["exit"], r["returns"]
    assert x["equity"] == pytest.approx(625.5)
    assert ret["moic"] == pytest.approx(625.5 / 599.5) and ret["irr"] == pytest.approx(625.5 / 599.5 - 1)


def test_irr_over_several_years_is_compound():
    r = lbo.run_lbo(RUN_KWARGS, VALUES, inputs(exit_year=5, exit_multiple=7.0))
    ret = r["returns"]
    assert ret["irr"] == pytest.approx(ret["moic"] ** (1 / 5) - 1)
    assert r["exit"]["multiple"] == 7.0
    # the sweep repays debt every year, so debt falls
    debt = r["schedule"].loc["Total debt, end"]
    assert (debt.diff().dropna() < 0).all()


def test_max_price_hits_target_irr_with_fixed_exit_multiple():
    inp = inputs(exit_year=5)
    p = lbo.max_price(RUN_KWARGS, VALUES, inp, 0.20)
    entry_multiple = lbo.run_lbo(RUN_KWARGS, VALUES, inp)["entry"]["entry_multiple"]
    at_p = lbo.run_lbo(RUN_KWARGS, VALUES, inputs(exit_year=5, premium=p / 10 - 1, exit_multiple=entry_multiple))
    assert at_p["returns"]["irr"] == pytest.approx(0.20, abs=1e-4)
    rng = lbo.price_range(RUN_KWARGS, VALUES, inp)
    assert rng["mid"] == pytest.approx(p) and rng["low"] < p < rng["high"]   # 25% IRR < 20% < 15%


def test_lower_rates_raise_the_max_price():
    inp = inputs(exit_year=5)
    lower = lbo.with_base_rate_shift(inp, -0.02)
    assert lower["tranches"][0]["base_rate"] == pytest.approx(0.03) and inp["tranches"][0]["base_rate"] == 0.05
    assert lbo.max_price(RUN_KWARGS, VALUES, lower) > lbo.max_price(RUN_KWARGS, VALUES, inp)


def test_warnings():
    inp = inputs()
    inp["tranches"][0]["multiple"] = 6.0                 # 7x in total, 136 interest vs 200 EBITDA
    w = " ".join(lbo.run_lbo(RUN_KWARGS, VALUES, inp)["warnings"])
    assert "7.0x EBITDA at entry" in w and "section 163(j)" in w and "below 2x" in w
    short = copy.deepcopy(inputs())
    short["tranches"][1]["maturity"] = 1                # notes due in Year 1, not enough cash
    assert any("Cash turns negative" in x for x in lbo.run_lbo(RUN_KWARGS, VALUES, short)["warnings"])
    base = lbo.run_lbo(RUN_KWARGS, VALUES, inputs())["warnings"]     # 76 interest = 38% of EBITDA
    assert len(base) == 1 and "section 163(j)" in base[0]


def test_exit_year_must_be_in_projection():
    with pytest.raises(ValueError):
        lbo.run_lbo(RUN_KWARGS, VALUES, inputs(exit_year=6))


def test_feasibility_compares_with_largest_deal():
    big = {k: v * 1e9 / 10 for k, v in VALUES.items() if k not in ("price", "shares_outstanding")}
    big.update(price=10.0, shares_outstanding=1e10)
    text = " ".join(lbo.feasibility(lbo.run_lbo({**RUN_KWARGS, "base_revenue": 1e11}, big, inputs())))
    assert "largest sponsor buyout on record" in text and "Electronic Arts" in text
