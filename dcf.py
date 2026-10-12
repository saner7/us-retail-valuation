"""Stage 3: discounted cash flow (DCF) valuation.

Idea: a company is worth the cash it will generate in the future, translated
into today's money. We
  1. project free cash flow for 5 years,
  2. estimate what happens after year 5 (the "terminal value"),
  3. discount everything back to today at the WACC (the return investors require),
  4. walk from enterprise value to a value per share.

Run directly for a quick look:
    python dcf.py          # tables in the terminal
    python dcf.py --html   # also save the sensitivity heatmaps to output/
"""

from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd

import comps
import config
import data_loader as dl

ASSUMPTION_LABELS = {
    "revenue_growth": "Revenue growth",
    "ebitda_margin": "EBITDA margin",
    "da_pct": "D&A (% of revenue)",
    "capex_pct": "Capex (% of revenue)",
    "nwc_pct": "Increase in working capital (% of revenue)",
    "tax_rate": "Tax rate",
}


# ---------------------------------------------------------------------------
# 1) Default assumptions from history
# ---------------------------------------------------------------------------

def historical_ratios(history: pd.DataFrame) -> pd.DataFrame:
    """Each assumption, measured in every past fiscal year.

    Sign conventions in Yahoo's cash flow statement:
      - capex is negative (cash going out), so we flip it to a positive cost,
      - "change in working capital" is negative when working capital grew
        (cash tied up in inventory etc.), so investment in NWC = -change_in_wc.
    """
    h = history.sort_index()
    ratios = pd.DataFrame(index=h.index)
    ratios["revenue_growth"] = h["revenue"].pct_change()
    ratios["ebitda_margin"] = h["ebitda"] / h["revenue"]
    ratios["da_pct"] = h["d_and_a"] / h["revenue"]
    ratios["capex_pct"] = -h["capex"] / h["revenue"]
    ratios["nwc_pct"] = -h["change_in_wc"] / h["revenue"]
    ratios["tax_rate"] = h["tax_provision"] / h["pretax_income"]
    return ratios


def revenue_cagr(history: pd.DataFrame) -> float:
    """Compound annual growth rate of revenue from the first to the last fiscal year."""
    rev = history.sort_index()["revenue"].dropna()
    if len(rev) < 2 or rev.iloc[0] <= 0:
        return np.nan
    return float((rev.iloc[-1] / rev.iloc[0]) ** (1 / (len(rev) - 1)) - 1)


def recent_growth(history: pd.DataFrame, revenue_ttm: float) -> float:
    """TTM revenue vs the last full fiscal year: the growth already visible in
    the quarters reported since the year end. NaN if either number is missing."""
    rev = history.sort_index()["revenue"].dropna()
    if rev.empty or dl.is_missing(revenue_ttm) or rev.iloc[-1] <= 0:
        return np.nan
    return float(revenue_ttm / rev.iloc[-1] - 1)


def growth_path(start: float, end: float, years: int = None) -> list:
    """Straight line from `start` (Year 1) to `end` (final year). Used for
    revenue growth and the EBITDA margin."""
    years = years or config.PROJECTION_YEARS
    return [float(x) for x in np.linspace(start, end, years)]


