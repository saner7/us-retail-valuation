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
    # 163(j): only 30% x 200 = 60 of the 76 is deductible; tax = (200 - 50 - 60) x 25% = 22.5
    assert y["Interest not deductible (163(j))"] == pytest.approx(16) and y["Taxes"] == pytest.approx(22.5)
    # pre-tax income 74 - 22.5 = 51.5 net income; FCF = 51.5 + 50 - 50
    assert y["Free cash flow (after interest)"] == pytest.approx(51.5)
    # required 1% x 600 = 6; sweep = cash 20 + 51.5 - 6 - minimum 20 = 45.5
    assert y["TLB: required repayment"] == pytest.approx(6) and y["TLB: cash sweep"] == pytest.approx(45.5)
    assert y["TLB: end"] == pytest.approx(548.5) and y["Notes: end"] == pytest.approx(200)
    assert y["Cash, end of year"] == pytest.approx(20)
    assert y["EBITDA / interest"] == pytest.approx(200 / 76)
    # exit at today's multiple (1,000 + 100) / 200 = 5.5x: 1,100 - net debt (748.5 - 20) = 371.5
    x, ret = r["exit"], r["returns"]
    assert x["multiple"] == pytest.approx(5.5) and x["equity"] == pytest.approx(371.5)
    assert ret["moic"] == pytest.approx(371.5 / 599.5) and ret["irr"] == pytest.approx(371.5 / 599.5 - 1)


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
    at_p = lbo.run_lbo(RUN_KWARGS, VALUES, inputs(exit_year=5, premium=p / 10 - 1))
    assert at_p["exit"]["multiple"] == pytest.approx(5.5)       # today's multiple, whatever the price
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
    assert len(base) == 1 and "section 163(j)" in base[0] and "Year 1" in base[0]


def test_exit_year_must_be_in_projection():
    with pytest.raises(ValueError):
        lbo.run_lbo(RUN_KWARGS, VALUES, inputs(exit_year=6))


def test_feasibility_compares_with_largest_deal():
    big = {k: v * 1e9 / 10 for k, v in VALUES.items() if k not in ("price", "shares_outstanding")}
    big.update(price=10.0, shares_outstanding=1e10)
    text = " ".join(lbo.feasibility(lbo.run_lbo({**RUN_KWARGS, "base_revenue": 1e11}, big, inputs())))
    assert "largest sponsor buyout on record" in text and "Electronic Arts" in text


def test_sensitivity_tables():
    inp = inputs(exit_year=5)
    t = lbo.irr_entry_exit(RUN_KWARGS, VALUES, inp)
    entry = lbo.run_lbo(RUN_KWARGS, VALUES, inp)["entry"]["entry_multiple"]          # 6.75x
    assert list(t.index) == pytest.approx([4.75, 5.75, 6.75, 7.75, 8.75])
    assert list(t.columns[:3]) == pytest.approx([4.5, 5.5, 6.5]) and t.columns[3] == lbo.EXIT_EQUALS_ENTRY
    # middle row, "= entry" column: exit at the entry multiple
    same = lbo.run_lbo(RUN_KWARGS, VALUES, inputs(exit_year=5, entry_multiple=entry, exit_multiple=entry))
    assert t.loc[entry, lbo.EXIT_EQUALS_ENTRY] == pytest.approx(same["returns"]["irr"])
    assert (t[5.5].diff().dropna() < 0).all()                  # paying more lowers the IRR
    lev = lbo.irr_leverage_exit(RUN_KWARGS, VALUES, inp)
    # 4x total = the case itself (TLB 3x + notes 1x)
    assert lev.loc[4.0, 5.5] == pytest.approx(lbo.run_lbo(RUN_KWARGS, VALUES, inp)["returns"]["irr"])
    assert lev.columns[3] == pytest.approx(entry)


def test_football_field_row():
    inp = inputs(exit_year=5)
    row = lbo.football_field_row(RUN_KWARGS, VALUES, inp)
    rng = lbo.price_range(RUN_KWARGS, VALUES, inp)
    assert row["label"] == "LBO analysis (15%-25% IRR)" and row["group"] == "LBO"
    assert (row["low"], row["mid"], row["high"]) == (rng["low"], rng["mid"], rng["high"])
    assert row["low"] < row["mid"] < row["high"]
