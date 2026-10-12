"""Stage 2: comparable companies analysis ("comps").

Idea: similar companies should trade at similar multiples. We measure how the
market values the peers (e.g. EV is 12x EBITDA), then apply that multiple to
the target's own EBITDA to estimate what the target could be worth.

Run directly for a quick look:
    python comps.py
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config
import data_loader as dl

# multiple name -> (numerator field, denominator field, label)
MULTIPLES = {
    "ev_sales": ("enterprise_value", "revenue_ttm", "EV / Sales"),
    "ev_ebitda": ("enterprise_value", "ebitda_ttm", "EV / EBITDA"),
    "pe": ("market_cap", "net_income_ttm", "P / E"),
    "pb": ("market_cap", "total_equity", "P / B"),
}

# margin name -> (numerator, denominator, label)
MARGINS = {
    "ebitda_margin": ("ebitda_ttm", "revenue_ttm", "EBITDA margin"),
    "net_margin": ("net_income_ttm", "revenue_ttm", "Net margin"),
}

# EV multiples value the whole firm, so net debt must be subtracted to reach equity.
# Equity multiples (P/E, P/B) already give the value of the shares directly.
EV_MULTIPLES = ["ev_sales", "ev_ebitda"]

LABELS = {k: v[2] for k, v in {**MULTIPLES, **MARGINS}.items()}

# Caveats shown next to a multiple in the app and the Excel export
MULTIPLE_NOTES = {
    "pb": ("P/B is shown for completeness but is not meaningful for retailers: their value comes "
           "from cash flow, not from the assets on the balance sheet, and share buybacks shrink "
           "book equity and inflate P/B. It is left out of the football field."),
}


# ---------------------------------------------------------------------------
# 1) Multiples for one company
# ---------------------------------------------------------------------------

def compute_multiples(values: dict, caps: dict = None):
    """Return (results, notes) for one company.

    results[name] = number, or NaN if excluded.
    notes[name]   = why it was excluded (only for excluded multiples).

    A multiple is excluded when:
      - an input is missing,
      - the denominator is zero or negative (e.g. P/E of a loss-making company
        would be negative, which says nothing useful about value),
      - the result is above the cap in config (not meaningful, "NM").
    """
    caps = caps if caps is not None else config.MULTIPLE_CAPS
    results, notes = {}, {}

    for name, (num_field, den_field, label) in MULTIPLES.items():
        num, den = values.get(num_field, np.nan), values.get(den_field, np.nan)
        if dl.is_missing(num) or dl.is_missing(den):
            results[name], notes[name] = np.nan, f"{label}: input missing"
        elif den <= 0:
            results[name], notes[name] = np.nan, f"{label}: {den_field} is zero or negative"
        elif num <= 0:
            results[name], notes[name] = np.nan, f"{label}: {num_field} is zero or negative"
        else:
            multiple = num / den
            if name in caps and multiple > caps[name]:
                results[name] = np.nan
                notes[name] = f"{label}: {multiple:.1f}x is above the {caps[name]:.0f}x cap (not meaningful)"
            else:
                results[name] = multiple

    # Margins: negative margins are real information, so we keep them
    for name, (num_field, den_field, label) in MARGINS.items():
        num, den = values.get(num_field, np.nan), values.get(den_field, np.nan)
        if dl.is_missing(num) or dl.is_missing(den) or den <= 0:
            results[name], notes[name] = np.nan, f"{label}: input missing"
        else:
            results[name] = num / den

    return results, notes


def multiples_table(companies: dict):
    """Rows = companies, columns = multiples and margins.
    Also returns a list of exclusion notes: [{ticker, metric, reason}]."""
    rows, notes = {}, []
    for ticker, c in companies.items():
        results, why = compute_multiples(c["values"])
        rows[ticker] = results
        notes += [{"ticker": ticker, "metric": LABELS[k], "reason": r} for k, r in why.items()]
    table = pd.DataFrame(rows).T[list(MULTIPLES) + list(MARGINS)]
    return table, pd.DataFrame(notes, columns=["ticker", "metric", "reason"])


# ---------------------------------------------------------------------------
# 2) Peer group statistics (target is NOT included)
# ---------------------------------------------------------------------------

def peer_stats(table: pd.DataFrame, peers: list) -> pd.DataFrame:
    """Mean, median, min, max, 25th and 75th percentile for each column,
    using peers only. Excluded (NaN) values are simply skipped."""
    peer_rows = table.loc[[p for p in peers if p in table.index]]
    stats = pd.DataFrame({
        "count": peer_rows.count(),
        "mean": peer_rows.mean(),
        "median": peer_rows.median(),
        "min": peer_rows.min(),
        "p25": peer_rows.quantile(0.25),   # linear interpolation, same as Excel PERCENTILE.INC
        "p75": peer_rows.quantile(0.75),
        "max": peer_rows.max(),
    })
    return stats.T


# ---------------------------------------------------------------------------
# 3) Implied value for the target
# ---------------------------------------------------------------------------

def valuation_range(stats_col: pd.Series, min_peers: int = None):
    """Pick the low/high multiples: interquartile range (25th-75th percentile)
    if there are enough peers, otherwise min-max. Returns (low, mid, high, method)."""
    min_peers = min_peers or config.MIN_PEERS_FOR_QUARTILES
    n = stats_col["count"]
    if n == 0:
        return np.nan, np.nan, np.nan, "no valid peers"
    if n >= min_peers:
        return stats_col["p25"], stats_col["median"], stats_col["p75"], \
            f"25th-75th percentile ({int(n)} peers)"
    return stats_col["min"], stats_col["median"], stats_col["max"], f"min-max (only {int(n)} peers)"


def implied_value(target_values: dict, multiple_name: str, multiples: tuple) -> dict:
    """Apply peer multiples to the target and walk the bridge to a per-share value.

    EV multiple:     metric x multiple = implied EV
                     implied EV - net debt = implied equity value
                     equity value / shares = value per share
    Equity multiple: metric x multiple = implied equity value (no net debt step)
                     equity value / shares = value per share

    `multiples` is a tuple of multiples (e.g. low, mid, high); the result has a
    list for each step in the same order.
    """
    _, den_field, label = MULTIPLES[multiple_name]
    metric = target_values.get(den_field, np.nan)
    net_debt = target_values.get("net_debt", np.nan)
    shares = target_values.get("shares_outstanding", np.nan)
    is_ev = multiple_name in EV_MULTIPLES

    result = {"multiple": label, "metric_name": den_field, "metric": metric,
              "is_ev_multiple": is_ev, "multiples": list(multiples),
              "net_debt": net_debt if is_ev else np.nan, "shares": shares, "note": ""}

    if dl.is_missing(metric) or metric <= 0:
        # Applying a multiple to a negative metric would give a negative "value"
        result["note"] = f"Target {den_field} is missing or not positive: cannot apply {label}"
        nan = [np.nan] * len(multiples)
        result.update(implied_ev=nan, equity_value=nan, per_share=nan)
        return result

    if is_ev:
        implied_ev = [metric * m for m in multiples]
        equity = [ev - net_debt for ev in implied_ev]
    else:
        implied_ev = [np.nan] * len(multiples)
        equity = [metric * m for m in multiples]

    result.update(implied_ev=implied_ev, equity_value=equity,
                  per_share=[e / shares for e in equity])
    return result


def football_field_rows(companies: dict, target: str = None, peers: list = None) -> pd.DataFrame:
    """One row per multiple: the low / median / high implied share price for the
    target, plus the method used. This is the main output of Stage 2."""
    target = target or config.TARGET
    peers = peers or config.PEERS
    table, _ = multiples_table(companies)
    stats = peer_stats(table, peers)
    target_values = companies[target]["values"]
    price = target_values["price"]

    rows = []
    for name in MULTIPLES:
        low, mid, high, method = valuation_range(stats[name])
        r = implied_value(target_values, name, (low, mid, high))
        lo_ps, mid_ps, hi_ps = r["per_share"]
        rows.append({
            "key": name, "multiple": r["multiple"], "method": method,
            "multiple_low": low, "multiple_median": mid, "multiple_high": high,
            "price_low": lo_ps, "price_median": mid_ps, "price_high": hi_ps,
            "upside_median": mid_ps / price - 1 if not math.isnan(mid_ps) else np.nan,
            "note": r["note"],
        })
    return pd.DataFrame(rows)


def bridge_table(companies: dict, target: str = None, peers: list = None) -> pd.DataFrame:
    """The full step-by-step bridge for every multiple, at low / median / high.
    Rows = steps of the bridge, columns = (multiple, low/median/high).
    "Target metric" is the denominator of that multiple (sales for EV/Sales,
    EBITDA for EV/EBITDA, ...). For P/E and P/B the EV and net debt rows are
    empty, because those multiples give equity value directly."""
    target = target or config.TARGET
    peers = peers or config.PEERS
    table, _ = multiples_table(companies)
    stats = peer_stats(table, peers)
    tv = companies[target]["values"]

    columns = {}
    for name in MULTIPLES:
        low, mid, high, _ = valuation_range(stats[name])
        r = implied_value(tv, name, (low, mid, high))
        for i, case in enumerate(["Low", "Median", "High"]):
            columns[(r["multiple"], case)] = {
                "Target metric": r["metric"],
                "x Peer multiple": r["multiples"][i],
                "= Implied enterprise value": r["implied_ev"][i],
                "- Net debt": r["net_debt"],
                "= Implied equity value": r["equity_value"][i],
                "/ Shares outstanding": r["shares"],
                "= Implied value per share": r["per_share"][i],
                "Upside vs current price": r["per_share"][i] / tv["price"] - 1,
            }
    return pd.DataFrame(columns)


# ---------------------------------------------------------------------------
# Command-line preview
# ---------------------------------------------------------------------------

def _fmt_multiple(x):
    return "NM" if dl.is_missing(x) else f"{x:.1f}x"


def _fmt_pct(x):
    return "N/A" if dl.is_missing(x) else f"{x:.1%}"


def main():
    data = dl.load_all()
    companies = data["companies"]
    table, notes = multiples_table(companies)

    def show(df):
        out = df.copy().astype(object)
        for col in out.columns:
            f = _fmt_pct if col in MARGINS else _fmt_multiple
            out[col] = df[col].map(f)
        out.columns = [LABELS[c] for c in out.columns]
        return out.to_string()

    print(f"\n=== Trading multiples (target {config.TARGET} marked with *) ===")
    shown = table.rename(index={config.TARGET: config.TARGET + " *"})
    print(show(shown))

    print("\n=== Peer statistics (target excluded) ===")
    stats = peer_stats(table, config.PEERS)
    counts = stats.loc["count"].astype(int)
    stat_view = stats.drop(index="count")
    print(show(stat_view))
    print("valid peers: " + ", ".join(f"{LABELS[k]} {v}" for k, v in counts.items()))

    print("\n=== Excluded values ===")
    print(notes.to_string(index=False) if not notes.empty else "None - all multiples were usable.")

    print(f"\n=== Implied value per share for {config.TARGET} "
          f"(current price ${companies[config.TARGET]['values']['price']:,.2f}) ===")
    ff = football_field_rows(companies)
    view = ff[["multiple", "method"]].copy()
    view["peer multiple (low / med / high)"] = ff.apply(
        lambda r: " / ".join(_fmt_multiple(r[c]) for c in ["multiple_low", "multiple_median", "multiple_high"]), axis=1)
    view["value per share (low / med / high)"] = ff.apply(
        lambda r: " / ".join(dl.fmt_number(r[c], "price") for c in ["price_low", "price_median", "price_high"]), axis=1)
    view["upside @ median"] = ff["upside_median"].map(_fmt_pct)
    print(view.to_string(index=False))
    for n in ff["note"]:
        if n:
            print("  note:", n)

    print("\n=== Bridge: EV / EBITDA (median case) - metric is TTM EBITDA ===")
    bridge = bridge_table(companies)[("EV / EBITDA", "Median")]
    for step, value in bridge.items():
        if "multiple" in step:
            text = _fmt_multiple(value)
        elif "Upside" in step:
            text = _fmt_pct(value)
        elif "per share" in step:
            text = dl.fmt_number(value, "price")
        else:
            text = dl.fmt_number(value)
        print(f"  {step:<30} {text:>12}")


if __name__ == "__main__":
    main()
