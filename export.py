"""Stage 4: Excel export.

The DCF and WACC sheets are built with live Excel formulas, so you can change an
input (blue text, yellow fill) and watch the value per share update, exactly
like a banker's model. Every other sheet holds values: they are the output of
the Python analysis at the moment of export.

Money is in millions of the target's trading currency, per-share values in
that currency (the Market data sheet shows each company in its own currency).
"""

from __future__ import annotations

import io
from datetime import datetime

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import comps
import config
import data_loader as dl
import dcf
import lbo
import precedents
import scenarios
import summary

M = 1e6   # money in the workbook is in millions

# Number formats (zeros show as "-", negatives in parentheses)
FMT_MONEY = '#,##0;(#,##0);"-"'
FMT_NUM2 = '#,##0.00;(#,##0.00);"-"'


def price_format(currency: str) -> str:
    """Per-share number format with the currency symbol, e.g. $1,234.50 or "CHF "1,234.50."""
    sym = config.currency_symbol(currency).replace('"', '')
    return f'"{sym}"#,##0.00;("{sym}"#,##0.00);"-"'
FMT_PCT = '0.0%;(0.0%);"-"'
FMT_MULT = '0.0"x";(0.0"x");"-"'
FMT_NUM = '0.000'
FMT_PCT2 = '0.00%;(0.00%);"-"'

# Styles: blue = input, black = formula, green = link to another sheet
FONT = Font(name="Arial", size=10)
BOLD = Font(name="Arial", size=10, bold=True)
TITLE = Font(name="Arial", size=14, bold=True)
NOTE = Font(name="Arial", size=9, italic=True, color="595959")
INPUT = Font(name="Arial", size=10, color="0000FF")
LINK = Font(name="Arial", size=10, color="008000")
INPUT_FILL = PatternFill("solid", fgColor="FFFF99")
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
TOTAL_BORDER = Border(top=Side(style="thin"))

ASSUMPTION_ROWS = list(dcf.ASSUMPTION_LABELS)   # revenue_growth, ebitda_margin, da, capex, nwc, tax


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clean(x):
    """openpyxl cannot write NaN; an empty cell is the honest equivalent."""
    if x is None:
        return None
    if isinstance(x, (float, np.floating)) and np.isnan(x):
        return None
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, pd.Timestamp):
        return x.to_pydatetime()
    return x


def _put(ws, row, col, value, fmt=None, font=FONT, fill=None, bold=False):
    cell = ws.cell(row=row, column=col, value=_clean(value))
    # Bridge-style labels such as "= Equity value" are text, not formulas.
    # (Our formulas never have a space after the "=".)
    if isinstance(value, str) and value.startswith("= "):
        cell.data_type = "s"
    cell.font = BOLD if bold else font
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = fill
    return cell


def _header(ws, row, labels, start_col=1):
    for i, label in enumerate(labels):
        c = _put(ws, row, start_col + i, label, bold=True, fill=HEADER_FILL)
        c.alignment = Alignment(horizontal="center" if i else "left", wrap_text=True)


def _title(ws, text, subtitle=None):
    _put(ws, 1, 1, text, font=TITLE)
    if subtitle:
        _put(ws, 2, 1, subtitle, font=NOTE)


def _widths(ws, widths: dict):
    for col, w in widths.items():
        ws.column_dimensions[col].width = w


def _write_frame(ws, df: pd.DataFrame, top: int, formats: dict = None, index_label: str = "",
                 default_fmt=None) -> int:
    """Write a DataFrame with its index as the first column. `formats` maps a
    column (or index value, for row-wise formats) to a number format.
    Returns the next free row."""
    formats = formats or {}
    _header(ws, top, [index_label] + [str(c) for c in df.columns])
    for i, (idx, row) in enumerate(df.iterrows(), start=1):
        _put(ws, top + i, 1, idx)
        for j, col in enumerate(df.columns, start=2):
            fmt = formats.get(col, formats.get(idx, default_fmt))
            _put(ws, top + i, j, row[col], fmt)
    return top + len(df) + 2


# ---------------------------------------------------------------------------
# WACC sheet (live formulas)
# ---------------------------------------------------------------------------

# Cell addresses other sheets refer to
WACC_CELL = "WACC!$C$18"


def _wacc_sheet(wb, model: dict, target: str, history_kd: float, ccy: str, rf_note: str):
    ws = wb.create_sheet("WACC")
    wi = model["wacc_inputs"]
    _title(ws, f"{target} - Weighted average cost of capital",
           f"Blue cells on yellow are inputs: change them and every formula updates. Money in {ccy} millions.")
    _header(ws, 4, ["Item", "", "Value", "Formula / source"])
    rows = [
        # row, label, value-or-formula, format, is_input, note
        (5, "Risk-free rate (10y government bond)", wi["risk_free"], FMT_PCT, True, rf_note),
        (6, "Beta", wi["beta"], FMT_NUM, True, "Yahoo: 5-year monthly beta vs the S&P 500"),
        (7, "Equity risk premium", wi["equity_risk_premium"], FMT_PCT, True, "Assumption"),
        (8, "Country risk premium", wi["country_risk_premium"], FMT_PCT, True, "0 for the US"),
        (9, "Cost of equity (CAPM)", "=C5+C6*C7+C8", FMT_PCT, False, "= risk-free + beta x ERP + CRP"),
        (11, "Credit spread", model["credit_spread"], FMT_PCT, True,
         f"Assumption. Historical interest expense / debt: {history_kd:.1%} (reference only)"
         if not np.isnan(history_kd) else "Assumption"),
        (12, "Pre-tax cost of debt", "=C5+C11", FMT_PCT, False, "= risk-free + credit spread"),
        (13, "Tax rate", wi["tax_rate"], FMT_PCT, True, "Same as the DCF tax rate assumption"),
        (14, "After-tax cost of debt", "=C12*(1-C13)", FMT_PCT, False, "= pre-tax cost of debt x (1 - tax)"),
        (15, f"Equity value (market cap, {ccy}m)", wi["equity_value"] / M, FMT_MONEY, True, "Yahoo, live"),
        (16, f"Debt ({ccy}m, excl. lease liabilities)", wi["debt_value"] / M, FMT_MONEY, True,
         "Total debt - lease liabilities (ASC 842: rent is already in EBITDA)"),
        (17, "Weight of equity E / (D + E)", "=C15/(C15+C16)", FMT_PCT, False, "Market-value weights"),
        # No risk-free rate entered -> #N/A instead of silently treating the empty cell as 0%
        (18, "WACC", '=IF(ISBLANK(C5),NA(),C17*C9+(1-C17)*C14)', FMT_PCT, False,
         "= E/(D+E) x Ke + D/(D+E) x Kd after tax (#N/A until a risk-free rate is entered)"),
    ]
    for r, label, value, fmt, is_input, note in rows:
        _put(ws, r, 1, label, bold=(r in (9, 14, 18)))
        if is_input:
            _put(ws, r, 3, value, fmt, font=INPUT, fill=INPUT_FILL)
        else:
            _put(ws, r, 3, value, fmt, bold=(r in (9, 14, 18)))
        _put(ws, r, 4, note, font=NOTE)
    ws["C18"].border = TOTAL_BORDER
    _widths(ws, {"A": 36, "B": 2, "C": 14, "D": 70})
    return ws


# ---------------------------------------------------------------------------
# DCF sheet (live formulas)
# ---------------------------------------------------------------------------

# Scenarios sheet layout (rows): per-year inputs for each scenario in columns B/C/D
SC_COLS = {"bear": "B", "base": "C", "bull": "D"}
SC_GROWTH, SC_MARGIN, SC_CAPEX = 5, 10, 15            # first row of each 5-year block
SC_TERMINAL, SC_EXIT, SC_WEIGHT, SC_WEIGHT_SUM = 20, 21, 22, 23
SC_SWITCH = "$B$13"                                     # on the DCF sheet


