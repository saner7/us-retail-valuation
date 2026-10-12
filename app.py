"""Streamlit app.

    streamlit run app.py

Everything shown here is computed by the same modules as the command line
(data_loader, comps, dcf, summary); the app only collects inputs and displays.
Any target and peer group can be chosen in the sidebar; the default is Target
(TGT) against US retail peers.
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st

import charts
import comps
import config
import data_loader as dl
import dcf
import export
import summary

YEARS = [f"Year {i}" for i in range(1, config.PROJECTION_YEARS + 1)]

st.set_page_config(page_title="Company valuation", page_icon="📊", layout="wide")


# ---------------------------------------------------------------------------
# Formatting helpers (tables are shown as text so every number has its unit)
# ---------------------------------------------------------------------------

def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def pct(x, digits=1):
    return "N/A" if _missing(x) else f"{x:.{digits}%}"


def mult(x):
    return "NM" if _missing(x) else f"{x:.1f}x"


def rows_height(df: pd.DataFrame) -> int:
    """Height that shows every row of a table without an inner scrollbar."""
    return 35 * (len(df) + 1) + 3


def money(x):
    """Millions / billions, e.g. 9.26bn (currency given in the table title)."""
    return dl.fmt_number(x)


# ---------------------------------------------------------------------------
# Company selection (a form, so the data is loaded only when the user submits)
# ---------------------------------------------------------------------------

st.session_state.setdefault("sel_target", config.TARGET)
st.session_state.setdefault("sel_peers", ", ".join(config.PEERS))
st.session_state.setdefault("refresh_token", 0)

with st.sidebar:
    with st.form("company"):
        st.text_input("Target ticker", value=st.session_state.sel_target, key="form_target",
                      help="Yahoo Finance symbol, with the exchange suffix outside the US "
                           "(e.g. RACE.MI for Milan, MC.PA for Paris, CFR.SW for Zurich).")
        st.text_input("Peer group (comma-separated)", value=st.session_state.sel_peers, key="form_peers",
                      help=f"Up to {config.MAX_PEERS} comparable companies.")
        if st.form_submit_button("Analyze", type="primary"):
            st.session_state.sel_target = st.session_state.form_target
            st.session_state.sel_peers = st.session_state.form_peers

TARGET, PEERS, selection_notes = summary.parse_tickers(st.session_state.sel_target, st.session_state.sel_peers)
if not TARGET:
    st.error("Enter a target ticker in the sidebar.")
    st.stop()


# ---------------------------------------------------------------------------
# Data (cached per company group; the refresh button forces a new download)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=config.CACHE_MAX_AGE_HOURS * 3600, show_spinner="Loading market data...")
def load_data(tickers: tuple, refresh_token: int, refresh: bool) -> dict:
    return dl.load_all(list(tickers), refresh=refresh)


@st.cache_data(show_spinner="Building the Excel file...")
def excel_bytes(key: str, _data: dict, _choices: dict, target: str, peers: tuple) -> bytes:
    # The underscore arguments are not hashed; `key` identifies the data and the inputs
    return export.build_workbook(_data, _choices, target, list(peers))


tickers = (TARGET, *PEERS)
refresh_now = st.session_state.get("refresh_for") == tickers
data = load_data(tickers, st.session_state.refresh_token if refresh_now else 0, refresh_now)
if TARGET in data["errors"]:
    st.error(f"Could not load the target company. {data['errors'][TARGET]}")
    st.stop()
companies = data["companies"]
PEERS = [p for p in PEERS if p in companies]     # peers that failed are reported below
tv = companies[TARGET]["values"]
NAME = summary.company_name(data, TARGET)
CCY = summary.target_currency(data, TARGET)
SYM = config.currency_symbol(CCY)


def cur(x):
    """A per-share amount in the target's currency, e.g. $153.64 or €349.00."""
    return "N/A" if _missing(x) else f"{SYM}{x:,.2f}"


defaults = summary.default_choices(data, TARGET)
reference = dcf.default_inputs(data, TARGET, PEERS)["assumption_reference"]


# ---------------------------------------------------------------------------
# Sidebar: every assumption, with its default and history next to it
# ---------------------------------------------------------------------------

