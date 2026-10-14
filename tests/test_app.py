"""Smoke tests for the Streamlit app, run offline with the made-up data set."""

import math

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import data_loader as dl
import dcf
import summary
from fake_data import make_data

EUR = {"RACE.MI": "EUR", "MC.PA": "EUR", "RMS.PA": "EUR"}


def fake_load_all(tickers=None, refresh=False):
    """Like dl.load_all: any ticker starting with 'BAD' fails, euro tickers have no risk-free rate."""
    tickers = list(tickers)
    good = [t for t in tickers if not t.startswith("BAD")] or ["DUMMY"]
    data = make_data(good, currencies=EUR, risk_free=None if tickers[0] in EUR else 0.04)
    data["errors"] = {t: f"{t}: Yahoo Finance has no quote for this symbol." for t in tickers if t.startswith("BAD")}
    data["companies"] = {t: c for t, c in data["companies"].items() if t in tickers}
    return data


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(dl, "load_all", fake_load_all)
    monkeypatch.setattr(dl, "load_overrides", lambda path=None: pd.DataFrame(columns=dl.OVERRIDE_COLUMNS))
    st.cache_data.clear()            # a test with another data source must not leave its data behind
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    return at


def metric(at, label):
    return next(m for m in at.metric if m.label == label)


def choose(at, target, peers):
    """The manual path: type the target and the peers."""
    at.radio(key="sel_mode").set_value("Enter tickers manually").run()
    at.text_input(key="form_target").set_value(target)
    at.text_input(key="form_peers").set_value(peers)
    next(b for b in at.button if b.label == "Analyze").click().run()     # the form's submit button
    return at


def test_app_runs_and_shows_the_model_result(app):
    at = app
    assert not at.exception
    data = make_data()
    r = dcf.run_dcf(**summary.build_model(data, summary.default_choices(data))["run_kwargs"])
    assert metric(at, "DCF: Gordon growth").value == f"${r['gordon']['value_per_share']:,.2f}"
    assert metric(at, "WACC (Gordon)").value.endswith("%")          # reverse DCF box is there
    assert [t.label for t in at.tabs] == ["Summary", "DCF", "LBO", "M&A", "Scenarios", "Comps", "Precedent Transactions",
                                         "Data & quality"]


def test_changing_an_input_changes_the_value(app):
    at = app
    before = metric(at, "DCF: Gordon growth").value
    at.number_input(key="in_margin_end").set_value(12.0).run()
    assert not at.exception
    assert metric(at, "DCF: Gordon growth").value != before


def test_per_year_table_can_be_switched_on(app):
    at = app
    at.checkbox(key="per_year_on").check().run()
    assert not at.exception


def test_other_company_group_and_dropped_peer(app):
    at = choose(app, "hd", "LOW, TJX, BADX")
    assert not at.exception
    assert "HD" in at.title[0].value
    texts = " ".join(w.value for w in at.warning) + " ".join(i.value for i in at.info)
    assert "Left out of the peer group: BADX" in texts
    assert "One-off items were not checked for HD, LOW, TJX" in texts


def test_invalid_target_shows_an_error_and_stops(app):
    at = choose(app, "BADTARGET", "LOW")
    assert not at.exception
    assert "Could not load the target company" in at.error[0].value


def test_euro_company_without_risk_free_rate(app):
    at = choose(app, "RACE.MI", "MC.PA, RMS.PA")
    assert not at.exception
    assert at.number_input(key="in_risk_free").value is None          # left empty, never guessed
    assert metric(at, "DCF: Gordon growth").value == "N/A"
    assert metric(at, "Current share price").value.startswith("€")
    # Typing in a rate brings the DCF back
    at.number_input(key="in_risk_free").set_value(2.5).run()
    assert not at.exception
    assert metric(at, "DCF: Gordon growth").value.startswith("€")


def test_scenarios_tab_and_weights_check(app):
    at = app
    assert not at.exception
    assert any("WACC is the same in all three" in m.value for m in at.markdown)
    at.number_input(key="w_bull").set_value(40.0).run()          # 25 + 50 + 40 = 115%
    assert not at.exception
    assert any("add up to 115.0%" in w.value for w in at.warning)