def _dcf_sheet(wb, model: dict, target: str, ccy: str, switch: int = 2):
    ws = wb.create_sheet("DCF")
    kw = model["run_kwargs"]
    years = config.PROJECTION_YEARS
    assumptions = {k: dcf._per_year(kw["assumptions"][k], years) for k in ASSUMPTION_ROWS}
    first, last = 3, 3 + years - 1                     # projection columns C..G
    col = get_column_letter
    L = col(last)

    _title(ws, f"{target} - Discounted cash flow",
           f"Blue cells on yellow are inputs. Money in {ccy} millions, per-share values in {ccy}. "
           "FCFF = EBIT x (1 - tax) + D&A - capex - increase in NWC.")

    # --- Inputs ------------------------------------------------------------
    _header(ws, 4, ["Inputs", "Value", "Note"])
    inputs = [
        (5, f"Base revenue (TTM, {ccy}m)", kw["base_revenue"] / M, FMT_MONEY, "Sum of the last 4 quarters"),
        (6, "Terminal growth rate", f"=INDEX(Scenarios!$B${SC_TERMINAL}:$D${SC_TERMINAL},1,{SC_SWITCH})",
         FMT_PCT, "Selected scenario (Scenarios sheet)"),
        (7, "Exit EV/EBITDA multiple", f"=INDEX(Scenarios!$B${SC_EXIT}:$D${SC_EXIT},1,{SC_SWITCH})",
         FMT_MULT, "Selected scenario (Scenarios sheet)"),
        (8, f"Net debt ({ccy}m)", kw["net_debt"] / M, FMT_MONEY, "Debt excl. leases - cash"),
        (9, "Shares outstanding (m)", kw["shares"] / M, FMT_MONEY, "Yahoo"),
        (10, f"Current share price ({ccy})", kw["price"], price_format(ccy), "Yahoo"),
        (11, "Mid-year convention (1 = on, 0 = off)", 1 if kw.get("mid_year", config.MID_YEAR_CONVENTION) else 0,
         "0", "Cash flows arrive during the year, not at its end"),
    ]
    for r, label, value, fmt, note in inputs:
        _put(ws, r, 1, label)
        if isinstance(value, str) and value.startswith("="):
            _put(ws, r, 2, value, fmt, font=LINK)
        else:
            _put(ws, r, 2, value, fmt, font=INPUT, fill=INPUT_FILL)
        _put(ws, r, 3, note, font=NOTE)
    _put(ws, 12, 1, "WACC")
    _put(ws, 12, 2, f"={WACC_CELL}", FMT_PCT, font=LINK)
    _put(ws, 12, 3, "From the WACC sheet", font=NOTE)
    _put(ws, 13, 1, "Scenario (1 = Bear, 2 = Base, 3 = Bull)", bold=True)
    _put(ws, 13, 2, switch, "0", font=INPUT, fill=INPUT_FILL, bold=True)
    _put(ws, 13, 3, "Switch: growth, margin, capex, terminal growth and exit multiple follow it", font=NOTE)
    dv = DataValidation(type="whole", operator="between", formula1="1", formula2="3", showErrorMessage=True,
                        error="Enter 1 (Bear), 2 (Base) or 3 (Bull)")
    ws.add_data_validation(dv)
    dv.add("B13")

    # --- Assumptions per year (inputs) --------------------------------------
    _header(ws, 14, ["Assumptions", "TTM"] + [f"Year {i}" for i in range(1, years + 1)])
    _put(ws, 15, 1, "Year number")
    for i in range(years):
        _put(ws, 15, first + i, i + 1, "0")
    arow = {}
    for i, k in enumerate(ASSUMPTION_ROWS):
        r = 16 + i
        arow[k] = r
        _put(ws, r, 1, dcf.ASSUMPTION_LABELS[k])
        block = {"revenue_growth": SC_GROWTH, "ebitda_margin": SC_MARGIN, "capex_pct": SC_CAPEX}.get(k)
        for j, value in enumerate(assumptions[k]):
            if block:   # scenario assumption: picked from the Scenarios sheet by the switch
                sr = block + j
                _put(ws, r, first + j, f"=INDEX(Scenarios!$B${sr}:$D${sr},1,{SC_SWITCH})", FMT_PCT, font=LINK)
            else:       # shared by all scenarios
                _put(ws, r, first + j, value, FMT_PCT, font=INPUT, fill=INPUT_FILL)
    ws.cell(row=arow["revenue_growth"], column=1).comment = Comment(
        "From the Scenarios sheet (selected scenario). Base default: Year 1 = TTM revenue vs last fiscal "
        "year, straight line to the Year 5 growth (default = terminal growth).", "model")
    ws.cell(row=arow["ebitda_margin"], column=1).comment = Comment(
        f"From the Scenarios sheet (selected scenario). Base default: Year 1 = TTM margin (one-offs removed), "
        f"straight line to the last {config.DEFAULT_HISTORY_YEARS} fiscal years' average.", "model")

    # --- Projection (formulas) ----------------------------------------------
    top = 16 + len(ASSUMPTION_ROWS) + 1       # 23
    _header(ws, top, [f"Projection ({ccy}m)", "TTM"] + [f"Year {i}" for i in range(1, years + 1)])
    p = {name: top + 1 + i for i, name in enumerate(
        ["revenue", "ebitda", "d_and_a", "ebit", "taxes", "nopat", "capex", "nwc", "fcff",
         "period", "factor", "pv"])}
    labels = {"revenue": "Revenue", "ebitda": "EBITDA", "d_and_a": "D&A", "ebit": "EBIT",
              "taxes": "Taxes on EBIT", "nopat": "NOPAT = EBIT x (1 - tax)", "capex": "Capex",
              "nwc": "Increase in net working capital", "fcff": "Free cash flow to the firm (FCFF)",
              "period": "Discount period (years)", "factor": "Discount factor", "pv": "PV of FCFF"}
    for name, r in p.items():
        _put(ws, r, 1, labels[name], bold=name in ("fcff", "pv"))
    _put(ws, p["revenue"], 2, "=$B$5", FMT_MONEY)
    for j in range(years):
        c, prev = col(first + j), col(first + j - 1)
        f = {
            "revenue": (f"={prev}{p['revenue']}*(1+{c}{arow['revenue_growth']})", FMT_MONEY),
            "ebitda": (f"={c}{p['revenue']}*{c}{arow['ebitda_margin']}", FMT_MONEY),
            "d_and_a": (f"={c}{p['revenue']}*{c}{arow['da_pct']}", FMT_MONEY),
            "ebit": (f"={c}{p['ebitda']}-{c}{p['d_and_a']}", FMT_MONEY),
            "taxes": (f"={c}{p['ebit']}*{c}{arow['tax_rate']}", FMT_MONEY),
            "nopat": (f"={c}{p['ebit']}-{c}{p['taxes']}", FMT_MONEY),
            "capex": (f"={c}{p['revenue']}*{c}{arow['capex_pct']}", FMT_MONEY),
            "nwc": (f"={c}{p['revenue']}*{c}{arow['nwc_pct']}", FMT_MONEY),
            "fcff": (f"={c}{p['nopat']}+{c}{p['d_and_a']}-{c}{p['capex']}-{c}{p['nwc']}", FMT_MONEY),
            "period": (f"={c}$15-0.5*$B$11", "0.0"),
            "factor": (f"=1/(1+$B$12)^{c}{p['period']}", FMT_NUM),
            "pv": (f"={c}{p['fcff']}*{c}{p['factor']}", FMT_MONEY),
        }
        for name, (formula, fmt) in f.items():
            cell = _put(ws, p[name], first + j, formula, fmt, bold=name in ("fcff", "pv"))
            if name == "fcff":
                cell.border = TOTAL_BORDER

    # --- Valuation (formulas) -------------------------------------------------
    v0 = p["pv"] + 2                          # 37
    _header(ws, v0, ["Valuation", "Gordon growth", "Exit multiple"])
    v = {name: v0 + 1 + i for i, name in enumerate(
        ["sum_pv", "tv", "tv_period", "pv_tv", "ev", "net_debt", "equity", "shares", "per_share",
         "price", "upside", "tv_share", "cross"])}
    vlabels = {"sum_pv": "Sum of PV of FCFF", "tv": "Terminal value",
               "tv_period": "Terminal value discount period", "pv_tv": "PV of terminal value",
               "ev": "= Enterprise value", "net_debt": "- Net debt", "equity": "= Equity value",
               "shares": "/ Shares outstanding (m)", "per_share": f"= Value per share ({ccy})",
               "price": f"Current share price ({ccy})", "upside": "Upside / (downside)",
               "tv_share": "Terminal value share of EV",
               "cross": "Cross-check: implied exit multiple / implied growth"}
    for name, r in v.items():
        _put(ws, r, 1, vlabels[name], bold=name in ("ev", "equity", "per_share"))
    pv_range = f"{col(first)}{p['pv']}:{L}{p['pv']}"
    fcff_n, ebitda_n = f"{L}{p['fcff']}", f"{L}{p['ebitda']}"
    gordon = {
        "sum_pv": (f"=SUM({pv_range})", FMT_MONEY),
        # Gordon is only defined when WACC > g; otherwise show #N/A rather than a wrong number
        "tv": (f"=IF($B$12>$B$6,{fcff_n}*(1+$B$6)/($B$12-$B$6),NA())", FMT_MONEY),
        "tv_period": (f"={L}{p['period']}", "0.0"),
    }
    exit_ = {
        "sum_pv": (f"=SUM({pv_range})", FMT_MONEY),
        "tv": (f"={ebitda_n}*$B$7", FMT_MONEY),
        "tv_period": (f"={L}$15", "0.0"),
    }
    for c, d in (("B", gordon), ("C", exit_)):
        d.update({
            "pv_tv": (f"={c}{v['tv']}/(1+$B$12)^{c}{v['tv_period']}", FMT_MONEY),
            "ev": (f"={c}{v['sum_pv']}+{c}{v['pv_tv']}", FMT_MONEY),
            "net_debt": ("=$B$8", FMT_MONEY),
            "equity": (f"={c}{v['ev']}-{c}{v['net_debt']}", FMT_MONEY),
            "shares": ("=$B$9", FMT_MONEY),
            "per_share": (f"={c}{v['equity']}/{c}{v['shares']}", price_format(ccy)),
            "price": ("=$B$10", price_format(ccy)),
            "upside": (f"={c}{v['per_share']}/{c}{v['price']}-1", FMT_PCT),
            "tv_share": (f"={c}{v['pv_tv']}/{c}{v['ev']}", FMT_PCT),
        })
    gordon["cross"] = (f"=B{v['tv']}/{ebitda_n}", FMT_MULT)                       # implied exit multiple
    exit_["cross"] = (f"=(C{v['tv']}*$B$12-{fcff_n})/(C{v['tv']}+{fcff_n})", FMT_PCT)  # implied growth
    for c, d in (("B", gordon), ("C", exit_)):
        ci = 2 if c == "B" else 3
        for name, (formula, fmt) in d.items():
            cell = _put(ws, v[name], ci, formula, fmt, bold=name in ("ev", "equity", "per_share"))
            if name in ("ev", "per_share"):
                cell.border = TOTAL_BORDER
    _put(ws, v["cross"] + 2, 1,
         "Gordon terminal value is discounted at N - 0.5 with the mid-year convention (it is a stream of "
         "cash flows); the exit multiple terminal value at N (a sale price at the end of year N).", font=NOTE)
    _put(ws, v["cross"] + 3, 1,
         f"Warning sign: terminal value above {config.TERMINAL_VALUE_WARNING:.0%} of enterprise value means "
         "the result depends heavily on long-term assumptions.", font=NOTE)
    _widths(ws, {"A": 44, "B": 15, "C": 15, "D": 13, "E": 13, "F": 13, "G": 13})
    ws.freeze_panes = "B5"
    return ws, v, p, arow