def default_assumptions(history: pd.DataFrame, revenue_ttm: float = None,
                        terminal_growth: float = None, n_years: int = None,
                        ebitda_ttm: float = None) -> dict:
    """Default assumptions, all editable in the app.

    - D&A, capex and tax rate: average of the last `n_years` fiscal years
      (config.DEFAULT_HISTORY_YEARS), so one unusual old year does not
      drive the whole forecast.
    - EBITDA margin: a list, Year 1 = current TTM margin (after manual one-off
      adjustments), moving in a straight line to the last `n_years` average in
      the final year. Without TTM data the average is used for every year.
    - Increase in NWC: config.DEFAULT_NWC_PCT (0%), because the history is too volatile.
    - Revenue growth: a list, Year 1 = TTM revenue vs the last fiscal year, falling
      in a straight line to the terminal growth rate in the final year. Without
      TTM revenue, Year 1 falls back to the historical CAGR.
    Missing history -> NaN, never a guess."""
    n_years = n_years or config.DEFAULT_HISTORY_YEARS
    terminal_growth = config.TERMINAL_GROWTH if terminal_growth is None else terminal_growth
    recent = historical_ratios(history).tail(n_years)
    defaults = {k: float(recent[k].mean()) for k in ASSUMPTION_LABELS}
    defaults["nwc_pct"] = config.DEFAULT_NWC_PCT
    start = recent_growth(history, revenue_ttm) if revenue_ttm is not None else np.nan
    if math.isnan(start):
        start = revenue_cagr(history)
    defaults["revenue_growth"] = growth_path(start, terminal_growth)
    if not (dl.is_missing(ebitda_ttm) or dl.is_missing(revenue_ttm)) and revenue_ttm > 0:
        defaults["ebitda_margin"] = growth_path(ebitda_ttm / revenue_ttm, defaults["ebitda_margin"])
    return defaults


def assumption_reference(history: pd.DataFrame, values: dict, n_years: int = None) -> pd.DataFrame:
    """What each default could have been, shown next to it in the app:
    average of all years, average of the last n years, latest year, and for the
    EBITDA margin the current TTM margin (after manual one-off adjustments)."""
    n_years = n_years or config.DEFAULT_HISTORY_YEARS
    ratios = historical_ratios(history)
    ref = pd.DataFrame({
        "all_years_avg": ratios.mean(),
        f"last_{n_years}y_avg": ratios.tail(n_years).mean(),
        "latest_year": ratios.iloc[-1] if not ratios.empty else np.nan,
    })
    ref["ttm"] = np.nan
    if not (dl.is_missing(values.get("ebitda_ttm")) or dl.is_missing(values.get("revenue_ttm"))):
        ref.loc["ebitda_margin", "ttm"] = values["ebitda_ttm"] / values["revenue_ttm"]
    ref.loc["revenue_growth", "all_years_avg"] = revenue_cagr(history)   # CAGR, not a simple average
    ref.loc["revenue_growth", "ttm"] = recent_growth(history, values.get("revenue_ttm"))
    return ref.loc[list(ASSUMPTION_LABELS)]


# ---------------------------------------------------------------------------
# 2) WACC
# ---------------------------------------------------------------------------

def wacc(risk_free: float, equity_risk_premium: float, beta: float, country_risk_premium: float,
         pre_tax_cost_of_debt: float, tax_rate: float, equity_value: float, debt_value: float) -> dict:
    """Weighted average cost of capital.

    Cost of equity (CAPM) = risk-free + beta x equity risk premium + country risk premium
    After-tax cost of debt = pre-tax cost of debt x (1 - tax)   (interest is tax-deductible)
    WACC = E/(D+E) x cost of equity + D/(D+E) x after-tax cost of debt

    Weights use market values: equity = market cap, debt = debt used for EV.
    """
    cost_of_equity = risk_free + beta * equity_risk_premium + country_risk_premium
    after_tax_kd = pre_tax_cost_of_debt * (1 - tax_rate)
    total = equity_value + debt_value
    we, wd = equity_value / total, debt_value / total
    return {"cost_of_equity": cost_of_equity, "after_tax_cost_of_debt": after_tax_kd,
            "weight_equity": we, "weight_debt": wd,
            "wacc": we * cost_of_equity + wd * after_tax_kd}


# ---------------------------------------------------------------------------
# 3) Projection and free cash flow
# ---------------------------------------------------------------------------

def _per_year(value, years: int) -> list:
    """Accept one number (same every year) or a list with one number per year."""
    if isinstance(value, (list, tuple, np.ndarray, pd.Series)):
        if len(value) != years:
            raise ValueError(f"expected {years} values, got {len(value)}")
        return [float(v) for v in value]
    return [float(value)] * years


