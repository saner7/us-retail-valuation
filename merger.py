"""
Merger model: is an acquisition accretive or dilutive to the buyer's EPS?

Accretion / dilution compares the buyer's earnings per share after the deal
(pro forma) with its EPS on its own (standalone). Paying in shares adds shares;
paying in cash costs interest (or the interest the cash was earning). The deal
is accretive when the target's earnings, plus synergies, more than cover that.

    pro forma net income = buyer NI + target NI
                           + (synergies - interest on new debt - interest lost on cash
                              + target interest saved if its debt is refinanced) x (1 - tax)
                           - amortisation of new intangibles x (1 - tax)        (only in the 'incl.' EPS)
    pro forma EPS        = pro forma NI / (buyer shares + new shares)

Run `python merger.py TGT DG` for a hypothetical example.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
import dcf
import summary

# The two EPS definitions, as shown to the user (internal keys stay "gaap" / "cash")
LABEL_INCL = "EPS incl. deal amortization"
LABEL_EXCL = "EPS excl. deal amortization (cash EPS)"
EPS_NOTE = ("Yahoo's forward EPS is usually an adjusted (non-GAAP) figure, so the 'incl. deal amortization' line is "
            "not reported GAAP earnings: it is that EPS after the amortization of the intangibles acquired in the deal.")

SIMPLIFICATIONS = [
    "Earnings: Yahoo's forward EPS (analyst consensus for the next fiscal year) when available, otherwise "
    "trailing-twelve-month net income with one-offs removed. Year 2 grows each company's earnings with its "
    "DCF Base revenue growth.",
    "Analyst consensus EPS is usually an adjusted (non-GAAP) figure, which can be well above trailing "
    "GAAP earnings (when a company has had impairments or one-offs). '" + LABEL_INCL + "' therefore means "
    "EPS after the deal's amortization, not reported GAAP earnings of the two companies.",
    "No calendarisation: if the two companies' fiscal years end in different months, their forward EPS cover "
    "different twelve-month periods.",
    "Purchase price allocation, simplified: a share of the price above the target's book value becomes "
    "amortisable intangibles (straight line); the rest is goodwill, which is not amortised. No deferred tax "
    "liability, no fair-value step-ups of other assets.",
    "Interest rates are held flat; new debt and cash used are fixed at closing (no debt paydown in Years 1-2).",
    "Transaction fees and integration costs are one-off: shown separately, not in the adjusted EPS.",
    "Revenue synergies are entered as their pre-tax profit contribution.",
    "Shares are issued at the buyer's current price; no exchange-ratio collars, no change in the buyer's "
    "valuation after the announcement.",
]


def _forward_eps(data: dict, ticker: str):
    fwd = data["companies"][ticker]["meta"].get("forward_eps")
    return float(fwd) if isinstance(fwd, (int, float)) and not pd.isna(fwd) else None


def pair_inputs(data: dict, buyer: str, target: str) -> tuple:
    """Both companies on the same earnings basis: forward EPS if both have it, otherwise TTM."""
    forward = _forward_eps(data, buyer) is not None and _forward_eps(data, target) is not None
    return company_inputs(data, buyer, forward), company_inputs(data, target, forward)


def company_inputs(data: dict, ticker: str, forward: bool = True) -> dict:
    """What the merger model needs about one company, with the source of its earnings."""
    c = data["companies"][ticker]
    v, hist = c["values"], c["history"]
    shares = v["shares_outstanding"]
    fwd = _forward_eps(data, ticker)
    if forward and fwd is not None:
        eps, source = fwd, "analyst consensus (Yahoo forward EPS)"
    else:
        eps, source = v["net_income_ttm"] / shares, "TTM net income (one-offs removed)"
    ch = summary.default_choices(data, ticker)
    growth = dcf.growth_path(ch["growth_start"], ch["growth_end"])
    interest = hist["interest_expense"].dropna() if "interest_expense" in hist else pd.Series(dtype=float)
    fy_end = pd.Timestamp(hist.index.max()).strftime("%d %b") if not hist.empty else "N/A"
    return {"ticker": ticker, "name": summary.company_name(data, ticker), "price": v["price"], "shares": shares,
            "eps": eps, "eps_source": source, "growth_y2": growth[1], "revenue": v["revenue_ttm"],
            "ebitda": v["ebitda_ttm"], "debt": v["debt_for_ev"], "cash": v["cash"], "net_debt": v["net_debt"],
            "book_equity": v["total_equity"], "interest": float(interest.iloc[-1]) if len(interest) else 0.0,
            "tax_rate": ch["tax_rate"], "fiscal_year_end": fy_end,
            "currency": summary.target_currency(data, ticker)}


def default_inputs(acq: dict, tgt: dict, risk_free: float, short_rate: float) -> dict:
    """Deal assumptions; the illustrative ones are labelled as such in the app."""
    return {
        "premium": 0.25, "offer_price": None,       # offer_price overrides the premium
        "stock_pct": 0.5,                           # share of the purchase price paid in new shares
        "debt_rate": risk_free + config.CREDIT_SPREAD,   # illustrative: 10y Treasury + the WACC credit spread
        "cash_rate": short_rate,                    # interest the buyer's cash earns (13-week T-bill)
        "refinance_target_debt": False,
        "cost_synergies": 0.01 * tgt["revenue"],    # illustrative: 1% of the target's sales, run rate
        "revenue_synergies": 0.0,                   # pre-tax profit contribution
        "phase_in": (0.5, 1.0),                     # share of run-rate synergies realised in Years 1 and 2
        "integration_cost_x": 1.0,                  # illustrative: one-off cost = 1x run-rate synergies
        "fee_pct": config.LBO_TRANSACTION_FEE_PCT,  # illustrative: % of the target's enterprise value
        "intangibles_pct": 0.30,                    # illustrative: share of the excess over book value
        "amortisation_years": 10,
        "min_cash_pct": config.LBO_MIN_CASH_PCT,    # cash the buyer keeps, % of its revenue
        "tax_rate": acq["tax_rate"],
    }


def run(acq: dict, tgt: dict, inp: dict, synergies_on: bool = True) -> dict:
    """The full model for Years 1 and 2."""
    offer = inp["offer_price"] if inp.get("offer_price") else tgt["price"] * (1 + inp["premium"])
    equity_value = offer * tgt["shares"]
    # optional direct inputs (e.g. from a real deal's filings): the fees, the new debt and the yearly amortisation
    fees = inp["fees_amount"] if inp.get("fees_amount") is not None else inp["fee_pct"] * (equity_value + tgt["net_debt"])
    refi = tgt["debt"] if inp["refinance_target_debt"] else 0.0
    stock = inp["stock_pct"] * equity_value
    cash_needed = equity_value - stock + fees + refi
    excess_cash = max(acq["cash"] - inp["min_cash_pct"] * acq["revenue"], 0.0)
    if inp.get("new_debt") is not None:
        new_debt = min(max(inp["new_debt"], 0.0), cash_needed)
        cash_used = cash_needed - new_debt
    else:
        cash_used = min(excess_cash, cash_needed)
        new_debt = cash_needed - cash_used
    new_shares = stock / acq["price"]
    pf_shares = acq["shares"] + new_shares
    tax = inp["tax_rate"]

    excess = max(equity_value - tgt["book_equity"], 0.0)
    intangibles = inp["intangibles_pct"] * excess
    amortisation = inp["amortisation_per_year"] if inp.get("amortisation_per_year") is not None \
        else intangibles / inp["amortisation_years"]
    run_rate = inp["cost_synergies"] + inp["revenue_synergies"]

    years = []
    for t in (0, 1):
        grow = (lambda c: (1 + c["growth_y2"]) if t == 1 else 1.0)
        acq_ni = acq["eps"] * acq["shares"] * grow(acq)
        tgt_ni = tgt["eps"] * tgt["shares"] * grow(tgt)
        syn = run_rate * inp["phase_in"][t] if synergies_on else 0.0
        interest_new = inp["debt_rate"] * new_debt
        lost = inp["cash_rate"] * cash_used
        saved = tgt["interest"] if inp["refinance_target_debt"] else 0.0
        before_syn = acq_ni + tgt_ni + (-interest_new - lost + saved) * (1 - tax)
        cash_ni = before_syn + syn * (1 - tax)
        gaap_ni = cash_ni - amortisation * (1 - tax)
        standalone = acq_ni / acq["shares"]
        eps_gaap, eps_cash = gaap_ni / pf_shares, cash_ni / pf_shares
        # synergies (realised in that year, pre-tax) that make GAAP EPS equal to standalone
        breakeven = (standalone * pf_shares - (before_syn - amortisation * (1 - tax))) / (1 - tax)
        years.append({
            "year": t + 1, "buyer_ni": acq_ni, "target_ni": tgt_ni, "synergies": syn,
            "new_debt_interest": interest_new, "interest_lost_on_cash": lost, "target_interest_saved": saved,
            "amortisation": amortisation, "pro_forma_ni_gaap": gaap_ni, "pro_forma_ni_cash": cash_ni,
            "standalone_eps": standalone, "eps_gaap": eps_gaap, "eps_cash": eps_cash,
            "accretion_gaap": eps_gaap - standalone, "accretion_gaap_pct": eps_gaap / standalone - 1,
            "accretion_cash": eps_cash - standalone, "accretion_cash_pct": eps_cash / standalone - 1,
            "breakeven_synergies": breakeven,
            "breakeven_run_rate": breakeven / inp["phase_in"][t] if inp["phase_in"][t] else np.nan,
        })

    debt_pf = acq["debt"] + new_debt + (0.0 if inp["refinance_target_debt"] else tgt["debt"])
    ebitda_pf = acq["ebitda"] + tgt["ebitda"] + (run_rate * inp["phase_in"][0] if synergies_on else 0.0)
    interest_pf = acq["interest"] + (0.0 if inp["refinance_target_debt"] else tgt["interest"]) \
        + inp["debt_rate"] * new_debt
    credit = {"debt": debt_pf, "ebitda": ebitda_pf, "interest": interest_pf,
              "debt_to_ebitda": debt_pf / ebitda_pf if ebitda_pf > 0 else np.nan,
              "ebitda_to_interest": ebitda_pf / interest_pf if interest_pf > 0 else np.nan}
    result = {
        "offer_price": offer, "premium": offer / tgt["price"] - 1, "equity_value": equity_value,
        "offer_pe": offer / tgt["eps"] if tgt["eps"] else np.nan,
        "sources_uses": {
            "uses": {"Purchase of target equity": equity_value, "Transaction fees": fees,
                     "Refinance target debt": refi},
            "sources": {"New buyer shares": stock, "Buyer's excess cash": cash_used, "New debt": new_debt}},
        "new_shares": new_shares, "pro_forma_shares": pf_shares,
        "ownership": {"buyer": acq["shares"] / pf_shares, "target": new_shares / pf_shares},
        "ppa": {"excess_over_book": excess,
                "intangibles": np.nan if inp.get("amortisation_per_year") is not None else intangibles,
                "goodwill": np.nan if inp.get("amortisation_per_year") is not None else excess - intangibles,
                "annual_amortisation": amortisation},
        "one_off": {"transaction_fees": fees, "integration_costs": inp["integration_cost_x"] * run_rate},
        "years": years, "credit": credit, "tax_rate": tax,
    }
    result["warnings"] = warnings(result, acq, tgt)
    return result


def warnings(r: dict, acq: dict, tgt: dict) -> list:
    out = []
    lev = r["credit"]["debt_to_ebitda"]
    if lev > 4:
        out.append(f"Pro forma debt is {lev:.1f}x EBITDA, above 4x: a heavy load for the combined company.")
    if lev > 3:
        out.append(f"Pro forma debt is {lev:.1f}x EBITDA: the borrowing spread would likely be higher at this "
                   "leverage than the rate assumed.")
    if acq["eps"] <= 0:
        out.append(f"{acq['ticker']} has zero or negative earnings: the accretion / dilution percentages are not "
                   "meaningful.")
    if tgt["eps"] <= 0:
        out.append(f"{tgt['ticker']} has zero or negative earnings: it dilutes EPS before synergies at any price.")
    if acq["fiscal_year_end"] != tgt["fiscal_year_end"]:
        out.append(f"Fiscal years end on different dates ({acq['ticker']}: {acq['fiscal_year_end']}, "
                   f"{tgt['ticker']}: {tgt['fiscal_year_end']}); earnings are not calendarised.")
    return out


def breakeven_premium(acq: dict, tgt: dict, inp: dict, low: float = -0.5, high: float = 3.0) -> float:
    """The highest premium at which Year 1 EPS incl. deal amortization (synergies included) is unchanged.
    NaN if the deal is dilutive even at `low` or still accretive at `high`."""
    def acc(p):
        return run(acq, tgt, {**inp, "premium": p, "offer_price": None})["years"][0]["accretion_gaap"]
    a_low, a_high = acc(low), acc(high)
    if np.isnan(a_low) or np.isnan(a_high) or a_low < 0 or a_high > 0:
        return np.nan
    for _ in range(80):
        mid = (low + high) / 2
        low, high = (mid, high) if acc(mid) >= 0 else (low, mid)
    return (low + high) / 2


def quick_check(acq: dict, tgt: dict, inp: dict, r: dict) -> dict:
    """Rules of thumb vs the model (Year 1, no synergies; amortisation excluded, i.e. cash EPS).
    All stock: accretive if the buyer's P/E > the offer P/E.
    All cash: accretive if the target's earnings yield at the offer > the after-tax cost of cash/debt.
    Mixed: compare the target's earnings yield with the weighted cost of the acquisition currency."""
    tax = inp["tax_rate"]
    buyer_pe = acq["price"] / acq["eps"] if acq["eps"] > 0 else np.nan
    offer_pe = r["offer_pe"]
    target_yield = tgt["eps"] / r["offer_price"]
    su = r["sources_uses"]["sources"]
    cash_total = su["Buyer's excess cash"] + su["New debt"]
    cash_cost = ((su["Buyer's excess cash"] * inp["cash_rate"] + su["New debt"] * inp["debt_rate"]) / cash_total
                 * (1 - tax)) if cash_total else inp["debt_rate"] * (1 - tax)
    w = inp["stock_pct"]
    cost = w * (1 / buyer_pe if buyer_pe > 0 else np.inf) + (1 - w) * cash_cost
    rule_accretive = target_yield > cost
    model = run(acq, tgt, inp, synergies_on=False)["years"][0]["accretion_cash"]
    return {"buyer_pe": buyer_pe, "offer_pe": offer_pe, "target_earnings_yield": target_yield,
            "stock_cost": 1 / buyer_pe if buyer_pe > 0 else np.nan, "cash_cost_after_tax": cash_cost,
            "weighted_cost": cost, "rule_accretive": rule_accretive, "model_accretion_no_synergies": model,
            "consistent": rule_accretive == (model > 0)}


