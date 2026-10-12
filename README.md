# US Retail Valuation: Comparable Companies + DCF

A valuation tool for **Target (TGT)**, benchmarked against **Walmart (WMT), Costco (COST),
Kroger (KR), Dollar General (DG) and BJ's Wholesale Club (BJ)**.
It combines a trading-comparables analysis with a discounted cash flow (DCF) model and
summarises both in a "football field" chart, the way an investment banking pitch book would.

> Built as a learning project by an IB Diploma student interested in economics, finance and
> investment banking.

![App screenshot](docs/screenshot.png)
*(screenshot to be added)*

## Status

| Stage | Content | Status |
|---|---|---|
| 1 | Data layer: download, cache, manual overrides, data quality report | Done |
| 2 | Comparable companies (multiples, peer statistics, implied value) | Planned |
| 3 | DCF (FCFF projection, WACC, Gordon growth + exit multiple, sensitivity) | Planned |
| 4 | Streamlit app, football field chart, Excel export | Planned |

## Methodology

### Data (Stage 1)
- Source: Yahoo Finance via `yfinance`. All figures are in **USD**.
- **TTM (trailing twelve months)** revenue, EBITDA and net income are rebuilt by summing the
  last four quarterly reports, not taken from Yahoo's summary fields. Yahoo's ready-made
  number is kept as a cross-check, and differences above 10% are shown in the data quality report.
- **Enterprise value** is calculated as market cap + total debt − cash, so every step of the
  bridge is visible. Yahoo's own EV is used only as a cross-check.
- **Risk-free rate**: the 10-year US Treasury yield (`^TNX`), downloaded live. The country
  risk premium defaults to 0 for the US.
- **Manual overrides**: any value can be replaced in `data/manual_overrides.csv`
  (with a source note). Every value is tagged as `auto`, `manual`, `derived` or `missing`.
- **No invented data**: if a value cannot be found it stays `N/A` and is flagged. It is never estimated.

### Data quality checks
Missing fields, zero or negative EBITDA, losses (P/E excluded), negative equity, price outside
the 52-week range, price × shares vs market cap, our EV vs Yahoo's EV, reporting currency,
and stale annual statements.

## Assumptions and limitations
- **Data quality**: Yahoo Finance is free and convenient but not audited. Key numbers should
  be checked against the companies' 10-K / 10-Q filings (SEC EDGAR).
- **Fiscal years differ**: Target, Walmart, Kroger, Dollar General and BJ's close their year in
  late January / early February, while Costco closes at the end of August. Annual data is
  therefore labelled by the fiscal year **end date**. Note that Target calls the year ending
  January 2026 "fiscal 2025".
- **Leases**: Yahoo's "total debt" includes lease liabilities. Retailers lease many stores, so
  this matters for net debt and EV. EBITDA should be read consistently with that choice.
- **TTM from quarters**: summing four quarters assumes no later restatements. Costco's
  quarters have unequal lengths (12/12/12/16 weeks), but four consecutive quarters still cover a full year.
- Yahoo usually provides only 4 years of annual statements.

## Project structure
```
config.py              tickers, settings and thresholds
data_loader.py         download, cache, overrides, data quality report
data/manual_overrides.csv
data/cache/            downloaded data (not in git)
tests/                 pytest unit tests (offline, small hand-checkable numbers)
```

## How to run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python data_loader.py            # snapshot, annual history and data quality report
python data_loader.py --refresh  # ignore the 24h cache and download again
pytest                           # run the unit tests
```

## Changing the companies
Edit `TARGET`, `PEERS` and `COMPANY_NAMES` in `config.py`.

*Disclaimer: for educational purposes only, not investment advice.*