# name -> (label, kind, help). kind "pct" is entered in percent, "x" as a multiple.
n = config.DEFAULT_HISTORY_YEARS
ref = reference
rf_data = data["risk_free"]
rf_help = (f"{rf_data['guidance']}. Downloaded live ({rf_data['date']})." if rf_data.get("rate") == rf_data.get("rate")
           and rf_data.get("date") else f"Not available automatically for {CCY}: enter the {rf_data['guidance']}.")
INPUTS = {
    "growth_start": ("Revenue growth, Year 1 (%)", "pct",
                     f"Default: TTM revenue vs last fiscal year ({pct(ref.loc['revenue_growth', 'ttm'])}). "
                     f"Falls in a straight line to the terminal growth rate in Year {config.PROJECTION_YEARS}. "
                     f"History: CAGR {pct(ref.loc['revenue_growth', 'all_years_avg'])}, "
                     f"latest year {pct(ref.loc['revenue_growth', 'latest_year'])}."),
    "terminal_growth": ("Terminal growth (%)", "pct",
                        "Long-run growth after the projection; also the Year 5 revenue growth."),
    "margin_start": ("EBITDA margin, Year 1 (%)", "pct",
                     f"Default: current TTM margin with one-offs removed ({pct(ref.loc['ebitda_margin', 'ttm'])})."),
    "margin_end": (f"EBITDA margin, Year {config.PROJECTION_YEARS} (%)", "pct",
                   f"Default: last {n} fiscal years' average ({pct(ref.loc['ebitda_margin', f'last_{n}y_avg'])}). "
                   f"All years: {pct(ref.loc['ebitda_margin', 'all_years_avg'])}."),
    "da_pct": ("D&A (% of revenue)", "pct",
               f"Default: last {n} years' average. All years: {pct(ref.loc['da_pct', 'all_years_avg'])}."),
    "capex_pct": ("Capex (% of revenue)", "pct",
                  f"Default: last {n} years' average. All years: {pct(ref.loc['capex_pct', 'all_years_avg'])}."),
    "nwc_pct": ("Increase in working capital (% of revenue)", "pct",
                f"Default 0%: the history is too volatile (last {n} years: "
                f"{pct(ref.loc['nwc_pct', f'last_{n}y_avg'])}, all years: {pct(ref.loc['nwc_pct', 'all_years_avg'])})."),
    "tax_rate": ("Tax rate (%)", "pct", f"Default: last {n} years' effective tax rate. Also used in the WACC."),
    "risk_free": (f"Risk-free rate, {CCY} (%)", "pct", rf_help),
    "beta": ("Beta", "num", "Yahoo: 5-year monthly beta against the company's home market index."),
    "equity_risk_premium": ("Equity risk premium (%)", "pct", "Assumption."),
    "country_risk_premium": ("Country risk premium (%)", "pct",
                             "Extra return for country risk. 0 for the US and other low-risk countries. "
                             "For euro area companies outside Germany it can be set to the country's 10-year "
                             "bond spread over the Bund (e.g. the BTP-Bund spread for Italy)."),
    "credit_spread": ("Credit spread over risk-free (%)", "pct", "Pre-tax cost of debt = risk-free + spread."),
    "exit_multiple": ("Exit EV/EBITDA multiple (x)", "x", "Default: peer median EV/EBITDA (TTM)."),
}


def _to_widget(name, value):
    # Not rounded: the defaults must reproduce the model exactly (display shows 2 decimals).
    # A missing default (e.g. no risk-free rate for this currency) leaves the field empty.
    if _missing(value):
        return None
    return float(value) * 100 if INPUTS[name][1] == "pct" else float(value)


def _from_widget(name, value):
    if value is None:
        return float("nan")
    return value / 100 if INPUTS[name][1] == "pct" else float(value)


def reset_inputs():
    for name in INPUTS:
        st.session_state[f"in_{name}"] = _to_widget(name, defaults[name])
    st.session_state["in_mid_year"] = defaults["mid_year"]
    st.session_state["per_year_on"] = False
    st.session_state.pop("per_year_editor", None)


# New company group -> every input back to that group's defaults
if st.session_state.get("inputs_for") != tickers:
    reset_inputs()
    st.session_state["inputs_for"] = tickers

