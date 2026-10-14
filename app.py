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
import lbo
import merger
import peers
import precedents
import scenarios
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
# Company selection: pick from the S&P 500 (with a suggested, editable peer group)
# or type tickers. Data is loaded only when the user presses Analyze.
# ---------------------------------------------------------------------------

st.session_state.setdefault("sel_target", config.TARGET)
st.session_state.setdefault("sel_peers", ", ".join(config.PEERS))
st.session_state.setdefault("sel_warnings", [])
st.session_state.setdefault("refresh_token", 0)

MODES = ["Pick from S&P 500", "Enter tickers manually"]


@st.cache_data
def load_peer_files():
    try:
        return peers.load_universe(), peers.load_curated(), peers.universe_header()
    except FileNotFoundError:
        return None, None, []


universe, curated, universe_head = load_peer_files()
if universe is None:
    MODES = MODES[1:]
else:
    LABELS = dict(zip(universe["yahoo_ticker"], universe["name"] + " (" + universe["yahoo_ticker"] + ")"))


def suggestion(target: str, source: str, count: int) -> dict:
    """{"peers", "reasons", "warnings", "rationale"} for the sidebar."""
    cur = peers.curated_for(target, curated)
    if cur and source == peers.CURATED_LABEL:
        return {"peers": cur["peers"], "reasons": {t: "curated" for t in cur["peers"]}, "warnings": [],
                "rationale": f"{cur['rationale']} (curated {cur['source_date']})"}
    s = peers.suggest_peers(target, universe, count)
    return {"peers": list(s["peers"]["yahoo_ticker"]), "reasons": dict(zip(s["peers"]["yahoo_ticker"],
                                                                         s["peers"]["reason"])),
            "warnings": s["warnings"], "rationale": None}


def fill_peers(reset_source: bool = False):
    """Put the suggested group into the editable list (on a new target or 'Use suggested peers')."""
    t = st.session_state.sp_target
    if reset_source or st.session_state.get("sp_source") is None:
        has_curated = peers.curated_for(t, curated) is not None
        st.session_state.sp_source = peers.CURATED_LABEL if has_curated else peers.GICS_LABEL
    st.session_state.sp_peers = suggestion(t, st.session_state.sp_source, st.session_state.sp_count)["peers"]
    st.session_state.sp_extra = ""


if universe is not None and "sp_target" not in st.session_state:
    st.session_state.sp_target = config.TARGET
    st.session_state.sp_count = config.PEERS_DEFAULT_COUNT
    fill_peers(reset_source=True)

with st.sidebar:
    mode = st.radio("Choose the company", MODES, key="sel_mode", horizontal=False)
    if mode == "Enter tickers manually":
        with st.form("company"):
            st.text_input("Target ticker", value=st.session_state.sel_target, key="form_target",
                          help="Yahoo Finance symbol, with the exchange suffix outside the US "
                               "(e.g. RACE.MI for Milan, MC.PA for Paris, CFR.SW for Zurich).")
            st.text_input("Peer group (comma-separated)", value=st.session_state.sel_peers, key="form_peers",
                          help=f"Up to {config.MAX_PEERS} comparable companies.")
            if st.form_submit_button("Analyze", type="primary"):
                st.session_state.sel_target = st.session_state.form_target
                st.session_state.sel_peers = st.session_state.form_peers
                st.session_state.sel_warnings = []
    else:
        st.selectbox("Company (type to search)", list(LABELS), key="sp_target", format_func=LABELS.get,
                     on_change=fill_peers, kwargs={"reset_source": True})
        target_sp = st.session_state.sp_target
        if peers.curated_for(target_sp, curated):
            st.radio("Peer suggestion", [peers.CURATED_LABEL, peers.GICS_LABEL], key="sp_source",
                     on_change=fill_peers)
        else:
            st.session_state.sp_source = peers.GICS_LABEL
        if st.session_state.sp_source == peers.GICS_LABEL:
            st.number_input("Number of suggested peers", 1, config.MAX_PEERS, key="sp_count", on_change=fill_peers,
                            help="Kept small by default: every peer is one more download from Yahoo Finance.")
        sug = suggestion(target_sp, st.session_state.sp_source, st.session_state.sp_count)
        options = list(LABELS) + [t for t in st.session_state.sp_peers if t not in LABELS]
        st.multiselect("Peer group (edit freely)", options, key="sp_peers",
                       format_func=lambda t: LABELS.get(t, t), max_selections=config.MAX_PEERS)
        st.text_input("Add other tickers (comma-separated)", key="sp_extra",
                      help="Companies outside the S&P 500 list, e.g. BJ or MC.PA.")
        chosen = list(st.session_state.sp_peers) + [t.strip().upper() for t in st.session_state.sp_extra.split(",")
                                                    if t.strip()]
        side_warnings = list(sug["warnings"])
        if (w := peers.size_warning(target_sp, chosen, universe)):
            side_warnings.append(w)
        for w in side_warnings:
            st.warning(w)
        b1, b2 = st.columns(2)
        b1.button("Use suggested peers", on_click=fill_peers)
        if b2.button("Analyze", type="primary"):
            st.session_state.sel_target = target_sp
            st.session_state.sel_peers = ", ".join(chosen)
            # the bank / insurer warning is already shown at the top by summary.analysis_warnings
            st.session_state.sel_warnings = [w for w in side_warnings if "bank or insurer" not in w]
        with st.expander("Why these peers"):
            if sug["rationale"]:
                st.markdown(f"**{peers.CURATED_LABEL}.** {sug['rationale']}")
            table = peers.peer_table(target_sp, chosen, universe, sug["reasons"])
            st.dataframe(table, hide_index=True, width="stretch",
                         column_config={"Market cap ($bn)": st.column_config.NumberColumn(format="%.0f"),
                                        "Size vs target": st.column_config.NumberColumn(format="%.2fx")})
            st.caption(peers.NOTE)
            st.caption(f"Market caps as of {peers.market_cap_dates(universe)} (Yahoo Finance). "
                       + " ".join(universe_head[:1]))

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
def excel_bytes(key: str, _data: dict, _choices: dict, target: str, peers: tuple, _scenarios: dict,
                _weights: dict, _prec: pd.DataFrame, _prec_settings: dict, _lbo_inputs: dict, switch: int) -> bytes:
    # The underscore arguments are not hashed; `key` identifies the data and the inputs
    return export.build_workbook(_data, _choices, target, list(peers), scenario_values=_scenarios,
                                 weights=_weights, precedent_deals=_prec, precedent_settings=_prec_settings,
                                 lbo_inputs=_lbo_inputs, scenario_switch=switch)


tickers = (TARGET, *PEERS)
refresh_now = st.session_state.get("refresh_for") == tickers
load_args = (tickers, st.session_state.refresh_token if refresh_now else 0, refresh_now)
data = load_data(*load_args)
if data["errors"]:
    load_data.clear(*load_args)      # do not keep a failed download for 24h: Analyze again retries it
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
    "growth_end": (f"Revenue growth, Year {config.PROJECTION_YEARS} (%)", "pct",
                   "Revenue growth moves in a straight line from Year 1 to this value. "
                   "Default: equal to the terminal growth rate."),
    "terminal_growth": ("Terminal growth (%)", "pct", "Long-run growth after the projection (Gordon growth)."),
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


def _to_pct_widget(value):
    return None if _missing(value) else float(value) * 100


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


# LBO inputs live in their tab; percent values are entered in percent
short_rate = data.get("short_rate", {})
LBO_DEFAULTS = {
    "lbo_scenario": config.SCENARIO_LABELS["base"],
    "lbo_mode": "Premium",
    "lbo_premium": config.LBO_PREMIUM * 100,
    "lbo_entry_multiple": lbo.entry_terms(tv, {"premium": config.LBO_PREMIUM})["entry_multiple"],
    "lbo_exit_multiple": lbo.current_multiple(tv),
    "lbo_exit_year": config.LBO_EXIT_YEAR,
    "lbo_target_irr": config.LBO_TARGET_IRR * 100,
    "lbo_floating_base": _to_pct_widget(short_rate.get("rate")),
    "lbo_txn_fee": config.LBO_TRANSACTION_FEE_PCT * 100,
    "lbo_fin_fee": config.LBO_FINANCING_FEE_PCT * 100,
    "lbo_min_cash": config.LBO_MIN_CASH_PCT * 100,
}