def _lbo_sheet(wb, values: dict, inputs: dict, result: dict, dcf_rows: dict, dcf_arow: dict, target: str,
               ccy: str, scenario: str):
    """LBO with live formulas. Operating lines link to the DCF sheet (which follows
    the scenario switch); everything else is calculated here. Interest is charged
    on opening balances, so there is no circular reference."""
    ws = wb.create_sheet("LBO", 2)
    col = get_column_letter
    n = result["exit"]["year"]
    first, last = 3, 3 + n - 1                      # Year 1..N in columns C.. (same letters as the DCF sheet)
    L = col(last)
    tranches = inputs["tranches"]
    _title(ws, f"{target} - Leveraged buyout (LBO)",
           f"Blue cells on yellow are inputs; debt terms and fees are ILLUSTRATIVE ASSUMPTIONS. Money in {ccy} "
           f"millions. Operating lines link to the DCF sheet ({scenario} scenario at export: change DCF!B13 to switch).")

    # --- Inputs ----------------------------------------------------------------
    _header(ws, 4, ["Inputs", "Value", "Note"])
    e = result["entry"]
    rows = [
        (5, f"Current share price ({ccy})", "=DCF!B10", price_format(ccy), "DCF sheet"),
        (6, "Shares outstanding (m)", "=DCF!B9", FMT_MONEY, "DCF sheet"),
        (7, f"Net debt ({ccy}m)", "=DCF!B8", FMT_MONEY, "DCF sheet: debt excl. leases - cash"),
        (8, f"Existing debt repaid ({ccy}m)", values["debt_for_ev"] / M, FMT_MONEY, "Financial debt, excl. leases"),
        (9, f"Existing cash ({ccy}m)", values["cash"] / M, FMT_MONEY, "Yahoo"),
        (10, f"TTM EBITDA ({ccy}m)", values["ebitda_ttm"] / M, FMT_MONEY, "One-offs removed; debt is sized on it"),
        (11, f"TTM revenue ({ccy}m)", "=DCF!B5", FMT_MONEY, "DCF sheet"),
        (12, "Premium over the current price", e["premium"], FMT_PCT, "Illustrative assumption"),
        (13, f"Offer price per share ({ccy})", "=B5*(1+B12)", price_format(ccy), ""),
        (14, f"Entry enterprise value ({ccy}m)", "=B13*B6+B7", FMT_MONEY, "Offer price x shares + net debt"),
        (15, "Entry EV / EBITDA", "=B14/B10", FMT_MULT, ""),
        (16, "Transaction fees (% of entry EV)", inputs["transaction_fee_pct"], FMT_PCT, "Illustrative assumption"),
        (17, "Financing fees (% of new debt)", inputs["financing_fee_pct"], FMT_PCT, "Illustrative assumption"),
        (18, "Minimum cash (% of revenue)", inputs["min_cash_pct"], FMT_PCT, "Illustrative assumption"),
        (19, "Exit EV / EBITDA", result["exit"]["multiple"], FMT_MULT,
         "Default: the target's current trading multiple (no premium)"),
        (20, "Target IRR", inputs["target_irr"], FMT_PCT, "The fund's required return"),
        (21, "Deductible interest cap (% of EBITDA)", config.LBO_INTEREST_DEDUCTION_CAP, FMT_PCT,
         "US section 163(j), simplified: the excess is ignored"),
    ]
    for r, label, value, fmt, note in rows:
        _put(ws, r, 1, label)
        formula = isinstance(value, str) and value.startswith("=")
        if formula:
            _put(ws, r, 2, value, fmt, font=LINK if "DCF!" in value else FONT)
        else:
            _put(ws, r, 2, value, fmt, font=INPUT, fill=INPUT_FILL)
        _put(ws, r, 3, note, font=NOTE)

    # --- Debt tranches -----------------------------------------------------------
    t0 = 23
    _header(ws, t0, ["Debt tranche", "x EBITDA", "Base rate", "Spread", "Interest rate", "Amortization (% a year)",
                     "Maturity (years)", "Cash sweep (1 = yes)", f"Amount ({ccy}m)"])
    trow = {}
    for i, t in enumerate(tranches):
        r = t0 + 1 + i
        trow[t["name"]] = r
        _put(ws, r, 1, f"{t['name']} ({t['rate_type']})")
        for c, v, f in [(2, t["multiple"], FMT_MULT), (3, t["base_rate"], FMT_PCT2), (4, t["spread"], FMT_PCT2),
                        (6, t["amortization"], FMT_PCT), (7, t["maturity"], "0"), (8, 1 if t.get("sweep") else 0, "0")]:
            _put(ws, r, c, v, f, font=INPUT, fill=INPUT_FILL)
        _put(ws, r, 5, f"=C{r}+D{r}", FMT_PCT2)
        _put(ws, r, 9, f"=B{r}*$B$10", FMT_MONEY)
    tot = t0 + 1 + len(tranches)
    _put(ws, tot, 1, "Total new debt", bold=True)
    _put(ws, tot, 2, f"=SUM(B{t0 + 1}:B{tot - 1})", FMT_MULT, bold=True)
    _put(ws, tot, 9, f"=SUM(I{t0 + 1}:I{tot - 1})", FMT_MONEY, bold=True)
    _put(ws, tot + 1, 1, "Floating base rate: 13-week T-bill (^IRX), a proxy for SOFR. Fixed: 10-year Treasury.",
         font=NOTE)

    # --- Sources and uses --------------------------------------------------------
    s0 = tot + 3
    _header(ws, s0, ["Uses", f"Amount ({ccy}m)", "", "Sources", f"Amount ({ccy}m)", "% of total"])
    uses = [("Purchase of equity", "=B13*B6"), ("Repay existing debt", "=B8"),
            ("Transaction fees", "=B16*B14"), ("Financing fees", f"=B17*I{tot}")]
    for i, (label, f) in enumerate(uses):
        _put(ws, s0 + 1 + i, 1, label)
        _put(ws, s0 + 1 + i, 2, f, FMT_MONEY)
    total_uses = s0 + 1 + len(uses)
    _put(ws, total_uses, 1, "Total uses", bold=True)
    _put(ws, total_uses, 2, f"=SUM(B{s0 + 1}:B{total_uses - 1})", FMT_MONEY, bold=True).border = TOTAL_BORDER
    sources = [(t["name"], f"=I{trow[t['name']]}") for t in tranches]
    sources.append(("Existing cash above minimum", "=MAX(B9-B18*B11,0)"))
    for i, (label, f) in enumerate(sources):
        _put(ws, s0 + 1 + i, 4, label)
        _put(ws, s0 + 1 + i, 5, f, FMT_MONEY)
    sponsor = s0 + 1 + len(sources)
    cash_used = sponsor - 1
    _put(ws, sponsor, 4, "Sponsor equity (balancing item)", bold=True)
    _put(ws, sponsor, 5, f"=B{total_uses}-SUM(E{s0 + 1}:E{sponsor - 1})", FMT_MONEY, bold=True)
    total_src = sponsor + 1
    _put(ws, total_src, 4, "Total sources", bold=True)
    _put(ws, total_src, 5, f"=SUM(E{s0 + 1}:E{sponsor})", FMT_MONEY, bold=True).border = TOTAL_BORDER
    for r in range(s0 + 1, total_src + 1):
        _put(ws, r, 6, f"=E{r}/E${total_src}", FMT_PCT)
    opening_cash = max(total_uses, total_src) + 1
    _put(ws, opening_cash, 1, "Cash after closing")
    _put(ws, opening_cash, 2, f"=B9-E{cash_used}", FMT_MONEY)

    # --- Schedule ------------------------------------------------------------------
    h = opening_cash + 2
    _header(ws, h, [f"Cash flow and debt schedule ({ccy}m)", "Closing"] + [f"Year {i}" for i in range(1, n + 1)])
    names = ["year", "revenue", "ebitda", "da", "capex", "nwc", "tax_rate"]
    names += [f"int_{i}" for i in range(len(tranches))]
    names += ["interest", "pretax", "deductible", "not_deductible", "taxes", "net_income", "fcf", "cash_start",
              "min_cash", "required", "available"]
    for i in range(len(tranches)):
        names += [f"start_{i}", f"req_{i}", f"sweep_{i}", f"end_{i}"]
    names += ["cash_end", "debt_end", "net_debt_end", "lev", "net_lev", "cover"]
    R = {name: h + 1 + i for i, name in enumerate(names)}
    labels = {"year": "Year number", "revenue": "Revenue (DCF sheet)", "ebitda": "EBITDA (DCF sheet)",
              "da": "D&A (DCF sheet)", "capex": "Capex (DCF sheet)", "nwc": "Increase in NWC (DCF sheet)",
              "tax_rate": "Tax rate (DCF sheet)", "interest": "Total interest", "pretax": "Pre-tax income",
              "deductible": "Deductible interest (163(j) cap)", "not_deductible": "Interest not deductible",
              "taxes": "Taxes", "net_income": "Net income",
              "fcf": "Free cash flow after interest", "cash_start": "Cash, start of year",
              "min_cash": "Minimum cash", "required": "Required repayments", "available": "Cash available for sweep",
              "cash_end": "Cash, end of year", "debt_end": "Total debt, end of year",
              "net_debt_end": "Net debt, end of year", "lev": "Total debt / EBITDA", "net_lev": "Net debt / EBITDA",
              "cover": "EBITDA / interest"}
    for i, t in enumerate(tranches):
        labels.update({f"int_{i}": f"{t['name']}: interest", f"start_{i}": f"{t['name']}: start of year",
                       f"req_{i}": f"{t['name']}: required repayment", f"sweep_{i}": f"{t['name']}: cash sweep",
                       f"end_{i}": f"{t['name']}: end of year"})
    for name, r in R.items():
        _put(ws, r, 1, labels[name], bold=name in ("fcf", "debt_end", "cash_end"))
    for i, t in enumerate(tranches):
        _put(ws, R[f"end_{i}"], 2, f"=I{trow[t['name']]}", FMT_MONEY)
    _put(ws, R["cash_end"], 2, f"=B{opening_cash}", FMT_MONEY)
    _put(ws, R["debt_end"], 2, f"=I{tot}", FMT_MONEY)

    link = {"revenue": "revenue", "ebitda": "ebitda", "da": "d_and_a", "capex": "capex", "nwc": "nwc"}
    for j in range(n):
        c, prev = col(first + j), col(first + j - 1)
        _put(ws, R["year"], first + j, j + 1, "0")
        for name, key in link.items():
            _put(ws, R[name], first + j, f"=DCF!{c}{dcf_rows[key]}", FMT_MONEY, font=LINK)
        _put(ws, R["tax_rate"], first + j, f"=DCF!{c}{dcf_arow['tax_rate']}", FMT_PCT, font=LINK)
        for i, t in enumerate(tranches):
            tr = trow[t["name"]]
            _put(ws, R[f"int_{i}"], first + j, f"=$E${tr}*{c}{R[f'start_{i}']}", FMT_MONEY)
            _put(ws, R[f"start_{i}"], first + j, f"={prev}{R[f'end_{i}']}", FMT_MONEY)
            _put(ws, R[f"req_{i}"], first + j,
                 f"=MIN({c}{R[f'start_{i}']},IF({c}${R['year']}>=$G${tr},{c}{R[f'start_{i}']},$F${tr}*$I${tr}))",
                 FMT_MONEY)
            earlier = "+".join(f"{c}{R[f'sweep_{k}']}" for k in range(i)) or "0"
            _put(ws, R[f"sweep_{i}"], first + j,
                 f"=IF($H${tr}=1,MIN(MAX({c}{R['available']}-({earlier}),0),{c}{R[f'start_{i}']}-{c}{R[f'req_{i}']}),0)",
                 FMT_MONEY)
            _put(ws, R[f"end_{i}"], first + j,
                 f"={c}{R[f'start_{i}']}-{c}{R[f'req_{i}']}-{c}{R[f'sweep_{i}']}", FMT_MONEY)
        ints = "+".join(f"{c}{R[f'int_{i}']}" for i in range(len(tranches)))
        reqs = "+".join(f"{c}{R[f'req_{i}']}" for i in range(len(tranches)))
        sweeps = "+".join(f"{c}{R[f'sweep_{i}']}" for i in range(len(tranches)))
        ends = "+".join(f"{c}{R[f'end_{i}']}" for i in range(len(tranches)))
        f = {
            "interest": f"={ints}",
            "pretax": f"={c}{R['ebitda']}-{c}{R['da']}-{c}{R['interest']}",
            "deductible": f"=MIN({c}{R['interest']},$B$21*{c}{R['ebitda']})",
            "not_deductible": f"={c}{R['interest']}-{c}{R['deductible']}",
            "taxes": f"=MAX({c}{R['ebitda']}-{c}{R['da']}-{c}{R['deductible']},0)*{c}{R['tax_rate']}",
            "net_income": f"={c}{R['pretax']}-{c}{R['taxes']}",
            "fcf": f"={c}{R['net_income']}+{c}{R['da']}-{c}{R['capex']}-{c}{R['nwc']}",
            "cash_start": f"={prev}{R['cash_end']}",
            "min_cash": f"=$B$18*{c}{R['revenue']}",
            "required": f"={reqs}",
            "available": f"={c}{R['cash_start']}+{c}{R['fcf']}-{c}{R['required']}-{c}{R['min_cash']}",
            "cash_end": f"={c}{R['cash_start']}+{c}{R['fcf']}-{c}{R['required']}-({sweeps})",
            "debt_end": f"={ends}",
            "net_debt_end": f"={c}{R['debt_end']}-{c}{R['cash_end']}",
            "lev": f"={c}{R['debt_end']}/{c}{R['ebitda']}",
            "net_lev": f"={c}{R['net_debt_end']}/{c}{R['ebitda']}",
            "cover": f"={c}{R['ebitda']}/{c}{R['interest']}",
        }
        for name, formula in f.items():
            fmt = FMT_MULT if name in ("lev", "net_lev", "cover") else FMT_MONEY
            cell = _put(ws, R[name], first + j, formula, fmt, bold=name in ("fcf", "debt_end", "cash_end"))
            if name in ("fcf", "debt_end"):
                cell.border = TOTAL_BORDER

    # --- Exit and returns ----------------------------------------------------------
    x0 = R["cover"] + 2
    _header(ws, x0, ["Exit and returns", "Value"])
    xr = {name: x0 + 1 + i for i, name in enumerate(
        ["ebitda", "multiple", "ev", "net_debt", "equity", "equity_in", "moic", "irr"])}
    exit_rows = {
        "ebitda": (f"EBITDA, Year {n}", f"={L}{R['ebitda']}", FMT_MONEY),
        "multiple": ("x Exit EV / EBITDA", "=B19", FMT_MULT),
        "ev": ("= Exit enterprise value", f"=B{x0 + 1}*B{x0 + 2}", FMT_MONEY),
        "net_debt": ("- Net debt at exit", f"={L}{R['net_debt_end']}", FMT_MONEY),
        "equity": ("= Sponsor equity at exit", f"=B{x0 + 3}-B{x0 + 4}", FMT_MONEY),
        "equity_in": ("Sponsor equity at entry", f"=E{sponsor}", FMT_MONEY),
        "moic": ("MOIC (money out / money in)", f"=B{x0 + 5}/B{x0 + 6}", '0.00"x"'),
        "irr": ("IRR (Excel IRR of the cash flows below)", None, FMT_PCT),
    }
    for name, (label, formula, fmt) in exit_rows.items():
        _put(ws, xr[name], 1, label, bold=name in ("equity", "irr", "moic"))
        if formula:
            _put(ws, xr[name], 2, formula, fmt, bold=name in ("equity", "moic"))
    cf = xr["irr"] + 2
    _header(ws, cf, ["Sponsor cash flows", "Closing"] + [f"Year {i}" for i in range(1, n + 1)])
    _put(ws, cf + 1, 1, f"Equity in (-) / out (+) ({ccy}m)")
    _put(ws, cf + 1, 2, f"=-B{xr['equity_in']}", FMT_MONEY)
    for j in range(n):
        _put(ws, cf + 1, first + j, f"=B{xr['equity']}" if j == n - 1 else 0, FMT_MONEY)
    _put(ws, xr["irr"], 2, f"=IRR(B{cf + 1}:{L}{cf + 1})", FMT_PCT, bold=True).border = TOTAL_BORDER

    # --- Maximum price -------------------------------------------------------------
    m0 = cf + 3
    _header(ws, m0, ["Maximum price per share", "Target IRR", "Equity needed", f"Max price ({ccy})", "vs price"])
    lo_irr, hi_irr = config.LBO_IRR_RANGE
    fixed = f"(B8+B16*B7+B{s0 + 4}-I{tot}-E{cash_used})"
    for i, (label, irr) in enumerate([("At the high IRR", hi_irr), ("At the target IRR", "=B20"), ("At the low IRR", lo_irr)]):
        r = m0 + 1 + i
        _put(ws, r, 1, label)
        _put(ws, r, 2, irr, FMT_PCT, **({} if isinstance(irr, str) else {"font": INPUT, "fill": INPUT_FILL}))
        _put(ws, r, 3, f"=B{xr['equity']}/(1+B{r})^{n}", FMT_MONEY)
        _put(ws, r, 4, f"=(C{r}-{fixed})/(B6*(1+B16))", price_format(ccy), bold=True)
        _put(ws, r, 5, f"=D{r}/B5-1", FMT_PCT)
    notes = ["Maximum price: with the exit multiple fixed, the cash flows and the exit equity do not depend on the "
             "price; the price only changes the sponsor's cheque. Equity needed = exit equity / (1 + IRR)^years; "
             "price = (equity needed - existing debt - fees on net debt - financing fees + new debt + cash used) "
             "/ (shares x (1 + transaction fee %)).", *lbo.SIMPLIFICATIONS]
    for i, t in enumerate(notes):
        _put(ws, m0 + 5 + i, 1, t, font=NOTE)
    _widths(ws, {"A": 42, "B": 13, "C": 13, "D": 30, "E": 13, "F": 13, "G": 13, "H": 13, "I": 13})
    ws.freeze_panes = "B5"
    return {"irr": f"B{xr['irr']}", "moic": f"B{xr['moic']}", "equity_in": f"E{sponsor}",
            "max_price": f"D{m0 + 2}", "max_price_low": f"D{m0 + 1}", "max_price_high": f"D{m0 + 3}",
            "debt_end": f"{L}{R['debt_end']}", "taxes_1": f"C{R['taxes']}"}


