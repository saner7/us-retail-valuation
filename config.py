"""Project settings. Change tickers or thresholds here, not inside the code."""

import os
from pathlib import Path

# --- Companies -------------------------------------------------------------
# The default analysis. Any other target and peer group can be chosen in the app.
TARGET = "TGT"
PEERS = ["WMT", "COST", "KR", "DG", "BJ"]
ALL_TICKERS = [TARGET] + PEERS
MAX_PEERS = 10            # each company costs ~6 Yahoo requests; more risks being blocked

COMPANY_NAMES = {         # display names for the default set; others use Yahoo's name
    "TGT": "Target",
    "WMT": "Walmart",
    "COST": "Costco",
    "KR": "Kroger",
    "DG": "Dollar General",
    "BJ": "BJ's Wholesale Club",
}

# Companies whose recent quarters were checked for one-off items (impairments,
# gains, refunds...) against their filings; adjustments are in manual_overrides.csv.
# For any other company the app warns that one-offs were not checked.
ONE_OFFS_REVIEWED = {"TGT", "WMT", "COST", "KR", "DG", "BJ"}   # reviewed Oct 2026

# Banks and insurers: EV/EBITDA and an FCFF DCF do not fit their business model.
# Matched against Yahoo's industry name.
FINANCIAL_INDUSTRY_KEYWORDS = ("Bank", "Insurance")

# --- Currency --------------------------------------------------------------
# Results are shown in the target's trading currency. If a company reports in a
# different currency than it trades in, its statement figures are converted at
# the latest Yahoo exchange rate (ticker "<FROM><TO>=X").
CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}   # others: code + space

# --- WACC defaults (used in Stage 3, all editable in the app) ---------------
# Risk-free rate: 10-year government bond yield in the target's currency.
# Yahoo only provides it for USD; for other currencies the user enters it.
RISK_FREE_TICKERS = {"USD": "^TNX"}
RISK_FREE_TICKER = RISK_FREE_TICKERS["USD"]
RISK_FREE_GUIDANCE = {
    "USD": "10-year US Treasury yield",
    "EUR": ("10-year German government bond (Bund) yield, e.g. from the Deutsche Bundesbank or the ECB. "
            "Euro area companies usually use the Bund as the risk-free rate; for countries such as Italy, "
            "the Italian-German yield spread (BTP-Bund spread) can optionally be added as the country risk premium"),
    "GBP": "10-year UK government bond (gilt) yield, e.g. from the Bank of England",
    "CHF": "10-year Swiss Confederation bond yield, e.g. from the Swiss National Bank",
    "JPY": "10-year Japanese government bond (JGB) yield, e.g. from the Ministry of Finance Japan",
}
COUNTRY_RISK_PREMIUM = 0.0        # extra premium for country risk; 0 for the US

# --- Files -----------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
# A copy of the cache that is committed to git. Used only when the live download
# fails and there is no local cache (e.g. a fresh cloud deploy blocked by Yahoo).
# Refresh it with: python data_loader.py --refresh --save-snapshot
SNAPSHOT_DIR = DATA_DIR / "snapshot"
OVERRIDES_PATH = DATA_DIR / "manual_overrides.csv"

# Re-download data only if the cached copy is older than this
CACHE_MAX_AGE_HOURS = 24

# --- Data quality thresholds -----------------------------------------------
EV_MISMATCH_TOLERANCE = 0.10     # our EV vs Yahoo's EV
MCAP_MISMATCH_TOLERANCE = 0.05   # price x shares vs reported market cap
STALE_STATEMENT_MONTHS = 15      # latest annual report older than this = stale

# --- Comparable companies (Stage 2) ----------------------------------------
# With at least this many valid peers we use the 25th-75th percentile as the
# valuation range; with fewer, percentiles are not meaningful so we use min-max.
MIN_PEERS_FOR_QUARTILES = 4

# Multiples above these caps are treated as "not meaningful" (NM) and excluded,
# e.g. a P/E of 400x because earnings were almost zero that year.
MULTIPLE_CAPS = {"ev_sales": 10.0, "ev_ebitda": 50.0, "pe": 100.0, "pb": 50.0}

# --- Leases ----------------------------------------------------------------
# Under US GAAP (ASC 842) store rent is an operating expense, so EBITDA is
# already AFTER rent. Yahoo's "total debt" also contains lease liabilities.
# Counting leases as debt too would count rent twice, so by default we remove
# them: debt used for EV = total debt - lease liabilities.
EXCLUDE_LEASES_FROM_DEBT = True

# --- DCF (Stage 3) - all of these are editable in the app ------------------
PROJECTION_YEARS = 5
EQUITY_RISK_PREMIUM = 0.050       # assumption: extra return investors demand for stocks over bonds
CREDIT_SPREAD = 0.0125            # assumption: pre-tax cost of debt = risk-free rate + this spread
TERMINAL_GROWTH = 0.025           # assumption: long-run growth, roughly long-term US inflation/GDP
MID_YEAR_CONVENTION = True

# Default assumptions = average of the last N fiscal years. Older years are still
# shown for reference. (Target's year ending Jan-2023 was an inventory/markdown
# year that would otherwise drag every average down.)
DEFAULT_HISTORY_YEARS = 3

# Increase in net working capital (% of revenue). Historically very volatile for
# retailers (one inventory build swings it by 2-3% of revenue), so the default is 0.
DEFAULT_NWC_PCT = 0.0

# Revenue growth: Year 1 = growth already visible in the data (TTM revenue vs the
# last full fiscal year), then a straight line down (or up) to the terminal growth
# rate in the final projection year.

# Sensitivity grid: WACC +/- 2% in 0.5% steps, terminal growth +/- 1% in 0.5% steps,
# exit multiple +/- 2x in 1x steps
SENS_WACC_RANGE, SENS_WACC_STEP = 0.02, 0.005
SENS_GROWTH_RANGE, SENS_GROWTH_STEP = 0.01, 0.005
SENS_MULTIPLE_RANGE, SENS_MULTIPLE_STEP = 2.0, 1.0

# Warn if the terminal value is more than this share of enterprise value
TERMINAL_VALUE_WARNING = 0.75

# --- Football field (Stage 4) ----------------------------------------------
# DCF bars span every combination of base WACC +/- FF_WACC_DELTA with terminal
# growth +/- FF_GROWTH_DELTA (Gordon) or exit multiple +/- FF_MULTIPLE_DELTA (exit).
FF_WACC_DELTA = 0.005
FF_GROWTH_DELTA = 0.005
FF_MULTIPLE_DELTA = 1.0
# Rows of the football field, top to bottom. P/B stays in the comps tables but is
# not on the chart (see comps.MULTIPLE_NOTES).
FOOTBALL_FIELD_ROWS = ["52w", "ev_ebitda", "pe", "ev_sales", "dcf_exit", "dcf_gordon"]

# --- SEC EDGAR -------------------------------------------------------------
# The SEC asks every automated request to identify itself in the User-Agent and
# answers 403 to requests without a contact address. Put your own contact in an
# environment variable, never in the code (it would end up in git history):
#     export SEC_USER_AGENT="Your Name your@email.com"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "us-retail-valuation educational project")


def currency_symbol(currency: str) -> str:
    """'$' for USD, '€' for EUR, otherwise the code and a space ('CHF ')."""
    if not currency:
        return ""
    return CURRENCY_SYMBOLS.get(currency, f"{currency} ")


def risk_free_guidance(currency: str) -> str:
    return RISK_FREE_GUIDANCE.get(currency, f"10-year government bond yield in {currency}")