def explain(qc: dict, inp: dict) -> str:
    w = inp["stock_pct"]
    verdict = "accretive" if qc["rule_accretive"] else "dilutive"
    model = "accretive" if qc["model_accretion_no_synergies"] > 0 else "dilutive"
    text = (f"Rule of thumb ({w:.0%} stock / {1 - w:.0%} cash): the target earns {qc['target_earnings_yield']:.1%} on "
            f"the offer price (offer P/E {qc['offer_pe']:.1f}x); the deal is paid for with shares that 'cost' "
            f"{qc['stock_cost']:.1%} (1 / buyer P/E {qc['buyer_pe']:.1f}x) and cash or debt that costs "
            f"{qc['cash_cost_after_tax']:.1%} after tax, {qc['weighted_cost']:.1%} weighted. So the rule says "
            f"{verdict}. The model without synergies ({LABEL_EXCL}, Year 1) is {model}: ")
    if qc["consistent"]:
        return text + "consistent."
    return text + ("not consistent, because fees are financed with debt or cash, and the excess cash and the "
                   "new debt carry different rates; with the target's yield this close to the cost, those "
                   "details decide.")


def missing_inputs(acq: dict, tgt: dict, inp: dict) -> list:
    """Names of the inputs that are missing (NaN): the model then shows N/A instead of guessing."""
    need = {"buyer price": acq["price"], "buyer shares": acq["shares"], "buyer EPS": acq["eps"],
            "buyer cash": acq["cash"], "buyer debt": acq["debt"], "buyer tax rate": inp["tax_rate"],
            "target price": tgt["price"], "target shares": tgt["shares"], "target EPS": tgt["eps"],
            "target net debt": tgt["net_debt"], "target book equity": tgt["book_equity"],
            "new debt rate": inp["debt_rate"], "cash interest rate": inp["cash_rate"]}
    return [k for k, v in need.items() if v is None or pd.isna(v)]


