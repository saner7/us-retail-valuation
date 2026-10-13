"""Stage A: any company - ticker input, errors, currencies, sector and one-off warnings.
All offline: Yahoo is replaced by small made-up data."""

import math

import numpy as np
import pandas as pd
import pytest

import config
import data_loader as dl
import summary
from fake_data import make_data


# --- ticker input ------------------------------------------------------------

def test_parse_tickers_cleans_the_input():
    target, peers, notes = summary.parse_tickers(" hd ", "low, TJX ,hd, low,, bby")
    assert target == "HD"
    assert peers == ["LOW", "TJX", "BBY"]
    assert any("HD is the target" in n for n in notes)
    assert any("LOW was listed twice" in n for n in notes)


def test_parse_tickers_limits_the_peer_group():
    target, peers, notes = summary.parse_tickers("A", ",".join(f"P{i}" for i in range(12)), max_peers=10)
    assert len(peers) == 10 and any("P10, P11 left out" in n for n in notes)


# --- downloading: errors never stop the other companies -------------------------------

class FakeTicker:
    def __init__(self, info):
        self.info = info


def test_unknown_symbol_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(dl.yf, "Ticker", lambda t: FakeTicker({"quoteType": None, "currentPrice": None}))
    with pytest.raises(dl.TickerNotFound, match="no quote"):
        dl.fetch_from_yahoo("XXXX")
    monkeypatch.setattr(dl.yf, "Ticker", lambda t: FakeTicker({"quoteType": "ETF", "marketCap": 1e9}))
    with pytest.raises(dl.TickerNotFound, match="not a company"):
        dl.fetch_from_yahoo("SPY")


def test_one_failing_company_is_left_out(monkeypatch, tmp_path):
    good = make_data(["AAA", "BBB"])["companies"]

    def load(t, overrides, refresh=False):
        if t == "BAD":
            raise dl.TickerNotFound("BAD: Yahoo Finance has no quote for this symbol.")
        return good[t]
    monkeypatch.setattr(dl, "load_company", load)
    monkeypatch.setattr(dl, "get_risk_free_rate", lambda ccy, refresh=False: {"rate": 0.04, "currency": ccy})
    data = dl.load_all(["AAA", "BAD", "BBB"])
    assert list(data["companies"]) == ["AAA", "BBB"]
    assert "no quote" in data["errors"]["BAD"]
    warnings = summary.analysis_warnings({**data, "risk_free": {"data_source": "live"}}, "AAA", ["BAD", "BBB"])
    assert any("Left out of the peer group: BAD" in w["text"] for w in warnings)


