"""Data layer: download company data from Yahoo Finance, cache it,
apply manual overrides and check data quality.

Run directly for a quick look:
    python data_loader.py            # use cache if fresh
    python data_loader.py --refresh  # force a new download

All money values are in USD (full units, not millions).
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

import config

# Our field name -> key in yfinance's `Ticker.info` dictionary.
# These are the "raw" fields we download directly.
SNAPSHOT_FIELDS = {
    "price": "currentPrice",
    "market_cap": "marketCap",
    "total_debt": "totalDebt",            # Yahoo includes lease liabilities here
    "cash": "totalCash",
    "revenue_ttm": "totalRevenue",        # trailing twelve months (TTM)
    "ebitda_ttm": "ebitda",
    "net_income_ttm": "netIncomeToCommon",
    "shares_outstanding": "sharesOutstanding",
    "book_value_per_share": "bookValue",
    "low_52w": "fiftyTwoWeekLow",
    "high_52w": "fiftyTwoWeekHigh",
}

# Raw fields read from the statements instead of the summary (see lease_liabilities)
STATEMENT_FIELDS = ["lease_liabilities"]
RAW_FIELDS = list(SNAPSHOT_FIELDS) + STATEMENT_FIELDS

# Fields we calculate ourselves from the raw fields (see compute_derived)
DERIVED_FIELDS = ["debt_for_ev", "net_debt", "enterprise_value", "total_equity"]

ALL_FIELDS = RAW_FIELDS + DERIVED_FIELDS

STATEMENTS = {
    "income": "income_stmt",
    "cashflow": "cashflow",
    "balance": "balance_sheet",
    "income_q": "quarterly_income_stmt",   # used to build trailing-twelve-month figures
    "balance_q": "quarterly_balance_sheet",  # used for the latest lease liabilities
}

# TTM fields we rebuild from the last 4 quarters: our field -> quarterly row name.
# Why: Yahoo's ready-made TTM summary numbers sometimes don't match the
# quarterly statements. Summing the quarters ourselves is transparent, and
# we still compare against Yahoo's number in the data quality report.
TTM_ROWS = {
    "revenue_ttm": "Total Revenue",
    "ebitda_ttm": "EBITDA",
    "net_income_ttm": "Net Income Common Stockholders",
}
TTM_MISMATCH_TOLERANCE = 0.10

# Annual line items we will need for the DCF (Stage 3).
# (statement, yfinance row name) -> our name
ANNUAL_ITEMS = {
    ("income", "Total Revenue"): "revenue",
    ("income", "EBITDA"): "ebitda",
    ("income", "EBIT"): "ebit",
    ("income", "Net Income"): "net_income",
    ("income", "Pretax Income"): "pretax_income",
    ("income", "Tax Provision"): "tax_provision",
    ("income", "Interest Expense"): "interest_expense",
    ("cashflow", "Depreciation And Amortization"): "d_and_a",
    ("cashflow", "Capital Expenditure"): "capex",
    ("cashflow", "Change In Working Capital"): "change_in_wc",
}


def is_missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


class TickerNotFound(Exception):
    """Yahoo has no stock quote for this symbol (typo, delisted, or not a stock)."""


class DataUnavailable(Exception):
    """The download failed and there is no cached or saved copy to fall back on."""


# ---------------------------------------------------------------------------
# 1) Downloading
# ---------------------------------------------------------------------------

def fetch_from_yahoo(ticker: str) -> dict:
    """Download one company's snapshot + annual statements. Needs internet."""
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    # An unknown symbol does not raise an error in yfinance: it returns empty fields
    if info.get("quoteType") is None and not isinstance(info.get("marketCap"), (int, float)):
        raise TickerNotFound(f"{ticker}: Yahoo Finance has no quote for this symbol. Check the spelling "
                             f"and the exchange suffix (e.g. RACE.MI for Milan, MC.PA for Paris).")
    if info.get("quoteType") not in (None, "EQUITY"):
        raise TickerNotFound(f"{ticker}: this is a {info.get('quoteType')}, not a company's stock.")

    snapshot = {}
    for field, yf_key in SNAPSHOT_FIELDS.items():
        value = info.get(yf_key)
        snapshot[field] = float(value) if isinstance(value, (int, float)) else None

    # yfinance sometimes leaves currentPrice empty; regularMarketPrice is the fallback
    if snapshot["price"] is None and isinstance(info.get("regularMarketPrice"), (int, float)):
        snapshot["price"] = float(info["regularMarketPrice"])

    meta = {
        "ticker": ticker,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "currency": info.get("currency"),                     # currency of the share price
        "financial_currency": info.get("financialCurrency"),  # currency of the reports
        "yahoo_enterprise_value": info.get("enterpriseValue"),  # kept only as a cross-check
        "beta": info.get("beta"),   # Yahoo: 5-year monthly beta vs the S&P 500, used in the WACC
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
    }

    statements = {}
    for name, attr in STATEMENTS.items():
        df = getattr(tk, attr)
        statements[name] = df if df is not None else pd.DataFrame()

    return {"snapshot": snapshot, "meta": meta, "statements": statements}