def _scenarios_sheet(wb, runs: dict, sc_defaults: dict, weights: dict, target: str, ccy: str):
    """Inputs of the three scenarios (live: the DCF sheet reads the selected one),
    and the three scenarios valued side by side with live formulas, so the
    results update when any input changes - without touching the switch."""
    ws = wb.create_sheet("Scenarios", 2)
    years = config.PROJECTION_YEARS
    col = get_column_letter
    _title(ws, f"{target} - Scenarios (Bear / Base / Bull)",
           "Blue cells on yellow are inputs. The DCF sheet uses the scenario chosen in its switch cell (B13); "
           "the results below value all three at once. WACC, D&A, working capital and tax are shared (DCF sheet).")
    _header(ws, 4, ["Scenario inputs", "Bear", "Base", "Bull", "Why (Bear / Bull default)"])
    blocks = [("revenue_growth", SC_GROWTH, "Revenue growth", "growth_start"),
              ("ebitda_margin", SC_MARGIN, "EBITDA margin", "margin_end"),
              ("capex_pct", SC_CAPEX, "Capex (% of revenue)", "capex_pct")]
    for key, row0, label, why in blocks:
        for j in range(years):
            _put(ws, row0 + j, 1, f"{label}, Year {j + 1}")
            for sc, c in SC_COLS.items():
                v = dcf._per_year(runs[sc]["run_kwargs"]["assumptions"][key], years)[j]
                _put(ws, row0 + j, ord(c) - 64, v, FMT_PCT, font=INPUT, fill=INPUT_FILL)
        _put(ws, row0, 5, sc_defaults["rationale"][why], font=NOTE)
    for row, label, key, fmt, why in [(SC_TERMINAL, "Terminal growth", "terminal_growth", FMT_PCT, "terminal_growth"),
                                      (SC_EXIT, "Exit EV/EBITDA", "exit_multiple", FMT_MULT, "exit_multiple")]:
        _put(ws, row, 1, label)
        for sc, c in SC_COLS.items():
            _put(ws, row, ord(c) - 64, runs[sc]["run_kwargs"][key], fmt, font=INPUT, fill=INPUT_FILL)
        _put(ws, row, 5, sc_defaults["rationale"][why], font=NOTE)
    _put(ws, SC_WEIGHT, 1, "Probability weight")
    for sc, c in SC_COLS.items():
        _put(ws, SC_WEIGHT, ord(c) - 64, weights[sc], FMT_PCT, font=INPUT, fill=INPUT_FILL)
    _put(ws, SC_WEIGHT_SUM, 1, "Sum of weights (must be 100%)")
    _put(ws, SC_WEIGHT_SUM, 2, f"=SUM(B{SC_WEIGHT}:D{SC_WEIGHT})", FMT_PCT)

    # --- Calculation blocks: one compact DCF per scenario -------------------------
    calc0, size = 40, 20
    D = "DCF!"
    first, last = 3, 2 + years
    L = col(last)
    out = {}
    for idx, (sc, scol) in enumerate(SC_COLS.items()):
        b = calc0 + idx * size
        _header(ws, b, [f"{config.SCENARIO_LABELS[sc]} calculation ({ccy}m)", "TTM"]
                + [f"Year {i}" for i in range(1, years + 1)])
        r = {name: b + 1 + i for i, name in enumerate(
            ["revenue", "ebitda", "d_and_a", "ebit", "taxes", "nopat", "capex", "nwc", "fcff", "pv", "sum_pv",
             "tv_g", "tv_x", "pv_tv_g", "pv_tv_x", "value_g", "value_x"])}
        labels = {"revenue": "Revenue", "ebitda": "EBITDA", "d_and_a": "D&A", "ebit": "EBIT", "taxes": "Taxes on EBIT",
                  "nopat": "NOPAT", "capex": "Capex", "nwc": "Increase in NWC", "fcff": "FCFF",
                  "pv": "PV of FCFF (DCF discount factors)", "sum_pv": "Sum of PV of FCFF",
                  "tv_g": "Terminal value: Gordon", "tv_x": "Terminal value: exit multiple",
                  "pv_tv_g": "PV of terminal value: Gordon", "pv_tv_x": "PV of terminal value: exit multiple",
                  "value_g": f"Value per share: Gordon ({ccy})", "value_x": f"Value per share: exit multiple ({ccy})"}
        for name, row in r.items():
            _put(ws, row, 1, labels[name], bold=name.startswith("value"))
        _put(ws, r["revenue"], 2, f"={D}$B$5", FMT_MONEY, font=LINK)
        for j in range(years):
            c, prev = col(first + j), col(first + j - 1)
            g, m, cx = f"${scol}${SC_GROWTH + j}", f"${scol}${SC_MARGIN + j}", f"${scol}${SC_CAPEX + j}"
            cells = {
                "revenue": f"={prev}{r['revenue']}*(1+{g})",
                "ebitda": f"={c}{r['revenue']}*{m}",
                "d_and_a": f"={c}{r['revenue']}*{D}{c}$18",
                "ebit": f"={c}{r['ebitda']}-{c}{r['d_and_a']}",
                "taxes": f"={c}{r['ebit']}*{D}{c}$21",
                "nopat": f"={c}{r['ebit']}-{c}{r['taxes']}",
                "capex": f"={c}{r['revenue']}*{cx}",
                "nwc": f"={c}{r['revenue']}*{D}{c}$20",
                "fcff": f"={c}{r['nopat']}+{c}{r['d_and_a']}-{c}{r['capex']}-{c}{r['nwc']}",
                "pv": f"={c}{r['fcff']}*{D}{c}$34",
            }
            for name, formula in cells.items():
                _put(ws, r[name], first + j, formula, FMT_MONEY)
        tg, ex = f"${scol}${SC_TERMINAL}", f"${scol}${SC_EXIT}"
        fcff_n, ebitda_n = f"{L}{r['fcff']}", f"{L}{r['ebitda']}"
        singles = {
            "sum_pv": (f"=SUM({col(first)}{r['pv']}:{L}{r['pv']})", FMT_MONEY),
            "tv_g": (f"=IF({D}$B$12>{tg},{fcff_n}*(1+{tg})/({D}$B$12-{tg}),NA())", FMT_MONEY),
            "tv_x": (f"={ebitda_n}*{ex}", FMT_MONEY),
            "pv_tv_g": (f"=B{r['tv_g']}/(1+{D}$B$12)^{D}{L}$33", FMT_MONEY),
            "pv_tv_x": (f"=B{r['tv_x']}/(1+{D}$B$12)^{D}{L}$15", FMT_MONEY),
            "value_g": (f"=(B{r['sum_pv']}+B{r['pv_tv_g']}-{D}$B$8)/{D}$B$9", price_format(ccy)),
            "value_x": (f"=(B{r['sum_pv']}+B{r['pv_tv_x']}-{D}$B$8)/{D}$B$9", price_format(ccy)),
        }
        for name, (formula, fmt) in singles.items():
            _put(ws, r[name], 2, formula, fmt, bold=name.startswith("value"))
        out[sc] = r

    # --- Results side by side -------------------------------------------------------
    top = 26
    _header(ws, top, [f"Results ({ccy})", "Bear", "Base", "Bull", "Probability-weighted"])
    res = [("Value per share: Gordon growth", "value_g", price_format(ccy)),
           ("  vs current price", "up_g", FMT_PCT),
           ("Value per share: exit multiple", "value_x", price_format(ccy)),
           ("  vs current price", "up_x", FMT_PCT),
           (f"Revenue, Year {years} ({ccy}m)", "revenue", FMT_MONEY),
           (f"EBITDA, Year {years} ({ccy}m)", "ebitda", FMT_MONEY),
           (f"FCFF, Year {years} ({ccy}m)", "fcff", FMT_MONEY)]
    for i, (label, key, fmt) in enumerate(res, start=top + 1):
        _put(ws, i, 1, label, bold=key.startswith("value"))
        for sc, scol in SC_COLS.items():
            r = out[sc]
            if key in ("value_g", "value_x"):
                f = f"=B{r[key]}"
            elif key == "up_g":
                f = f"=B{r['value_g']}/{D}$B$10-1"
            elif key == "up_x":
                f = f"=B{r['value_x']}/{D}$B$10-1"
            else:
                f = f"={L}{r[key]}"
            _put(ws, i, ord(scol) - 64, f, fmt, bold=key.startswith("value"))
        if key in ("value_g", "value_x"):
            # Weights must add up to 100%, otherwise #N/A (never rescaled)
            _put(ws, i, 5, f"=IF(ABS($B${SC_WEIGHT_SUM}-1)>0.000001,NA(),SUMPRODUCT($B${SC_WEIGHT}:$D${SC_WEIGHT},B{i}:D{i}))",
                 fmt, bold=True)
        elif key in ("up_g", "up_x"):
            _put(ws, i, 5, f"=E{i - 1}/{D}$B$10-1", fmt)
    _put(ws, top + len(res) + 2, 1, "Probability-weighted value = sum of each scenario's value x its weight; "
                                    "#N/A until the weights add up to 100%.", font=NOTE)
    _widths(ws, {"A": 40, "B": 14, "C": 14, "D": 14, "E": 16, "F": 13, "G": 13})
    ws.column_dimensions["E"].width = 16
    return ws


