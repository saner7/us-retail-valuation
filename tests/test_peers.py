"""Peer suggestions on a small, made-up universe."""

import pandas as pd
import pytest

import build_universe
import peers

ROWS = [  # ticker, name, sector, sub-industry, cik, market cap ($bn)
    ("AAA", "Alpha", "Tech", "Hardware", 1, 100), ("BBB", "Beta", "Tech", "Hardware", 2, 50),
    ("CCC", "Gamma", "Tech", "Hardware", 3, 200), ("DDD", "Delta", "Tech", "Hardware", 4, 400),
    ("EEE", "Epsilon", "Tech", "Hardware", 5, float("nan")),
    ("GOOGL", "Gee A", "Tech", "Hardware", 6, 1000), ("GOOG", "Gee C", "Tech", "Hardware", 6, 1000),
    ("AAA.B", "Alpha B", "Tech", "Hardware", 1, 100),
    ("SOLO", "Solo", "Energy", "Pipelines", 7, 30), ("PIP", "Pipe", "Energy", "Pipelines", 8, 25),
    ("OIL", "Oil", "Energy", "Integrated Oil", 9, 300), ("GAS", "Gas", "Energy", "Gas Drilling", 10, 35),
    ("BNK", "Bank", "Financials", "Diversified Banks", 11, 500),
]


@pytest.fixture
def universe(tmp_path):
    table = pd.DataFrame({"Symbol": [r[0] for r in ROWS], "Security": [r[1] for r in ROWS],
                          "GICS Sector": [r[2] for r in ROWS], "GICS Sub-Industry": [r[3] for r in ROWS],
                          "Headquarters Location": "X", "CIK": [r[4] for r in ROWS]})
    df = build_universe.tidy(table)
    df["market_cap"] = [r[5] * 1e9 for r in ROWS]
    df["market_cap_date"] = "2026-10-13"
    path = tmp_path / "u.csv"
    build_universe.save(df, "123", path)
    return peers.load_universe(path), path


def test_file_format(universe):
    u, path = universe
    assert u.loc[u["ticker"] == "AAA.B", "yahoo_ticker"].item() == "AAA-B"
    assert u.loc[u["ticker"] == "GOOGL", "share_classes"].item() == "GOOG"
    assert u.loc[u["ticker"] == "AAA", "cik"].item() == "0000000001"
    head = peers.universe_header(path)
    assert "revision 123" in head[0] and "maintained by MSCI and S&P" in head[2]
    assert peers.market_cap_dates(u) == "2026-10-13"


def test_same_sub_industry_by_size_without_other_share_classes(universe):
    u, _ = universe
    s = peers.suggest_peers("AAA", u)
    got = list(s["peers"]["yahoo_ticker"])
    # 50bn and 200bn are both 2x away (ln 2), 400bn 4x, 1,000bn 10x; unknown size last; never AAA-B;
    # only one class of GOOGL / GOOG
    assert got == ["BBB", "CCC", "DDD", "GOOGL", "EEE"]
    assert not s["fallback_used"] and set(s["peers"]["reason"]) == {"same sub-industry"}
    assert s["peers"].loc[0, "size_vs_target"] == pytest.approx(0.5)
    assert list(peers.suggest_peers("AAA", u, n=2)["peers"]["yahoo_ticker"]) == ["BBB", "CCC"]
    assert len(peers.suggest_peers("AAA", u, n=50)["peers"]) == 5          # capped by what exists


def test_sector_fallback_when_sub_industry_is_too_small(universe):
    u, _ = universe
    s = peers.suggest_peers("SOLO", u)
    assert s["fallback_used"] and "filled from the Energy sector" in s["warnings"][0]
    assert list(s["peers"]["yahoo_ticker"]) == ["PIP", "GAS", "OIL"]   # sub-industry first, then by size
    assert list(s["peers"]["reason"]) == ["same sub-industry"] + ["same sector (fallback)"] * 2


def test_financial_warning_and_unknown_ticker(universe):
    u, _ = universe
    s = peers.suggest_peers("BNK", u)
    assert any("bank or insurer" in w for w in s["warnings"]) and s["peers"].empty
    assert "not in the S&P 500" in peers.suggest_peers("RACE.MI", u)["error"]
    assert peers.suggest_peers("aaa-b", u)["target"]["name"] == "Alpha B"     # Yahoo format, any case
    unknown = peers.suggest_peers("EEE", u)                                  # target without a market cap
    assert unknown["peers"]["size_vs_target"].isna().all() and "not ranked by size" in unknown["warnings"][-1]