def fetch_latest_close(symbol: str) -> dict:
    """Latest daily close of a Yahoo series (bond yield or exchange rate)."""
    hist = yf.Ticker(symbol).history(period="1mo")["Close"].dropna()
    if hist.empty:
        raise ValueError(f"no recent prices for {symbol}")
    return {"fetched_at": datetime.now().isoformat(timespec="seconds"),
            "value": float(hist.iloc[-1]), "date": hist.index[-1].strftime("%Y-%m-%d")}


def fetch_risk_free_from_yahoo(symbol: str = None) -> dict:
    """Latest 10-year government bond yield. Yahoo quotes ^TNX in percent
    (e.g. 4.25 means 4.25%), so we divide by 100 to get a decimal."""
    r = fetch_latest_close(symbol or config.RISK_FREE_TICKER)
    return {"fetched_at": r["fetched_at"], "rate": r["value"] / 100, "date": r["date"]}


# ---------------------------------------------------------------------------
# 2) Caching
#    Snapshot -> JSON, statements -> CSV, so you can open them and look.
# ---------------------------------------------------------------------------

def _cache_paths(ticker: str, directory=None) -> dict:
    directory = directory or config.CACHE_DIR
    paths = {"snapshot": directory / f"{ticker}_snapshot.json"}
    for name in STATEMENTS:
        paths[name] = directory / f"{ticker}_{name}.csv"
    return paths


def _is_fresh(fetched_at: str, max_age_hours: float) -> bool:
    return datetime.now() - datetime.fromisoformat(fetched_at) < timedelta(hours=max_age_hours)


def save_company_cache(ticker: str, data: dict) -> None:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    paths = _cache_paths(ticker)
    with open(paths["snapshot"], "w") as f:
        json.dump({"snapshot": data["snapshot"], "meta": data["meta"]}, f, indent=2)
    for name in STATEMENTS:
        data["statements"][name].to_csv(paths[name])


def load_company_cache(ticker: str, directory=None):
    """Return cached data, or None if there is no cache yet.
    `directory` defaults to the cache; pass config.SNAPSHOT_DIR for the snapshot."""
    paths = _cache_paths(ticker, directory)
    if not paths["snapshot"].exists():
        return None
    with open(paths["snapshot"]) as f:
        data = json.load(f)
    data["statements"] = {}
    for name in STATEMENTS:
        if paths[name].exists() and paths[name].stat().st_size > 0:
            df = pd.read_csv(paths[name], index_col=0)
            df.columns = pd.to_datetime(df.columns)
            data["statements"][name] = df
        else:
            data["statements"][name] = pd.DataFrame()
    return data


def get_company_raw(ticker: str, refresh: bool = False) -> dict:
    """Use the cache if it is fresh, otherwise download.
    If the download fails, fall back to the old cache, then to the committed
    snapshot, and warn. meta["data_source"] says which one was used:
    "live" (downloaded now or within the cache age), "old cache" or "snapshot"."""
    cached = load_company_cache(ticker)
    if cached and not refresh and _is_fresh(cached["meta"]["fetched_at"], config.CACHE_MAX_AGE_HOURS):
        cached["meta"].setdefault("data_source", "live")
        return cached
    try:
        data = fetch_from_yahoo(ticker)
        data["meta"]["data_source"] = "live"
        save_company_cache(ticker, data)
        return data
    except TickerNotFound:
        raise
    except Exception as e:
        fallback = cached or load_company_cache(ticker, config.SNAPSHOT_DIR)
        if fallback:
            source = "old cache" if cached else "snapshot"
            fallback["meta"]["data_source"] = source
            warnings.warn(f"{ticker}: download failed ({e}); using {source} from {fallback['meta']['fetched_at']}")
            return fallback
        saved = snapshot_tickers()
        raise DataUnavailable(
            f"{ticker}: the download from Yahoo Finance failed ({str(e)[:120]}) and there is no saved copy "
            f"of its data. Saved fallback data exists only for {', '.join(saved) or 'no company'}. "
            f"Yahoo often blocks shared cloud servers for a while: try again later.") from e


