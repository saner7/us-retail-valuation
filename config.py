"""Project settings. Change tickers or thresholds here, not inside the code."""

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