def project(base_revenue: float, assumptions: dict, years: int = None) -> pd.DataFrame:
    """Build the projection table. Columns = Year 1..N.

    FCFF (free cash flow to the firm) is the cash left for ALL investors
    (shareholders and lenders) after the business has paid taxes and reinvested:
        EBIT        = EBITDA - D&A
        FCFF        = EBIT x (1 - tax) + D&A - capex - increase in working capital
    D&A is added back because it is a cost on paper, not a cash payment.
    """
    years = years or config.PROJECTION_YEARS
    a = {k: _per_year(assumptions[k], years) for k in ASSUMPTION_LABELS}

    rows = {k: [] for k in ["revenue", "revenue_growth", "ebitda", "ebitda_margin", "d_and_a", "ebit",
                            "taxes", "nopat", "capex", "increase_in_nwc", "fcff"]}
    revenue = base_revenue
    for i in range(years):
        revenue = revenue * (1 + a["revenue_growth"][i])
        ebitda = revenue * a["ebitda_margin"][i]
        d_and_a = revenue * a["da_pct"][i]
        ebit = ebitda - d_and_a
        taxes = ebit * a["tax_rate"][i]
        nopat = ebit - taxes                      # = EBIT x (1 - tax)
        capex = revenue * a["capex_pct"][i]
        nwc = revenue * a["nwc_pct"][i]
        fcff = nopat + d_and_a - capex - nwc
        for k, v in [("revenue", revenue), ("revenue_growth", a["revenue_growth"][i]), ("ebitda", ebitda),
                     ("ebitda_margin", a["ebitda_margin"][i]), ("d_and_a", d_and_a), ("ebit", ebit),
                     ("taxes", taxes), ("nopat", nopat), ("capex", capex), ("increase_in_nwc", nwc),
                     ("fcff", fcff)]:
            rows[k].append(v)
    return pd.DataFrame(rows, index=[f"Year {i + 1}" for i in range(years)]).T


# ---------------------------------------------------------------------------
# 4) Discounting and terminal value
# ---------------------------------------------------------------------------

def discount_periods(years: int, mid_year: bool) -> np.ndarray:
    """How many years away each cash flow is.
    End-of-year: 1, 2, 3, ...  Mid-year: 0.5, 1.5, 2.5, ...
    Mid-year is more realistic because cash comes in during the year, not
    all on 31 December, so it is discounted half a year less."""
    t = np.arange(1, years + 1, dtype=float)
    return t - 0.5 if mid_year else t


def discount_factors(rate: float, periods) -> np.ndarray:
    """1 / (1 + rate)^t : what $1 received in t years is worth today."""
    return 1 / (1 + rate) ** np.asarray(periods, dtype=float)


def gordon_terminal_value(final_fcff: float, wacc_rate: float, growth: float) -> float:
    """Gordon growth model: value at the end of the last year of all cash flows
    after it, assuming they grow forever at `growth`:
        TV = FCFF(N) x (1 + g) / (WACC - g)
    Only valid if g < WACC; otherwise the formula breaks (infinite/negative)."""
    if growth >= wacc_rate:
        return np.nan
    return final_fcff * (1 + growth) / (wacc_rate - growth)


def exit_multiple_terminal_value(final_ebitda: float, multiple: float) -> float:
    """Exit multiple: assume the business could be sold at the end of year N
    for the same EV/EBITDA multiple that peers trade at today."""
    return final_ebitda * multiple


def implied_growth(terminal_value: float, final_fcff: float, wacc_rate: float) -> float:
    """The perpetual growth rate that an exit-multiple terminal value implies
    (solve the Gordon formula for g). Useful sanity check."""
    return (terminal_value * wacc_rate - final_fcff) / (terminal_value + final_fcff)


def implied_exit_multiple(terminal_value: float, final_ebitda: float) -> float:
    return terminal_value / final_ebitda


# ---------------------------------------------------------------------------
# 5) Full DCF
# ---------------------------------------------------------------------------