def test_precedent_tab_default_and_toggles(app):
    at = app
    texts = " ".join(w.value for w in at.warning)
    assert "Small sample: only 4 deals" in texts
    captions = " ".join(c.value for c in at.caption)
    assert "Precedents = deals' EV/EBITDA, 25th-75th percentile (4 deals)" in captions
    at.toggle(key="prec_secondary").set_value(True).run()
    assert not at.exception
    assert "Small sample" not in " ".join(w.value for w in at.warning)      # 6 deals >= threshold of 5


def test_precedent_row_hidden_for_other_sub_sector(app):
    at = choose(app, "HD", "LOW, TJX")
    assert not at.exception
    assert any("from other sub-sectors" in i.value for i in at.info)


def lbo_case(data, **changes):
    import lbo
    choices = summary.default_choices(data)
    kw = summary.build_model(data, choices)["run_kwargs"]
    tv = data["companies"]["TGT"]["values"]
    inputs = lbo.default_inputs(data["short_rate"]["rate"], choices["risk_free"])
    inputs.update(exit_multiple=lbo.current_multiple(tv), **changes)
    return lbo, kw, tv, inputs


def test_lbo_tab_matches_the_engine_and_feeds_the_football_field(app):
    at = app
    lbo, kw, tv, inputs = lbo_case(make_data())
    r = lbo.run_lbo(kw, tv, inputs)
    assert metric(at, "Sponsor IRR").value == f"{r['returns']['irr']:.1%}"
    assert metric(at, "Max price for 20% IRR").value == f"${lbo.max_price(kw, tv, inputs):,.2f}"
    captions = " ".join(c.value for c in at.caption)
    assert "LBO = maximum price a fund can pay for a 25% (low) to 15% (high) IRR" in captions
    assert "illustrative assumptions" in captions and "SOFR proxy" in captions
    at.number_input(key="lbo_premium").set_value(40.0).run()
    assert not at.exception
    r40 = lbo.run_lbo(kw, tv, {**inputs, "premium": 0.40})
    assert metric(at, "Sponsor IRR").value == f"{r40['returns']['irr']:.1%}"
    # the maximum price does not depend on the premium paid (exit multiple fixed)
    assert metric(at, "Max price for 20% IRR").value == f"${lbo.max_price(kw, tv, inputs):,.2f}"


def test_lbo_tranche_edit_and_warnings(app):
    at = app
    at.session_state["lbo_tranches"] = {"edited_rows": {0: {"x EBITDA": 6.0}}, "added_rows": [], "deleted_rows": []}
    at.run()
    assert not at.exception
    assert any("8.0x EBITDA at entry" in w.value for w in at.warning)


def test_lbo_needs_base_rate_for_euro_company(app):
    at = choose(app, "RACE.MI", "MC.PA, RMS.PA")
    assert not at.exception
    assert any("Enter the base rates" in i.value for i in at.info)


def test_partial_download_is_explained_at_the_top(monkeypatch):
    def partial(tickers=None, refresh=False):
        data = fake_load_all(tickers, refresh)
        c = data["companies"][list(tickers)[0]]
        c["sources"]["ebitda_ttm"] = "missing"
        c["values"]["high_52w"] = c["values"]["price"] * 0.9
        return data
    monkeypatch.setattr(dl, "load_all", partial)
    st.cache_data.clear()                     # other tests cached the complete data set
    monkeypatch.setattr(dl, "load_overrides", lambda path=None: pd.DataFrame(columns=dl.OVERRIDE_COLUMNS))
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    texts = [w.value for w in at.warning]
    assert any("Some data could not be downloaded" in t and "TTM EBITDA" in t for t in texts)
    assert any("outside its 52-week range" in t for t in texts)


def sidebar_warnings(at):
    return " ".join(w.value for w in at.sidebar.warning)


def test_sp500_path_defaults_to_target_and_its_curated_group(app):
    at = app
    assert at.radio(key="sel_mode").value == "Pick from S&P 500"
    assert at.selectbox(key="sp_target").value == "TGT"
    assert at.radio(key="sp_source").value == "Curated peer group (analyst judgment)"
    assert at.multiselect(key="sp_peers").value == ["WMT", "COST", "KR", "DG", "BJ"]
    assert "Target" in at.title[0].value and "(TGT)" in at.title[0].value
    assert any("Market caps as of" in c.value for c in at.sidebar.caption)


