"""
Leveraged buyout (LBO) analysis.

Question: if a private equity fund (the "sponsor") bought the company with
mostly borrowed money, what is the most it could pay per share and still earn
its target return?

  - Operations come from the DCF projection (dcf.project) of the chosen
    scenario: no second set of operating assumptions.
  - Entry: current price + a premium (or an entry EV/EBITDA multiple).
  - Sources & uses: what the purchase costs and how it is paid for.
  - Debt schedule: each tranche's interest, required repayments and a cash
    sweep (spare cash repays the prepayable debt).
  - Exit: EV = exit multiple x EBITDA in the exit year; sponsor equity =
    exit EV - net debt. Returns = IRR and MOIC.
  - Reverse: the highest price per share that still gives the target IRR.

Interest is charged on the debt at the START of each year, so the model (and
the Excel version) has no circular reference. Run `python lbo.py` for TGT.
"""

import copy
import math

import numpy as np
import pandas as pd

import config
import dcf

# Known simplifications, shown in the app, the Excel export and the README
SIMPLIFICATIONS = [
    "Interest is charged on the debt balance at the start of each year (no circular reference); "
    "repayments during the year do not lower that year's interest.",
    "No interest income on cash; no revolving credit facility, so a cash shortfall is only flagged.",
    "US interest deduction limit (section 163(j)), simplified: deductible interest is capped at 30% "
    "of EBITDA each year and the excess is ignored (in reality it carries forward to later years).",
    "Taxes are never negative and losses are not carried forward.",
    "Fees are paid at closing and not amortised; no management options or equity pool, no "
    "dividends or dividend recaps; the sponsor owns 100% of the equity.",
    "No tax or fees at exit; leases stay off balance sheet debt, as in the rest of the model.",
    "The exit multiple defaults to the target's current trading EV/EBITDA, not the entry multiple: "
    "the entry multiple includes the takeover premium, so exiting there assumes the fund sells above "
    "today's market value, and sponsors usually underwrite more cautiously. It is held fixed while "
    "solving for the maximum price; tying it to the entry price would let a higher purchase price "
    "raise its own assumed sale price, which makes the answer circular. Exiting at the entry "
    "multiple is shown in the sensitivity table.",
    "The floating base rate is the 13-week Treasury bill yield (^IRX), a proxy for SOFR, held flat "
    "for all years; the fixed rate is the 10-year Treasury plus a spread.",
]


def default_inputs(base_rate: float, treasury_10y: float) -> dict:
    """Every LBO input at its default (illustrative assumptions from config)."""
    tranches = []
    for t in config.LBO_TRANCHES:
        base = base_rate if t["rate_type"] == "floating" else treasury_10y
        tranches.append({**t, "base_rate": base})
    return {
        "premium": config.LBO_PREMIUM,
        "entry_multiple": None,           # set to value the entry at a multiple instead of a premium
        "exit_multiple": None,            # None = the target's current trading EV/EBITDA
        "exit_year": config.LBO_EXIT_YEAR,
        "tranches": tranches,
        "transaction_fee_pct": config.LBO_TRANSACTION_FEE_PCT,
        "financing_fee_pct": config.LBO_FINANCING_FEE_PCT,
        "min_cash_pct": config.LBO_MIN_CASH_PCT,
        "target_irr": config.LBO_TARGET_IRR,
    }


def tranche_rate(t: dict) -> float:
    return t["base_rate"] + t["spread"]


def current_multiple(values: dict) -> float:
    """The target's EV/EBITDA at today's share price (no premium)."""
    market_cap = values.get("market_cap") or values["price"] * values["shares_outstanding"]
    return (market_cap + values["net_debt"]) / values["ebitda_ttm"]