# ---------------------------------------------------------------------------
# Value sheets
# ---------------------------------------------------------------------------

def _summary_sheet(wb, data, model, ff, reverse, results, target, peers, ccy, sc_table=None):
    ws = wb.active
    ws.title = "Summary"
    v = data["companies"][target]["values"]
    rf = data["risk_free"]
    _title(ws, f"{summary.company_name(data, target)} ({target}) - valuation summary",
           f"Exported {datetime.now():%Y-%m-%d %H:%M}. Peers: {', '.join(peers)}. Values on this sheet are "
           "a snapshot; the DCF and WACC sheets are live formulas.")
    _put(ws, 4, 1, f"Current share price ({ccy})")
    _put(ws, 4, 2, v["price"], price_format(ccy))
    _put(ws, 5, 1, f"Market cap ({ccy}m)")
    _put(ws, 5, 2, v["market_cap"] / M, FMT_MONEY)
    _put(ws, 6, 1, f"Risk-free rate ({rf['date'] or 'entered by the user'})")
    _put(ws, 6, 2, model["wacc_inputs"]["risk_free"], FMT_PCT)
    _put(ws, 7, 1, "WACC")
    _put(ws, 7, 2, model["wacc"]["wacc"], FMT_PCT)

    _header(ws, 9, [f"Football field: value per share ({ccy})", "Low", "Mid", "High", "Bear", "Bull", "Basis"])
    for i, (_, r) in enumerate(ff.iterrows(), start=10):
        _put(ws, i, 1, r["label"])
        for j, k in enumerate(["low", "mid", "high", "bear", "bull"], start=2):
            _put(ws, i, j, r.get(k), price_format(ccy))
        _put(ws, i, 7, r["basis"], font=NOTE)
    row = 10 + len(ff) + 1
    pb_note = ("P/B is included because the target is a financial company." if summary.is_financial(data, target)
               else "P/B is not in the football field: see the note on the Comps sheet.")
    _put(ws, row, 1, f"Mid = peer median (comps) or base case (DCF). {pb_note}", font=NOTE)

    row += 2
    _header(ws, row, ["DCF (base case)", "Gordon growth", "Exit multiple"])
    for i, (label, key, fmt) in enumerate([(f"Value per share ({ccy})", "value_per_share", price_format(ccy)),
                                           ("Upside / (downside)", "upside", FMT_PCT),
                                           ("Terminal value share of EV", "tv_share_of_ev", FMT_PCT)],
                                          start=row + 1):
        _put(ws, i, 1, label)
        _put(ws, i, 2, results["gordon"][key], fmt)
        _put(ws, i, 3, results["exit"][key], fmt)

    row += 5
    _header(ws, row, ["Reverse DCF: what today's price implies", "Value", "Model"])
    kw = model["run_kwargs"]
    for i, (label, key, fmt, base) in enumerate([
            ("WACC (Gordon growth)", "wacc", FMT_PCT, kw["wacc_rate"]),
            ("Terminal growth (Gordon growth)", "terminal_growth", FMT_PCT, kw["terminal_growth"]),
            ("Exit EV/EBITDA multiple", "exit_multiple", FMT_MULT, kw["exit_multiple"])], start=row + 1):
        _put(ws, i, 1, label)
        _put(ws, i, 2, reverse[key], fmt)
        _put(ws, i, 3, base, fmt)
    _put(ws, row + 4, 1, "Each line changes one input until the model value equals the current price.",
         font=NOTE)
    row += 6
    if sc_table is not None:
        _header(ws, row, ["Scenarios (values at export; live on the Scenarios sheet)", "Bear", "Base", "Bull",
                          "Probability-weighted"])
        for i, (label, key) in enumerate([("Value per share: Gordon growth", "gordon_value"),
                                          ("Value per share: exit multiple", "exit_value")], start=row + 1):
            _put(ws, i, 1, label)
            for j, c in enumerate(list(config.SCENARIOS) + ["weighted"], start=2):
                _put(ws, i, j, sc_table.loc[key, c], price_format(ccy))
        row += 4
    for i, w in enumerate(results["warnings"], start=row):
        _put(ws, i, 1, "Warning: " + w, font=NOTE)
    _widths(ws, {"A": 44, "B": 14, "C": 14, "D": 14, "E": 14, "F": 14, "G": 80})