def reset_lbo():
    for k, v in LBO_DEFAULTS.items():
        st.session_state[k] = v
    st.session_state.pop("lbo_tranches", None)


def reset_inputs():
    reset_lbo()
    for name in INPUTS:
        st.session_state[f"in_{name}"] = _to_widget(name, defaults[name])
    st.session_state["in_mid_year"] = defaults["mid_year"]
    st.session_state["per_year_on"] = False
    st.session_state.pop("per_year_editor", None)
    st.session_state.pop("scenario_editor", None)
    for sc in config.SCENARIOS:
        st.session_state[f"w_{sc}"] = config.SCENARIO_WEIGHTS[sc] * 100


# New company group -> every input back to that group's defaults
if st.session_state.get("inputs_for") != tickers:
    reset_inputs()
    st.session_state["inputs_for"] = tickers
for k, v in LBO_DEFAULTS.items():
    st.session_state.setdefault(k, v)

with st.sidebar:
    fetched = companies[TARGET]["meta"].get("fetched_at", "")
    snap = dl.snapshot_date(TARGET)
    source = companies[TARGET]["meta"].get("data_source", "live")
    st.caption(f"Data: Yahoo Finance, {'live' if source == 'live' else 'saved'} data fetched "
               f"{fetched.replace('T', ' ')}. Saved fallback data for {TARGET}: {snap or 'none'}. "
               f"Peers used: {', '.join(PEERS) or 'none'}. Amounts in {CCY}.")
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
    for name in ["growth_start", "growth_end", "terminal_growth", "margin_start", "margin_end",
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


# ---------------------------------------------------------------------------
# Scenarios. Base = the sidebar inputs; Bear / Bull defaults are derived from
# history (scenarios.default_scenarios) and can be edited in the Scenarios tab.
# As with the year-by-year table, the edits live in session_state.
# ---------------------------------------------------------------------------

SC_COLS = [config.SCENARIO_LABELS[sc] for sc in config.SCENARIOS]
sc_defaults = scenarios.default_scenarios(data, TARGET, PEERS, choices)


def scenario_base_table(sc_def: dict) -> pd.DataFrame:
    rows = {}
    for f, (label, unit) in scenarios.FIELDS.items():
        scale = 100 if unit == "%" else 1
        rows[f"{label} ({unit})"] = {config.SCENARIO_LABELS[sc]: (None if _missing(sc_def[sc][f]) else sc_def[sc][f] * scale)
                                      for sc in config.SCENARIOS}
        rows[f"{label} ({unit})"]["Why (Bear / Bull default)"] = sc_def["rationale"][f]
    return pd.DataFrame(rows).T


sc_table = scenario_base_table(sc_defaults)
for row, cols in st.session_state.get("scenario_editor", {}).get("edited_rows", {}).items():
    for colname, value in cols.items():
        if colname in ("Bear", "Bull") and value is not None:
            sc_table.iloc[int(row), sc_table.columns.get_loc(colname)] = value
sc_values = {}
for sc in config.SCENARIOS:
    col = config.SCENARIO_LABELS[sc]
    sc_values[sc] = {f: (float("nan") if sc_table.iloc[i][col] is None or _missing(sc_table.iloc[i][col])
                         else float(sc_table.iloc[i][col]) / (100 if unit == "%" else 1))
                     for i, (f, (_, unit)) in enumerate(scenarios.FIELDS.items())}
for sc in config.SCENARIOS:
    st.session_state.setdefault(f"w_{sc}", config.SCENARIO_WEIGHTS[sc] * 100)
weights = {sc: (st.session_state[f"w_{sc}"] or 0) / 100 for sc in config.SCENARIOS}
sc_runs = scenarios.run_scenarios(data, choices, sc_values, TARGET)
sc_results = scenarios.results_table(sc_runs, weights)

# ---------------------------------------------------------------------------
# LBO: inputs are drawn in their tab and read here first (for the football field)
# ---------------------------------------------------------------------------

LBO_COLS = {"x EBITDA": ("multiple", 1), "Spread (%)": ("spread", 100), "Amortization (% a year)": ("amortization", 100),
            "Maturity (years)": ("maturity", 1), "Cash sweep": ("sweep", None)}


def lbo_tranche_table() -> pd.DataFrame:
    df = pd.DataFrame([{col: (t[key] * f if f else t[key]) for col, (key, f) in LBO_COLS.items()}
                       for t in config.LBO_TRANCHES], index=[t["name"] for t in config.LBO_TRANCHES])
    for row, cols in st.session_state.get("lbo_tranches", {}).get("edited_rows", {}).items():
        for colname, value in cols.items():
            if colname in df.columns and value is not None:
                df.iloc[int(row), df.columns.get_loc(colname)] = value
    return df


def _pct_in(key):
    v = st.session_state.get(key)
    return float("nan") if v is None else v / 100


lbo_table = lbo_tranche_table()
lbo_sc = {config.SCENARIO_LABELS[k]: k for k in config.SCENARIOS}[st.session_state["lbo_scenario"]]
lbo_kw = sc_runs[lbo_sc]["run_kwargs"]
lbo_inputs = lbo.default_inputs(_pct_in("lbo_floating_base"), choices["risk_free"])
lbo_inputs.update(
    premium=_pct_in("lbo_premium"),
    entry_multiple=st.session_state["lbo_entry_multiple"] if st.session_state["lbo_mode"] == "Entry multiple" else None,
    exit_multiple=st.session_state["lbo_exit_multiple"], exit_year=int(st.session_state["lbo_exit_year"]),
    target_irr=_pct_in("lbo_target_irr"), transaction_fee_pct=_pct_in("lbo_txn_fee"),
    financing_fee_pct=_pct_in("lbo_fin_fee"), min_cash_pct=_pct_in("lbo_min_cash"))
for t, (_, r) in zip(lbo_inputs["tranches"], lbo_table.iterrows()):
    for col, (key, f) in LBO_COLS.items():
        t[key] = bool(r[col]) if f is None else float(r[col]) / f
lbo_ready = not any(_missing(x) for x in [lbo_inputs["premium"], lbo_inputs["exit_multiple"], lbo_inputs["target_irr"],
                                           tv.get("ebitda_ttm"), *[t["base_rate"] for t in lbo_inputs["tranches"]]]) \
    and tv.get("ebitda_ttm", 0) > 0
lbo_result = lbo.run_lbo(lbo_kw, tv, lbo_inputs) if lbo_ready else None
lbo_row = lbo.football_field_row(lbo_kw, tv, lbo_inputs, lbo_result) if lbo_ready else None

# ---------------------------------------------------------------------------
# Precedent transactions: built-in file or the user's upload (this session only).
# The settings are drawn in their tab, read here first for the football field.
# ---------------------------------------------------------------------------

prec_error = None
uploaded = st.session_state.get("prec_upload")
try:
    prec_all = precedents.load_precedents(uploaded.getvalue() if uploaded is not None else None)
except precedents.PrecedentFileError as e:
    prec_error = str(e)
    prec_all = precedents.load_precedents()
prec_settings = {"include_secondary": bool(st.session_state.get("prec_secondary", False)),
                 "include_terminated": bool(st.session_state.get("prec_terminated", False))}
prec_mask = precedents.included_mask(prec_all, **prec_settings)
prec_deals = prec_all[prec_mask]
prec_applies = precedents.applicable(companies[TARGET]["meta"], TARGET, prec_deals)
prec_row = precedents.football_field_row(tv, prec_deals) if prec_applies else None

ff = summary.football_field_data(companies, kw, TARGET, PEERS, scenario_runs=sc_runs if HAS_DCF else None,
                                 precedent_row=prec_row, lbo_row=lbo_row)
reverse = summary.reverse_dcf(kw)
mtable, exclusions = comps.multiples_table(companies)
pstats = comps.peer_stats(mtable, PEERS)
price = tv["price"]

st.title(f"{NAME} ({TARGET}) valuation")
st.caption(f"Trading comparables, precedent transactions, LBO and DCF with scenarios. Amounts in {CCY}. For educational purposes only, not investment advice.")

for note in selection_notes:
    st.info(note)
if st.session_state.sel_target == TARGET:
    for w in st.session_state.sel_warnings:
        st.warning(w)
for w in summary.data_warnings(data, TARGET, PEERS) + \
        summary.analysis_warnings(data, TARGET, PEERS + [t for t in data["errors"] if t != TARGET]):
    (st.warning if w["level"] == "warning" else st.info)(w["text"])

# If Yahoo blocked the download (common on shared cloud servers), say which saved data is shown
stale = {name: src for name, src in dl.data_sources(data).items() if src != "live"}
if stale:
    dates = sorted({companies[t]["meta"]["fetched_at"][:10] for t in stale if t in companies}
                   | ({data["risk_free"]["fetched_at"][:10]} if "risk_free_rate" in stale else set()))
    st.warning(f"Live data unavailable; showing saved data as of {', '.join(dates)} for {', '.join(stale)}. "
               "Yahoo Finance often blocks shared cloud servers for a while: try **Refresh data** later.")

tab_summary, tab_dcf, tab_lbo, tab_ma, tab_sc, tab_comps, tab_prec, tab_data = st.tabs(
    ["Summary", "DCF", "LBO", "M&A", "Scenarios", "Comps", "Precedent Transactions", "Data & quality"])


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
    st.caption(f"Bars: comps = peer 25th-75th percentile (min-max with fewer than {config.MIN_PEERS_FOR_QUARTILES} "
               "peers), tick = median; DCF = WACC "
               f"±{config.FF_WACC_DELTA:.1%} x terminal growth ±{config.FF_GROWTH_DELTA:.1%} "
               f"(Gordon) or exit multiple ±{config.FF_MULTIPLE_DELTA:.0f}x, tick = base case. "
               + (f"Precedents = deals' EV/EBITDA, {prec_row['basis']}, tick = median (see their tab). " if prec_row else "")
               + (f"LBO = maximum price a fund can pay for a {config.LBO_IRR_RANGE[1]:.0%} (low) to "
                  f"{config.LBO_IRR_RANGE[0]:.0%} (high) IRR, tick = {lbo_inputs['target_irr']:.0%} (see the LBO tab). "
                  if lbo_row else "")
               + ("P/B is shown because the target is a financial company." if summary.is_financial(data, TARGET)
                  else "P/B is left out (see the Comps tab).")
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

    for w in result["warnings"] + ([lw] if (lw := summary.wacc_warning(model)) else []):
        st.warning(w)

    with st.expander("Football field data"):
        view = ff[["label", "low", "mid", "high", "basis"]].copy()
        for c in ["low", "mid", "high"]:
            view[c] = view[c].map(cur)
        st.dataframe(view.set_index("label"), width="stretch")

    excel_key = repr((tickers, companies[TARGET]["meta"].get("fetched_at"), sorted(choices.items()),
                      sorted((k, sorted(v.items())) for k, v in sc_values.items()), sorted(weights.items()),
                      sorted(prec_settings.items()), prec_all.to_csv(index=False), repr(lbo_inputs), lbo_sc))
    st.download_button("Download Excel model", type="primary",
                       data=excel_bytes(excel_key, data, choices, TARGET, tuple(PEERS), sc_values, weights,
                                        prec_all, prec_settings, lbo_inputs, config.SCENARIOS.index(lbo_sc) + 1),
                       file_name=f"{TARGET}_valuation.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       help="DCF, WACC, Scenarios and LBO sheets use live Excel formulas; the other sheets hold values. "
                            "The DCF sheet's scenario switch is set to the LBO tab's scenario.")


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
                       column_config={y: st.column_config.NumberColumn(y, format="%.2f %%", step=0.0001)
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
# LBO
# ---------------------------------------------------------------------------

def bn(x):
    if _missing(x):
        return "N/A"
    return f"{x / 1e6:,.2f}m" if 0 < abs(x) < 1e6 else money(x)


with tab_lbo:
    st.markdown(
        "**If a private equity fund bought the company with mostly borrowed money, what is the most it could "
        "pay per share and still earn its target return?** The fund (the *sponsor*) puts in equity, borrows "
        "the rest against the company's EBITDA, uses the cash flow to repay debt and sells after a few years. "
        "Operations come from the DCF projection of the chosen scenario.")
    c = st.columns(4)
    c[0].selectbox("Operating scenario", list(config.SCENARIO_LABELS.values()), key="lbo_scenario",
                   help="Revenue, EBITDA, D&A, capex, working capital and tax from this DCF scenario.")
    c[1].radio("Entry price set by", ["Premium", "Entry multiple"], key="lbo_mode", horizontal=True)
    if st.session_state["lbo_mode"] == "Premium":
        c[2].number_input("Premium over the current price (%)", key="lbo_premium", step=1.0, format="%.1f",
                          help="Illustrative assumption: buyers of listed companies usually pay a premium to win "
                               "shareholders' approval.")
    else:
        c[2].number_input("Entry EV / EBITDA (x)", key="lbo_entry_multiple", step=0.25, format="%.2f")
    c[3].number_input("Exit EV / EBITDA (x)", key="lbo_exit_multiple", step=0.25, format="%.2f",
                      help=f"Default: {TARGET}'s current trading multiple ({mult(LBO_DEFAULTS['lbo_exit_multiple'])}), "
                           "not the entry multiple, which includes the premium (see the notes below).")
    c = st.columns(4)
    c[0].selectbox("Exit year", list(range(1, config.PROJECTION_YEARS + 1)), key="lbo_exit_year")
    c[1].number_input("Target IRR (%)", key="lbo_target_irr", step=1.0, format="%.1f",
                      help="The fund's required annual return; the football field shows 15%-25%.")
    c[2].number_input("Floating base rate (%)", key="lbo_floating_base", step=0.05, format="%.2f",
                      help="Proxy for SOFR: the 13-week US Treasury bill yield (^IRX)"
                           + (f", downloaded {short_rate.get('date')}." if short_rate.get("date") else ". Enter it."))
    c[3].number_input("Minimum cash (% of revenue)", key="lbo_min_cash", step=0.5, format="%.1f",
                      help="Illustrative assumption: cash the business keeps to operate; the rest repays debt.")
    c = st.columns(4)
    c[0].number_input("Transaction fees (% of entry EV)", key="lbo_txn_fee", step=0.1, format="%.2f",
                      help="Illustrative assumption: advisory, legal and other deal costs.")
    c[1].number_input("Financing fees (% of new debt)", key="lbo_fin_fee", step=0.1, format="%.2f",
                      help="Illustrative assumption: arrangement fees paid to the lenders.")
    c[3].button("Reset LBO inputs", on_click=reset_lbo)
    st.data_editor(lbo_tranche_table(), key="lbo_tranches", width="stretch",
                   column_config={"x EBITDA": st.column_config.NumberColumn(format="%.2f", step=0.25),
                                  "Spread (%)": st.column_config.NumberColumn(format="%.2f", step=0.05),
                                  "Amortization (% a year)": st.column_config.NumberColumn(format="%.2f", step=0.25),
                                  "Maturity (years)": st.column_config.NumberColumn(format="%d", step=1)})
    st.caption(
        "Debt sizes, spreads, amortization, maturities and fees are **illustrative assumptions**, not market data. "
        f"{config.LBO_TRANCHES[0]['name']}: floating, base rate {pct(lbo_inputs['tranches'][0]['base_rate'], 2)} "
        f"(13-week T-bill, SOFR proxy) + spread, prepayable (cash sweep). {config.LBO_TRANCHES[1]['name']}: fixed, "
        f"10-year Treasury {pct(choices['risk_free'], 2)} (sidebar risk-free rate) + spread, repaid at maturity.")

    if not lbo_ready:
        st.info("Enter the base rates (and a risk-free rate in the sidebar) to run the LBO; it needs positive EBITDA.")
    else:
        r = lbo_result
        e, su, x, ret = r["entry"], r["sources_uses"], r["exit"], r["returns"]
        rng = lbo.price_range(lbo_kw, tv, lbo_inputs, r)
        m = st.columns(5)
        m[0].metric("Offer price", cur(e["offer_price"]), f"{pct(e['premium'])} premium", delta_color="off")
        m[1].metric("Entry EV / EBITDA", mult(e["entry_multiple"]))
        m[2].metric("Sponsor IRR", pct(ret["irr"]))
        m[3].metric("MOIC", "N/A" if _missing(ret["moic"]) else f"{ret['moic']:.2f}x")
        m[4].metric(f"Max price for {pct(lbo_inputs['target_irr'], 0)} IRR", cur(rng["mid"]),
                    None if _missing(rng["mid"]) else pct(rng["mid"] / price - 1) + " vs price")
        st.caption("IRR: the yearly return on the sponsor's equity. MOIC: money out / money in. "
                   "The maximum price keeps the exit multiple fixed.")
        for w in r["warnings"]:
            st.warning(w)

        st.subheader(f"Sources and uses ({CCY})")
        a, b = st.columns(2)
        uses = pd.DataFrame({"Amount": [bn(v) for v in su["uses"].values()]}, index=list(su["uses"]))
        uses.loc["Total uses"] = bn(su["total"])
        a.dataframe(uses, width="stretch", column_config={"_index": st.column_config.Column(width="medium")})
        src = pd.DataFrame({"Amount": [bn(v) for v in su["sources"].values()],
                            "% of total": [pct(v / su["total"]) for v in su["sources"].values()],
                            "x EBITDA": [mult(v / e["ebitda"]) for v in su["sources"].values()]},
                           index=list(su["sources"]))
        src.loc["Total sources"] = [bn(su["total"]), "100.0%", mult(su["total"] / e["ebitda"])]
        b.dataframe(src, width="stretch", column_config={"_index": st.column_config.Column(width="medium")})
        st.caption(f"Entry EV {bn(e['entry_ev'])} = offer price x shares + net debt. Existing debt is repaid; "
                   "the sponsor's equity is the balancing item.")

        st.subheader(f"Cash flow and debt schedule ({CCY})")
        sched = r["schedule"]
        ratio_rows = ["Total debt / EBITDA", "Net debt / EBITDA", "EBITDA / interest", "Interest / EBITDA"]
        view = pd.DataFrame(index=sched.index, columns=sched.columns, dtype=object)
        for row in sched.index:
            f = pct if row == "Interest / EBITDA" else mult if row in ratio_rows else bn
            view.loc[row] = sched.loc[row].map(f).values
        st.dataframe(view, width="stretch", height=rows_height(view),
                     column_config={"_index": st.column_config.Column(width="large")})
        st.caption("Interest is charged on the debt at the start of each year (no circular reference). Interest above "
                   f"{config.LBO_INTEREST_DEDUCTION_CAP:.0%} of EBITDA is not tax-deductible (section 163(j), "
                   "simplified). Cash above the minimum repays the prepayable loan (cash sweep).")

        st.subheader("Exit and returns")
        a, b = st.columns(2)
        exit_rows = [(f"EBITDA, Year {x['year']}", bn(x["ebitda"])), ("x Exit multiple", mult(x["multiple"])),
                     ("= Exit enterprise value", bn(x["ev"])), ("- Net debt at exit", bn(x["net_debt"])),
                     ("= Sponsor equity at exit", bn(x["equity"])), ("Sponsor equity at entry", bn(ret["equity_in"])),
                     ("MOIC", "N/A" if _missing(ret["moic"]) else f"{ret['moic']:.2f}x"), ("IRR", pct(ret["irr"]))]
        a.dataframe(pd.DataFrame(exit_rows, columns=["", "Value"]).set_index(""), width="stretch")
        lo_irr, hi_irr = config.LBO_IRR_RANGE
        prices = pd.DataFrame({"Max price per share": [cur(rng["low"]), cur(rng["mid"]), cur(rng["high"])],
                               "vs current price": [pct(p / price - 1) if not _missing(p) else "N/A"
                                                    for p in (rng["low"], rng["mid"], rng["high"])]},
                              index=[f"{hi_irr:.0%} IRR", f"{lbo_inputs['target_irr']:.0%} IRR (target)",
                                     f"{lo_irr:.0%} IRR"])
        b.dataframe(prices, width="stretch")
        b.caption(f"Current price {cur(price)}. These three prices are the LBO row of the football field.")

        st.subheader("Sensitivity: sponsor IRR")
        h1, h2 = st.columns(2)
        h1.plotly_chart(charts.irr_heatmap(lbo.irr_entry_exit(lbo_kw, tv, lbo_inputs), lbo_inputs["target_irr"],
                                           "Entry x exit multiple", (e["entry_multiple"], x["multiple"])),
                        width="stretch")
        h2.plotly_chart(charts.irr_heatmap(lbo.irr_leverage_exit(lbo_kw, tv, lbo_inputs), lbo_inputs["target_irr"],
                                           "Total leverage x exit multiple",
                                           (r["credit"]["entry_leverage"], x["multiple"])),
                        width="stretch")
        st.caption(f"Exit columns: {TARGET}'s current multiple ±1x and exit at the entry multiple (\"= entry\": each "
                   "row's own entry multiple; right table: the entry multiple of your case). Leverage changes "
                   f"through the {config.LBO_TRANCHES[0]['name']}.")

        st.subheader("Is such a buyout realistic?")
        st.info("\n\n".join(lbo.feasibility(r)).replace("$", "\\$"))     # "$" would start a formula
        st.caption(" · ".join(f"[{d['name']}]({d['source']})" for d in config.LBO_LARGEST_DEALS))
        with st.expander("Simplifications and why the exit is at today's multiple"):
            st.markdown("\n".join(f"- {t}" for t in lbo.SIMPLIFICATIONS))


# ---------------------------------------------------------------------------
# M&A: accretion / dilution of the buyer's EPS
# ---------------------------------------------------------------------------

EX_PATH_OK = merger.EXAMPLES_PATH.exists()
EX_NAME = "DKS-FL-2025"
MA_MODES = ["Pick from S&P 500", "Enter ticker manually", "Load example: DKS–FL (2025)"]


def ma_default_target() -> str:
    """A peer that is already downloaded: the first one smaller than the buyer, else the first peer."""
    if not PEERS:
        return ""
    buyer = peers.find(universe, TARGET) if universe is not None else None
    if buyer is not None and buyer["market_cap"] > 0:
        for t in PEERS:
            p = peers.find(universe, t)
            if p is not None and 0 < p["market_cap"] < buyer["market_cap"]:
                return t
    return PEERS[0]


def ma_num(key: str) -> float:
    v = st.session_state.get(key)
    return float("nan") if v is None else float(v)


def ma_pct(key: str) -> float:
    return ma_num(key) / 100


def ma_reset(defaults: dict):
    for k, v in defaults.items():
        st.session_state[k] = v


with tab_ma:
    example_mode = EX_PATH_OK and st.session_state.get("ma_mode") == MA_MODES[2]
    if example_mode:
        st.info("**Historical transaction, inputs from SEC filings** (sources at the bottom of this tab). Dick's "
                "acquisition of Foot Locker was announced on 15 May 2025. The result is a model exercise, not a "
                "recommendation or a forecast; the inputs are editable.")
    else:
        st.warning("**Hypothetical transaction, for illustration only.** The result is neither a recommendation nor "
                   "a forecast; the deal terms are illustrative assumptions you can edit.")
    st.markdown(
        "**Would buying another company raise or lower the buyer's earnings per share (EPS)?** If EPS goes up the deal "
        "is *accretive*, if it goes down it is *dilutive*. Paying in shares adds shares; paying in cash costs "
        "interest (or the interest the cash was earning); the target's earnings and the *synergies* (savings the "
        "merger creates) have to make up for that.")

    # --- who buys whom -----------------------------------------------------------------
    if st.session_state.get("ma_buyer") != TARGET:        # a new buyer: start from its default target
        st.session_state["ma_buyer"] = TARGET
        default = ma_default_target()
        in_list = universe is not None and default in LABELS
        st.session_state["ma_mode"] = MA_MODES[0] if in_list else MA_MODES[1]
        if in_list:
            st.session_state["ma_target_sp"] = default
        st.session_state["ma_target_text"] = default
    modes = [m for i, m in enumerate(MA_MODES) if (i == 0 and universe is not None) or i == 1 or (i == 2 and EX_PATH_OK)]
    c1, c2 = st.columns([1, 2])
    mode = c1.radio("Company to acquire", modes, key="ma_mode")
    ex_basis = None
    if mode == MA_MODES[0]:
        c1.markdown(f"**Buyer:** {NAME} ({TARGET})")
        c2.selectbox("Company to acquire (type to search)", list(LABELS), key="ma_target_sp", format_func=LABELS.get)
        ma_target = st.session_state["ma_target_sp"]
    elif mode == MA_MODES[1]:
        c1.markdown(f"**Buyer:** {NAME} ({TARGET})")
        c2.text_input("Ticker (Yahoo Finance symbol)", key="ma_target_text")
        ma_target = st.session_state["ma_target_text"].strip().upper()
    else:
        ma_target = "FL"
        c1.markdown("**Buyer:** DICK'S Sporting Goods (DKS), **target:** Foot Locker (FL)")
        ex_labels = {k: v for k, v in merger.EXAMPLE_BASES.items() if k != "fy2024"}
        ex_basis = c2.radio("Earnings basis for the comparison", list(ex_labels), key="ma_ex_basis",
                            format_func=ex_labels.get)

    tdata = None
    if example_mode:
        tdata = True                 # the example brings its own data
    elif not ma_target:
        st.info("Choose the company to acquire.")
    elif ma_target == TARGET:
        st.info(f"The buyer is {TARGET}: choose a different company to acquire.")
    elif ma_target in companies:
        tdata = data
    else:
        tdata = load_data((ma_target,), 0, False)
        if tdata["errors"]:
            load_data.clear((ma_target,), 0, False)       # a failed download is not kept
            st.error(f"Could not load {ma_target}. {tdata['errors'][ma_target]}")
            tdata = None

    examples = merger.load_examples() if EX_PATH_OK else None
    short_now = data.get("short_rate", {}).get("rate", float("nan"))
    if tdata is not None and example_mode:
        acq, tgt, ex_inp, ex_facts = merger.example_pair(examples, EX_NAME, ex_basis,
                                                         0.04 if _missing(short_now) else short_now)
    elif tdata is not None:
        ma_data = {"companies": {TARGET: companies[TARGET], ma_target: tdata["companies"][ma_target]},
                   "risk_free": data["risk_free"], "short_rate": data.get("short_rate", {})}
        try:
            acq, tgt = merger.pair_inputs(ma_data, TARGET, ma_target)
        except Exception as e:
            st.error(f"The merger model could not use the data of {ma_target} ({str(e)[:150]}).")
            acq = tgt = None
        if acq is not None and acq["currency"] != tgt["currency"]:
            st.error(f"{TARGET} trades in {acq['currency']} and {ma_target} in {tgt['currency']}; the model does not "
                     "convert currencies. Choose a company that trades in the same currency.")
            acq = tgt = None

    if tdata is not None and acq is not None:
        if example_mode:
            base = ex_inp
        else:
            base = merger.default_inputs(acq, tgt, choices["risk_free"], short_now)
        direct = lambda k, scale=1e-6: None if base.get(k) is None else base[k] * scale
        defaults = {
            "ma_price_mode": "Offer price" if example_mode else "Premium", "ma_premium": 25.0,
            "ma_offer_price": base["offer_price"] if example_mode else round(tgt["price"] * 1.25, 2),
            "ma_stock": base["stock_pct"] * 100 if example_mode else 50.0,
            "ma_debt_rate": None if _missing(base["debt_rate"]) else base["debt_rate"] * 100,
            "ma_cash_rate": None if _missing(base["cash_rate"]) else base["cash_rate"] * 100,
            "ma_refi": False, "ma_cost_syn": base["cost_synergies"] / tgt["revenue"] * 100 if example_mode else 1.0,
            "ma_rev_syn": 0.0, "ma_phase1": 50.0, "ma_phase2": 100.0,
            "ma_integ": 1.0, "ma_fee": base["fee_pct"] * 100, "ma_intang": 30.0, "ma_amort": 10,
            "ma_mincash": base["min_cash_pct"] * 100,
            "ma_tax": None if _missing(base["tax_rate"]) else base["tax_rate"] * 100,
            "ma_new_debt": direct("new_debt"), "ma_fees_amt": direct("fees_amount"),
            "ma_amort_amt": direct("amortisation_per_year")}
        ma_key = ("example", EX_NAME) if example_mode else (TARGET, ma_target)
        if st.session_state.get("ma_for") != ma_key:
            ma_reset(defaults)
            st.session_state["ma_for"] = ma_key
        for k, v in defaults.items():
            st.session_state.setdefault(k, v)

        st.caption(f"{acq['ticker']}: {acq['eps_source']}, fiscal year ends {acq['fiscal_year_end']}. "
                   f"{tgt['ticker']}: {tgt['eps_source']}, fiscal year ends {tgt['fiscal_year_end']}. "
                   + ("Year 1 is the first full fiscal year after closing (fiscal 2026); Year 2 grows the target's "
                      "earnings as stated above and holds the buyer's flat." if example_mode else
                      "Year 2 grows each company's earnings at its DCF Base revenue growth."))

        # --- assumptions -------------------------------------------------------------
        st.subheader("Deal assumptions")
        a = st.columns(4)
        a[0].radio("Offer set by", ["Premium", "Offer price"], key="ma_price_mode", horizontal=True)
        if st.session_state["ma_price_mode"] == "Premium":
            a[1].number_input("Premium over the target's price (%)", key="ma_premium", step=1.0, format="%.1f",
                              help="Illustrative assumption.")
        else:
            a[1].number_input(f"Offer price per share ({CCY})", key="ma_offer_price", step=1.0, format="%.2f")
        a[2].number_input("Paid in stock (%)", key="ma_stock", step=5.0, format="%.0f",
                          help="The rest is cash: the buyer's spare cash (above its minimum cash) first, then new debt.")
        a[3].checkbox("Refinance the target's debt", key="ma_refi",
                      help="Off: the target's debt is assumed and its interest stays in its earnings. On: the debt is "
                           "repaid with new debt at the new rate.")
        b = st.columns(4)
        b[0].number_input("Interest rate on new debt (%)", key="ma_debt_rate", step=0.05, format="%.2f",
                          help="Illustrative assumption: 10-year Treasury + the credit spread used in the WACC.")
        b[1].number_input("Interest earned on cash (%)", key="ma_cash_rate", step=0.05, format="%.2f",
                          help="13-week T-bill (^IRX), what the cash used would have earned.")
        b[2].number_input("Buyer tax rate (%)", key="ma_tax", step=0.5, format="%.1f",
                          help="The buyer's DCF tax rate (average of the last 3 fiscal years).")
        b[3].number_input("Transaction fees (% of target EV)", key="ma_fee", step=0.1, format="%.2f",
                          help="Illustrative assumption; one-off, not in the adjusted EPS.")
        s = st.columns(4)
        s[0].number_input("Cost synergies (% of target sales, run rate)", key="ma_cost_syn", step=0.1, format="%.2f",
                          help="Illustrative assumption.")
        s[1].number_input(f"Revenue synergies ({CCY}m of profit)", key="ma_rev_syn", step=10.0, format="%.0f",
                          help="Pre-tax profit contribution at the run rate; default 0.")
        s[2].number_input("Synergies realised, Year 1 (%)", key="ma_phase1", step=5.0, format="%.0f",
                          help="Illustrative assumption.")
        s[3].number_input("Synergies realised, Year 2 (%)", key="ma_phase2", step=5.0, format="%.0f")
        p = st.columns(4)
        p[0].number_input("Intangibles (% of the price above book value)", key="ma_intang", step=5.0, format="%.0f",
                          help="Illustrative: the part of the price paid above the target's book value that is "
                               "assigned to amortisable intangibles (brands, customer relationships). The rest is "
                               "goodwill, which is not amortised.")
        p[1].number_input("Amortisation period (years)", key="ma_amort", min_value=1, step=1, format="%d")
        p[2].number_input("Integration cost (x run-rate synergies)", key="ma_integ", step=0.5, format="%.1f",
                          help="Illustrative assumption; one-off, not in the adjusted EPS.")
        p[3].number_input("Minimum cash kept (% of buyer sales)", key="ma_mincash", step=0.5, format="%.1f")
        p[3].button("Reset M&A inputs", on_click=ma_reset, args=(defaults,))
        with st.expander("Set directly (optional; leave empty to let the model work it out)", expanded=example_mode):
            d = st.columns(3)
            d[0].number_input(f"New debt ({CCY}m)", key="ma_new_debt", step=50.0, format="%.0f",
                              help="Empty: the buyer's spare cash is used first and the rest is new debt.")
            d[1].number_input(f"Transaction fees ({CCY}m)", key="ma_fees_amt", step=5.0, format="%.1f",
                              help="Empty: the fee percentage above times the target's enterprise value.")
            d[2].number_input(f"Amortization per year ({CCY}m, pre-tax)", key="ma_amort_amt", step=1.0, format="%.2f",
                              help="Empty: the intangibles share above of the price above book value, over the "
                                   "amortisation period.")

        inp = {"premium": ma_pct("ma_premium"),
               "offer_price": ma_num("ma_offer_price") if st.session_state["ma_price_mode"] == "Offer price" else None,
               "stock_pct": ma_pct("ma_stock"), "debt_rate": ma_pct("ma_debt_rate"),
               "cash_rate": ma_pct("ma_cash_rate"), "refinance_target_debt": bool(st.session_state["ma_refi"]),
               "cost_synergies": ma_pct("ma_cost_syn") * tgt["revenue"],
               "revenue_synergies": (ma_num("ma_rev_syn") if not _missing(ma_num("ma_rev_syn")) else 0.0) * 1e6,
               "phase_in": (ma_pct("ma_phase1"), ma_pct("ma_phase2")),
               "integration_cost_x": ma_num("ma_integ"), "fee_pct": ma_pct("ma_fee"),
               "intangibles_pct": ma_pct("ma_intang"), "amortisation_years": int(st.session_state["ma_amort"] or 1),
               "min_cash_pct": ma_pct("ma_mincash"), "tax_rate": ma_pct("ma_tax")}
        for key, field in (("ma_new_debt", "new_debt"), ("ma_fees_amt", "fees_amount"),
                           ("ma_amort_amt", "amortisation_per_year")):
            v = st.session_state.get(key)
            inp[field] = None if v is None else float(v) * 1e6
        gaps = merger.missing_inputs(acq, tgt, inp)
        gaps += [k for k, v in [("premium", inp["premium"] if inp["offer_price"] is None else 0),
                                ("stock share", inp["stock_pct"]), ("synergies", inp["cost_synergies"]),
                                ("synergy phase-in", inp["phase_in"][0] + inp["phase_in"][1]),
                                ("fees", inp["fee_pct"]), ("integration cost", inp["integration_cost_x"]),
                                ("intangibles share", inp["intangibles_pct"]),
                                ("minimum cash", inp["min_cash_pct"])] if _missing(v)]
        if gaps:
            st.info("Not enough data to run the merger model, so the results are N/A. Missing or empty: "
                    + ", ".join(gaps) + ". Enter the value (rates can be typed in the boxes above) or choose "
                    "another company.")
        else:
            r = merger.run(acq, tgt, inp)
            be_premium = merger.breakeven_premium(acq, tgt, inp)
            y1, y2 = r["years"]
            sym_cur = lambda x: cur(x).replace("$", "\\$")      # a bare "$" would start a formula
            sgn = lambda x: "N/A" if _missing(x) else f"{x:+.1%}"

            m = st.columns(2)
            m[0].metric(f"{merger.LABEL_INCL}, Year 1", sgn(y1["accretion_gaap_pct"]),
                        f"{sym_cur(y1['eps_gaap'])} vs {sym_cur(y1['standalone_eps'])} standalone",
                        delta_color="off", delta_arrow="off",
                        help="Change in the buyer's EPS, after the amortization of the intangibles acquired. "
                             + merger.EPS_NOTE)
            m[1].metric(f"{merger.LABEL_EXCL}, Year 1", sgn(y1["accretion_cash_pct"]),
                        f"{sym_cur(y1['eps_cash'])} vs {sym_cur(y1['standalone_eps'])} standalone",
                        delta_color="off", delta_arrow="off", help="Change in the buyer's EPS, before that amortization.")
            m = st.columns(3)
            if y1["breakeven_run_rate"] <= 0:       # already accretive without any synergies
                m[0].metric("Breakeven synergies", "None needed", f"{bn(-y1['breakeven_run_rate'])} of cushion",
                            delta_color="off", delta_arrow="off",
                            help="Year 1 EPS incl. deal amortization is already above standalone before synergies; the "
                                 "cushion is the yearly (run-rate) saving that could be given up.")
            else:
                m[0].metric("Breakeven synergies", bn(y1["breakeven_run_rate"]),
                            f"run rate; {bn(y1['breakeven_synergies'])} in Year 1", delta_color="off",
                            delta_arrow="off", help="Yearly cost savings that would leave Year 1 EPS incl. deal "
                                                    "amortization unchanged.")
            m[1].metric("Breakeven premium", "None" if _missing(be_premium) else pct(be_premium),
                        f"offer {pct(r['premium'])}", delta_color="off", delta_arrow="off",
                        help="The highest premium at which Year 1 EPS incl. deal amortization, synergies included, "
                             "is unchanged.")
            m[2].metric("Ownership", f"{r['ownership']['buyer']:.0%} / {r['ownership']['target']:.0%}",
                        "buyer / target", delta_color="off", delta_arrow="off",
                        help=f"{r['new_shares'] / 1e6:,.1f}m new buyer shares are issued.")
            for w in r["warnings"]:
                st.warning(w)

            # --- both EPS definitions side by side ------------------------------------------
            st.subheader("EPS with and without the deal's amortization")
            eps_table = pd.DataFrame(
                [[cur(y["standalone_eps"]), cur(y["eps_gaap"]), sgn(y["accretion_gaap_pct"]), cur(y["eps_cash"]),
                  sgn(y["accretion_cash_pct"])] for y in (y1, y2)],
                index=["Year 1", "Year 2"],
                columns=pd.MultiIndex.from_tuples([("Buyer standalone", "EPS"), (merger.LABEL_INCL, "EPS"),
                                                   (merger.LABEL_INCL, "vs standalone"), (merger.LABEL_EXCL, "EPS"),
                                                   (merger.LABEL_EXCL, "vs standalone")]))
            st.dataframe(eps_table, width="stretch")
            st.caption(
                f"**{merger.LABEL_INCL}** is after the amortization of the intangible assets acquired in the deal; "
                f"**{merger.LABEL_EXCL}** leaves that amortization out. The gap between them comes entirely from it "
                f"({bn(r['ppa']['annual_amortisation'])} a year before tax here). Companies usually put the adjusted "
                "EPS, which excludes it, at the front of their acquisition announcements. So when a deal is called "
                "'accretive', check which EPS definition is meant: it can be accretive without the amortization and "
                "dilutive with it. One-off transaction fees and integration costs are in neither EPS: "
                f"{bn(r['one_off']['transaction_fees'])} and {bn(r['one_off']['integration_costs'])}. "
                f"**Note:** {merger.EPS_NOTE}")

            # --- model vs the buyer's announced expectation (real-deal example only) --------------------
            if example_mode:
                st.subheader("Model vs Dick's expectation")
                short_ex = 0.04 if _missing(short_now) else short_now
                cmp_rows = []
                for b_key, b_label in ex_labels.items():
                    a2, t2, _, _ = merger.example_pair(examples, EX_NAME, b_key, short_ex)
                    z1, z2 = merger.run(a2, t2, inp)["years"]
                    cmp_rows.append({
                        "Earnings basis": b_label, f"{t2['ticker']} EPS used, Year 1": cur(t2["eps"]),
                        "Year 1 (incl. amort.)": sgn(z1["accretion_gaap_pct"]),
                        "Year 1 (excl. amort.)": sgn(z1["accretion_cash_pct"]),
                        "Year 2 (incl. amort.)": sgn(z2["accretion_gaap_pct"]),
                        "Breakeven synergies": "None needed" if z1["breakeven_run_rate"] <= 0
                        else bn(z1["breakeven_run_rate"])})
                st.dataframe(pd.DataFrame(cmp_rows).set_index("Earnings basis"), width="stretch")
                st.caption(f"EPS change versus {acq['ticker']} standalone. 'incl. amort.' = {merger.LABEL_INCL}; "
                           f"'excl. amort.' = {merger.LABEL_EXCL}. Synergies are the model's illustrative phase-in "
                           "(50% in Year 1, 100% in Year 2) of the midpoint of Dick's $100-125 million.".replace("$", "\\$"))
                a3, t3, _, _ = merger.example_pair(examples, EX_NAME, "fy2024", short_ex)
                chk = merger.run(a3, t3, inp, synergies_on=False)["years"][0]
                ref_pf = ex_facts["ref_pf_eps_fy2024_cash"] / ex_facts["ref_eps_fy2024_standalone"] - 1
                st.markdown((
                    f"**What Dick's said:** {ex_facts['ref_expectation']}, with $100-125 million of cost synergies "
                    "in the medium term. No number is given. "
                    f"**Mechanics check:** on fiscal 2024 actuals without synergies, Dick's own pro forma EPS falls "
                    f"from ${ex_facts['ref_eps_fy2024_standalone']:.2f} to ${ex_facts['ref_pf_eps_fy2024_cash']:.2f} "
                    f"({ref_pf:+.1%}); this model gives {chk['accretion_gaap_pct']:+.1%}. Dick's figure includes about "
                    "$53 million after tax of one-time costs and purchase-accounting items the model leaves out "
                    "(and the model deducts the interest lost on the cash used, which Dick's pro forma does not).")
                    .replace("$", "\\$"))
                st.markdown((
                    "**Which basis is comparable?** Dick's expectation is for the *first full fiscal year after "
                    "closing*, *excluding one-time costs*, and with the cost synergies. That is basis (b): management's "
                    "projections for fiscal 2026 describe a recovering Foot Locker, and the model reports the one-time "
                    "costs separately. Basis (a) starts from Foot Locker's trailing twelve months, which include a "
                    f"$363 million net loss in the first quarter of fiscal 2025 (with ${ex_facts['t_impairment_q'] / 1e6:,.0f} "
                    "million of impairment charges), so it shows a large dilution that Dick's expectation does not "
                    "describe: it measures the starting point, not the first full year. Basis (b) is itself generous: "
                    "it is built from *adjusted* EBITDA of a plan that nearly doubles Foot Locker's earnings, and Foot "
                    "Locker's own board asked for the lower scenario (b') because of tariffs and weak first-quarter "
                    "results. The deal is accretive in both (b) cases, but only if Foot Locker recovers.")
                    .replace("$", "\\$"))

            # --- sources and uses, purchase price allocation ---------------------------------
            st.subheader(f"Sources and uses ({CCY})")
            u, so = r["sources_uses"]["uses"], r["sources_uses"]["sources"]
            c1, c2, c3 = st.columns(3)
            c1.dataframe(pd.DataFrame({"Amount": [bn(v) for v in u.values()] + [bn(sum(u.values()))]},
                                      index=["Purchase of target equity", "Transaction fees", "Refinance target debt",
                                             "Total uses"]), width="stretch")
            c2.dataframe(pd.DataFrame({"Amount": [bn(v) for v in so.values()] + [bn(sum(so.values()))]},
                                      index=["New buyer shares", "Buyer's excess cash", "New debt", "Total sources"]),
                         width="stretch")
            ppa = r["ppa"]
            c3.dataframe(pd.DataFrame({"Amount": [bn(ppa["excess_over_book"]), bn(ppa["intangibles"]),
                                                  bn(ppa["goodwill"]), bn(ppa["annual_amortisation"])]},
                                      index=["Price above book value", "Amortisable intangibles", "Goodwill (not amortised)",
                                             "Amortisation per year"]), width="stretch")
            st.caption(f"Offer {cur(r['offer_price'])} per share ({pct(r['premium'])} premium), equity {bn(r['equity_value'])}, "
                       f"offer P/E {mult(r['offer_pe'])}. The purchase price allocation is simplified: the price above "
                       "the target's book value is split into intangibles (amortised) and goodwill (not amortised).")

            # --- EPS bridge (waterfall) --------------------------------------------------
            st.subheader("EPS bridge")
            w1, w2 = st.columns(2)
            basis_label = w1.radio("EPS definition", [merger.LABEL_INCL, merger.LABEL_EXCL], key="ma_basis",
                                   horizontal=True)
            year = 1 if w2.radio("Year", ["Year 1", "Year 2"], key="ma_year", horizontal=True) == "Year 1" else 2
            basis = "gaap" if basis_label == merger.LABEL_INCL else "cash"
            st.plotly_chart(
                charts.eps_waterfall(merger.bridge(r, acq, year, basis),
                                     f"{acq['ticker']} Year {year} EPS bridge, {basis_label}: from standalone to "
                                     f"pro forma ({'example deal: ' + acq['ticker'] + ' / ' + tgt['ticker'] if example_mode else 'hypothetical deal for ' + ma_target})",
                                     CCY), width="stretch")

            # --- quick check ---------------------------------------------------------------
            st.subheader("Quick check")
            st.markdown(merger.explain(merger.quick_check(acq, tgt, inp, r), inp).replace("$", "\\$"))

            # --- sensitivities ---------------------------------------------------------------
            st.subheader(f"Sensitivity: Year 1 EPS vs standalone ({basis_label})")
            h1, h2 = st.columns(2)
            t1 = merger.sensitivity_premium_stock(acq, tgt, inp, basis)
            h1.plotly_chart(charts.accretion_heatmap(t1, "Premium x share paid in stock", (r["premium"], inp["stock_pct"])),
                            width="stretch")
            t2 = merger.sensitivity_synergy_premium(acq, tgt, inp, basis)
            h2.plotly_chart(charts.accretion_heatmap(
                t2, "Cost synergies x premium", (inp["cost_synergies"] / tgt["revenue"], r["premium"]),
                row_fmt=lambda x: f"{x:.1%}"), width="stretch")

            # --- pro forma credit, downloads, simplifications --------------------------------
            cr = r["credit"]
            st.caption(f"Pro forma credit (Year 1): debt {bn(cr['debt'])}, debt / EBITDA {mult(cr['debt_to_ebitda'])}, "
                       f"EBITDA / interest {mult(cr['ebitda_to_interest'])}.")
            st.download_button("Download M&A Excel (live formulas)", type="primary",
                               data=export.build_merger_workbook(acq, tgt, inp, r, be_premium, CCY),
                               file_name=f"{acq['ticker']}_{tgt['ticker']}_merger.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with st.expander("Simplifications"):
                st.markdown("\n".join(f"- {t}" for t in merger.SIMPLIFICATIONS))
            if example_mode:
                with st.expander("Sources for every number (SEC filings)"):
                    st.dataframe(merger.example_sources(examples, EX_NAME), hide_index=True, width="stretch")
                    st.caption("Trailing twelve months = fiscal 2024 + Q1 fiscal 2025 - Q1 fiscal 2024. Rows marked "
                               "DERIVED are calculated from sourced numbers.")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

with tab_sc:
    st.markdown(
        "Three versions of the future. **Bear**, **Base** and **Bull** differ in how the business performs "
        "(growth, margins, capex, terminal growth, exit multiple). The **WACC is the same in all three**: "
        "it measures how risky the cash flows are, not how good they turn out to be. D&A, working capital "
        "and the tax rate are shared too.")
    st.subheader("Assumptions")
    st.data_editor(scenario_base_table(sc_defaults), key="scenario_editor", width="stretch",
                   disabled=["Base", "Why (Bear / Bull default)"],
                   # a fine step: the editor cuts displayed values to the step size instead of rounding
                   column_config={c: st.column_config.NumberColumn(c, format="%.2f", step=0.0001) for c in SC_COLS}
                   | {"Why (Bear / Bull default)": st.column_config.TextColumn(width="large")})
    st.caption("Base comes from the sidebar; edit Bear and Bull here (percentages in %, the exit multiple in x). "
               "Reset inputs in the sidebar restores the defaults.")
    with st.expander("Why these Bear / Bull defaults"):
        for f, (label, _) in scenarios.FIELDS.items():
            st.markdown(f"- **{label}**: {sc_defaults['rationale'][f]}")

    st.subheader("Probability weights")
    w1, w2, w3 = st.columns(3)
    for col, sc in zip((w1, w2, w3), config.SCENARIOS):
        col.number_input(f"{config.SCENARIO_LABELS[sc]} (%)", key=f"w_{sc}", min_value=0.0, max_value=100.0,
                         step=5.0, format="%.1f")
    if not scenarios.weights_ok(weights):
        st.warning(f"The weights add up to {sum(weights.values()):.1%}, not 100%: the probability-weighted "
                   f"value is N/A until they do.")

    st.subheader(f"Results ({CCY})")
    if not HAS_DCF:
        st.info(f"Enter the risk-free rate in the sidebar ({rf_data['guidance']}) to run the scenarios.")
    labels = {"revenue_y5": f"Revenue, Year {config.PROJECTION_YEARS}", "ebitda_y5": f"EBITDA, Year {config.PROJECTION_YEARS}",
              "fcff_y5": f"FCFF, Year {config.PROJECTION_YEARS}",
              "gordon_ev": "Enterprise value (Gordon)", "gordon_value": "Value per share: Gordon growth",
              "gordon_upside": "  vs current price", "exit_ev": "Enterprise value (exit multiple)",
              "exit_value": "Value per share: exit multiple", "exit_upside": "  vs current price "}
    shown = pd.DataFrame(index=[labels[k] for k in sc_results.index], columns=SC_COLS + ["Probability-weighted"],
                         dtype=object)
    for k in sc_results.index:
        f = pct if "upside" in k else cur if k.endswith("_value") else money
        shown.loc[labels[k]] = [("" if c == "weighted" and _missing(sc_results.loc[k, c]) and k not in
                                 ("gordon_value", "exit_value", "gordon_upside", "exit_upside")
                                 else f(sc_results.loc[k, c])) for c in list(config.SCENARIOS) + ["weighted"]]
    st.dataframe(shown, width="stretch", height=rows_height(shown))
    st.caption("Probability-weighted value = Bear x weight + Base x weight + Bull x weight, for each DCF method.")
    if HAS_DCF:
        st.plotly_chart(charts.scenario_chart(sc_results, price, f"{TARGET} scenarios: value per share", CCY),
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
# Precedent transactions
# ---------------------------------------------------------------------------

with tab_prec:
    st.markdown(
        "What acquirers actually paid for comparable companies. A buyer taking control usually pays a "
        "**control premium**, so precedent multiples often come out above trading multiples; older deals also "
        "reflect the market conditions of their time. **Only EV/EBITDA is used**: it is the standard yardstick in "
        "acquisitions, and EV/Sales would compare businesses with very different margins.")
    if prec_error:
        st.error(f"Your file could not be used, so the built-in deals are shown. {prec_error}")
    c1, c2 = st.columns(2)
    c1.toggle("Include second-tier deals (Staples, At Home)", key="prec_secondary",
              help="Less similar business models; left out by default.")
    c2.toggle("Include terminated deals (Kroger-Albertsons)", key="prec_terminated",
              help="Agreed but never completed, so the price was never paid; left out by default.")

    view = prec_all.copy()
    view.insert(0, "In calculation", prec_mask.map({True: "Yes", False: "No"}))
    view["announcement_date"] = view["announcement_date"].dt.strftime("%Y-%m-%d")
    view["enterprise_value"] = view["enterprise_value"].map(lambda x: "N/A" if _missing(x) else f"{x:,.0f}")
    view["ev_ebitda"] = view["ev_ebitda"].map(mult)
    view["premium_pct"] = view["premium_pct"].map(lambda x: "N/A" if _missing(x) else f"{x:.1f}%")
    view = view.rename(columns={"announcement_date": "Announced", "target": "Target", "acquirer": "Acquirer",
                                "status": "Status", "tier": "Tier", "deal_type": "Type",
                                "enterprise_value": "EV (m)", "currency": "Ccy", "ev_ebitda": "EV / LTM EBITDA",
                                "premium_pct": "Premium (info only)", "source_url": "Source"})
    st.dataframe(view[["In calculation", "Announced", "Target", "Acquirer", "Status", "Tier", "Type", "EV (m)", "Ccy",
                       "EV / LTM EBITDA", "Premium (info only)", "Source"]], width="stretch", hide_index=True,
                 column_config={"Source": st.column_config.LinkColumn("Source", display_text="SEC filing")})
    st.caption("Premiums are for information only and are not used: they are measured against different reference "
               "prices (a single close, 10- to 30-day averages, or a price months before the announcement).")
    with st.expander("Sources and notes for every figure"):
        for _, d in prec_all.iterrows():
            st.markdown(f"**{d['target']} / {d['acquirer']}** ({d['announcement_date']:%Y-%m-%d}): {d['notes']}")

    pv = precedents.valuation(tv, prec_deals)
    n_deals = int(pv["stats"]["count"])
    if not prec_applies:
        sectors = ", ".join(sorted(set(prec_deals["sector"]))) or "none"
        st.info(f"The deals in the calculation are from other sub-sectors ({sectors}) than {TARGET}, so they are "
                f"not used for it and the football field has no precedent row. You can upload your own CSV with "
                f"deals from {TARGET}'s sector below.")
    elif n_deals == 0:
        st.warning("No deal in the calculation: switch on more deals above or upload your own file.")
    else:
        if pv["small_sample"]:
            st.warning(f"Small sample: only {n_deals} deals in the calculation. Treat the range as a rough "
                       f"reference, not a market benchmark.")
        st.subheader(f"Implied value per share ({CCY})")
        st_ = pv["stats"]
        st.dataframe(pd.DataFrame({"EV / LTM EBITDA": [mult(st_[k]) for k in ["min", "p25", "median", "mean", "p75", "max"]]},
                                  index=["Min", "25th percentile", "Median", "Mean", "75th percentile", "Max"]).T,
                     width="stretch")
        b = pv["bridge"]
        rows = {"Target metric (TTM EBITDA)": [money(b["metric"])] * 3,
                "x Precedent multiple": [mult(x) for x in b["multiples"]],
                "= Implied enterprise value": [money(x) for x in b["implied_ev"]],
                "- Net debt": [money(b["net_debt"])] * 3,
                "= Implied equity value": [money(x) for x in b["equity_value"]],
                "/ Shares outstanding": [money(b["shares"])] * 3,
                "= Implied value per share": [cur(x) for x in b["per_share"]],
                "Upside vs current price": [pct(x / price - 1) for x in b["per_share"]]}
        st.dataframe(pd.DataFrame(rows, index=["Low", "Median", "High"]).T, width="stretch")
        st.caption(f"Range: {pv['method'].replace('peers', 'deals')}, the same rule as the comps.")

    if TARGET in config.PRECEDENT_SELECTED_FOR and uploaded is None and not prec_error:   # describes the built-in deals
        default_deals = prec_all[precedents.included_mask(prec_all)]
        d_med = precedents.multiple_stats(default_deals)["median"]
        own = mtable.loc[TARGET, "ev_ebitda"] if TARGET in mtable.index else float("nan")
        st.subheader("Commentary")
        st.markdown(
            f"The precedent multiples (median about **{d_med:.1f}x** for the default deals) sit below {TARGET}'s own "
            f"EV/EBITDA (**{mult(own)}**) and the comps median (**{mult(pstats.loc['median', 'ev_ebitda'])}**). "
            "Most of the deals involve smaller grocery chains, where margins are typically thin, or companies under "
            "pressure; half of them were leveraged buyouts by private equity funds, which can only pay what the "
            "debt and their return targets allow; and they come from different market periods. Amazon's purchase "
            "of Whole Foods, the only deal by an outside operating company seeking synergies, has the highest "
            "multiple (10.6x). So the usual rule that precedent values come out above the comps does not hold in "
            "this sample.")

    st.subheader("Use your own deals")
    st.file_uploader("Upload a precedents CSV (same columns as the template; used for this session only)",
                     type="csv", key="prec_upload")
    st.download_button("Download the CSV template", data=precedents.template_csv(), file_name="precedents_template.csv",
                       mime="text/csv")
    st.caption("Amounts in millions; ev_ebitda as a number (e.g. 7.1); status completed / terminated / pending; "
               "tier core / secondary; sector must match the target's sector code to be shown "
               f"({TARGET}: {precedents.target_sector(companies[TARGET]['meta']) or 'not mapped'}). "
               "Lines starting with # are comments.")


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