def snapshot_tickers() -> list:
    """Tickers that have a committed fallback copy in config.SNAPSHOT_DIR."""
    return sorted(p.name[: -len("_snapshot.json")] for p in config.SNAPSHOT_DIR.glob("*_snapshot.json"))


def _cached_series(filename: str, fetch, refresh: bool = False) -> dict:
    """Cache + fallback logic shared by the risk-free rate and exchange rates.
    Order: fresh cache -> download -> old cache -> committed snapshot.
    Raises if all of them fail."""
    path = config.CACHE_DIR / filename
    cached = None
    if path.exists():
        with open(path) as f:
            cached = json.load(f)
        if not refresh and _is_fresh(cached["fetched_at"], config.CACHE_MAX_AGE_HOURS):
            cached.setdefault("data_source", "live")
            return cached
    try:
        result = fetch()
        result["data_source"] = "live"
    except Exception as e:
        fallback, source = cached, "old cache"
        snapshot_path = config.SNAPSHOT_DIR / filename
        if fallback is None and snapshot_path.exists():
            with open(snapshot_path) as f:
                fallback, source = json.load(f), "snapshot"
        if fallback:
            fallback["data_source"] = source
            warnings.warn(f"{filename}: download failed ({e}); using {source} from {fallback['fetched_at']}")
            return fallback
        raise
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    return result


def get_risk_free_rate(currency: str = "USD", refresh: bool = False) -> dict:
    """10-year government bond yield in `currency`, used in the WACC.

    Yahoo only has it for USD (^TNX). For any other currency the rate is NaN
    with data_source "manual": the user must enter it (never a guess).
    If the USD download fails with no saved copy, data_source is "unavailable"."""
    currency = currency or "USD"
    symbol = config.RISK_FREE_TICKERS.get(currency)
    base = {"currency": currency, "guidance": config.risk_free_guidance(currency)}
    if symbol is None:
        return {**base, "rate": np.nan, "date": None, "fetched_at": None, "data_source": "manual"}
    filename = "risk_free_rate.json" if currency == "USD" else f"risk_free_rate_{currency}.json"
    try:
        r = _cached_series(filename, lambda: fetch_risk_free_from_yahoo(symbol), refresh)
    except Exception as e:
        return {**base, "rate": np.nan, "date": None, "fetched_at": None, "data_source": "unavailable",
                "error": str(e)[:200]}
    return {**base, **r}


def get_fx_rate(from_ccy: str, to_ccy: str, refresh: bool = False) -> dict:
    """Exchange rate: 1 unit of from_ccy in to_ccy (Yahoo "EURUSD=X").
    rate is NaN if it cannot be downloaded and there is no saved copy."""
    if from_ccy == to_ccy:
        return {"rate": 1.0, "date": None, "data_source": "live"}
    symbol = f"{from_ccy}{to_ccy}=X"
    try:
        r = _cached_series(f"fx_{from_ccy}{to_ccy}.json", lambda: fetch_latest_close(symbol), refresh)
    except Exception as e:
        return {"rate": np.nan, "date": None, "data_source": "unavailable", "error": str(e)[:200]}
    return {"rate": r["value"], "date": r["date"], "data_source": r.get("data_source", "live")}


# ---------------------------------------------------------------------------
# 3) Trailing twelve months (TTM) from quarterly statements
# ---------------------------------------------------------------------------

def ttm_from_quarters(quarterly: pd.DataFrame) -> dict:
    """Sum the latest 4 quarters for each TTM field.
    Returns NaN for a field if the 4 quarters are incomplete or not consecutive."""
    result = {field: np.nan for field in TTM_ROWS}
    if quarterly.empty or quarterly.shape[1] < 4:
        return result
    last4 = quarterly[sorted(quarterly.columns, reverse=True)[:4]]
    span_days = (last4.columns[0] - last4.columns[-1]).days
    if not 250 <= span_days <= 300:        # 4 consecutive quarter-ends are ~273 days apart
        return result
    for field, row in TTM_ROWS.items():
        if row in last4.index and last4.loc[row].notna().all():
            result[field] = float(last4.loc[row].sum())
    return result