def test_pick_apple_curated_then_gics_with_size_warning(app):
    at = app
    at.selectbox(key="sp_target").set_value("AAPL").run()
    assert at.multiselect(key="sp_peers").value == ["MSFT", "GOOGL", "META", "AMZN"]
    assert "Peers differ greatly in size" not in sidebar_warnings(at)
    at.radio(key="sp_source").set_value("GICS suggestion").run()
    gics = at.multiselect(key="sp_peers").value
    assert len(gics) == 6 and "DELL" in gics and "GOOG" not in gics
    assert "Peers differ greatly in size" in sidebar_warnings(at)
    next(b for b in at.button if b.label == "Analyze").click().run()
    assert not at.exception
    assert "(AAPL)" in at.title[0].value
    assert any("Peers differ greatly in size" in w.value for w in at.warning)          # also at the top


def test_edit_the_group_and_restore_the_suggestion(app):
    at = app
    at.selectbox(key="sp_target").set_value("KO").run()
    assert at.multiselect(key="sp_peers").value == ["PEP", "MNST", "KDP"]
    at.multiselect(key="sp_peers").unselect("MNST").select("PG").run()
    at.text_input(key="sp_extra").set_value("nestle.sw").run()
    next(b for b in at.button if b.label == "Analyze").click().run()
    assert not at.exception
    assert at.session_state["sel_peers"] == "PEP, KDP, PG, NESTLE.SW"
    next(b for b in at.button if b.label == "Use suggested peers").click().run()
    assert at.multiselect(key="sp_peers").value == ["PEP", "MNST", "KDP"]


def test_sector_fallback_warning(app):
    at = app
    at.selectbox(key="sp_target").set_value("XOM").run()
    assert "filled from the Energy sector" in sidebar_warnings(at)
    next(b for b in at.button if b.label == "Analyze").click().run()
    assert any("filled from the Energy sector" in w.value for w in at.warning)


def test_failed_download_is_not_cached(monkeypatch):
    calls = []

    def flaky(tickers=None, refresh=False):
        calls.append(1)
        data = fake_load_all(tickers, refresh)
        if len(calls) == 1:                                # Yahoo blocks the first try
            data["errors"] = {tickers[0]: f"Yahoo Finance did not return data for {tickers[0]}."}
        return data
    monkeypatch.setattr(dl, "load_all", flaky)
    monkeypatch.setattr(dl, "load_overrides", lambda path=None: pd.DataFrame(columns=dl.OVERRIDE_COLUMNS))
    st.cache_data.clear()
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    assert any("did not return data for TGT" in e.value for e in at.error)
    at.run()                                               # e.g. pressing Analyze again
    assert not at.error and "(TGT)" in at.title[0].value


def test_saved_data_banner_and_snapshot_date(monkeypatch):
    def saved(tickers=None, refresh=False):
        data = fake_load_all(tickers, refresh)
        data["companies"][tickers[0]]["meta"]["data_source"] = "snapshot"
        return data
    monkeypatch.setattr(dl, "load_all", saved)
    monkeypatch.setattr(dl, "load_overrides", lambda path=None: pd.DataFrame(columns=dl.OVERRIDE_COLUMNS))
    st.cache_data.clear()
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    assert any(w.value.startswith("Live data unavailable; showing saved data as of 2026-10-12 for TGT")
               for w in at.warning)
    assert any("Saved fallback data for TGT:" in c.value for c in at.sidebar.caption)


def test_blocked_yahoo_opens_any_sp500_company_from_saved_data(monkeypatch, tmp_path):
    """Like the live app when Yahoo blocks it: every download is empty, the committed snapshot is used."""
    import config

    def blocked(ticker):
        raise dl.YahooEmpty(f"Yahoo Finance did not return data for {ticker}.")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(dl, "fetch_from_yahoo", blocked)
    monkeypatch.setattr(dl, "_cached_series", lambda name, fetch, refresh=False: dl.json.load(
        open(config.SNAPSHOT_DIR / name)) | {"data_source": "snapshot"})
    st.cache_data.clear()
    at = AppTest.from_file("../app.py", default_timeout=120)
    at.run()
    for target, peers in (("TSLA", "GM, F"), ("ABBV", "LLY, MRK")):
        at = choose(at, target, peers)
        assert not at.exception and not at.error, target
        assert f"({target})" in at.title[0].value
        assert any(w.value.startswith("Live data unavailable; showing saved data as of") for w in at.warning)