def entry_terms(values: dict, inputs: dict) -> dict:
    """Offer price, purchase equity value, entry EV and entry multiple.
    Entry EV = offer price x shares + existing net debt (the buyer takes on,
    and here repays, the debt)."""
    ebitda0, shares, price = values["ebitda_ttm"], values["shares_outstanding"], values["price"]
    if inputs.get("entry_multiple") is not None:
        ev = inputs["entry_multiple"] * ebitda0
        offer = (ev - values["net_debt"]) / shares
    else:
        offer = price * (1 + inputs["premium"])
        ev = offer * shares + values["net_debt"]
    return {"offer_price": offer, "premium": offer / price - 1, "equity_purchase": offer * shares,
            "entry_ev": ev, "entry_multiple": ev / ebitda0, "ebitda": ebitda0}


def sources_and_uses(values: dict, inputs: dict, entry: dict) -> dict:
    """Uses = what must be paid; sources = where the money comes from.
    The sponsor's equity is the plug that makes the two sides equal."""
    ebitda0 = values["ebitda_ttm"]
    debt = {t["name"]: t["multiple"] * ebitda0 for t in inputs["tranches"]}
    total_debt = sum(debt.values())
    uses = {
        "Purchase of equity": entry["equity_purchase"],
        "Repay existing debt": values["debt_for_ev"],
        "Transaction fees": inputs["transaction_fee_pct"] * entry["entry_ev"],
        "Financing fees": inputs["financing_fee_pct"] * total_debt,
    }
    min_cash0 = inputs["min_cash_pct"] * values["revenue_ttm"]
    cash_used = max(values["cash"] - min_cash0, 0.0)
    sources = {**debt, "Existing cash above minimum": cash_used}
    sources["Sponsor equity"] = sum(uses.values()) - sum(sources.values())
    return {"uses": uses, "sources": sources, "total": sum(uses.values()), "total_debt": total_debt,
            "sponsor_equity": sources["Sponsor equity"], "opening_cash": values["cash"] - cash_used,
            "debt": debt}