def replace_ttm(snapshot: dict, quarterly: pd.DataFrame):
    """Put our 4-quarter TTM values into the snapshot. Yahoo's summary value is
    kept only as a fallback. Returns (new_snapshot, notes) where notes explain
    what happened, for the data quality report."""
    snapshot, notes = dict(snapshot), {}
    for field, ours in ttm_from_quarters(quarterly).items():
        yahoo = snapshot.get(field)
        if math.isnan(ours):
            if not is_missing(yahoo):
                notes[field] = "4 quarters not available: using Yahoo's summary TTM value (less reliable)"
            continue
        snapshot[field] = ours
        if not is_missing(yahoo) and yahoo != 0 and abs(ours / yahoo - 1) > TTM_MISMATCH_TOLERANCE:
            notes[field] = (f"TTM = sum of last 4 quarters. Yahoo's summary value "
                            f"({yahoo / 1e9:,.2f}bn) differs by {yahoo / ours - 1:+.0%}")
    return snapshot, notes


def lease_liabilities(balance_q: pd.DataFrame):
    """Lease liabilities from the most recent quarterly balance sheet.
    Returns (value, date) or (NaN, None) if Yahoo has no such row."""
    row = "Capital Lease Obligations"   # Yahoo's name; in practice mostly operating leases
    if balance_q.empty or row not in balance_q.index:
        return np.nan, None
    series = balance_q.loc[row].dropna()
    if series.empty:
        return np.nan, None
    latest = max(series.index)
    return float(series[latest]), latest


# ---------------------------------------------------------------------------
# 4) Manual overrides
# ---------------------------------------------------------------------------

OVERRIDE_COLUMNS = ["ticker", "field", "value", "adjusted_quarter", "ttm_end", "note"]


def load_overrides(path=config.OVERRIDES_PATH) -> pd.DataFrame:
    """Read data/manual_overrides.csv. Lines starting with '#' are comments.

    `adjusted_quarter` and `ttm_end` are optional. Fill them in when a TTM value
    is adjusted for a one-off item: `adjusted_quarter` is the quarter that
    contains the one-off, `ttm_end` the last quarter of the TTM window the value
    was calculated for (both as Yahoo labels the quarter end, YYYY-MM-DD).
    They let the data quality report warn when the override has gone stale."""
    if not path.exists():
        return pd.DataFrame(columns=OVERRIDE_COLUMNS)
    df = pd.read_csv(path, comment="#", skipinitialspace=True)
    df = df.dropna(subset=["ticker", "field", "value"])
    for col in OVERRIDE_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    unknown = set(df["field"]) - set(ALL_FIELDS)
    if unknown:
        warnings.warn(f"Unknown field(s) in overrides file, ignored: {sorted(unknown)}")
        df = df[df["field"].isin(ALL_FIELDS)]
    return df


def override_checks(ticker: str, overrides: pd.DataFrame, quarterly: pd.DataFrame) -> list:
    """Warnings for overrides of TTM values that no longer match the data.

    A TTM override adjusted for a one-off is a fixed number, but the TTM window
    moves every quarter:
      - once the adjusted quarter has left the 4-quarter window, the one-off is
        no longer in Yahoo's numbers either, so the override must be removed;
      - once a newer quarter is reported, the override was calculated for an
        old window and must be recalculated.
    Returns a list of (field, status, comment)."""
    flags = []
    rows = overrides[overrides["ticker"] == ticker]
    if rows.empty or quarterly.empty:
        return flags
    window = sorted(quarterly.columns, reverse=True)[:4]
    latest = window[0]
    for _, row in rows.iterrows():
        field = row["field"]
        if not is_missing(row.get("adjusted_quarter")):
            adjusted = pd.Timestamp(row["adjusted_quarter"])
            if adjusted not in window:
                flags.append((field, "Suspicious",
                              f"Stale override: it adjusts the quarter ending {adjusted:%Y-%m-%d}, which is no "
                              f"longer in the TTM window ({window[-1]:%Y-%m-%d} to {latest:%Y-%m-%d}). "
                              f"Remove it from data/manual_overrides.csv"))
                continue
        if not is_missing(row.get("ttm_end")) and latest > pd.Timestamp(row["ttm_end"]):
            flags.append((field, "Suspicious",
                          f"Stale override: calculated for the TTM window ending {row['ttm_end']}, but the "
                          f"quarter ending {latest:%Y-%m-%d} is now available. Recalculate it"))
    return flags