def bridge(r: dict, acq: dict, year: int = 1, basis: str = "gaap") -> pd.DataFrame:
    """Pro forma EPS bridge for one year, per buyer share (what the waterfall chart shows):
    buyer EPS -> target earnings -> synergies -> interest on new debt -> interest lost on cash
    (-> target interest saved) (-> amortisation, only in the 'incl.' EPS) -> the effect of the new shares -> pro forma EPS.
    Columns: step, value, kind (total / change). The steps add up to the pro forma EPS."""
    y = r["years"][year - 1]
    t = r["tax_rate"]
    n = acq["shares"]
    rows = [("Buyer EPS (standalone)", y["standalone_eps"], "total"),
            ("Target earnings", y["target_ni"] / n, "change"),
            ("Synergies (after tax)", y["synergies"] * (1 - t) / n, "change"),
            ("Interest on new debt", -y["new_debt_interest"] * (1 - t) / n, "change"),
            ("Interest lost on cash", -y["interest_lost_on_cash"] * (1 - t) / n, "change")]
    if y["target_interest_saved"]:
        rows.append(("Target interest saved", y["target_interest_saved"] * (1 - t) / n, "change"))
    ni = y["pro_forma_ni_cash"]
    if basis == "gaap":
        rows.append(("Amortisation of intangibles", -y["amortisation"] * (1 - t) / n, "change"))
        ni = y["pro_forma_ni_gaap"]
    rows.append(("Effect of new shares", ni / r["pro_forma_shares"] - ni / n, "change"))
    rows.append(("Pro forma EPS", ni / r["pro_forma_shares"], "total"))
    return pd.DataFrame(rows, columns=["step", "value", "kind"])