def run_lbo(run_kwargs: dict, values: dict, inputs: dict) -> dict:
    """Full LBO: entry, sources & uses, yearly schedule, exit, returns,
    credit statistics and warnings."""
    proj = dcf.project(run_kwargs["base_revenue"], run_kwargs["assumptions"])
    n = int(inputs["exit_year"])
    if not 1 <= n <= proj.shape[1]:
        raise ValueError(f"Exit year must be between 1 and {proj.shape[1]} (the projection length).")
    tax = dcf._per_year(run_kwargs["assumptions"]["tax_rate"], proj.shape[1])

    entry = entry_terms(values, inputs)
    su = sources_and_uses(values, inputs, entry)
    tranches = inputs["tranches"]
    bal = dict(su["debt"])
    cash = su["opening_cash"]

    rows = {}
    def put(key, value):
        rows.setdefault(key, []).append(value)

    for i in range(n):
        col = proj.iloc[:, i]
        interest = {t["name"]: tranche_rate(t) * bal[t["name"]] for t in tranches}
        total_interest = sum(interest.values())
        ebt = col["ebitda"] - col["d_and_a"] - total_interest
        deductible = min(total_interest, config.LBO_INTEREST_DEDUCTION_CAP * col["ebitda"])   # section 163(j)
        taxes = max(col["ebitda"] - col["d_and_a"] - deductible, 0.0) * tax[i]
        net_income = ebt - taxes
        fcf = net_income + col["d_and_a"] - col["capex"] - col["increase_in_nwc"]
        mandatory = {}
        for t in tranches:
            due = bal[t["name"]] if i + 1 >= t["maturity"] else t["amortization"] * su["debt"][t["name"]]
            mandatory[t["name"]] = min(bal[t["name"]], due)
        min_cash = inputs["min_cash_pct"] * col["revenue"]
        available = cash + fcf - sum(mandatory.values()) - min_cash
        sweep = {}
        for t in tranches:
            room = bal[t["name"]] - mandatory[t["name"]]
            sweep[t["name"]] = min(max(available, 0.0), room) if t.get("sweep") else 0.0
            available -= sweep[t["name"]]
        cash_begin = cash
        cash = cash + fcf - sum(mandatory.values()) - sum(sweep.values())

        put("Revenue", col["revenue"]); put("EBITDA", col["ebitda"]); put("D&A", col["d_and_a"])
        put("Interest", total_interest); put("Pre-tax income", ebt)
        put("Interest not deductible (163(j))", total_interest - deductible); put("Taxes", taxes)
        put("Net income", net_income); put("Capex", col["capex"])
        put("Increase in NWC", col["increase_in_nwc"]); put("Free cash flow (after interest)", fcf)
        put("Cash, start of year", cash_begin); put("Minimum cash", min_cash)
        for t in tranches:
            name = t["name"]
            put(f"{name}: start", bal[name]); put(f"{name}: interest", interest[name])
            put(f"{name}: required repayment", mandatory[name]); put(f"{name}: cash sweep", sweep[name])
            bal[name] -= mandatory[name] + sweep[name]
            put(f"{name}: end", bal[name])
        total_debt = sum(bal.values())
        put("Total debt, end", total_debt); put("Cash, end of year", cash)
        put("Net debt, end", total_debt - cash)
        put("Total debt / EBITDA", total_debt / col["ebitda"])
        put("Net debt / EBITDA", (total_debt - cash) / col["ebitda"])
        put("EBITDA / interest", col["ebitda"] / total_interest if total_interest else np.nan)
        put("Interest / EBITDA", total_interest / col["ebitda"])

    schedule = pd.DataFrame(rows, index=[f"Year {i + 1}" for i in range(n)]).T

    exit_multiple = inputs["exit_multiple"] if inputs.get("exit_multiple") is not None else current_multiple(values)
    exit_ebitda = schedule.loc["EBITDA"].iloc[-1]
    exit_ev = exit_multiple * exit_ebitda
    exit_net_debt = schedule.loc["Net debt, end"].iloc[-1]
    exit_equity = exit_ev - exit_net_debt
    equity_in = su["sponsor_equity"]
    returns = _returns(equity_in, exit_equity, n)

    result = {"entry": entry, "sources_uses": su, "schedule": schedule,
              "exit": {"year": n, "multiple": exit_multiple, "ebitda": exit_ebitda, "ev": exit_ev,
                       "net_debt": exit_net_debt, "equity": exit_equity},
              "returns": returns,
              "credit": {"entry_leverage": su["total_debt"] / entry["ebitda"],
                         "min_coverage": float(schedule.loc["EBITDA / interest"].min()),
                         "max_interest_share": float(schedule.loc["Interest / EBITDA"].max())},
              "inputs": inputs}
    result["warnings"] = lbo_warnings(result)
    return result


def _returns(equity_in: float, equity_out: float, years: int) -> dict:
    """MOIC = money out / money in. IRR = the yearly rate that turns the
    money in into the money out: with no cash in between, MOIC^(1/years) - 1
    (the same as Excel's IRR on -equity in, 0, ..., +equity out)."""
    if not equity_in or equity_in <= 0:
        return {"equity_in": equity_in, "equity_out": equity_out, "moic": np.nan, "irr": np.nan}
    moic = equity_out / equity_in
    irr = moic ** (1 / years) - 1 if moic > 0 else -1.0
    return {"equity_in": equity_in, "equity_out": equity_out, "moic": moic, "irr": irr}