def _sensitivity_sheet(wb, tables, price, ccy):
    ws = wb.create_sheet("Sensitivity")
    _title(ws, f"DCF sensitivity: value per share ({ccy})",
           f"Values at export (not live). Current share price {config.currency_symbol(ccy)}{price:,.2f}.")
    row = 4
    for method, title, colfmt in [("gordon", "WACC (rows) x terminal growth (columns)", FMT_PCT),
                                  ("exit", "WACC (rows) x exit EV/EBITDA (columns)", FMT_MULT)]:
        t = tables[method]
        _put(ws, row, 1, title, bold=True)
        row += 1
        _put(ws, row, 1, "WACC", bold=True, fill=HEADER_FILL)
        for j, x in enumerate(t.columns, start=2):
            _put(ws, row, j, x, colfmt, bold=True, fill=HEADER_FILL)
        for i, w in enumerate(t.index, start=row + 1):
            _put(ws, i, 1, w, FMT_PCT, bold=True)
            for j, x in enumerate(t.columns, start=2):
                _put(ws, i, j, t.loc[w, x], price_format(ccy))
        row += len(t) + 3
    _widths(ws, {"A": 12})


def _comps_sheet(wb, data, target, peers):
    ws = wb.create_sheet("Comps")
    table, notes = comps.multiples_table(data["companies"])
    stats = comps.peer_stats(table, peers)
    _title(ws, "Trading comparables (TTM)", f"Target {target} is shown but never part of the peer statistics.")
    fmts = {c: (FMT_PCT if c in comps.MARGINS else FMT_MULT) for c in table.columns}
    view = table.rename(columns=comps.LABELS)
    vfmt = {comps.LABELS[k]: f for k, f in fmts.items()}
    view.index = [f"{t} (target)" if t == target else t for t in view.index]
    row = _write_frame(ws, view, 4, vfmt, "Company")
    _put(ws, row - 1, 1, "Empty cell = not meaningful (NM), see exclusions below.", font=NOTE)
    stat_view = stats.rename(columns=comps.LABELS)
    count = stat_view.loc[["count"]]
    stat_view = stat_view.drop(index="count")
    row = _write_frame(ws, stat_view, row + 1, vfmt, "Peer statistics")
    _put(ws, row - 1, 1, "Valid peers: " + ", ".join(f"{c} {int(n)}" for c, n in count.iloc[0].items()),
         font=NOTE)
    row += 1
    _put(ws, row, 1, "Notes", bold=True)
    for k, text in comps.MULTIPLE_NOTES.items():
        row += 1
        _put(ws, row, 1, f"{comps.LABELS[k]}: {text}", font=NOTE)
    row += 2
    _put(ws, row, 1, "Excluded values", bold=True)
    if notes.empty:
        _put(ws, row + 1, 1, "None - all multiples were usable.", font=NOTE)
    for i, (_, n) in enumerate(notes.iterrows(), start=row + 1):
        _put(ws, i, 1, n["ticker"])
        _put(ws, i, 2, n["reason"])
    _widths(ws, {"A": 24, **{get_column_letter(i): 14 for i in range(2, 9)}})


