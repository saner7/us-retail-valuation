"""Smoke tests for the Streamlit app, run offline with the made-up data set."""

import pytest
from streamlit.testing.v1 import AppTest

import data_loader as dl
import dcf
import summary
from fake_data import make_data


@pytest.fixture
def app(monkeypatch):
    data = make_data()
    monkeypatch.setattr(dl, "load_all", lambda tickers=None, refresh=False: data)
    monkeypatch.setattr(dl, "load_overrides", lambda path=None: make_overrides())
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.run()
    return at, data


def make_overrides():
    import pandas as pd
    return pd.DataFrame(columns=dl.OVERRIDE_COLUMNS)


def metric(at, label):
    return next(m for m in at.metric if m.label == label)


def test_app_runs_and_shows_the_model_result(app):
    at, data = app
    assert not at.exception
    r = dcf.run_dcf(**summary.build_model(data, summary.default_choices(data))["run_kwargs"])
    assert metric(at, "DCF: Gordon growth").value == f"${r['gordon']['value_per_share']:,.2f}"
    assert metric(at, "WACC (Gordon)").value.endswith("%")          # reverse DCF box is there
    assert [t.label for t in at.tabs] == ["Summary", "DCF", "Comps", "Data & quality"]


def test_changing_an_input_changes_the_value(app):
    at, _ = app
    before = metric(at, "DCF: Gordon growth").value
    at.number_input(key="in_margin_end").set_value(12.0).run()
    assert not at.exception
    assert metric(at, "DCF: Gordon growth").value != before


def test_per_year_table_can_be_switched_on(app):
    at, _ = app
    at.checkbox(key="per_year_on").check().run()
    assert not at.exception
