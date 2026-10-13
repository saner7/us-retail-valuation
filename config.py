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
WACC_MIN_SPREAD = 0.02           # WACC less than this above the risk-free rate: warning
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

# --- Scenarios (Stage B) ----------------------------------------------------
# Bear / Base / Bull differ in operating performance only; the WACC is the same
# in all three (it measures risk, not performance).
SCENARIOS = ["bear", "base", "bull"]
SCENARIO_LABELS = {"bear": "Bear", "base": "Base", "bull": "Bull"}
SCENARIO_WEIGHTS = {"bear": 0.25, "base": 0.50, "bull": 0.25}
# Bear/Bull defaults move this share of the way from Base to the most extreme
# historical value (0.5 = halfway). Moving all the way stacks every worst (best)
# year at once, which is not realistic.
SCENARIO_HALFWAY = 0.5
SCENARIO_TERMINAL_STEP = 0.005     # terminal growth: Base -/+ 0.5 percentage points
# Bull exit multiple at most this many times the target's own current EV/EBITDA:
# assuming it trades like the group's most premium members in 5 years is too optimistic.
BULL_EXIT_CAP_VS_OWN = 1.35

# --- Precedent transactions (Stage C) ----------------------------------------
PRECEDENTS_PATH = DATA_DIR / "precedents.csv"
UNIVERSE_PATH = DATA_DIR / "sp500_universe.csv"      # build_universe.py
YAHOO_RETRIES = 2                 # extra tries when Yahoo answers with nothing
YAHOO_RETRY_PAUSE = 2.0           # seconds between them
CURATED_PEERS_PATH = DATA_DIR / "curated_peers.csv"  # analyst-chosen groups, used before GICS
PEERS_SIZE_GAP = 10               # every peer below 1/10 or above 10x the target: size warning
PEERS_DEFAULT_COUNT = 6           # suggested peers; kept small to limit Yahoo requests
PEERS_MIN_SUB_INDUSTRY = 3        # fewer same-sub-industry peers than this: fill from the sector
# Fewer deals than this in the calculation -> "small sample" warning
PRECEDENT_SMALL_SAMPLE = 5
# Yahoo country + industry -> the `sector` codes used in precedents.csv. The
# precedent row is shown only if the target's code matches a sector of the deals
# in the calculation (or the target is one the deal list was selected for).
PRECEDENT_SECTORS = {
    ("United States", "Grocery Stores"): "us_retail_grocery",
    ("United States", "Department Stores"): "us_retail_department",
    ("United States", "Discount Stores"): "us_retail_discount",
    ("United States", "Home Improvement Retail"): "us_retail_specialty",
    ("United States", "Specialty Retail"): "us_retail_specialty",
    ("United States", "Apparel Retail"): "us_retail_specialty",
    ("United States", "Home Furnishings & Fixtures"): "us_retail_specialty",
}
PRECEDENT_SELECTED_FOR = {"TGT"}    # the default deal list was chosen for Target

# --- Football field (Stage 4) ----------------------------------------------
# DCF bars span every combination of base WACC +/- FF_WACC_DELTA with terminal
# growth +/- FF_GROWTH_DELTA (Gordon) or exit multiple +/- FF_MULTIPLE_DELTA (exit).
FF_WACC_DELTA = 0.005
FF_GROWTH_DELTA = 0.005
FF_MULTIPLE_DELTA = 1.0
# Rows of the football field, top to bottom. P/B stays in the comps tables but is
# not on the chart (see comps.MULTIPLE_NOTES).
FOOTBALL_FIELD_ROWS = ["52w", "ev_ebitda", "pe", "ev_sales", "prec_ev_ebitda", "lbo", "dcf_exit", "dcf_gordon"]

# --- LBO (leveraged buyout) -------------------------------------------------
# Every number below is an ILLUSTRATIVE ASSUMPTION (not market data): a typical
# shape for a large sponsor buyout, editable in the app. Market data used by the
# LBO: the share price, the 10-year Treasury (^TNX, fixed-rate notes) and the
# 13-week Treasury bill (^IRX) as a PROXY for SOFR, the floating base rate.
SHORT_RATE_TICKER = "^IRX"
LBO_PREMIUM = 0.25                # offer price over the current share price
LBO_TRANSACTION_FEE_PCT = 0.015   # advisory, legal and other deal costs, % of entry EV
LBO_FINANCING_FEE_PCT = 0.02      # arrangement fees, % of new debt
LBO_MIN_CASH_PCT = 0.02           # cash the business keeps to operate, % of revenue
LBO_TARGET_IRR = 0.20             # sponsor's required return; the range is 15%-25%
LBO_IRR_RANGE = (0.15, 0.25)
LBO_EXIT_YEAR = 5
LBO_TRANCHES = [
    # multiple = x entry EBITDA; floating rate = base rate + spread; fixed rate = 10y Treasury + spread
    {"name": "Term Loan B", "multiple": 3.0, "rate_type": "floating", "spread": 0.0325,
     "amortization": 0.01, "maturity": 7, "sweep": True},
    {"name": "Senior Notes", "multiple": 2.0, "rate_type": "fixed", "spread": 0.035,
     "amortization": 0.0, "maturity": 8, "sweep": False},
]
LBO_MIN_INTEREST_COVERAGE = 2.0   # EBITDA / interest below this: warning
# Interagency Guidance on Leveraged Lending (Fed, OCC, FDIC, March 2013): total
# debt / EBITDA above 6x "raises concerns for most industries".
LBO_MAX_LEVERAGE = 6.0
# US tax code section 163(j): net interest deduction limited to 30% of adjusted
# taxable income (EBITDA-based). Not modelled; a warning is shown instead.
LBO_INTEREST_DEDUCTION_CAP = 0.30
# Largest sponsor take-privates, for the feasibility note (company press releases filed with the SEC)
LBO_LARGEST_DEALS = [
    {"name": "Electronic Arts (PIF, Silver Lake, Affinity Partners, 2025)", "ev": 55e9, "equity": 36e9,
     "debt": 20e9, "source": "https://www.sec.gov/Archives/edgar/data/712515/000114036125036415/ef20056167_ex99-1.htm",
     "note": "EV ~$55bn, equity ~$36bn, $20bn committed debt; called the largest all-cash sponsor "
             "take-private in history (EA 8-K Ex.99.1, 2025-09-29)"},
    {"name": "TXU (KKR, TPG, 2007)", "ev": 45e9, "equity": None, "debt": None,
     "source": "https://www.sec.gov/Archives/edgar/data/1023291/000095013407004081/d44014exv99w1.htm",
     "note": "transaction valued at $45bn (TXU 8-K Ex.99.1, 2007-02-26)"},
]

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
