"""Project settings. Change tickers or thresholds here, not inside the code."""

import os
from pathlib import Path

# --- Companies -------------------------------------------------------------
TARGET = "TGT"
PEERS = ["WMT", "COST", "KR", "DG", "BJ"]
ALL_TICKERS = [TARGET] + PEERS

COMPANY_NAMES = {
    "TGT": "Target",
    "WMT": "Walmart",
    "COST": "Costco",
    "KR": "Kroger",
    "DG": "Dollar General",
    "BJ": "BJ's Wholesale Club",
}

# --- Currency --------------------------------------------------------------
BASE_CURRENCY = "USD"     # all companies report in USD; no conversion needed

# --- WACC defaults (used in Stage 3, all editable in the app) ---------------
RISK_FREE_TICKER = "^TNX"         # 10-year US Treasury yield, downloaded live
COUNTRY_RISK_PREMIUM = 0.0        # extra premium for country risk; 0 for the US

# --- Files -----------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent
DATA_DIR = PROJECT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
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

# --- SEC EDGAR -------------------------------------------------------------
# The SEC asks every automated request to identify itself in the User-Agent and
# answers 403 to requests without a contact address. Put your own contact in an
# environment variable, never in the code (it would end up in git history):
#     export SEC_USER_AGENT="Your Name your@email.com"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "us-retail-valuation educational project")
