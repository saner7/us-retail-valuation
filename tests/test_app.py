"""Smoke tests for the Streamlit app, run offline with the made-up data set."""

import math

import pandas as pd
import pytest
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
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    return at


def metric(at, label):
    return next(m for m in at.metric if m.label == label)


def choose(at, target, peers):
    at.text_input(key="form_target").set_value(target)
    at.text_input(key="form_peers").set_value(peers)
    at.button[0].click().run()          # the form's "Analyze" button is the first button
    return at


def test_app_runs_and_shows_the_model_result(app):
    at = app
    assert not at.exception
    data = make_data()
    r = dcf.run_dcf(**summary.build_model(data, summary.default_choices(data))["run_kwargs"])
    assert metric(at, "DCF: Gordon growth").value == f"${r['gordon']['value_per_share']:,.2f}"
    assert metric(at, "WACC (Gordon)").value.endswith("%")          # reverse DCF box is there
    assert [t.label for t in at.tabs] == ["Summary", "DCF", "Scenarios", "Comps", "Data & quality"]


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