def run_dcf(base_revenue: float, assumptions: dict, wacc_rate: float, terminal_growth: float,
            exit_multiple: float, net_debt: float, shares: float, price: float,
            mid_year: bool = None) -> dict:
    """Run both terminal value methods and return every step of the bridge.

    Discounting the terminal value (Rosenbaum & Pearl convention):
      - Gordon growth: TV comes from a cash flow stream, so with the mid-year
        convention it is discounted at N - 0.5, like the last cash flow.
      - Exit multiple: TV is a sale price at a point in time (end of year N),
        so it is always discounted at N.
    """
    mid_year = config.MID_YEAR_CONVENTION if mid_year is None else mid_year
    proj = project(base_revenue, assumptions)
    years = proj.shape[1]
    periods = discount_periods(years, mid_year)
    factors = discount_factors(wacc_rate, periods)
    pv_fcff = proj.loc["fcff"].values * factors
    sum_pv_fcff = float(pv_fcff.sum())

    final_fcff, final_ebitda = proj.loc["fcff"].iloc[-1], proj.loc["ebitda"].iloc[-1]
    tv = {
        "gordon": gordon_terminal_value(final_fcff, wacc_rate, terminal_growth),
        "exit": exit_multiple_terminal_value(final_ebitda, exit_multiple),
    }
    tv_period = {"gordon": periods[-1], "exit": float(years)}

    results = {}
    for method in ["gordon", "exit"]:
        pv_tv = tv[method] * float(discount_factors(wacc_rate, [tv_period[method]])[0])
        ev = sum_pv_fcff + pv_tv
        equity = ev - net_debt
        per_share = equity / shares
        results[method] = {
            "terminal_value": tv[method],
            "tv_discount_period": tv_period[method],
            "pv_terminal_value": pv_tv,
            "sum_pv_fcff": sum_pv_fcff,
            "enterprise_value": ev,
            "tv_share_of_ev": pv_tv / ev if ev else np.nan,
            "net_debt": net_debt,
            "equity_value": equity,
            "shares": shares,
            "value_per_share": per_share,
            "upside": per_share / price - 1,
        }
    # Cross-checks: what each method implies about the other
    results["gordon"]["implied_exit_multiple"] = implied_exit_multiple(tv["gordon"], final_ebitda)
    results["exit"]["implied_growth"] = implied_growth(tv["exit"], final_fcff, wacc_rate)

    proj.loc["discount_period"] = periods
    proj.loc["discount_factor"] = factors
    proj.loc["pv_fcff"] = pv_fcff
    return {"projection": proj, "gordon": results["gordon"], "exit": results["exit"],
            "warnings": dcf_warnings(results, wacc_rate, terminal_growth)}


def dcf_warnings(results: dict, wacc_rate: float, growth: float) -> list:
    out = []
    if growth >= wacc_rate:
        out.append("Terminal growth must be below WACC: the Gordon growth value is not defined.")
    for method, label in [("gordon", "Gordon growth"), ("exit", "Exit multiple")]:
        share = results[method]["tv_share_of_ev"]
        if not math.isnan(share) and share > config.TERMINAL_VALUE_WARNING:
            out.append(f"{label}: terminal value is {share:.0%} of enterprise value "
                       f"(above {config.TERMINAL_VALUE_WARNING:.0%}). The result depends heavily "
                       f"on long-term assumptions.")
    return out


# ---------------------------------------------------------------------------
# 6) Sensitivity tables
# ---------------------------------------------------------------------------

def _grid(center: float, half_range: float, step: float) -> list:
    n = int(round(half_range / step))
    return [round(center + i * step, 6) for i in range(-n, n + 1)]