def lbo_warnings(r: dict) -> list:
    out = []
    c, s = r["credit"], r["schedule"]
    if c["entry_leverage"] > config.LBO_MAX_LEVERAGE:
        out.append(f"Total debt is {c['entry_leverage']:.1f}x EBITDA at entry, above the "
                   f"{config.LBO_MAX_LEVERAGE:.0f}x that US bank regulators' leveraged lending guidance (2013) "
                   "says raises concerns for most industries.")
    low = [y for y, v in s.loc["EBITDA / interest"].items() if v < config.LBO_MIN_INTEREST_COVERAGE]
    if low:
        out.append(f"Interest coverage (EBITDA / interest) is below {config.LBO_MIN_INTEREST_COVERAGE:.0f}x in "
                   f"{', '.join(low)}: little room if EBITDA falls.")
    capped = s.loc["Interest not deductible (163(j))"]
    if (capped > 0).any():
        years = [y for y, v in capped.items() if v > 0]
        out.append(f"Interest is above {config.LBO_INTEREST_DEDUCTION_CAP:.0%} of EBITDA in {', '.join(years)}: "
                   "under US tax law (section 163(j)) the excess is not deductible, so taxes are higher. The "
                   "model ignores the excess instead of carrying it forward (slightly conservative).")
    short = [y for y, v in s.loc["Cash, end of year"].items() if v < 0]
    if short:
        out.append(f"Cash turns negative in {', '.join(short)}: the debt cannot be serviced without a "
                   "revolving credit facility (not modelled).")
    if r["sources_uses"]["sponsor_equity"] <= 0:
        out.append("The debt covers the whole price: no sponsor equity is needed, so IRR is not meaningful.")
    elif r["exit"]["equity"] <= 0:
        out.append("The debt left at exit is larger than the exit value: the sponsor's equity is wiped out.")
    return out


# ---------------------------------------------------------------------------
# Reverse: highest price for a target IRR
# ---------------------------------------------------------------------------

def _irr_at_price(run_kwargs, values, inputs, price):
    inp = {**inputs, "entry_multiple": None, "premium": price / values["price"] - 1}
    r = run_lbo(run_kwargs, values, inp)
    if r["sources_uses"]["sponsor_equity"] <= 0:
        return math.inf
    return r["returns"]["irr"]


def max_price(run_kwargs: dict, values: dict, inputs: dict, target_irr: float = None,
              tol: float = 1e-4) -> float:
    """Highest offer price per share at which the sponsor still earns
    `target_irr`. The exit multiple is fixed at its value in the run with the
    user's inputs (by default the current trading multiple; see SIMPLIFICATIONS). NaN if no price in a wide range works."""
    target_irr = inputs["target_irr"] if target_irr is None else target_irr
    base = run_lbo(run_kwargs, values, inputs)
    fixed = {**inputs, "exit_multiple": base["exit"]["multiple"]}
    lo, hi = values["price"] * 0.01, values["price"] * 5
    f_lo = _irr_at_price(run_kwargs, values, fixed, lo)
    f_hi = _irr_at_price(run_kwargs, values, fixed, hi)
    if not (f_lo >= target_irr >= f_hi):
        return np.nan
    for _ in range(200):                    # IRR falls as the price rises
        mid = (lo + hi) / 2
        if _irr_at_price(run_kwargs, values, fixed, mid) >= target_irr:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def price_range(run_kwargs: dict, values: dict, inputs: dict) -> dict:
    """Max price at the high IRR (low end), the target IRR (mid) and the low IRR (high end)."""
    lo_irr, hi_irr = config.LBO_IRR_RANGE
    return {"low": max_price(run_kwargs, values, inputs, hi_irr),
            "mid": max_price(run_kwargs, values, inputs, inputs["target_irr"]),
            "high": max_price(run_kwargs, values, inputs, lo_irr),
            "irr_range": (lo_irr, hi_irr)}


def with_base_rate_shift(inputs: dict, shift: float) -> dict:
    """Same inputs with every rate's base (floating and fixed) moved by `shift`."""
    out = copy.deepcopy(inputs)
    for t in out["tranches"]:
        t["base_rate"] += shift
    return out


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------

