"""Stage C: precedent transactions.

What acquirers actually paid for comparable companies, measured as enterprise
value / LTM EBITDA. Buyers taking control usually pay a premium, so precedent
multiples tend to be higher than trading multiples, and old deals reflect old
market conditions. Only EV/EBITDA is used: it is the standard yardstick in
acquisitions, and EV/Sales would compare businesses with very different margins.

The deals are in data/precedents.csv, every figure with its SEC source. Users
can upload their own file with the same columns in the app.
"""

from __future__ import annotations

import io
import math

import numpy as np
import pandas as pd

import comps
import config

COLUMNS = ["announcement_date", "target", "acquirer", "status", "deal_type", "enterprise_value", "currency",
           "ltm_revenue", "ltm_ebitda", "ev_revenue", "ev_ebitda", "premium_pct", "source_url", "notes", "tier",
           "sector"]
NUMERIC = ["enterprise_value", "ltm_revenue", "ltm_ebitda", "ev_revenue", "ev_ebitda", "premium_pct"]
STATUSES = {"completed", "terminated", "pending"}
TIERS = {"core", "secondary"}


class PrecedentFileError(ValueError):
    """The file does not have the expected columns or values."""


def load_precedents(source=None) -> pd.DataFrame:
    """Read and check a precedents CSV (path, file-like object or bytes).
    Lines starting with '#' are comments. Raises PrecedentFileError with a
    message the user can act on."""
    source = config.PRECEDENTS_PATH if source is None else source
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    try:
        df = pd.read_csv(source, comment="#", skipinitialspace=True)
    except Exception as e:
        raise PrecedentFileError(f"Could not read the file as CSV: {e}") from e
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise PrecedentFileError(f"Missing columns: {', '.join(missing)}. Download the template for the layout.")
    df = df[COLUMNS].copy()
    for c in NUMERIC:
        converted = pd.to_numeric(df[c], errors="coerce")
        bad = df[c].notna() & converted.isna()
        if bad.any():
            raise PrecedentFileError(f"Column {c} must hold numbers (row(s) {', '.join(str(i + 1) for i in df.index[bad])}).")
        df[c] = converted
    for c, allowed in (("status", STATUSES), ("tier", TIERS)):
        df[c] = df[c].astype(str).str.strip().str.lower()
        wrong = sorted(set(df[c]) - allowed)
        if wrong:
            raise PrecedentFileError(f"Column {c} has unknown value(s) {wrong}; allowed: {sorted(allowed)}.")
    df["sector"] = df["sector"].astype(str).str.strip()
    df["announcement_date"] = pd.to_datetime(df["announcement_date"], errors="coerce")
    return df.reset_index(drop=True)


def template_csv() -> bytes:
    """An empty file with the expected columns, for users who bring their own deals."""
    return (",".join(COLUMNS) + "\n").encode()


def included_mask(df: pd.DataFrame, include_secondary: bool = False, include_terminated: bool = False) -> pd.Series:
    """Which deals enter the calculation. Default: core, completed deals only.
    Deals without a positive EV/EBITDA can never be used."""
    ok = df["ev_ebitda"].notna() & (df["ev_ebitda"] > 0)
    ok &= df["tier"].eq("core") | include_secondary
    ok &= df["status"].eq("completed") | (include_terminated & df["status"].eq("terminated"))
    return ok


def target_sector(meta: dict):
    """The precedents.csv sector code of a company, from Yahoo's country and industry (None if unmapped)."""
    return config.PRECEDENT_SECTORS.get((meta.get("country"), meta.get("industry")))


def applicable(meta: dict, ticker: str, deals: pd.DataFrame) -> bool:
    """Show the precedent row only when the target's sector matches one of the
    deals in the calculation, or the deal list was selected for this target."""
    if deals.empty:
        return False
    if ticker in config.PRECEDENT_SELECTED_FOR:
        return True
    return target_sector(meta) in set(deals["sector"])


def multiple_stats(deals: pd.DataFrame) -> pd.Series:
    """Same statistics as the comps (count, mean, median, min, p25, p75, max) for EV/EBITDA."""
    x = deals["ev_ebitda"].dropna()
    return pd.Series({"count": x.count(), "mean": x.mean(), "median": x.median(), "min": x.min(),
                      "p25": x.quantile(0.25), "p75": x.quantile(0.75), "max": x.max()})


def valuation(target_values: dict, deals: pd.DataFrame) -> dict:
    """Value per share for the target from the deals' EV/EBITDA: the comps
    range rule (25th-75th percentile with 4+ deals, else min-max) and the same
    bridge (EBITDA x multiple - net debt, / shares)."""
    stats = multiple_stats(deals)
    if stats["count"] == 0:
        return {"stats": stats, "method": "no deals", "multiples": (np.nan,) * 3,
                "bridge": None, "per_share": (np.nan,) * 3, "small_sample": True}
    low, mid, high, method = comps.valuation_range(stats)
    bridge = comps.implied_value(target_values, "ev_ebitda", (low, mid, high))
    return {"stats": stats, "method": method, "multiples": (low, mid, high), "bridge": bridge,
            "per_share": tuple(bridge["per_share"]), "small_sample": stats["count"] < config.PRECEDENT_SMALL_SAMPLE}


def football_field_row(target_values: dict, deals: pd.DataFrame) -> dict | None:
    """Row for summary.football_field_data, or None if there is nothing to show."""
    v = valuation(target_values, deals)
    lo, mid, hi = v["per_share"]
    if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in (lo, hi)):
        return None
    m_lo, m_mid, m_hi = v["multiples"]
    return {"label": "Precedents: EV / EBITDA", "group": "Precedent transactions", "low": lo, "mid": mid, "high": hi,
            "basis": f"{v['method'].replace('peers', 'deals')}: {m_lo:.1f}x / {m_mid:.1f}x / {m_hi:.1f}x"
                     + (" (small sample)" if v["small_sample"] else "")}