def sensitivity(run_kwargs: dict, method: str, wacc_values: list, second_values: list) -> pd.DataFrame:
    """Value per share for every combination of WACC (rows) and either terminal
    growth (method='gordon') or exit multiple (method='exit') (columns)."""
    key = "terminal_growth" if method == "gordon" else "exit_multiple"
    table = pd.DataFrame(index=wacc_values, columns=second_values, dtype=float)
    for w in wacc_values:
        for x in second_values:
            r = run_dcf(**{**run_kwargs, "wacc_rate": w, key: x})
            table.loc[w, x] = r[method]["value_per_share"]
    table.index.name = "WACC"
    table.columns.name = "Terminal growth" if method == "gordon" else "Exit EV/EBITDA"
    return table


def sensitivity_tables(run_kwargs: dict) -> dict:
    waccs = _grid(run_kwargs["wacc_rate"], config.SENS_WACC_RANGE, config.SENS_WACC_STEP)
    growths = _grid(run_kwargs["terminal_growth"], config.SENS_GROWTH_RANGE, config.SENS_GROWTH_STEP)
    mults = _grid(run_kwargs["exit_multiple"], config.SENS_MULTIPLE_RANGE, config.SENS_MULTIPLE_STEP)
    return {"gordon": sensitivity(run_kwargs, "gordon", waccs, growths),
            "exit": sensitivity(run_kwargs, "exit", waccs, mults)}


def implied_by_price(run_kwargs: dict, key: str, method: str, low: float, high: float,
                     target_price: float = None) -> float:
    """Reverse DCF: the value of one input (`key`, e.g. 'wacc_rate') at which the
    model's value per share equals the current price, all else unchanged.
    Solved by bisection between `low` and `high`; NaN if the price is not reached
    anywhere in that range."""
    target_price = run_kwargs["price"] if target_price is None else target_price

    def gap(x):
        return run_dcf(**{**run_kwargs, key: x})[method]["value_per_share"] - target_price

    g_low, g_high = gap(low), gap(high)
    if any(math.isnan(v) for v in (g_low, g_high)) or g_low * g_high > 0:
        return np.nan
    for _ in range(60):
        mid = (low + high) / 2
        g_mid = gap(mid)
        if g_low * g_mid <= 0:
            high = mid
        else:
            low, g_low = mid, g_mid
    return (low + high) / 2


# ---------------------------------------------------------------------------
# 7) Default inputs for the target, straight from the data
# ---------------------------------------------------------------------------

def default_inputs(data: dict, ticker: str = None, peers: list = None) -> dict:
    """Everything run_dcf needs, with defaults taken from the data and config.
    Also returns the WACC breakdown and where each default came from.
    `peers` (default: every loaded company except the target) set the exit multiple."""
    ticker = ticker or config.TARGET
    peers = peers if peers is not None else [t for t in data["companies"] if t != ticker]
    c = data["companies"][ticker]
    v = c["values"]
    assumptions = default_assumptions(c["history"], revenue_ttm=v["revenue_ttm"], ebitda_ttm=v["ebitda_ttm"])

    rf = data["risk_free"]["rate"]
    beta = c["meta"].get("beta")
    wacc_inputs = {
        "risk_free": rf,
        "equity_risk_premium": config.EQUITY_RISK_PREMIUM,
        "beta": beta if beta is not None else np.nan,
        "country_risk_premium": config.COUNTRY_RISK_PREMIUM,
        "pre_tax_cost_of_debt": rf + config.CREDIT_SPREAD,
        "tax_rate": assumptions["tax_rate"],
        "equity_value": v["market_cap"],
        "debt_value": v["debt_for_ev"],
    }
    w = wacc(**wacc_inputs)

    # Exit multiple = peer median EV/EBITDA from the comps (target excluded)
    table, _ = comps.multiples_table(data["companies"])
    exit_multiple = float(comps.peer_stats(table, peers).loc["median", "ev_ebitda"])

    run_kwargs = {
        "base_revenue": v["revenue_ttm"],
        "assumptions": assumptions,
        "wacc_rate": w["wacc"],
        "terminal_growth": config.TERMINAL_GROWTH,
        "exit_multiple": exit_multiple,
        "net_debt": v["net_debt"],
        "shares": v["shares_outstanding"],
        "price": v["price"],
    }
    # Historical interest rate, shown only for reference next to our assumption
    h = c["history"].sort_index()
    hist_kd = h["interest_expense"].iloc[-1] / v["debt_for_ev"] if "interest_expense" in h else np.nan
    return {"run_kwargs": run_kwargs, "wacc_inputs": wacc_inputs, "wacc": w,
            "historical_ratios": historical_ratios(c["history"]),
            "assumption_reference": assumption_reference(c["history"], v),
            "historical_cost_of_debt": float(hist_kd)}