def feasibility(r: dict) -> list:
    """Compare the size of the deal with the largest sponsor buyouts on record."""
    su, ev = r["sources_uses"], r["entry"]["entry_ev"]
    biggest = config.LBO_LARGEST_DEALS[0]
    lines = [f"Entry enterprise value ${ev / 1e9:.0f}bn, new debt ${su['total_debt'] / 1e9:.0f}bn and sponsor "
             f"equity ${su['sponsor_equity'] / 1e9:.0f}bn."]
    lines.append(f"For comparison, {biggest['name']}: {biggest['note']}. TXU (2007): "
                 f"{config.LBO_LARGEST_DEALS[1]['note']}.")
    ratio = ev / biggest["ev"]
    if ratio > 1:
        lines.append(f"This deal would be {ratio:.1f}x the largest sponsor buyout on record, with "
                     f"{su['sponsor_equity'] / biggest['equity']:.1f}x its equity cheque and "
                     f"{su['total_debt'] / biggest['debt']:.1f}x its debt. Read the result as a valuation "
                     "yardstick (what a financial buyer could justify), not as a realistic deal.")
    else:
        lines.append("The size is within the range of deals that have been financed before.")
    return lines


# ---------------------------------------------------------------------------
# Command-line preview (TGT, Base scenario)
# ---------------------------------------------------------------------------

def main():
    import data_loader as dl
    import summary

    data = dl.load_all()
    choices = summary.default_choices(data)
    kw = summary.build_model(data, choices)["run_kwargs"]
    values = data["companies"][config.TARGET]["values"]
    short = dl.get_short_rate()
    inputs = default_inputs(short["rate"], data["risk_free"]["rate"])
    r = run_lbo(kw, values, inputs)
    bn = lambda x: f"{x / 1e9:,.2f}"

    e = r["entry"]
    print(f"Base rate (^IRX, SOFR proxy) {short['rate']:.2%} ({short.get('date')}), "
          f"10y Treasury {data['risk_free']['rate']:.2%}")
    print(f"Offer ${e['offer_price']:.2f} ({e['premium']:.0%} premium), entry EV {bn(e['entry_ev'])}bn "
          f"= {e['entry_multiple']:.2f}x EBITDA {bn(e['ebitda'])}bn")
    su = r["sources_uses"]
    print("\nUses ($bn)");  [print(f"  {k:32s}{bn(v):>10s}") for k, v in su["uses"].items()]
    print("Sources ($bn)"); [print(f"  {k:32s}{bn(v):>10s}  {v / su['total']:.0%}") for k, v in su["sources"].items()]
    print(f"  Total {bn(su['total'])}bn")
    for t in inputs["tranches"]:
        print(f"  {t['name']}: {t['multiple']}x, rate {tranche_rate(t):.2%}")
    s = r["schedule"].copy()
    ratio_rows = ["Total debt / EBITDA", "Net debt / EBITDA", "EBITDA / interest", "Interest / EBITDA"]
    print("\n" + (s.drop(index=ratio_rows) / 1e9).round(2).to_string())
    print(s.loc[ratio_rows].round(2).to_string())
    x, ret = r["exit"], r["returns"]
    print(f"\nExit Year {x['year']}: {x['multiple']:.2f}x x {bn(x['ebitda'])} = EV {bn(x['ev'])}bn, net debt "
          f"{bn(x['net_debt'])}bn, equity {bn(x['equity'])}bn")
    print(f"Equity in {bn(ret['equity_in'])}bn -> MOIC {ret['moic']:.2f}x, IRR {ret['irr']:.1%}")
    rng = price_range(kw, values, inputs)
    print(f"Max price: 25% IRR ${rng['low']:.2f}, 20% IRR ${rng['mid']:.2f}, 15% IRR ${rng['high']:.2f} "
          f"(price ${values['price']:.2f})")
    low = with_base_rate_shift(inputs, -0.02)
    print(f"Base rates 2 points lower: 20% IRR max price ${max_price(kw, values, low):.2f}")
    print("\nWarnings:"); [print(" -", w) for w in r["warnings"]]
    print("\nFeasibility:"); [print(" -", f) for f in feasibility(r)]


if __name__ == "__main__":
    main()
