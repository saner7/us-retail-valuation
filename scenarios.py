"""Stage B: scenario analysis (Bear / Base / Bull).

Each scenario has its own revenue growth path (Year 1 -> Year 5), EBITDA margin
path, capex, terminal growth and exit multiple. D&A, working capital, the tax
rate and the WACC are shared: the scenarios describe how the business performs,
the WACC describes how risky it is, and that does not change with the outcome.

Default Bear / Bull values (all editable in the app) move halfway from Base to
the most extreme value in the company's own history, see default_scenarios().
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

import comps
import config
import dcf
import summary

# The assumptions that differ by scenario, in display order: name -> (label, unit)
FIELDS = {
    "growth_start": ("Revenue growth, Year 1", "%"),
    "growth_end": (f"Revenue growth, Year {config.PROJECTION_YEARS}", "%"),
    "margin_start": ("EBITDA margin, Year 1", "%"),
    "margin_end": (f"EBITDA margin, Year {config.PROJECTION_YEARS}", "%"),
    "capex_pct": ("Capex (% of revenue)", "%"),
    "terminal_growth": ("Terminal growth", "%"),
    "exit_multiple": ("Exit EV/EBITDA", "x"),
}
# Year-by-year assumptions that belong to a scenario (the rest are shared)
SCENARIO_PATHS = ["revenue_growth", "ebitda_margin", "capex_pct"]


def _toward(base: float, hist: pd.Series, worse_is_low: bool, share: float):
    """(bear, bull): move `share` of the way from base to the most extreme
    historical value, the same distance on both sides. No history -> base."""
    hist = hist.dropna()
    if hist.empty or base is None or math.isnan(base):
        return base, base
    step = share * max(base - hist.min(), hist.max() - base, 0.0)
    return (base - step, base + step) if worse_is_low else (base + step, base - step)


def default_scenarios(data: dict, target: str, peers: list, base_choices: dict, share: float = None) -> dict:
    """{"bear": {...}, "base": {...}, "bull": {...}, "rationale": {field: text}}.

    Base is the user's current inputs. Bear and Bull:
      - Year 1 revenue growth and Year 5 EBITDA margin: halfway from Base to the
        furthest historical year, symmetric around Base;
      - capex: halfway towards the highest (Bear) / lowest (Bull) historical ratio;
      - Year 1 EBITDA margin: today's margin in every scenario (they diverge over time);
      - terminal growth: Base -/+ 0.5 percentage points; Year 5 growth = that rate;
      - exit multiple: halfway between the peer median and the low / high end of the
        peer range, Bull capped at 1.35x the target's own EV/EBITDA (never below Base).
    """
    share = config.SCENARIO_HALFWAY if share is None else share
    hist = dcf.historical_ratios(data["companies"][target]["history"])
    base = {f: base_choices[f] for f in FIELDS}
    bear, bull = dict(base), dict(base)

    def hist_col(k):
        return hist[k] if k in hist else pd.Series(dtype=float)

    bear["growth_start"], bull["growth_start"] = _toward(base["growth_start"], hist_col("revenue_growth"), True, share)
    bear["margin_end"], bull["margin_end"] = _toward(base["margin_end"], hist_col("ebitda_margin"), True, share)
    bear["capex_pct"], bull["capex_pct"] = _toward(base["capex_pct"], hist_col("capex_pct"), False, share)
    step = config.SCENARIO_TERMINAL_STEP
    bear["terminal_growth"], bull["terminal_growth"] = base["terminal_growth"] - step, base["terminal_growth"] + step
    bear["growth_end"], bull["growth_end"] = bear["terminal_growth"], bull["terminal_growth"]

    # Exit multiple from the peer range (same range logic as the comps)
    table, _ = comps.multiples_table(data["companies"])
    peers = [p for p in peers if p in table.index]
    low, mid, high, _ = comps.valuation_range(comps.peer_stats(table, peers)["ev_ebitda"]) if peers else (np.nan,) * 4
    own = table.loc[target, "ev_ebitda"] if target in table.index else np.nan
    cap = config.BULL_EXIT_CAP_VS_OWN * own
    bear["exit_multiple"] = (low + mid) / 2
    bull_uncapped = (mid + high) / 2
    bull["exit_multiple"] = bull_uncapped if math.isnan(cap) else max(min(bull_uncapped, cap), base["exit_multiple"])

    def pct(x):
        return "N/A" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1%}"

    def rng(k):
        h = hist_col(k).dropna()
        return f"history {pct(h.min())} to {pct(h.max())}" if not h.empty else "no history: Bear/Bull = Base"

    half = f"{share:.0%} of the way from Base to the most extreme year"
    rationale = {
        "growth_start": f"{half}, both directions ({rng('revenue_growth')})",
        "growth_end": "equal to each scenario's terminal growth",
        "margin_start": "today's TTM margin (one-offs removed) in every scenario; they diverge over time",
        "margin_end": f"{half}, both directions ({rng('ebitda_margin')})",
        "capex_pct": f"{share:.0%} of the way to the highest (Bear) / lowest (Bull) year ({rng('capex_pct')})",
        "terminal_growth": f"Base -/+ {step:.1%} points",
        "exit_multiple": (f"halfway between the peer median ({mid:.1f}x) and the peer low ({low:.1f}x) / high "
                          f"({high:.1f}x); Bull capped at {config.BULL_EXIT_CAP_VS_OWN}x {target}'s own "
                          f"EV/EBITDA ({own:.1f}x -> cap {cap:.1f}x): reaching the group's most premium "
                          f"multiples within 5 years is too optimistic"
                          if not (math.isnan(mid) or math.isnan(own)) else "peer or own EV/EBITDA not available"),
    }
    return {"bear": bear, "base": base, "bull": bull, "rationale": rationale,
            "bull_exit_uncapped": bull_uncapped, "bull_exit_cap": cap}


def scenario_choices(base_choices: dict, values: dict, is_base: bool) -> dict:
    """The full set of model inputs for one scenario: the shared inputs from
    Base plus the scenario's own values. A year-by-year table edited for Base
    only applies to the other scenarios for the shared rows (D&A, NWC, tax)."""
    ch = {**base_choices, **values}
    if not is_base and base_choices.get("per_year"):
        ch["per_year"] = {k: v for k, v in base_choices["per_year"].items() if k not in SCENARIO_PATHS}
    return ch


def run_scenarios(data: dict, base_choices: dict, scenario_values: dict, target: str) -> dict:
    """{scenario: {"choices", "run_kwargs", "result"}} for bear, base and bull."""
    out = {}
    for s in config.SCENARIOS:
        ch = scenario_choices(base_choices, scenario_values[s], s == "base")
        kw = summary.build_model(data, ch, target)["run_kwargs"]
        out[s] = {"choices": ch, "run_kwargs": kw, "result": dcf.run_dcf(**kw)}
    return out


def weights_ok(weights: dict) -> bool:
    return abs(sum(weights.values()) - 1.0) < 1e-6


def weighted_value(values: dict, weights: dict) -> float:
    """Probability-weighted value: sum of value x weight. NaN if the weights do
    not add up to 100% or a scenario value is missing (never rescaled)."""
    if not weights_ok(weights):
        return np.nan
    vals = [values[s] for s in config.SCENARIOS]
    if any(v is None or math.isnan(v) for v in vals):
        return np.nan
    return float(sum(values[s] * weights[s] for s in config.SCENARIOS))


def results_table(runs: dict, weights: dict) -> pd.DataFrame:
    """Rows = key outputs, columns = Bear / Base / Bull / Probability-weighted."""
    rows = {}
    for s in config.SCENARIOS:
        r, kw = runs[s]["result"], runs[s]["run_kwargs"]
        p = r["projection"]
        rows[s] = {
            "revenue_y5": p.loc["revenue"].iloc[-1], "ebitda_y5": p.loc["ebitda"].iloc[-1],
            "fcff_y5": p.loc["fcff"].iloc[-1],
            "gordon_ev": r["gordon"]["enterprise_value"], "gordon_value": r["gordon"]["value_per_share"],
            "gordon_upside": r["gordon"]["upside"],
            "exit_ev": r["exit"]["enterprise_value"], "exit_value": r["exit"]["value_per_share"],
            "exit_upside": r["exit"]["upside"],
        }
    table = pd.DataFrame(rows)
    price = runs["base"]["run_kwargs"]["price"]
    weighted = {k: weighted_value(table.loc[k].to_dict(), weights) for k in ["gordon_value", "exit_value"]}
    table["weighted"] = np.nan
    table.loc["gordon_value", "weighted"] = weighted["gordon_value"]
    table.loc["exit_value", "weighted"] = weighted["exit_value"]
    table.loc["gordon_upside", "weighted"] = weighted["gordon_value"] / price - 1
    table.loc["exit_upside", "weighted"] = weighted["exit_value"] / price - 1
    return table