# ---------------------------------------------------------------------------
# Command-line preview
# ---------------------------------------------------------------------------

def _pct(x):
    return "N/A" if dl.is_missing(x) or (isinstance(x, float) and math.isnan(x)) else f"{x:.1%}"


def main():
    parser = argparse.ArgumentParser(description="DCF valuation of the target company")
    parser.add_argument("--html", action="store_true", help="save sensitivity heatmaps to output/")
    args = parser.parse_args()

    data = dl.load_all()
    inputs = default_inputs(data)
    kw, wi, w = inputs["run_kwargs"], inputs["wacc_inputs"], inputs["wacc"]

    print(f"\n=== {config.TARGET}: historical ratios (basis for default assumptions) ===")
    hr = inputs["historical_ratios"].copy()
    hr.index = hr.index.strftime("FY end %Y-%m")
    hr.columns = [ASSUMPTION_LABELS[c] for c in hr.columns]
    print(hr.T.apply(lambda col: col.map(_pct)).to_string())
    n = config.DEFAULT_HISTORY_YEARS
    print(f"\nDefault assumptions vs history (default = last {n} fiscal years' average unless noted):")
    ref = inputs["assumption_reference"]
    print(f"  {'':<45} {'default':>8} {'all yrs':>8} {f'last {n}y':>8} {'latest':>8} {'TTM':>8}")
    for k, label in ASSUMPTION_LABELS.items():
        a = kw["assumptions"][k]
        shown = f"{_pct(a[0])}>{_pct(a[-1])}" if isinstance(a, list) else _pct(a)
        r = ref.loc[k]
        print(f"  {label:<45} {shown:>8} {_pct(r['all_years_avg']):>8} {_pct(r[f'last_{n}y_avg']):>8} "
              f"{_pct(r['latest_year']):>8} {_pct(r['ttm']):>8}")
    print("  Revenue growth: Year 1 = TTM vs last fiscal year, straight line to terminal growth in "
          "the final year ('all yrs' = CAGR).\n  EBITDA margin: Year 1 = TTM (one-offs removed), straight "
          f"line to the last {n} years' average in the final year. NWC default 0% (history too volatile).")

    print("\n=== WACC ===")
    print(f"  Risk-free rate (10y Treasury)   {_pct(wi['risk_free']):>7}")
    print(f"  Beta (Yahoo)                    {wi['beta']:>7.2f}")
    print(f"  Equity risk premium             {_pct(wi['equity_risk_premium']):>7}  (assumption)")
    print(f"  Country risk premium            {_pct(wi['country_risk_premium']):>7}")
    print(f"  = Cost of equity                {_pct(w['cost_of_equity']):>7}")
    print(f"  Pre-tax cost of debt            {_pct(wi['pre_tax_cost_of_debt']):>7}  "
          f"(risk-free + {config.CREDIT_SPREAD:.2%} spread; historical interest/debt "
          f"{_pct(inputs['historical_cost_of_debt'])})")
    print(f"  = After-tax cost of debt        {_pct(w['after_tax_cost_of_debt']):>7}")
    print(f"  Weights E / D                   {_pct(w['weight_equity'])} / {_pct(w['weight_debt'])}")
    print(f"  = WACC                          {_pct(w['wacc']):>7}")

    r = run_dcf(**kw)
    print(f"\n=== Projection (USD, base = TTM revenue {dl.fmt_number(kw['base_revenue'])}) ===")
    proj = r["projection"]
    out = proj.copy().astype(object)
    for row in proj.index:
        f = _pct if row in ("revenue_growth", "ebitda_margin") else \
            (lambda x: f"{x:.3f}") if row in ("discount_period", "discount_factor") else dl.fmt_number
        out.loc[row] = proj.loc[row].map(f)
    print(out.to_string())

    print(f"\n=== Valuation (current price ${kw['price']:,.2f}) ===")
    g, e = r["gordon"], r["exit"]
    rows = [("Sum of PV of FCFF", "sum_pv_fcff", dl.fmt_number),
            ("Terminal value", "terminal_value", dl.fmt_number),
            ("PV of terminal value", "pv_terminal_value", dl.fmt_number),
            ("= Enterprise value", "enterprise_value", dl.fmt_number),
            ("- Net debt", "net_debt", dl.fmt_number),
            ("= Equity value", "equity_value", dl.fmt_number),
            ("/ Shares", "shares", dl.fmt_number),
            ("= Value per share", "value_per_share", lambda x: f"{x:,.2f}"),
            ("Upside / downside", "upside", _pct),
            ("TV share of EV", "tv_share_of_ev", _pct)]
    print(f"  {'':<24}{'Gordon (g=' + _pct(kw['terminal_growth']) + ')':>22}"
          f"{'Exit (' + format(kw['exit_multiple'], '.1f') + 'x)':>22}")
    for label, key, f in rows:
        print(f"  {label:<24}{f(g[key]):>22}{f(e[key]):>22}")
    print(f"  {'Cross-check':<24}{'implies ' + format(g['implied_exit_multiple'], '.1f') + 'x':>22}"
          f"{'implies g=' + _pct(e['implied_growth']):>22}")
    for wmsg in r["warnings"]:
        print("  WARNING:", wmsg)
    iw = implied_by_price(kw, "wacc_rate", "gordon", kw["terminal_growth"] + 0.001, 0.20)
    ig = implied_by_price(kw, "terminal_growth", "gordon", -0.02, kw["wacc_rate"] - 0.001)
    im = implied_by_price(kw, "exit_multiple", "exit", 0.0, 50.0)
    print(f"  Reverse DCF at today's price: WACC {_pct(iw)} (Gordon), terminal growth {_pct(ig)} "
          f"(Gordon), exit multiple {im:.1f}x")

    tables = sensitivity_tables(kw)
    for method, title in [("gordon", "WACC x terminal growth"), ("exit", "WACC x exit multiple")]:
        t = tables[method].copy()
        t.index = [_pct(x) for x in t.index]
        t.columns = [_pct(x) if method == "gordon" else f"{x:.1f}x" for x in t.columns]
        print(f"\n=== Sensitivity: value per share, {title} ===")
        print(t.apply(lambda col: col.map(lambda x: "N/A" if math.isnan(x) else f"{x:,.0f}")).to_string())

    if args.html:
        import charts
        config.PROJECT_DIR.joinpath("output").mkdir(exist_ok=True)
        path = config.PROJECT_DIR / "output" / "dcf_sensitivity.html"
        figs = [
            charts.sensitivity_heatmap(tables["gordon"], kw["price"],
                                       f"{config.TARGET} DCF - Gordon growth: value per share",
                                       base_x=kw["terminal_growth"], base_wacc=kw["wacc_rate"]),
            charts.sensitivity_heatmap(tables["exit"], kw["price"],
                                       f"{config.TARGET} DCF - Exit multiple: value per share",
                                       x_is_multiple=True, base_x=kw["exit_multiple"],
                                       base_wacc=kw["wacc_rate"]),
        ]
        with open(path, "w") as f:
            for i, fig in enumerate(figs):
                f.write(fig.to_html(full_html=False, include_plotlyjs="cdn" if i == 0 else False))
        print(f"\nHeatmaps saved to {path}")


if __name__ == "__main__":
    main()