with st.sidebar:
    fetched = companies[TARGET]["meta"].get("fetched_at", "")
    st.caption(f"Data: Yahoo Finance, fetched {fetched.replace('T', ' ')}. Peers used: "
               f"{', '.join(PEERS) or 'none'}. Amounts in {CCY}.")
    c1, c2 = st.columns(2)
    if c1.button("Refresh data", help="Ignore the 24h cache and download again"):
        st.session_state.refresh_token += 1
        st.session_state.refresh_for = tickers
        st.rerun()
    c2.button("Reset inputs", on_click=reset_inputs)

    def widget(name, step):
        label, kind, help_text = INPUTS[name]
        st.number_input(label, key=f"in_{name}", step=step, format="%.2f", help=help_text)

    st.subheader("Operating assumptions")
    for name in ["growth_start", "terminal_growth", "margin_start", "margin_end",
                 "da_pct", "capex_pct", "nwc_pct", "tax_rate"]:
        widget(name, 0.1)
    st.subheader("WACC")
    for name in ["risk_free", "beta", "equity_risk_premium", "country_risk_premium", "credit_spread"]:
        widget(name, 0.05 if name == "beta" else 0.1)
    st.subheader("Terminal value")
    widget("exit_multiple", 0.5)
    st.checkbox("Mid-year convention", key="in_mid_year",
                help="Cash flows arrive during the year, so they are discounted half a year less.")

choices = {name: _from_widget(name, st.session_state[f"in_{name}"]) for name in INPUTS}
choices["mid_year"] = st.session_state["in_mid_year"]


# ---------------------------------------------------------------------------
# Optional year-by-year table. The editor itself is drawn in the DCF tab, but
# its edits live in session_state, so the model can use them here, first.
# ---------------------------------------------------------------------------

def per_year_base(ch: dict) -> pd.DataFrame:
    a = summary.assumptions_from_choices({**ch, "per_year": None})
    df = pd.DataFrame({YEARS[i]: [a[k][i] * 100 for k in dcf.ASSUMPTION_LABELS] for i in range(len(YEARS))},
                      index=[dcf.ASSUMPTION_LABELS[k] for k in dcf.ASSUMPTION_LABELS])
    return df


base_table = per_year_base(choices)
if st.session_state.get("per_year_on"):
    table = base_table.copy()
    edits = st.session_state.get("per_year_editor", {}).get("edited_rows", {})
    for row, cols in edits.items():
        for colname, value in cols.items():
            if value is not None:
                table.iloc[int(row), table.columns.get_loc(colname)] = value
    choices["per_year"] = {k: list(table.iloc[i] / 100) for i, k in enumerate(dcf.ASSUMPTION_LABELS)}


# ---------------------------------------------------------------------------
# Run the model once; every tab reads from these results
# ---------------------------------------------------------------------------

model = summary.build_model(data, choices, TARGET)
kw = model["run_kwargs"]
HAS_DCF = not _missing(kw["wacc_rate"])
result = dcf.run_dcf(**kw)
ff = summary.football_field_data(companies, kw, TARGET, PEERS)
reverse = summary.reverse_dcf(kw)
mtable, exclusions = comps.multiples_table(companies)
pstats = comps.peer_stats(mtable, PEERS)
price = tv["price"]

st.title(f"{NAME} ({TARGET}) valuation")
st.caption(f"Trading comparables + DCF. Amounts in {CCY}. For educational purposes only, not investment advice.")

for note in selection_notes:
    st.info(note)
for w in summary.analysis_warnings(data, TARGET, PEERS + [t for t in data["errors"] if t != TARGET]):
    (st.warning if w["level"] == "warning" else st.info)(w["text"])

# If Yahoo blocked the download (common on shared cloud servers), say which saved data is shown
stale = {name: src for name, src in dl.data_sources(data).items() if src != "live"}
if stale:
    dates = sorted({companies[t]["meta"]["fetched_at"][:10] for t in stale if t in companies}
                   | ({data["risk_free"]["fetched_at"][:10]} if "risk_free_rate" in stale else set()))
    st.warning(f"Live download from Yahoo Finance failed, so saved data is shown for {', '.join(stale)} "
               f"(from {', '.join(dates)}). Try **Refresh data** later.")

tab_summary, tab_dcf, tab_comps, tab_data = st.tabs(["Summary", "DCF", "Comps", "Data & quality"])


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

