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
| 2 | Comparable companies (multiples, peer statistics, implied value) | Done |
| 3 | DCF (FCFF projection, WACC, Gordon growth + exit multiple, sensitivity) | Done |
| 4 | Streamlit app, football field chart, Excel export | Planned |

## Methodology

### Data (Stage 1)
- Source: Yahoo Finance via `yfinance`. All figures are in **USD**.
- **TTM (trailing twelve months)** revenue, EBITDA and net income are rebuilt by summing the
  last four quarterly reports, not taken from Yahoo's summary fields. Yahoo's ready-made
  number is kept as a cross-check, and differences above 10% are shown in the data quality report.
- **Enterprise value** is calculated as market cap + debt − cash, so every step of the
  bridge is visible. Yahoo's own EV is used only as a cross-check.
- **Leases are excluded from debt** (switch: `EXCLUDE_LEASES_FROM_DEBT` in `config.py`).
  Under US GAAP (ASC 842) store rent is an operating expense, so EBITDA is already after rent;
  counting lease liabilities as debt as well would count rent twice. Yahoo does not separate
  finance from operating leases, so small finance leases are excluded too.
- **Risk-free rate**: the 10-year US Treasury yield (`^TNX`), downloaded live. The country
  risk premium defaults to 0 for the US.
- **Manual overrides**: any value can be replaced in `data/manual_overrides.csv`
  (with a source note). Every value is tagged as `auto`, `manual`, `derived` or `missing`.
- **No invented data**: if a value cannot be found it stays `N/A` and is flagged. It is never estimated.

### Comparable companies (Stage 2)
- Multiples: **EV/Sales, EV/EBITDA, P/E, P/B**, plus EBITDA margin and net margin (all TTM).
- Peer statistics (mean, median, min, max, 25th/75th percentile) use the **peers only**;
  the target is never included in its own benchmark.
- **Exclusions**: a multiple is dropped (shown as "NM", not meaningful) if an input is missing,
  the denominator is zero or negative (e.g. P/E of a loss-making company), or it exceeds a
  sanity cap set in `config.py` (e.g. P/E > 100x). Every exclusion is listed with its reason.
- **Valuation range**: the 25th-75th percentile of peer multiples (min-max if fewer than
  4 valid peers), with the median as the mid-point.
- **Bridge to value per share**:
  - EV multiples: target metric × multiple = implied EV → − net debt = equity value → ÷ shares = value per share
  - Equity multiples (P/E, P/B): target metric × multiple = equity value → ÷ shares (no net debt step)
- Peer selection matters: Walmart and Costco trade at premium multiples, which pulls the peer
  median up. The range is a starting point for judgement, not an answer.

### DCF (Stage 3)
- **5-year projection** starting from TTM revenue (Year 1 = the next twelve months).
  Default assumptions are historical averages (revenue growth: CAGR) and are shown next to the
  history they come from: revenue growth, EBITDA margin, D&A, capex and NWC investment as % of
  revenue, effective tax rate.
- **FCFF** = EBIT × (1 − tax) + D&A − capex − increase in net working capital.
- **WACC** = E/(D+E) × cost of equity + D/(D+E) × after-tax cost of debt, with market-value weights.
  - Cost of equity (CAPM) = risk-free rate (10y Treasury, live) + beta (Yahoo) × equity risk
    premium (assumption: 5.0%) + country risk premium (0 for the US).
  - Pre-tax cost of debt = risk-free rate + credit spread (assumption: 1.25%). The historical
    interest expense / debt ratio is shown for reference but not used, because it reflects
    bonds issued when rates were lower.
- **Terminal value**, two methods side by side:
  - Gordon growth: FCFF(N) × (1 + g) / (WACC − g), default g = 2.5%
  - Exit multiple: EBITDA(N) × peer median EV/EBITDA from the comps
  - Each is cross-checked against the other (implied exit multiple / implied growth rate).
- **Mid-year convention** (default on): cash flows are discounted at t − 0.5. Following
  Rosenbaum & Pearl, the Gordon terminal value is discounted at N − 0.5, the exit multiple
  terminal value at N (it is a sale price at the end of year N).
- **Bridge**: EV − net debt = equity value ÷ shares = value per share, vs current price.
- **Sensitivity tables**: WACC ±2% (0.5% steps) × terminal growth ±1% (0.5% steps), and
  WACC × exit multiple ±2x (1x steps), shown as heatmaps colored around the current price.
- A warning is shown when the terminal value is more than 75% of enterprise value.

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
- Yahoo usually provides only 4 years of annual statements, so the default DCF assumptions
  rest on 3-4 data points. One unusual year (e.g. a big working capital build) moves the average.
- The DCF uses the same assumption for all 5 years by default. Per-year inputs are supported.
- The equity risk premium, credit spread and terminal growth are judgement calls, not data.

## Project structure
```
config.py              tickers, settings and thresholds
data_loader.py         download, cache, overrides, data quality report
comps.py               trading multiples, peer statistics, implied value bridge
dcf.py                 projection, FCFF, WACC, terminal value, sensitivity tables
charts.py              Plotly charts (sensitivity heatmap)
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
python comps.py                  # comparable companies analysis
python dcf.py                    # DCF valuation in the terminal
python dcf.py --html             # also save sensitivity heatmaps to output/
pytest                           # run the unit tests
```

## Changing the companies
Edit `TARGET`, `PEERS` and `COMPANY_NAMES` in `config.py`.

*Disclaimer: for educational purposes only, not investment advice.*
