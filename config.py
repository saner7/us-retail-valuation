"""Project settings. Change tickers or thresholds here, not inside the code."""

from pathlib import Path

# --- Companies -------------------------------------------------------------
# BIST tickers on Yahoo Finance end with ".IS"
TARGET = "MGROS.IS"
PEERS = ["BIMAS.IS", "SOKM.IS", "CRFSA.IS"]
ALL_TICKERS = [TARGET] + PEERS

COMPANY_NAMES = {
    "MGROS.IS": "Migros",
    "BIMAS.IS": "BIM",
    "SOKM.IS": "SOK Marketler",
    "CRFSA.IS": "CarrefourSA",
}

# --- Currency --------------------------------------------------------------
BASE_CURRENCY = "TRY"     # all raw data is stored in TRY
FX_TICKER = "TRY=X"       # Yahoo quote: how many TRY one USD buys

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