def apply_overrides(snapshot: dict, ticker: str, overrides: pd.DataFrame):
    """Start from Yahoo data, replace any value given in the overrides file.

    Returns (values, sources). sources[field] is 'auto', 'manual' or 'missing'.
    """
    values, sources = {}, {}
    for field in RAW_FIELDS:
        value = snapshot.get(field)
        values[field] = np.nan if is_missing(value) else float(value)
        sources[field] = "missing" if is_missing(value) else "auto"

    for _, row in overrides[overrides["ticker"] == ticker].iterrows():
        values[row["field"]] = float(row["value"])
        sources[row["field"]] = "manual"
    return values, sources


def compute_derived(values: dict, sources: dict, exclude_leases: bool = None):
    """Calculate net debt, EV and total equity.

    Debt for EV      = total debt - lease liabilities   (if exclude_leases)
                     = total debt                       (otherwise)
    Net debt         = debt for EV - cash
    Enterprise value = market cap + net debt
    Total equity     = book value per share x shares outstanding

    A derived field is skipped if the user already overrode it manually.
    If any input is missing, the result is NaN (never a guess).
    """
    if exclude_leases is None:
        exclude_leases = config.EXCLUDE_LEASES_FROM_DEBT
    values, sources = dict(values), dict(sources)

    def put(field, result):
        if sources.get(field) == "manual":
            return
        values[field] = result
        sources[field] = "missing" if math.isnan(result) else "derived"

    if exclude_leases:
        put("debt_for_ev", values["total_debt"] - values["lease_liabilities"])
    else:
        put("debt_for_ev", values["total_debt"])
    put("net_debt", values["debt_for_ev"] - values["cash"])
    put("enterprise_value", values["market_cap"] + values["net_debt"])
    put("total_equity", values["book_value_per_share"] * values["shares_outstanding"])
    return values, sources


# ---------------------------------------------------------------------------
# 5) Annual history (for the DCF later)
# ---------------------------------------------------------------------------

def annual_history(statements: dict) -> pd.DataFrame:
    """Pick the line items the DCF needs. Rows = fiscal years, columns = items.
    Items Yahoo doesn't provide stay NaN.

    The index is the fiscal year END DATE, not a calendar year, because US
    retailers close their year at different times (Target/Walmart: late
    January, Costco: end of August)."""
    rows = {}
    for (stmt, yf_row), name in ANNUAL_ITEMS.items():
        df = statements.get(stmt, pd.DataFrame())
        if not df.empty and yf_row in df.index:
            rows[name] = df.loc[yf_row]
        else:
            rows[name] = pd.Series(dtype=float)
    hist = pd.DataFrame(rows)
    if hist.empty:
        return hist
    hist.index = pd.to_datetime(hist.index)
    hist.index.name = "fiscal_year_end"
    # Yahoo sometimes adds an extra year where every value is empty: drop it
    return hist.dropna(how="all").sort_index()


# ---------------------------------------------------------------------------
# 6) Data quality report
# ---------------------------------------------------------------------------