def _bridge_sheet(wb, data, target, peers, ccy):
    ws = wb.create_sheet("Bridge")
    b = comps.bridge_table(data["companies"], target, peers)
    _title(ws, f"Implied value bridge for {target} ({ccy}m unless stated)",
           "EV multiples: metric x multiple = EV, - net debt = equity, / shares = value per share. "
           "P/E and P/B give equity value directly.")
    _header(ws, 4, ["Step"] + [f"{m} - {case}" for m, case in b.columns])
    for i, step in enumerate(b.index, start=5):
        _put(ws, i, 1, step)
        for j, colkey in enumerate(b.columns, start=2):
            x = b.loc[step, colkey]
            if "multiple" in step:
                _put(ws, i, j, x, FMT_MULT)
            elif "Upside" in step:
                _put(ws, i, j, x, FMT_PCT)
            elif "per share" in step:
                _put(ws, i, j, x, price_format(ccy))
            else:
                _put(ws, i, j, None if pd.isna(x) else x / M, FMT_MONEY)
    _widths(ws, {"A": 30, **{get_column_letter(i): 15 for i in range(2, 2 + len(b.columns))}})


def _precedents_sheet(wb, deals: pd.DataFrame, mask: pd.Series, applies: bool, target_values: dict,
                      target: str, ccy: str):
    ws = wb.create_sheet("Precedents")
    _title(ws, "Precedent transactions: EV / LTM EBITDA (values at export)",
           "Amounts in millions of the deal currency. Only EV/EBITDA is used: it is the standard yardstick in "
           "acquisitions, and EV/Sales would compare businesses with very different margins.")
    _header(ws, 4, ["In calculation", "Announced", "Target", "Acquirer", "Status", "Tier", "Type", "EV (m)", "Currency",
                    "EV / LTM EBITDA", "LTM EBITDA (m)", "Premium (info only)", "Source", "Notes"])
    for i, (idx, d) in enumerate(deals.iterrows(), start=5):
        values = ["Yes" if mask[idx] else "No", d["announcement_date"].strftime("%Y-%m-%d"), d["target"], d["acquirer"],
                  d["status"], d["tier"], d["deal_type"], d["enterprise_value"], d["currency"], d["ev_ebitda"],
                  d["ltm_ebitda"], None if pd.isna(d["premium_pct"]) else d["premium_pct"] / 100, d["source_url"],
                  d["notes"]]
        fmts = [None, None, None, None, None, None, None, FMT_MONEY, None, FMT_MULT, FMT_MONEY, FMT_PCT, None, None]
        for j, (v, f) in enumerate(zip(values, fmts), start=1):
            _put(ws, i, j, v, f, font=NOTE if j == 14 else FONT)
    row = 5 + len(deals) + 1
    _put(ws, row, 1, "Premiums are for information only: they are measured against different reference prices "
                     "(a single close, 10- to 30-day averages, or a price months before the announcement).", font=NOTE)
    row += 2
    included = deals[mask]
    if not applies:
        _put(ws, row, 1, f"Not used for {target}: the deals in the calculation are from other sub-sectors.", bold=True)
    elif included.empty:
        _put(ws, row, 1, "No deal in the calculation.", bold=True)
    else:
        v = precedents.valuation(target_values, included)
        if v["small_sample"]:
            _put(ws, row, 1, f"Small sample: only {int(v['stats']['count'])} deals in the calculation.", bold=True)
            row += 1
        _header(ws, row, ["EV / LTM EBITDA of the deals in the calculation", "Value"])
        for i, k in enumerate(["count", "min", "p25", "median", "mean", "p75", "max"], start=row + 1):
            _put(ws, i, 1, k)
            _put(ws, i, 2, v["stats"][k], "0" if k == "count" else FMT_MULT)
        row += 10
        b = v["bridge"]
        _header(ws, row, [f"Implied value for {target} ({ccy}m unless stated)", "Low", "Median", "High"])
        lines = [("Target metric (TTM EBITDA)", [b["metric"] / M] * 3, FMT_MONEY),
                 ("x Precedent multiple", b["multiples"], FMT_MULT),
                 ("= Implied enterprise value", [x / M for x in b["implied_ev"]], FMT_MONEY),
                 ("- Net debt", [b["net_debt"] / M] * 3, FMT_MONEY),
                 ("= Implied equity value", [x / M for x in b["equity_value"]], FMT_MONEY),
                 ("/ Shares outstanding (m)", [b["shares"] / M] * 3, FMT_MONEY),
                 (f"= Implied value per share ({ccy})", b["per_share"], price_format(ccy))]
        for i, (label, vals, f) in enumerate(lines, start=row + 1):
            _put(ws, i, 1, label, bold=label.startswith("= Implied value"))
            for j, x in enumerate(vals, start=2):
                _put(ws, i, j, x, f)
        _put(ws, row + len(lines) + 1, 1, f"Range: {v['method'].replace('peers', 'deals')}.", font=NOTE)
    _widths(ws, {"A": 34, "B": 12, "C": 22, "D": 30, "E": 11, "F": 10, "G": 16, "H": 10, "I": 9, "J": 10,
                 "K": 12, "L": 12, "M": 40, "N": 120})


