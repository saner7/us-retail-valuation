"""Suggest a peer group for an S&P 500 company from data/sp500_universe.csv.

Rules:
  1. Never the target's other share classes (same CIK = same company), and at
     most one share class of each peer (the first listed).
  2. Companies in the target's GICS sub-industry first.
  3. If the sub-industry has fewer than PEERS_MIN_SUB_INDUSTRY other companies,
     fill up from the same GICS sector (flagged as a fallback). With 3 or more,
     a small but similar group is preferred: no fill-up.
  4. Closest in size first: |ln(peer market cap / target market cap)|, so twice
     as big and half as big count as equally far. Unknown market caps go last.

A suggestion is a starting point: choosing peers is the analyst's judgment.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config


def load_universe(path=None) -> pd.DataFrame:
    df = pd.read_csv(path or config.UNIVERSE_PATH, comment="#", dtype={"cik": str, "share_classes": str})
    df["share_classes"] = df["share_classes"].fillna("")
    df["market_cap_date"] = df["market_cap_date"].fillna("")
    return df


def universe_header(path=None) -> list:
    """The comment lines at the top of the file (source, dates, GICS note)."""
    with open(path or config.UNIVERSE_PATH) as f:
        return [line[1:].strip() for line in f if line.startswith("#")]


def market_cap_dates(universe: pd.DataFrame) -> str:
    d = universe["market_cap_date"].replace("", np.nan).dropna()
    if d.empty:
        return "not available"
    return d.min() if d.min() == d.max() else f"{d.min()} to {d.max()}"


def find(universe: pd.DataFrame, ticker: str) -> pd.Series | None:
    t = ticker.strip().upper()
    hit = universe[(universe["yahoo_ticker"] == t) | (universe["ticker"] == t)]
    return None if hit.empty else hit.iloc[0]


def is_financial(sub_industry: str) -> bool:
    return any(k in (sub_industry or "") for k in config.FINANCIAL_INDUSTRY_KEYWORDS)


def suggest_peers(target: str, universe: pd.DataFrame, n: int = None) -> dict:
    """{"peers": DataFrame, "fallback_used", "target": row, "warnings": [...]}; "error" if not found.
    Peers columns: yahoo_ticker, name, gics_sub_industry, market_cap, size_vs_target, reason."""
    n = max(1, min(n or config.PEERS_DEFAULT_COUNT, config.MAX_PEERS))
    row = find(universe, target)
    if row is None:
        return {"error": f"{target} is not in the S&P 500 list; enter its peers manually."}
    others = universe[universe["cik"] != row["cik"]].drop_duplicates("cik")      # one class per company
    same_sub = others[others["gics_sub_industry"] == row["gics_sub_industry"]].assign(reason="same sub-industry")
    pool = same_sub
    fallback = len(same_sub) < config.PEERS_MIN_SUB_INDUSTRY
    if fallback:
        sector = others[(others["gics_sector"] == row["gics_sector"])
                        & (others["gics_sub_industry"] != row["gics_sub_industry"])]
        pool = pd.concat([same_sub, sector.assign(reason="same sector (fallback)")])

    cap = row["market_cap"]
    def distance(c):
        return abs(math.log(c / cap)) if c > 0 and cap > 0 else math.inf
    pool = pool.assign(size_vs_target=pool["market_cap"] / cap if cap > 0 else np.nan,
                       _dist=pool["market_cap"].map(distance),
                       _rank=(pool["reason"] != "same sub-industry").astype(int))
    # Sub-industry peers before sector peers, then closest in size
    pool = pool.sort_values(["_rank", "_dist", "yahoo_ticker"]).head(n)
    peers = pool[["yahoo_ticker", "name", "gics_sub_industry", "market_cap", "size_vs_target", "reason"]]

    warnings = []
    if fallback:
        k = len(same_sub)
        count = "No other S&P 500 company shares" if k == 0 else \
            f"Only {k} other S&P 500 compan{'y shares' if k == 1 else 'ies share'}"
        warnings.append(f"{count} the sub-industry '{row['gics_sub_industry']}', so the group is filled from the "
                        f"{row['gics_sector']} sector (broader and less comparable).")
    if is_financial(row["gics_sub_industry"]):
        warnings.append(f"{row['yahoo_ticker']} is a bank or insurer: EV/EBITDA and an FCFF DCF do not fit "
                        "financial companies; P/E and P/B are more meaningful.")
    if pd.isna(cap):
        warnings.append(f"No saved market cap for {row['yahoo_ticker']}: peers are not ranked by size.")
    return {"peers": peers.reset_index(drop=True), "fallback_used": fallback, "target": row, "warnings": warnings}


# ---------------------------------------------------------------------------
# Curated groups and the size check
# ---------------------------------------------------------------------------

CURATED_LABEL = "Curated peer group (analyst judgment)"
GICS_LABEL = "GICS suggestion"
SIZE_WARNING = "Peers differ greatly in size from the target; multiples may not be comparable."
NOTE = ("Suggested peers are based on GICS classification and size. Choosing a peer group is an analyst's "
        "judgment: for example, Apple is classified with hardware makers but is often valued against large "
        "platform companies, and Ferrari is classified as an automaker but trades like a luxury brand.")


def load_curated(path=None) -> pd.DataFrame:
    df = pd.read_csv(path or config.CURATED_PEERS_PATH, comment="#", dtype=str)
    df["target"] = df["target"].str.strip().str.upper()
    return df


def curated_for(target: str, curated: pd.DataFrame) -> dict | None:
    hit = curated[curated["target"] == target.strip().upper()]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return {"peers": r["peers"].split(), "rationale": r["rationale"], "source_date": r["source_date"]}


def size_warning(target: str, peer_tickers: list, universe: pd.DataFrame) -> str | None:
    """SIZE_WARNING if every peer with a known market cap is below 1/PEERS_SIZE_GAP or above
    PEERS_SIZE_GAP times the target's."""
    row = find(universe, target)
    if row is None or not row["market_cap"] > 0:
        return None
    ratios = [p["market_cap"] / row["market_cap"] for p in (find(universe, t) for t in peer_tickers)
              if p is not None and p["market_cap"] > 0]
    gap = config.PEERS_SIZE_GAP
    if ratios and all(r < 1 / gap or r > gap for r in ratios):
        return SIZE_WARNING
    return None


def peer_table(target: str, peer_tickers: list, universe: pd.DataFrame, reasons: dict = None) -> pd.DataFrame:
    """One row per peer: sub-industry, market cap, size vs target and why it is there."""
    row = find(universe, target)
    cap = row["market_cap"] if row is not None else np.nan
    out = []
    for t in peer_tickers:
        p = find(universe, t)
        out.append({"Peer": t,
                    "Sub-industry": p["gics_sub_industry"] if p is not None else "not in the S&P 500 list",
                    "Market cap ($bn)": p["market_cap"] / 1e9 if p is not None else np.nan,
                    "Size vs target": p["market_cap"] / cap if p is not None and cap > 0 else np.nan,
                    "Reason": (reasons or {}).get(t, "added by you")})
    return pd.DataFrame(out)