def ma_engine(**changes):
    """The merger model on the fake data, the way the M&A tab sets it up (TGT buys KR by default)."""
    import merger
    data = make_data()
    sub = {"companies": {t: data["companies"][t] for t in ("TGT", "KR")}, "risk_free": data["risk_free"],
           "short_rate": data["short_rate"]}
    acq, tgt = merger.pair_inputs(sub, "TGT", "KR")
    inp = merger.default_inputs(acq, tgt, summary.default_choices(data)["risk_free"], data["short_rate"]["rate"])
    inp.update(changes)
    return merger, acq, tgt, inp, merger.run(acq, tgt, inp)


def test_ma_tab_shows_the_hypothetical_warning_and_matches_the_engine(app):
    at = app
    assert any(w.value.startswith("**Hypothetical transaction, for illustration only.**") for w in at.warning)
    merger, acq, tgt, inp, r = ma_engine()
    assert at.session_state["ma_target_sp"] == "KR"            # the first peer smaller than the buyer
    y1 = r["years"][0]
    assert metric(at, "EPS incl. deal amortization, Year 1").value == f"{y1['accretion_gaap_pct']:+.1%}"
    assert metric(at, "EPS excl. deal amortization (cash EPS), Year 1").value == f"{y1['accretion_cash_pct']:+.1%}"
    assert metric(at, "Ownership").value == f"{r['ownership']['buyer']:.0%} / {r['ownership']['target']:.0%}"
    captions = " ".join(c.value for c in at.caption)
    assert "**EPS incl. deal amortization** is after the amortization" in captions
    assert "which EPS definition is meant" in captions and "usually an adjusted (non-GAAP) figure" in captions
    assert "fiscal year ends" in captions


def test_ma_assumptions_basis_switch_and_chosen_target(app):
    at = app
    at.number_input(key="ma_premium").set_value(40.0).run()
    merger, acq, tgt, inp, r = ma_engine(premium=0.40)
    assert not at.exception
    assert metric(at, "EPS incl. deal amortization, Year 1").value == f"{r['years'][0]['accretion_gaap_pct']:+.1%}"
    at.radio(key="ma_basis").set_value("EPS excl. deal amortization (cash EPS)").run()                  # waterfall and heatmaps switch basis
    at.radio(key="ma_year").set_value("Year 2").run()
    assert not at.exception
    at.radio(key="ma_mode").set_value("Enter ticker manually").run()      # the manual path, a loaded peer
    at.text_input(key="ma_target_text").set_value("wmt").run()
    assert not at.exception and at.session_state["ma_for"] == ("TGT", "WMT")
    at.checkbox(key="ma_refi").set_value(True).run()
    assert not at.exception


def test_ma_different_currency_and_same_company(app):
    at = app
    at.radio(key="ma_mode").set_value("Enter ticker manually").run()
    at.text_input(key="ma_target_text").set_value("RACE.MI").run()
    assert any("trades in USD and RACE.MI in EUR" in e.value for e in at.error)
    at.text_input(key="ma_target_text").set_value("TGT").run()
    assert any("choose a different company" in i.value for i in at.info)


def test_ma_example_mode_dks_fl(app):
    import merger
    at = app
    assert at.radio(key="ma_mode").options[-1] == "Load example: DKS–FL (2025)"
    at.radio(key="ma_mode").set_value("Load example: DKS–FL (2025)").run()
    assert not at.exception
    assert any(i.value.startswith("**Historical transaction, inputs from SEC filings**") for i in at.info)
    assert not any("Hypothetical transaction" in w.value for w in at.warning)
    df = merger.load_examples()
    for basis in ("ttm", "mgmt"):
        acq, tgt, inp, f = merger.example_pair(df, "DKS-FL-2025", basis, short_rate=0.03)    # the fake data's T-bill
        y1 = merger.run(acq, tgt, inp)["years"][0]
        assert metric(at, "EPS incl. deal amortization, Year 1").value == f"{y1['accretion_gaap_pct']:+.1%}"
        at.radio(key="ma_ex_basis").set_value("mgmt").run()
        assert not at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "Year 1 is the first full fiscal year after closing" in captions
    assert any("Model vs Dick's expectation" in s.value for s in at.subheader)
    # editing an input still works, and switching back to a hypothetical deal restores the warning
    at.number_input(key="ma_stock").set_value(50.0).run()
    assert not at.exception
    at.radio(key="ma_mode").set_value("Pick from S&P 500").run()
    assert any("Hypothetical transaction" in w.value for w in at.warning) and not at.exception