def _market_data_sheet(wb, data):
    ws = wb.create_sheet("Market data")
    companies = data["companies"]
    _title(ws, "Market and financial data (millions of each company's trading currency; per-share fields "
               "in that currency, see the Currency row)",
           "Source: a = Yahoo Finance, m = manual override (see Data quality for the source note), "
           "d = derived by us. TTM = sum of the last 4 quarters.")
    tickers = list(companies)
    _header(ws, 4, ["Field"] + tickers + ["Source " + t for t in tickers])
    _put(ws, 5, 1, "currency", bold=True)
    for j, t in enumerate(tickers, start=2):
        _put(ws, 5, j, companies[t]["meta"].get("currency") or "N/A", bold=True)
    per_share = {"price", "low_52w", "high_52w", "book_value_per_share"}
    for i, field in enumerate(dl.ALL_FIELDS, start=6):
        _put(ws, i, 1, field)
        for j, t in enumerate(tickers, start=2):
            x = companies[t]["values"][field]
            if field in per_share:
                _put(ws, i, j, x, FMT_NUM2)
            else:
                _put(ws, i, j, None if dl.is_missing(x) else x / M, FMT_MONEY)
            _put(ws, i, j + len(tickers), companies[t]["sources"][field])
    _widths(ws, {"A": 24, **{get_column_letter(i): 12 for i in range(2, 2 + 2 * len(tickers))}})


def _quality_sheet(wb, data):
    ws = wb.create_sheet("Data quality")
    q = dl.quality_table(data["companies"])
    _title(ws, "Data quality report", "Fields with status OK and no comment are included for completeness.")
    _header(ws, 4, ["Ticker", "Field", "Source", "Status", "Comment"])
    for i, (_, r) in enumerate(q.iterrows(), start=5):
        for j, k in enumerate(["ticker", "field", "source", "status", "comment"], start=1):
            _put(ws, i, j, r[k], bold=(k == "status" and r[k] == "Suspicious"))
    _widths(ws, {"A": 8, "B": 22, "C": 9, "D": 11, "E": 120})


def _history_sheet(wb, data, target, reference):
    ws = wb.create_sheet("History")
    c = data["companies"][target]
    ccy = c["meta"].get("currency") or ""
    _title(ws, f"{target} annual history ({ccy} millions) and the ratios behind the DCF defaults",
           "Fiscal years are labelled by their end date.")
    h = c["history"].sort_index().copy()
    h.index = [d.strftime("FY end %Y-%m") for d in h.index]
    row = _write_frame(ws, (h / M).T, 4, index_label="Line item", default_fmt=FMT_MONEY)
    ratios = dcf.historical_ratios(c["history"])
    ratios.index = [d.strftime("FY end %Y-%m") for d in ratios.index]
    ratios = ratios.T.rename(index=dcf.ASSUMPTION_LABELS)
    row = _write_frame(ws, ratios, row, index_label="Ratio", default_fmt=FMT_PCT)
    ref = reference.rename(index=dcf.ASSUMPTION_LABELS)
    n = config.DEFAULT_HISTORY_YEARS
    ref.columns = ["All years (growth: CAGR)", f"Last {n} years", "Latest year", "TTM"]
    _write_frame(ws, ref, row, index_label="Reference", default_fmt=FMT_PCT)
    _widths(ws, {"A": 42, **{get_column_letter(i): 16 for i in range(2, 8)}})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_workbook(data: dict, choices: dict, target: str = None, peers: list = None,
                   scenario_values: dict = None, weights: dict = None, scenario_switch: int = 2,
                   precedent_deals: pd.DataFrame = None, precedent_settings: dict = None,
                   lbo_inputs: dict = None) -> bytes:
    """The full model as an .xlsx file (bytes), for st.download_button or a file.
    `choices` are the app's (Base) inputs (summary.default_choices() for the defaults);
    `scenario_values` the Bear/Base/Bull values (scenarios.default_scenarios() if omitted);
    `scenario_switch` the scenario the DCF sheet shows (1 Bear, 2 Base, 3 Bull), also the LBO's operations;
    `lbo_inputs` the LBO tab's inputs (lbo.default_inputs() with today's multiple as exit if omitted)."""
    target = target or config.TARGET
    peers = peers if peers is not None else [t for t in data["companies"] if t != target]
    ccy = summary.target_currency(data, target)
    model = summary.build_model(data, choices, target)
    kw = model["run_kwargs"]
    results = dcf.run_dcf(**kw)
    reverse = summary.reverse_dcf(kw)
    has_wacc = not pd.isna(kw["wacc_rate"])
    tables = dcf.sensitivity_tables(kw) if has_wacc else None
    defaults = dcf.default_inputs(data, target, peers)
    sc_defaults = scenarios.default_scenarios(data, target, peers, choices)
    scenario_values = scenario_values or {sc: sc_defaults[sc] for sc in config.SCENARIOS}
    weights = weights or dict(config.SCENARIO_WEIGHTS)
    runs = scenarios.run_scenarios(data, choices, scenario_values, target)
    prec_all = precedent_deals if precedent_deals is not None else precedents.load_precedents()
    prec_settings = precedent_settings or {}
    prec_mask = precedents.included_mask(prec_all, **prec_settings)
    target_values = data["companies"][target]["values"]
    prec_applies = precedents.applicable(data["companies"][target]["meta"], target, prec_all[prec_mask])
    prec_row = precedents.football_field_row(target_values, prec_all[prec_mask]) if prec_applies else None
    lbo_sc = config.SCENARIOS[scenario_switch - 1]
    lbo_kw = runs[lbo_sc]["run_kwargs"]
    if lbo_inputs is None:
        lbo_inputs = lbo.default_inputs(data.get("short_rate", {}).get("rate", np.nan), choices["risk_free"])
        lbo_inputs["exit_multiple"] = lbo.current_multiple(target_values)
    lbo_ready = (target_values.get("ebitda_ttm") or 0) > 0 and not any(
        pd.isna(x) for x in [lbo_inputs["exit_multiple"], *[t["base_rate"] for t in lbo_inputs["tranches"]]])
    lbo_result = lbo.run_lbo(lbo_kw, target_values, lbo_inputs) if lbo_ready else None
    lbo_row = lbo.football_field_row(lbo_kw, target_values, lbo_inputs, lbo_result) if lbo_ready else None
    ff = summary.football_field_data(data["companies"], kw, target, peers, scenario_runs=runs if has_wacc else None,
                                     precedent_row=prec_row, lbo_row=lbo_row)
    sc_table = scenarios.results_table(runs, weights)

    rf = data["risk_free"]
    if pd.isna(model["wacc_inputs"]["risk_free"]):
        rf_note = f"ENTER the {rf['guidance']}"
    elif rf.get("data_source") in ("manual", "unavailable") or model["wacc_inputs"]["risk_free"] != rf.get("rate"):
        rf_note = "Entered by the user"
    else:
        rf_note = f"Yahoo {config.RISK_FREE_TICKERS.get(rf['currency'], '')}, {rf['date']}"

    wb = Workbook()
    _summary_sheet(wb, data, model, ff, reverse, results, target, peers, ccy, sc_table)
    _, _, dcf_rows, dcf_arow = _dcf_sheet(wb, model, target, ccy, scenario_switch)
    _scenarios_sheet(wb, runs, sc_defaults, weights, target, ccy)
    if lbo_ready:                           # placed right after the DCF sheet
        _lbo_sheet(wb, target_values, lbo_inputs, lbo_result, dcf_rows, dcf_arow, target, ccy,
                   config.SCENARIO_LABELS[lbo_sc])
    else:
        ws = wb.create_sheet("LBO", 2)
        _title(ws, "LBO: N/A", "Enter the base rates (and a risk-free rate) in the app; the LBO needs positive EBITDA.")
    _wacc_sheet(wb, model, target, defaults["historical_cost_of_debt"], ccy, rf_note)
    if has_wacc:
        _sensitivity_sheet(wb, tables, kw["price"], ccy)
    else:
        ws = wb.create_sheet("Sensitivity")
        _title(ws, "DCF sensitivity: N/A", "Enter a risk-free rate in the app to calculate the WACC and the DCF.")
    _comps_sheet(wb, data, target, peers)
    _bridge_sheet(wb, data, target, peers, ccy)
    _precedents_sheet(wb, prec_all, prec_mask, prec_applies, target_values, target, ccy)
    _market_data_sheet(wb, data)
    _quality_sheet(wb, data)
    _history_sheet(wb, data, target, defaults["assumption_reference"])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


if __name__ == "__main__":
    data = dl.load_all()
    path = config.PROJECT_DIR / "output" / f"{config.TARGET}_valuation.xlsx"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(build_workbook(data, summary.default_choices(data)))
    print(f"Saved {path}")