def quality_checks(ticker: str, values: dict, sources: dict, meta: dict,
                   history: pd.DataFrame, today: datetime = None,
                   notes: dict = None, flags: list = None) -> pd.DataFrame:
    """One row per field (plus a few company-level checks).
    status: OK / Missing / Suspicious / Note.
    `notes` are extra explanations (e.g. from replace_ttm), shown as 'Note'.
    `flags` are extra (field, status, comment) issues (e.g. from override_checks)."""
    today = today or datetime.now()
    issues = {f: [] for f in ALL_FIELDS}   # field -> list of (status, comment)
    for field, comment in (notes or {}).items():
        issues[field].append(("Note", comment))
    for field, status, comment in (flags or []):
        issues[field].append((status, comment))

    def flag(field, status, comment):
        issues[field].append((status, comment))

    v = values
    if v["ebitda_ttm"] <= 0:
        flag("ebitda_ttm", "Suspicious", "EBITDA is zero or negative: EV/EBITDA will be meaningless")
    if v["net_income_ttm"] < 0:
        flag("net_income_ttm", "Note", "Loss-making: P/E will be excluded")
    if v["total_equity"] <= 0:
        flag("total_equity", "Suspicious", "Equity is zero or negative: P/B will be meaningless")
    if v["price"] < v["low_52w"] or v["price"] > v["high_52w"]:
        flag("price", "Suspicious", "Price is outside the 52-week range")

    implied_mcap = v["price"] * v["shares_outstanding"]
    if v["market_cap"] > 0 and abs(implied_mcap / v["market_cap"] - 1) > config.MCAP_MISMATCH_TOLERANCE:
        flag("market_cap", "Suspicious",
             f"Price x shares differs from market cap by {implied_mcap / v['market_cap'] - 1:+.1%}")

    if v["lease_liabilities"] > v["total_debt"]:
        flag("lease_liabilities", "Suspicious", "Lease liabilities are larger than total debt")

    # Yahoo's EV includes leases, so compare like for like
    yahoo_ev = meta.get("yahoo_enterprise_value")
    if not is_missing(yahoo_ev) and v["enterprise_value"] > 0:
        comparable = v["market_cap"] + v["total_debt"] - v["cash"]
        diff = comparable / yahoo_ev - 1
        if abs(diff) > config.EV_MISMATCH_TOLERANCE:
            flag("enterprise_value", "Suspicious", f"Differs from Yahoo's EV by {diff:+.1%} (both incl. leases)")

    rows = []
    for field in ALL_FIELDS:
        if sources[field] == "missing":
            status, comment = "Missing", "Not available - add it to data/manual_overrides.csv"
        elif issues[field]:
            # Show the most serious status, but keep every comment
            issues[field].sort(key=lambda x: x[0] != "Suspicious")
            status = issues[field][0][0]
            comment = " | ".join(c for _, c in issues[field])
        else:
            status, comment = "OK", ""
        rows.append({"ticker": ticker, "field": field, "value": v[field],
                     "source": sources[field], "status": status, "comment": comment})

    # Company-level checks
    trade_ccy, fin_ccy = meta.get("currency"), meta.get("financial_currency")
    fx = meta.get("fx")
    if not trade_ccy or not fin_ccy:
        rows.append({"ticker": ticker, "field": "currency", "value": np.nan, "source": "missing",
                     "status": "Missing", "comment": "Yahoo does not say which currency the price or "
                                                     "the reports are in: figures may not be comparable"})
    elif fx:
        if is_missing(fx["rate"]):
            status, comment = "Suspicious", (f"Reports are in {fin_ccy} but the share trades in {trade_ccy}, "
                                             f"and the exchange rate could not be downloaded: statement "
                                             f"figures are left out (N/A)")
        else:
            status, comment = "Note", (f"Reports are in {fin_ccy}, the share trades in {trade_ccy}: statement "
                                       f"figures converted at {fx['rate']:.4f} {trade_ccy} per {fin_ccy} "
                                       f"({fx['date']}, latest rate for all periods)")
        rows.append({"ticker": ticker, "field": "currency", "value": fx["rate"], "source": "derived",
                     "status": status, "comment": comment})

    if history.empty:
        rows.append({"ticker": ticker, "field": "annual_statements", "value": np.nan,
                     "source": "missing", "status": "Missing",
                     "comment": "No annual statements from Yahoo"})
    else:
        last_end = history.index.max()
        months_old = (today - last_end).days / 30.44
        label = f"Latest annual statement ends {last_end:%Y-%m-%d}"
        if months_old > config.STALE_STATEMENT_MONTHS:
            status, comment = "Suspicious", f"{label} ({months_old:.0f} months old)"
        else:
            status, comment = "OK", label
        rows.append({"ticker": ticker, "field": "annual_statements", "value": np.nan,
                     "source": "auto", "status": status, "comment": comment})

        missing_items = [c for c in history.columns if history[c].isna().all()]
        if missing_items:
            rows.append({"ticker": ticker, "field": "annual_items", "value": np.nan,
                         "source": "missing", "status": "Missing",
                         "comment": "No history for: " + ", ".join(missing_items)})

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 7) Putting it together
# ---------------------------------------------------------------------------

