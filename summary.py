"""Stage 4: pull the comps and the DCF together into one valuation summary,
the data behind the football field chart.

Each row is one method with a low / mid / high value per share:
  - 52-week trading range (what the market actually paid; mid is empty),
  - one row per trading multiple (peer 25th-75th percentile, median as mid),
  - DCF Gordon growth and DCF exit multiple (base case as mid; the bar spans
    small changes in WACC and terminal growth / exit multiple).
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

import comps
import config
import dcf

GROUPS = {"market": "Market", "comps": "Trading comps", "dcf": "DCF"}


def dcf_range(run_kwargs: dict, method: str) -> dict:
    """Low / base / high value per share for one DCF method.

    The bar covers every combination of WACC +/- FF_WACC_DELTA with terminal
    growth +/- FF_GROWTH_DELTA (Gordon) or exit multiple +/- FF_MULTIPLE_DELTA
    (exit), i.e. a 3 x 3 grid around the base case."""
    w = run_kwargs["wacc_rate"]
    if method == "gordon":
        key, x, dx = "terminal_growth", run_kwargs["terminal_growth"], config.FF_GROWTH_DELTA
    else:
        key, x, dx = "exit_multiple", run_kwargs["exit_multiple"], config.FF_MULTIPLE_DELTA
    dw = config.FF_WACC_DELTA
    values = [dcf.run_dcf(**{**run_kwargs, "wacc_rate": wi, key: xi})[method]["value_per_share"]
              for wi, xi in itertools.product([w - dw, w, w + dw], [x - dx, x, x + dx])]
    values = [v for v in values if not np.isnan(v)]
    base = dcf.run_dcf(**run_kwargs)[method]["value_per_share"]
    return {"low": min(values) if values else np.nan, "mid": base,
            "high": max(values) if values else np.nan}


def football_field_data(companies: dict, run_kwargs: dict, target: str = None,
                        peers: list = None, row_order: list = None) -> pd.DataFrame:
    """Rows in display order (top to bottom), as set in config.FOOTBALL_FIELD_ROWS.
    Columns: key, label, group, low, mid, high, basis (how the range was built,
    for tooltips and the Excel export)."""
    target = target or config.TARGET
    peers = peers or config.PEERS
    row_order = row_order or config.FOOTBALL_FIELD_ROWS
    v = companies[target]["values"]
    rows = {"52w": {"label": "52-week trading range", "group": GROUPS["market"],
                    "low": v["low_52w"], "mid": np.nan, "high": v["high_52w"],
                    "basis": "Lowest and highest share price of the last 52 weeks"}}

    for _, r in comps.football_field_rows(companies, target, peers).iterrows():
        rows[r["key"]] = {"label": f"Comps: {r['multiple']}", "group": GROUPS["comps"],
                          "low": r["price_low"], "mid": r["price_median"], "high": r["price_high"],
                          "basis": f"Peer {r['method']}: {r['multiple_low']:.1f}x / "
                                   f"{r['multiple_median']:.1f}x / {r['multiple_high']:.1f}x"
                                   + (f" ({r['note']})" if r["note"] else "")}

    w = run_kwargs["wacc_rate"]
    dw = config.FF_WACC_DELTA
    g, m = run_kwargs["terminal_growth"], run_kwargs["exit_multiple"]
    rows["dcf_exit"] = {"label": "DCF: Exit multiple", "group": GROUPS["dcf"],
                        **dcf_range(run_kwargs, "exit"),
                        "basis": f"WACC {w - dw:.1%}-{w + dw:.1%}, exit EV/EBITDA "
                                 f"{m - config.FF_MULTIPLE_DELTA:.1f}x-{m + config.FF_MULTIPLE_DELTA:.1f}x; "
                                 f"base case WACC {w:.1%}, {m:.1f}x"}
    rows["dcf_gordon"] = {"label": "DCF: Gordon growth", "group": GROUPS["dcf"],
                          **dcf_range(run_kwargs, "gordon"),
                          "basis": f"WACC {w - dw:.1%}-{w + dw:.1%}, terminal growth "
                                   f"{g - config.FF_GROWTH_DELTA:.1%}-{g + config.FF_GROWTH_DELTA:.1%}; "
                                   f"base case WACC {w:.1%}, g {g:.1%}"}
    out = pd.DataFrame([{"key": k, **rows[k]} for k in row_order if k in rows],
                       columns=["key", "label", "group", "low", "mid", "high", "basis"])
    return out


def reverse_dcf(run_kwargs: dict) -> dict:
    """What today's share price implies, one input at a time (all else unchanged):
    the WACC and terminal growth for the Gordon method, the exit multiple for the
    exit method. NaN when no value in a sensible range reaches the price."""
    w, g = run_kwargs["wacc_rate"], run_kwargs["terminal_growth"]
    return {
        "wacc": dcf.implied_by_price(run_kwargs, "wacc_rate", "gordon", g + 0.001, 0.25),
        "terminal_growth": dcf.implied_by_price(run_kwargs, "terminal_growth", "gordon", -0.05, w - 0.001),
        "exit_multiple": dcf.implied_by_price(run_kwargs, "exit_multiple", "exit", 0.0, 60.0),
    }


# ---------------------------------------------------------------------------
# User choices -> DCF inputs (shared by the app and the Excel export)
# ---------------------------------------------------------------------------

# Assumptions the user sets as one number; the two "paths" are set by start and end value
SCALAR_ASSUMPTIONS = ["da_pct", "capex_pct", "nwc_pct", "tax_rate"]


def default_choices(data: dict, target: str = None) -> dict:
    """Every number the user can change in the app, at its default value."""
    d = dcf.default_inputs(data, target)
    a, wi, kw = d["run_kwargs"]["assumptions"], d["wacc_inputs"], d["run_kwargs"]
    return {
        "growth_start": a["revenue_growth"][0],
        "terminal_growth": kw["terminal_growth"],
        "margin_start": a["ebitda_margin"][0] if isinstance(a["ebitda_margin"], list) else a["ebitda_margin"],
        "margin_end": a["ebitda_margin"][-1] if isinstance(a["ebitda_margin"], list) else a["ebitda_margin"],
        **{k: a[k] for k in SCALAR_ASSUMPTIONS},
        "risk_free": wi["risk_free"], "beta": wi["beta"],
        "equity_risk_premium": wi["equity_risk_premium"],
        "country_risk_premium": wi["country_risk_premium"],
        "credit_spread": config.CREDIT_SPREAD,
        "exit_multiple": kw["exit_multiple"],
        "mid_year": config.MID_YEAR_CONVENTION,
        "per_year": None,   # optional {assumption: [Year 1..N]} that replaces the above
    }


def assumptions_from_choices(choices: dict, years: int = None) -> dict:
    """Year-by-year assumptions. Revenue growth runs in a straight line from
    growth_start to the terminal growth rate, the EBITDA margin from margin_start
    to margin_end; the rest are flat. A per-year table, if given, wins."""
    years = years or config.PROJECTION_YEARS
    a = {"revenue_growth": dcf.growth_path(choices["growth_start"], choices["terminal_growth"], years),
         "ebitda_margin": dcf.growth_path(choices["margin_start"], choices["margin_end"], years)}
    a.update({k: [float(choices[k])] * years for k in SCALAR_ASSUMPTIONS})
    for k, values in (choices.get("per_year") or {}).items():
        a[k] = [float(x) for x in values]
    return a


def build_model(data: dict, choices: dict, target: str = None) -> dict:
    """Turn the user's choices into WACC inputs and run_dcf keyword arguments.
    The WACC uses the tax rate choice (one number), even if the per-year table
    changes the tax rate of individual years."""
    target = target or config.TARGET
    v = data["companies"][target]["values"]
    wacc_inputs = {
        "risk_free": choices["risk_free"],
        "equity_risk_premium": choices["equity_risk_premium"],
        "beta": choices["beta"],
        "country_risk_premium": choices["country_risk_premium"],
        "pre_tax_cost_of_debt": choices["risk_free"] + choices["credit_spread"],
        "tax_rate": choices["tax_rate"],
        "equity_value": v["market_cap"],
        "debt_value": v["debt_for_ev"],
    }
    w = dcf.wacc(**wacc_inputs)
    run_kwargs = {
        "base_revenue": v["revenue_ttm"],
        "assumptions": assumptions_from_choices(choices),
        "wacc_rate": w["wacc"],
        "terminal_growth": choices["terminal_growth"],
        "exit_multiple": choices["exit_multiple"],
        "net_debt": v["net_debt"],
        "shares": v["shares_outstanding"],
        "price": v["price"],
        "mid_year": choices["mid_year"],
    }
    return {"wacc_inputs": wacc_inputs, "wacc": w, "run_kwargs": run_kwargs,
            "credit_spread": choices["credit_spread"]}