with tab_summary:
    g, e = result["gordon"], result["exit"]
    ev_ebitda = ff.set_index("key").loc["ev_ebitda"] if "ev_ebitda" in set(ff["key"]) else None
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Current share price", cur(price))
    def vs_price(x):
        return None if _missing(x) else pct(x) + " vs price"
    m2.metric("DCF: Gordon growth", cur(g["value_per_share"]), vs_price(g["upside"]))
    m3.metric("DCF: Exit multiple", cur(e["value_per_share"]), vs_price(e["upside"]))
    if ev_ebitda is not None:
        m4.metric("Comps: EV/EBITDA median", cur(ev_ebitda["mid"]), vs_price(ev_ebitda["mid"] / price - 1))

    st.plotly_chart(charts.football_field(ff, price, f"{TARGET} football field: value per share", CCY),
                    width="stretch")
    st.caption("Bars: comps = peer 25th-75th percentile, tick = median; DCF = WACC "
               f"±{config.FF_WACC_DELTA:.1%} x terminal growth ±{config.FF_GROWTH_DELTA:.1%} "
               f"(Gordon) or exit multiple ±{config.FF_MULTIPLE_DELTA:.0f}x, tick = base case. "
               "P/B is left out (see the Comps tab)."
               + ("" if HAS_DCF else " DCF rows appear once a risk-free rate is entered."))

    with st.container(border=True):
        st.markdown("**Reverse DCF: what today's price implies** "
                    "(one input changed at a time until the model value equals the current price)")
        r1, r2, r3 = st.columns(3)
        r1.metric("WACC (Gordon)", pct(reverse["wacc"]), f"model {pct(kw['wacc_rate'])}", delta_color="off", delta_arrow="off")
        r2.metric("Terminal growth (Gordon)", pct(reverse["terminal_growth"]),
                  f"model {pct(kw['terminal_growth'])}", delta_color="off", delta_arrow="off")
        r3.metric("Exit EV/EBITDA", mult(reverse["exit_multiple"]),
                  f"model {mult(kw['exit_multiple'])}", delta_color="off", delta_arrow="off")

    for w in result["warnings"]:
        st.warning(w)

    with st.expander("Football field data"):
        view = ff[["label", "low", "mid", "high", "basis"]].copy()
        for c in ["low", "mid", "high"]:
            view[c] = view[c].map(cur)
        st.dataframe(view.set_index("label"), width="stretch")

    excel_key = repr((tickers, companies[TARGET]["meta"].get("fetched_at"), sorted(choices.items())))
    st.download_button("Download Excel model", type="primary",
                       data=excel_bytes(excel_key, data, choices, TARGET, tuple(PEERS)),
                       file_name=f"{TARGET}_valuation.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       help="DCF and WACC sheets use live Excel formulas; the other sheets hold values.")


# ---------------------------------------------------------------------------
# DCF
# ---------------------------------------------------------------------------

with tab_dcf:
    st.subheader("Assumptions vs history")
    shown = summary.assumptions_from_choices(choices)
    rows = []
    for k, label in dcf.ASSUMPTION_LABELS.items():
        r = reference.loc[k]
        vals = shown[k]
        rows.append({"Assumption": label,
                     "Year 1": pct(vals[0]), f"Year {len(vals)}": pct(vals[-1]),
                     "All years (growth: CAGR)": pct(r["all_years_avg"]),
                     f"Last {n} years": pct(r[f"last_{n}y_avg"]),
                     "Latest year": pct(r["latest_year"]), "TTM": pct(r["ttm"])})
    st.dataframe(pd.DataFrame(rows).set_index("Assumption"), width="stretch")
    st.caption("Change the assumptions in the sidebar. EBITDA TTM margin is after removing one-off items "
               "(manual overrides, see Data & quality).")

    st.checkbox("Edit assumptions year by year", key="per_year_on",
                help="Overrides the sidebar values for individual years. Values in percent.")
    if st.session_state.get("per_year_on"):
        st.data_editor(base_table, key="per_year_editor", width="stretch",
                       column_config={y: st.column_config.NumberColumn(y, format="%.2f %%", step=0.01)
                                      for y in YEARS})
        st.caption("Starts from the sidebar values; your edits replace them for the edited cells. "
                   "The WACC keeps using the sidebar tax rate.")

    st.subheader(f"Projection ({CCY})")
    proj = result["projection"]
    labels = {"revenue": "Revenue", "revenue_growth": "Revenue growth", "ebitda": "EBITDA",
              "ebitda_margin": "EBITDA margin", "d_and_a": "D&A", "ebit": "EBIT", "taxes": "Taxes",
              "nopat": "NOPAT", "capex": "Capex", "increase_in_nwc": "Increase in NWC", "fcff": "FCFF",
              "discount_period": "Discount period", "discount_factor": "Discount factor",
              "pv_fcff": "PV of FCFF"}
    out = pd.DataFrame(index=[labels[i] for i in proj.index], columns=proj.columns, dtype=object)
    for key in proj.index:
        f = pct if key in ("revenue_growth", "ebitda_margin") else \
            (lambda x: f"{x:.3f}") if key in ("discount_period", "discount_factor") else money
        out.loc[labels[key]] = proj.loc[key].map(f).values
    st.dataframe(out, width="stretch", height=rows_height(out))

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("WACC")
        wi, w = model["wacc_inputs"], model["wacc"]
        wacc_rows = [("Risk-free rate", pct(wi["risk_free"], 2)),
                     ("Beta", "N/A" if _missing(wi["beta"]) else f"{wi['beta']:.2f}"),
                     ("Equity risk premium", pct(wi["equity_risk_premium"])),
                     ("Country risk premium", pct(wi["country_risk_premium"])),
                     ("= Cost of equity", pct(w["cost_of_equity"], 2)),
                     ("Pre-tax cost of debt", pct(wi["pre_tax_cost_of_debt"], 2)),
                     ("Tax rate", pct(wi["tax_rate"])),
                     ("= After-tax cost of debt", pct(w["after_tax_cost_of_debt"], 2)),
                     ("Weight of equity / debt", f"{pct(w['weight_equity'])} / {pct(w['weight_debt'])}"),
                     ("= WACC", pct(w["wacc"], 2))]
        st.dataframe(pd.DataFrame(wacc_rows, columns=["", "Value"]).set_index(""), width="stretch")
        hist_kd = dcf.default_inputs(data, TARGET, PEERS)["historical_cost_of_debt"]
        st.caption(f"Debt excludes lease liabilities (ASC 842). Historical interest expense / debt: "
                   f"{pct(hist_kd)} (reference only; reflects older, cheaper bonds).")
    with c2:
        st.subheader("Valuation")
        vrows = [("Sum of PV of FCFF", "sum_pv_fcff", money), ("PV of terminal value", "pv_terminal_value", money),
                 ("= Enterprise value", "enterprise_value", money), ("- Net debt", "net_debt", money),
                 ("= Equity value", "equity_value", money), ("/ Shares", "shares", money),
                 ("= Value per share", "value_per_share", cur), ("Upside / downside", "upside", pct),
                 ("Terminal value share of EV", "tv_share_of_ev", pct)]
        vt = pd.DataFrame({f"Gordon (g = {pct(kw['terminal_growth'])})": [f(g[k]) for _, k, f in vrows],
                           f"Exit ({mult(kw['exit_multiple'])})": [f(e[k]) for _, k, f in vrows]},
                          index=[label for label, _, _ in vrows])
        vt.loc["Cross-check"] = [f"implies {mult(g['implied_exit_multiple'])}",
                                 f"implies g = {pct(e['implied_growth'])}"]
        st.dataframe(vt, width="stretch")

    st.subheader("Sensitivity")
    if not HAS_DCF:
        st.info(f"Enter the risk-free rate in the sidebar ({rf_data['guidance']}) to see the sensitivity tables.")
    else:
        tables = dcf.sensitivity_tables(kw)
        h1, h2 = st.columns(2)
        h1.plotly_chart(charts.sensitivity_heatmap(tables["gordon"], price, "Gordon growth: value per share",
                                                   base_x=kw["terminal_growth"], base_wacc=kw["wacc_rate"],
                                                   currency=CCY),
                        width="stretch")
        h2.plotly_chart(charts.sensitivity_heatmap(tables["exit"], price, "Exit multiple: value per share",
                                                   x_is_multiple=True, base_x=kw["exit_multiple"],
                                                   base_wacc=kw["wacc_rate"], currency=CCY),
                        width="stretch")


# ---------------------------------------------------------------------------
# Comps
# ---------------------------------------------------------------------------

def show_multiples(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().astype(object)
    for c in out.columns:
        out[c] = df[c].map(pct if c in comps.MARGINS else mult)
    return out.rename(columns=comps.LABELS)


with tab_comps:
    st.subheader("Trading multiples (TTM)")
    shown_table = mtable.rename(index={TARGET: f"{TARGET} (target)"})
    st.dataframe(show_multiples(shown_table), width="stretch")
    st.subheader("Peer statistics (target excluded)")
    st.dataframe(show_multiples(pstats.drop(index="count")), width="stretch")
    st.caption("Valid peers: " + ", ".join(f"{comps.LABELS[k]} {int(v)}" for k, v in pstats.loc["count"].items()))
    for key, note in comps.MULTIPLE_NOTES.items():
        st.info(f"**{comps.LABELS[key]}**: {note}")
    if exclusions.empty:
        st.caption("Excluded values: none - all multiples were usable.")
    else:
        st.write("Excluded values")
        st.dataframe(exclusions, width="stretch", hide_index=True)

    st.subheader("Implied value per share")
    ffc = comps.football_field_rows(companies, TARGET, PEERS)
    view = pd.DataFrame({
        "Method": ffc["method"].values,
        "Peer multiple (low / median / high)": [" / ".join(mult(r[c]) for c in ["multiple_low", "multiple_median", "multiple_high"])
                                                for _, r in ffc.iterrows()],
        "Value per share (low / median / high)": [" / ".join(cur(r[c]) for c in ["price_low", "price_median", "price_high"])
                                                  for _, r in ffc.iterrows()],
        "Upside at median": ffc["upside_median"].map(pct).values,
    }, index=ffc["multiple"].values)
    st.dataframe(view, width="stretch")

    bridge = comps.bridge_table(companies, TARGET, PEERS)
    choice = st.selectbox("Bridge for", [comps.LABELS[k] for k in comps.MULTIPLES], index=1)
    b = bridge[choice].copy().astype(object)
    for step in b.index:
        f = mult if "multiple" in step else pct if "Upside" in step else cur if "per share" in step else money
        b.loc[step] = bridge[choice].loc[step].map(f).values
    st.dataframe(b, width="stretch")


# ---------------------------------------------------------------------------
# Data & quality
# ---------------------------------------------------------------------------

with tab_data:
    st.subheader("Snapshot")
    snap = dl.snapshot_table(companies)
    src = pd.DataFrame({t: c["sources"] for t, c in companies.items()}).loc[dl.ALL_FIELDS]
    shown = snap.copy().astype(object)
    for field in snap.index:
        for t in snap.columns:
            shown.loc[field, t] = f"{dl.fmt_number(snap.loc[field, t], field)} [{src.loc[field, t][0]}]"
    st.dataframe(shown, width="stretch", height=rows_height(shown))
    ccys = ", ".join(f"{t} {c['meta'].get('currency') or 'N/A'}" for t, c in companies.items())
    st.caption("Source in brackets: a = Yahoo Finance, m = manual override, d = derived. Each company in its "
               f"own trading currency ({ccys}), bn / m; TTM = sum of the last 4 quarters. Leases are excluded "
               "from debt.")

    st.subheader("Data quality report")
    q = dl.quality_table(companies)
    only_issues = st.toggle("Show issues only", value=True)
    if only_issues:
        q = q[q["status"] != "OK"]
    st.dataframe(q[["ticker", "field", "source", "status", "comment"]], width="stretch", hide_index=True)

    st.subheader("Manual overrides")
    st.dataframe(dl.load_overrides(), width="stretch", hide_index=True)
    st.caption("Edit data/manual_overrides.csv to change these. Overrides of TTM values are flagged "
               "in the report above once their TTM window is out of date.")

    st.subheader(f"{TARGET} annual history")
    hist = companies[TARGET]["history"].sort_index().copy()
    hist.index = hist.index.strftime("FY end %Y-%m")
    st.dataframe(hist.T.apply(lambda col: col.map(money)), width="stretch", height=rows_height(hist.T))