# Fields Yahoo gives in the REPORTING currency (checked on RACE vs RACE.MI: Ferrari
# trades in USD in New York and EUR in Milan, but debt, cash, revenue, EBITDA and the
# statements are identical in both, i.e. in EUR). Price, market cap and 52-week
# range are in the trading currency.
REPORTING_CURRENCY_FIELDS = ["total_debt", "cash", "revenue_ttm", "ebitda_ttm", "net_income_ttm",
                             "lease_liabilities"]


def convert_to_trading_currency(snapshot: dict, history: pd.DataFrame, statements: dict, meta: dict,
                                fx_rate: float):
    """Put statement figures into the currency the share trades in, so EV and
    the multiples compare like with like. Book value per share is rebuilt from
    the balance sheet (Yahoo's own converted figure uses an outdated rate).
    With a missing rate the converted fields become NaN: no guessing."""
    snapshot, history = dict(snapshot), history.copy()
    for field in REPORTING_CURRENCY_FIELDS:
        if not is_missing(snapshot.get(field)):
            snapshot[field] = snapshot[field] * fx_rate
    balance_q = statements.get("balance_q", pd.DataFrame())
    shares = snapshot.get("shares_outstanding")
    if not balance_q.empty and "Stockholders Equity" in balance_q.index and not is_missing(shares) and shares:
        equity = balance_q.loc["Stockholders Equity"].dropna()
        if not equity.empty:
            snapshot["book_value_per_share"] = float(equity[max(equity.index)]) / shares * fx_rate
    history = history * fx_rate        # every history column is an amount of money
    return snapshot, history


def load_company(ticker: str, overrides: pd.DataFrame, refresh: bool = False) -> dict:
    raw = get_company_raw(ticker, refresh)
    snapshot, ttm_notes = replace_ttm(raw["snapshot"], raw["statements"]["income_q"])
    snapshot["lease_liabilities"], lease_date = lease_liabilities(raw["statements"]["balance_q"])
    raw["meta"]["lease_date"] = lease_date.strftime("%Y-%m-%d") if lease_date is not None else None
    history = annual_history(raw["statements"])
    trade_ccy, fin_ccy = raw["meta"].get("currency"), raw["meta"].get("financial_currency")
    raw["meta"].pop("fx", None)
    if trade_ccy and fin_ccy and trade_ccy != fin_ccy:
        fx = get_fx_rate(fin_ccy, trade_ccy, refresh)
        raw["meta"]["fx"] = {"from": fin_ccy, "to": trade_ccy, "rate": fx["rate"], "date": fx["date"]}
        snapshot, history = convert_to_trading_currency(snapshot, history, raw["statements"], raw["meta"],
                                                        fx["rate"])
    # Manual overrides are in the trading currency, so they come after the conversion
    values, sources = apply_overrides(snapshot, ticker, overrides)
    values, sources = compute_derived(values, sources)
    # A manual value replaces the TTM calculation, so its note no longer applies
    ttm_notes = {f: n for f, n in ttm_notes.items() if sources[f] != "manual"}
    mine = overrides[overrides["ticker"] == ticker]
    for _, row in mine.iterrows():
        note = "" if is_missing(row["note"]) else f": {row['note']}"
        ttm_notes[row["field"]] = f"Manual value{note}"
    flags = override_checks(ticker, overrides, raw["statements"]["income_q"])
    quality = quality_checks(ticker, values, sources, raw["meta"], history, notes=ttm_notes, flags=flags)
    return {"ticker": ticker, "values": values, "sources": sources, "meta": raw["meta"],
            "statements": raw["statements"], "history": history, "quality": quality}


def load_all(tickers=None, refresh: bool = False) -> dict:
    """Load every company plus the risk-free rate. This is what the app calls.

    The first ticker is the target; the risk-free rate follows its trading
    currency. A company that cannot be loaded does not stop the others: it is
    left out and its error message is kept in data["errors"]."""
    tickers = list(tickers or config.ALL_TICKERS)
    overrides = load_overrides()
    companies, errors = {}, {}
    for t in tickers:
        try:
            companies[t] = load_company(t, overrides, refresh)
        except Exception as e:
            errors[t] = str(e)
    target = tickers[0]
    currency = companies[target]["meta"].get("currency") if target in companies else None
    return {"companies": companies, "errors": errors, "target": target,
            "risk_free": get_risk_free_rate(currency or "USD", refresh)}