def _grid(acq, tgt, inp, rows, cols, make_inputs, basis="gaap") -> pd.DataFrame:
    out = pd.DataFrame(index=rows, columns=cols, dtype=float)
    key = "accretion_gaap_pct" if basis == "gaap" else "accretion_cash_pct"
    for rv in rows:
        for cv in cols:
            out.loc[rv, cv] = run(acq, tgt, make_inputs(rv, cv))["years"][0][key]
    return out


def grid_values(base: float, grid: list) -> list:
    """The grid plus the user's own value, sorted, so the user's case is always in the table."""
    return sorted({round(x, 6) for x in grid + [base]})


def sensitivity_premium_stock(acq: dict, tgt: dict, inp: dict, basis: str = "gaap") -> pd.DataFrame:
    """Year 1 accretion / dilution %: premium (rows) x share of the price paid in stock (columns)."""
    premium = (inp["offer_price"] / tgt["price"] - 1) if inp.get("offer_price") else inp["premium"]
    rows = grid_values(premium, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    cols = grid_values(inp["stock_pct"], [0.0, 0.25, 0.5, 0.75, 1.0])
    t = _grid(acq, tgt, inp, rows, cols,
              lambda p, w: {**inp, "premium": p, "offer_price": None, "stock_pct": w}, basis)
    t.index.name, t.columns.name = "Premium", "Paid in stock"
    return t


def sensitivity_synergy_premium(acq: dict, tgt: dict, inp: dict, basis: str = "gaap") -> pd.DataFrame:
    """Year 1 accretion / dilution %: run-rate cost synergies (rows, as % of the target's sales)
    x premium (columns)."""
    premium = (inp["offer_price"] / tgt["price"] - 1) if inp.get("offer_price") else inp["premium"]
    base_pct = inp["cost_synergies"] / tgt["revenue"] if tgt["revenue"] else 0.0
    rows = grid_values(base_pct, [0.0, 0.005, 0.01, 0.02, 0.03])
    cols = grid_values(premium, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    t = _grid(acq, tgt, inp, rows, cols,
              lambda sp, p: {**inp, "cost_synergies": sp * tgt["revenue"], "premium": p, "offer_price": None}, basis)
    t.index.name, t.columns.name = "Cost synergies (% of target sales)", "Premium"
    return t


# ---------------------------------------------------------------------------
# Real transactions from SEC filings (data/merger_examples.csv)
# ---------------------------------------------------------------------------

EXAMPLES_PATH = config.DATA_DIR / "merger_examples.csv"
EXAMPLE_BASES = {
    "ttm": "(a) Trailing twelve months from the SEC filings",
    "mgmt": "(b) Foot Locker management projections (DEFM14A), Dick's TTM",
    "mgmt_downside": "(b') Foot Locker's additional, lower scenario (DEFM14A), Dick's TTM",
    "fy2024": "Mechanics check: fiscal 2024 actuals, no synergies (compare with Dick's own pro forma)",
}


def load_examples(path=None) -> pd.DataFrame:
    """Long table: example, field, value, unit, source, note. Every number carries its source."""
    return pd.read_csv(path or EXAMPLES_PATH, comment="#", dtype=str, keep_default_na=False)


def example_names(df: pd.DataFrame) -> list:
    return list(dict.fromkeys(df["example"]))


def example_facts(df: pd.DataFrame, name: str) -> dict:
    """{field: value} (numbers as floats, text as is) for one example."""
    out = {}
    for _, r in df[df["example"] == name].iterrows():
        try:
            out[r["field"]] = float(r["value"])
        except ValueError:
            out[r["field"]] = r["value"]
    return out


def example_sources(df: pd.DataFrame, name: str) -> pd.DataFrame:
    return df[df["example"] == name][["field", "value", "unit", "source", "note"]].reset_index(drop=True)


def _ttm(f: dict, side: str, metric: str) -> float:
    """Last fiscal year + the latest quarter - the same quarter a year earlier."""
    return f[f"{side}_{metric}_fy"] + f[f"{side}_{metric}_q"] - f[f"{side}_{metric}_qp"]


def example_pair(df: pd.DataFrame, name: str, basis: str = "ttm", short_rate: float = 0.04) -> tuple:
    """(acquirer, target, inputs, facts) for a real deal, in the same shapes the model uses elsewhere.
    basis "ttm": both companies' trailing twelve months from the filings, held flat.
    basis "mgmt" / "mgmt_downside": the target's earnings derived from its management's projected adjusted
    EBITDA for the first two full fiscal years after closing: (EBITDA - D&A - interest) x (1 - tax) (illustrative,
    D&A and interest held at the last fiscal year); the buyer stays on its trailing twelve months."""
    f = example_facts(df, name)
    tax = f["tax_rate"]
    b_ni, t_ni = _ttm(f, "b", "ni"), _ttm(f, "t", "ni")
    acq = {"ticker": f["buyer_ticker"], "name": f["buyer_name"], "price": f["buyer_price"], "shares": f["b_shares"],
           "eps": b_ni / f["b_shares"], "eps_source": "TTM net income from SEC filings (10-K + 10-Qs)",
           "growth_y2": 0.0, "revenue": _ttm(f, "b", "revenue"), "ebitda": _ttm(f, "b", "opinc") + _ttm(f, "b", "da"),
           "debt": f["b_debt"], "cash": f["b_cash"], "net_debt": f["b_debt"] - f["b_cash"],
           "book_equity": f["b_equity"], "interest": _ttm(f, "b", "interest"), "tax_rate": tax,
           "fiscal_year_end": f["fiscal_year_end"], "currency": "USD"}
    eps, growth, source = t_ni / f["t_shares"], 0.0, "TTM net income from SEC filings (10-K + 10-Qs)"
    if basis == "fy2024":       # the fiscal year Dick's own pro forma statements use
        acq.update(eps=f["b_ni_fy"] / f["b_shares_fy"], shares=f["b_shares_fy"],
                   eps_source="fiscal 2024 net income (10-K)")
        eps, source = f["t_ni_fy"] / f["t_shares"], "fiscal 2024 net income (10-K)"
    elif basis != "ttm":
        key = "t_ebitda_alt" if basis == "mgmt_downside" else "t_ebitda_proj"
        ni1, ni2 = [(f[f"{key}_fy{y}"] - f["t_da_fy"] - f["t_interest_fy"]) * (1 - tax) for y in (2026, 2027)]
        eps, growth = ni1 / f["t_shares"], ni2 / ni1 - 1
        source = ("derived from management's projected adjusted EBITDA (DEFM14A): (EBITDA - D&A - interest) x "
                  f"(1 - {tax:.0%}), FY2026E; Year 2 = FY2027E")
    tgt = {"ticker": f["target_ticker"], "name": f["target_name"], "price": f["target_unaffected_price"],
           "shares": f["t_shares"], "eps": eps, "eps_source": source, "growth_y2": growth,
           "revenue": _ttm(f, "t", "revenue"), "ebitda": f["t_ebitda_adjusted"], "debt": f["t_net_debt"] + f["t_cash"],
           "cash": f["t_cash"], "net_debt": f["t_net_debt"], "book_equity": f["t_equity"],
           "interest": f["t_interest_fy"], "tax_rate": tax, "fiscal_year_end": f["fiscal_year_end"], "currency": "USD"}
    inp = default_inputs(acq, tgt, risk_free=0.0, short_rate=short_rate)
    notes_rate = (f["fin_tax_effect_fy"] / tax) / f["new_debt"]          # all-in yearly financing cost / notes issued
    inp.update(offer_price=f["offer_price"], stock_pct=f["stock_pct"], debt_rate=notes_rate, new_debt=f["new_debt"],
               fees_amount=f["fees_amount"], amortisation_per_year=f["amortisation_per_year"], tax_rate=tax,
               cost_synergies=(f["cost_synergies_low"] + f["cost_synergies_high"]) / 2, refinance_target_debt=False)
    return acq, tgt, inp, f


def main():
    import sys

    import data_loader as dl
    buyer, target = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else ("TGT", "DG")
    data = dl.load_all([buyer, target], refresh="--refresh" in sys.argv)
    acq, tgt = pair_inputs(data, buyer, target)
    if acq["currency"] != tgt["currency"]:
        raise SystemExit("The two companies trade in different currencies; the model does not convert them.")
    inp = default_inputs(acq, tgt, data["risk_free"]["rate"], data["short_rate"]["rate"])
    gaps = missing_inputs(acq, tgt, inp)
    if gaps:
        raise SystemExit(f"Not enough data to run the model (missing: {', '.join(gaps)}); results would be N/A.")
    r = run(acq, tgt, inp)
    bn = lambda x: f"{x / 1e9:,.2f}bn"
    print(f"HYPOTHETICAL, FOR ILLUSTRATION ONLY: {acq['name']} acquires {tgt['name']}")
    for c in (acq, tgt):
        print(f"  {c['ticker']}: price ${c['price']:.2f}, EPS ${c['eps']:.2f} ({c['eps_source']}), P/E "
              f"{c['price'] / c['eps']:.1f}x, Year 2 growth {c['growth_y2']:.1%}, fiscal year ends {c['fiscal_year_end']}")
    print(f"Offer ${r['offer_price']:.2f} ({r['premium']:.0%} premium), equity {bn(r['equity_value'])}, offer P/E "
          f"{r['offer_pe']:.1f}x; {inp['stock_pct']:.0%} stock; new debt at {inp['debt_rate']:.2%}, cash earns "
          f"{inp['cash_rate']:.2%}; tax {inp['tax_rate']:.1%}")
    for side in ("uses", "sources"):
        print(f"  {side}: " + ", ".join(f"{k} {bn(v)}" for k, v in r["sources_uses"][side].items()))
    print(f"  new shares {r['new_shares'] / 1e6:,.1f}m; ownership buyer {r['ownership']['buyer']:.1%} / target "
          f"{r['ownership']['target']:.1%}")
    p = r["ppa"]
    print(f"  PPA: excess over book {bn(p['excess_over_book'])}, intangibles {bn(p['intangibles'])}, goodwill "
          f"{bn(p['goodwill'])}, amortisation {bn(p['annual_amortisation'])} a year")
    for y in r["years"]:
        print(f"Year {y['year']}: buyer NI {bn(y['buyer_ni'])} + target NI {bn(y['target_ni'])} + synergies "
              f"{bn(y['synergies'])} - new interest {bn(y['new_debt_interest'])} - lost interest "
              f"{bn(y['interest_lost_on_cash'])} (pre-tax) -> EPS incl. amortization ${y['eps_gaap']:.2f} vs ${y['standalone_eps']:.2f}"
              f" ({y['accretion_gaap_pct']:+.1%}); EPS excl. amortization ${y['eps_cash']:.2f} ({y['accretion_cash_pct']:+.1%}); "
              f"breakeven synergies {bn(y['breakeven_synergies'])} (run rate {bn(y['breakeven_run_rate'])})")
    print(f"Breakeven premium (Year 1, with synergies): {breakeven_premium(acq, tgt, inp):.1%}")
    c = r["credit"]
    print(f"Pro forma debt / EBITDA {c['debt_to_ebitda']:.2f}x, EBITDA / interest {c['ebitda_to_interest']:.1f}x; "
          f"one-off: fees {bn(r['one_off']['transaction_fees'])}, integration {bn(r['one_off']['integration_costs'])}")
    qc = quick_check(acq, tgt, inp, r)
    print(explain(qc, inp))
    for w in r["warnings"]:
        print(" !", w)


if __name__ == "__main__":
    main()
