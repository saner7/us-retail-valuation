"""A small, made-up but complete data set in the same shape as
data_loader.load_all(), so the app, the football field and the Excel export can
be tested offline. Numbers are round so results can be checked by hand."""

from datetime import datetime

import pandas as pd

import config
import data_loader as dl

QUARTERS = pd.to_datetime(["2026-07-31", "2026-04-30", "2026-01-31", "2025-10-31"])
YEARS = pd.to_datetime(["2023-01-31", "2024-01-31", "2025-01-31", "2026-01-31"])


def company(ticker: str, scale: float, ev_ebitda: float, currency: str = "USD",
            industry: str = "Discount Stores") -> dict:
    """Revenue 1,000 x scale; EBITDA margin 8%; market cap set so that
    EV / EBITDA is roughly `ev_ebitda`."""
    revenue, ebitda = 1000.0 * scale, 80.0 * scale
    debt, leases, cash = 200.0 * scale, 50.0 * scale, 50.0 * scale
    net_debt = debt - leases - cash
    market_cap = ebitda * ev_ebitda - net_debt
    shares = 10.0 * scale
    snapshot = {
        "price": market_cap / shares, "market_cap": market_cap, "total_debt": debt, "cash": cash,
        "revenue_ttm": revenue, "ebitda_ttm": ebitda, "net_income_ttm": 30.0 * scale,
        "shares_outstanding": shares, "book_value_per_share": 20.0,
        "low_52w": market_cap / shares * 0.8, "high_52w": market_cap / shares * 1.2,
        "lease_liabilities": leases,
    }
    values, sources = dl.apply_overrides(snapshot, ticker, pd.DataFrame(columns=dl.OVERRIDE_COLUMNS))
    values, sources = dl.compute_derived(values, sources)
    rev_hist = [revenue / 1.02 ** k for k in (4, 3, 2, 1)]
    history = pd.DataFrame({
        "revenue": rev_hist, "ebitda": [r * 0.08 for r in rev_hist], "ebit": [r * 0.05 for r in rev_hist],
        "net_income": [r * 0.03 for r in rev_hist], "pretax_income": [r * 0.04 for r in rev_hist],
        "tax_provision": [r * 0.01 for r in rev_hist], "interest_expense": [5.0 * scale] * 4,
        "d_and_a": [r * 0.03 for r in rev_hist], "capex": [-r * 0.035 for r in rev_hist],
        "change_in_wc": [0.0] * 4,
    }, index=YEARS)
    history.index.name = "fiscal_year_end"
    meta = {"ticker": ticker, "fetched_at": "2026-10-12T09:00:00", "currency": currency,
            "financial_currency": currency, "yahoo_enterprise_value": market_cap + debt - cash, "beta": 1.0,
            "name": f"{ticker} Inc.", "sector": "Consumer Defensive", "industry": industry,
            "data_source": "live"}
    income_q = pd.DataFrame([[revenue / 4] * 4, [ebitda / 4] * 4, [7.5 * scale] * 4], columns=QUARTERS,
                            index=["Total Revenue", "EBITDA", "Net Income Common Stockholders"])
    quality = dl.quality_checks(ticker, values, sources, meta, history, today=datetime(2026, 10, 12))
    return {"ticker": ticker, "values": values, "sources": sources, "meta": meta,
            "statements": {"income_q": income_q}, "history": history, "quality": quality}


def make_data(tickers=None, currencies=None, industries=None, risk_free=0.04) -> dict:
    """Default: the configured target + peers, all in USD, risk-free rate 4%.
    `currencies` / `industries` map ticker -> value for the ones that differ.
    risk_free=None mimics a currency without an automatic rate (user must enter it)."""
    tickers = tickers or config.ALL_TICKERS
    currencies, industries = currencies or {}, industries or {}
    multiples = [10.0, 12.0, 8.0, 14.0, 6.0, 9.0]
    companies = {t: company(t, 1.0 + i, multiples[i % len(multiples)], currencies.get(t, "USD"),
                            industries.get(t, "Discount Stores"))
                 for i, t in enumerate(tickers)}
    ccy = companies[tickers[0]]["meta"]["currency"]
    rf = {"currency": ccy, "guidance": config.risk_free_guidance(ccy)}
    if risk_free is None:
        rf.update(rate=float("nan"), date=None, fetched_at=None, data_source="manual")
    else:
        rf.update(rate=risk_free, date="2026-10-09", fetched_at="2026-10-12T09:00:00", data_source="live")
    short = {"rate": 0.03 if ccy == "USD" else float("nan"), "date": "2026-10-09",
             "fetched_at": "2026-10-12T09:00:00", "data_source": "live" if ccy == "USD" else "manual"}
    return {"companies": companies, "errors": {}, "target": tickers[0], "risk_free": rf, "short_rate": short}