def data_sources(data: dict) -> dict:
    """Where each piece of data came from: {name: "live" | "old cache" | "snapshot"}.
    A risk-free rate the user must type in ("manual") is not listed."""
    out = {t: c["meta"].get("data_source", "live") for t, c in data["companies"].items()}
    if data["risk_free"].get("data_source", "live") != "manual":
        out["risk_free_rate"] = data["risk_free"].get("data_source", "live")
    return out


def save_snapshot(tickers=None) -> None:
    """Copy the cached data of `tickers` (default set if omitted) and the US
    risk-free rate into config.SNAPSHOT_DIR (committed to git), so a fresh
    deploy still opens if Yahoo blocks the download."""
    import shutil
    tickers = tickers or config.ALL_TICKERS
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for t in tickers:
        for path in _cache_paths(t).values():
            if path.exists():
                shutil.copy2(path, config.SNAPSHOT_DIR / path.name)
    rf = config.CACHE_DIR / "risk_free_rate.json"
    if rf.exists():
        shutil.copy2(rf, config.SNAPSHOT_DIR / rf.name)


def snapshot_table(companies: dict) -> pd.DataFrame:
    """Rows = fields, columns = tickers."""
    return pd.DataFrame({t: c["values"] for t, c in companies.items()}).loc[ALL_FIELDS]


def quality_table(companies: dict) -> pd.DataFrame:
    return pd.concat([c["quality"] for c in companies.values()], ignore_index=True)


# ---------------------------------------------------------------------------
# Command-line preview
# ---------------------------------------------------------------------------

def fmt_number(x, field: str = "") -> str:
    """Readable numbers: 1.23bn, 456.7m, prices with 2 decimals."""
    if is_missing(x):
        return "N/A"
    if field in ("price", "low_52w", "high_52w", "book_value_per_share"):
        return f"{x:,.2f}"
    for size, suffix in ((1e9, "bn"), (1e6, "m")):
        if abs(x) >= size:
            return f"{x / size:,.2f}{suffix}"
    return f"{x:,.0f}"


def main():
    parser = argparse.ArgumentParser(description="Download and check the default company set")
    parser.add_argument("--refresh", action="store_true", help="ignore cache, download again")
    parser.add_argument("--save-snapshot", action="store_true",
                        help="copy the cache to data/snapshot/ (the committed fallback)")
    args = parser.parse_args()

    data = load_all(refresh=args.refresh)
    if args.save_snapshot:
        if any(s != "live" for s in data_sources(data).values()):
            raise SystemExit("Not saving the snapshot: some data is not fresh. Run with --refresh first.")
        save_snapshot()
        print(f"Snapshot saved to {config.SNAPSHOT_DIR}")
    companies, rf = data["companies"], data["risk_free"]
    for t, err in data["errors"].items():
        print(f"ERROR {err}")
    ccy = companies[config.TARGET]["meta"].get("currency") if config.TARGET in companies else "?"

    print(f"\nRisk-free rate ({rf['guidance'].split(',')[0]}): {rf['rate']:.2%} ({rf['date']})")
    print(f"\n=== Snapshot ({ccy}) - source in brackets: a=auto, m=manual, d=derived ===")
    table = {}
    for t, c in companies.items():
        table[t] = {f: f"{fmt_number(c['values'][f], f)} [{c['sources'][f][0]}]" for f in ALL_FIELDS}
    print(pd.DataFrame(table).to_string())

    print(f"\n=== Annual history: {config.TARGET} ({ccy}) ===")
    hist = companies[config.TARGET]["history"].copy()
    hist.index = hist.index.strftime("FY end %Y-%m")
    print(hist.T.apply(lambda col: col.map(fmt_number)).to_string())

    print("\n=== Data quality issues (fields marked OK are hidden) ===")
    q = quality_table(companies)
    issues = q[q["status"] != "OK"][["ticker", "field", "status", "comment"]]
    print(issues.to_string(index=False) if not issues.empty else "No issues found.")
    counts = q.groupby(["ticker", "status"]).size().unstack(fill_value=0)
    print("\n=== Summary ===")
    print(counts.to_string())


if __name__ == "__main__":
    main()