def test_no_saved_copy_message_names_the_saved_companies(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(dl, "fetch_from_yahoo", lambda t: (_ for _ in ()).throw(RuntimeError("429 Too Many Requests")))
    with pytest.raises(dl.DataUnavailable) as err:
        dl.get_company_raw("RACE.MI")         # not in the S&P 500: no saved copy
    assert "no saved copy" in str(err.value) and "the S&P 500 and the default set" in str(err.value)


# --- currencies ---------------------------------------------------------------------

def raw_company(trade="USD", fin="EUR"):
    """Ferrari-like: trades in USD, reports in EUR. 4 quarters of 100 revenue / 30 EBITDA."""
    q = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
    income_q = pd.DataFrame([[100.0] * 4, [30.0] * 4, [20.0] * 4], columns=q,
                            index=["Total Revenue", "EBITDA", "Net Income Common Stockholders"])
    balance_q = pd.DataFrame([[50.0] * 4, [500.0] * 4], columns=q,
                             index=["Capital Lease Obligations", "Stockholders Equity"])
    snapshot = {"price": 50.0, "market_cap": 5000.0, "total_debt": 200.0, "cash": 100.0,
                "revenue_ttm": 400.0, "ebitda_ttm": 120.0, "net_income_ttm": 80.0,
                "shares_outstanding": 100.0, "book_value_per_share": 999.0, "low_52w": 40.0, "high_52w": 60.0}
    history = {"income": pd.DataFrame({pd.Timestamp("2025-12-31"): {"Total Revenue": 400.0}})}
    return {"snapshot": snapshot,
            "meta": {"ticker": "FER", "fetched_at": "2026-10-12T09:00:00", "currency": trade,
                     "financial_currency": fin, "yahoo_enterprise_value": None, "beta": 1.0},
            "statements": {"income_q": income_q, "balance_q": balance_q, **history,
                           "cashflow": pd.DataFrame(), "balance": pd.DataFrame()}}


def test_statement_figures_are_converted_to_the_trading_currency(monkeypatch):
    monkeypatch.setattr(dl, "get_company_raw", lambda t, refresh=False: raw_company())
    monkeypatch.setattr(dl, "get_fx_rate", lambda a, b, refresh=False: {"rate": 1.1, "date": "2026-10-09"})
    c = dl.load_company("FER", pd.DataFrame(columns=dl.OVERRIDE_COLUMNS))
    v = c["values"]
    assert v["revenue_ttm"] == pytest.approx(440.0)            # 400 EUR x 1.1
    assert v["ebitda_ttm"] == pytest.approx(132.0)
    assert v["net_debt"] == pytest.approx((200 - 50 - 100) * 1.1)
    assert v["enterprise_value"] == pytest.approx(5000 + 55)   # market cap is already in USD
    assert v["book_value_per_share"] == pytest.approx(5.5)     # equity 500 / 100 shares x 1.1, not Yahoo's 999
    assert c["history"].loc[pd.Timestamp("2025-12-31"), "revenue"] == pytest.approx(440.0)
    row = c["quality"].set_index("field").loc["currency"]
    assert row["status"] == "Note" and "converted at 1.1000" in row["comment"]


def test_missing_exchange_rate_gives_na_not_a_guess(monkeypatch):
    monkeypatch.setattr(dl, "get_company_raw", lambda t, refresh=False: raw_company())
    monkeypatch.setattr(dl, "get_fx_rate", lambda a, b, refresh=False: {"rate": np.nan, "date": None})
    c = dl.load_company("FER", pd.DataFrame(columns=dl.OVERRIDE_COLUMNS))
    assert math.isnan(c["values"]["ebitda_ttm"]) and math.isnan(c["values"]["enterprise_value"])
    assert c["quality"].set_index("field").loc["currency", "status"] == "Suspicious"


def test_same_currency_is_not_converted(monkeypatch):
    monkeypatch.setattr(dl, "get_company_raw", lambda t, refresh=False: raw_company("EUR", "EUR"))
    c = dl.load_company("FER", pd.DataFrame(columns=dl.OVERRIDE_COLUMNS))
    assert c["values"]["revenue_ttm"] == 400.0 and "fx" not in c["meta"]


def test_mixed_currency_group_is_explained():
    data = make_data(["RACE.MI", "MC.PA", "CFR.SW"], currencies={"RACE.MI": "EUR", "MC.PA": "EUR", "CFR.SW": "CHF"})
    texts = " ".join(w["text"] for w in summary.analysis_warnings(data, "RACE.MI", ["MC.PA", "CFR.SW"]))
    assert "mixes currencies: CFR.SW (CHF)" in texts and "shown in RACE.MI's trading currency, EUR" in texts


def test_risk_free_rate_outside_usd_must_be_entered():
    rf = dl.get_risk_free_rate("EUR")
    assert math.isnan(rf["rate"]) and rf["data_source"] == "manual"
    assert "Bund" in rf["guidance"] and "BTP-Bund spread" in rf["guidance"]
    data = make_data(["RACE.MI", "MC.PA"], currencies={"RACE.MI": "EUR", "MC.PA": "EUR"}, risk_free=None)
    texts = " ".join(w["text"] for w in summary.analysis_warnings(data, "RACE.MI", ["MC.PA"]))
    assert "Enter the risk-free rate" in texts


def test_no_risk_free_rate_means_no_dcf_rows_but_comps_still_work():
    data = make_data(["RACE.MI", "MC.PA", "RMS.PA"], currencies={t: "EUR" for t in ["RACE.MI", "MC.PA", "RMS.PA"]},
                     risk_free=None)
    choices = summary.default_choices(data, "RACE.MI")
    assert math.isnan(choices["risk_free"])
    kw = summary.build_model(data, choices, "RACE.MI")["run_kwargs"]
    ff = summary.football_field_data(data["companies"], kw, "RACE.MI", ["MC.PA", "RMS.PA"])
    assert not any(k.startswith("dcf_") for k in ff["key"])
    assert ff.set_index("key").loc["ev_ebitda", "mid"] > 0


# --- sector and one-off warnings ------------------------------------------------------

def test_bank_target_gets_a_warning():
    data = make_data(["JPM", "BAC"], industries={"JPM": "Banks - Diversified", "BAC": "Banks - Diversified"})
    texts = " ".join(w["text"] for w in summary.analysis_warnings(data, "JPM", ["BAC"]))
    assert "financial company" in texts and "EV/EBITDA" in texts
    retail = make_data(["HD", "LOW"], industries={"HD": "Home Improvement Retail"})
    assert not any("financial company" in w["text"] for w in summary.analysis_warnings(retail, "HD", ["LOW"]))


def test_one_offs_not_checked_warning_only_for_unreviewed_companies():
    default = make_data()
    assert not any("One-off items were not checked" in w["text"]
                   for w in summary.analysis_warnings(default, config.TARGET, config.PEERS))
    other = make_data(["HD", "LOW", "TGT"])
    texts = " ".join(w["text"] for w in summary.analysis_warnings(other, "HD", ["LOW", "TGT"]))
    assert "not checked for HD, LOW" in texts and "TGT" not in texts.split("not checked for")[1].split(".")[0]


def test_overrides_only_apply_to_their_own_ticker():
    overrides = dl.load_overrides()                     # the real file: KR and TGT only
    assert set(overrides["ticker"]) == {"KR", "TGT"}
    snap = {f: 1.0 for f in dl.RAW_FIELDS}
    values, sources = dl.apply_overrides(snap, "HD", overrides)
    assert set(sources.values()) == {"auto"}            # nothing from KR/TGT leaks into HD


def test_pb_on_football_field_only_for_financial_targets():
    bank = make_data(["JPM", "BAC", "C", "WFC"], industries={t: "Banks - Diversified" for t in ["JPM", "BAC", "C", "WFC"]})
    kw = summary.build_model(bank, summary.default_choices(bank, "JPM"), "JPM")["run_kwargs"]
    keys = list(summary.football_field_data(bank["companies"], kw, "JPM", ["BAC", "C", "WFC"])["key"])
    assert keys.index("pb") == keys.index("pe") + 1          # P/B right after P/E
    retail = make_data()
    kw = summary.build_model(retail, summary.default_choices(retail), config.TARGET)["run_kwargs"]
    assert "pb" not in set(summary.football_field_data(retail["companies"], kw)["key"])


def test_target_reporting_in_another_currency_gets_a_risk_free_warning():
    data = make_data(["RACE", "MC.PA"], currencies={"MC.PA": "EUR"})
    data["companies"]["RACE"]["meta"]["fx"] = {"from": "EUR", "to": "USD", "rate": 1.12, "date": "2026-10-12"}
    w = [x for x in summary.analysis_warnings(data, "RACE", ["MC.PA"]) if "reports in EUR but trades in USD" in x["text"]]
    assert w and w[0]["level"] == "warning"
    assert "EUR risk-free rate" in w[0]["text"] and "RACE.MI" in w[0]["text"]
    # A peer with converted figures only gets the information note, not the target warning
    data = make_data(["RACE.MI", "CFR.SW"], currencies={"RACE.MI": "EUR", "CFR.SW": "CHF"})
    data["companies"]["CFR.SW"]["meta"]["fx"] = {"from": "EUR", "to": "CHF", "rate": 0.93, "date": "2026-10-12"}
    assert not any(x["level"] == "warning" and "risk-free" in x["text"] and "CFR.SW reports" in x["text"]
                   for x in summary.analysis_warnings(data, "RACE.MI", ["CFR.SW"]))
