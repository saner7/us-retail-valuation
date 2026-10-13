# US Retail Valuation: Comps, Precedent Transactions, LBO + DCF

**Live app:** [tgt-valuation.streamlit.app](https://tgt-valuation.streamlit.app/)

_Interactive valuation of Target (NYSE: TGT) using comparable companies, precedent transactions, an LBO analysis and a DCF with Bear / Base / Bull scenarios, with a football field summary and a linked Excel model. Built as a learning project by an IB Diploma student interested in investment banking._

---

A valuation tool for **Target (TGT)**, benchmarked against **Walmart (WMT), Costco (COST),
Kroger (KR), Dollar General (DG) and BJ's Wholesale Club (BJ)**.
It combines a trading-comparables analysis, precedent transactions (what acquirers paid for
similar US retailers), a leveraged buyout (LBO) analysis (what a private equity fund could pay)
and a discounted cash flow (DCF) model with Bear / Base / Bull scenarios, and summarises them in a "football field" chart, the way an investment banking pitch book would.
Any other company can be analysed: pick it from the S&P 500 (the app suggests an editable peer
group) or type any Yahoo Finance ticker and its peers.

> Built as a learning project by an IB Diploma student interested in economics, finance and
> investment banking.

![App screenshot](docs/screenshot.png)
*Summary tab: football field with comps, precedent transactions, LBO and DCF (Bear / Bull markers), DCF results and the reverse DCF box (data downloaded 12 October 2026; share prices are the 9 October close).*

## Status

| Stage | Content | Status |
|---|---|---|
| 1 | Data layer: download, cache, manual overrides, data quality report | Done |
| 2 | Comparable companies (multiples, peer statistics, implied value) | Done |
| 3 | DCF (FCFF projection, WACC, Gordon growth + exit multiple, sensitivity) | Done |
| 4 | Streamlit app, football field chart, Excel export | Done |
| A | Any company: ticker and peer selection, currencies, financial companies | Done |
| B | Bear / Base / Bull scenarios and a probability-weighted value | Done |
| C | Precedent transactions (EV/EBITDA) with SEC sources | Done |
| L | LBO analysis: sources & uses, debt schedule, IRR / MOIC, maximum price, Excel sheet | Done |
| S | Pick from the S&P 500 with a suggested, editable peer group (GICS, size, curated groups) | Done |

## Methodology

### Data (Stage 1)
- Source: Yahoo Finance via `yfinance`. All figures for the default set are in **USD**; other
  companies are shown in their trading currency (see *Analysing other companies*).
- **TTM (trailing twelve months)** revenue, EBITDA and net income are rebuilt by summing the
  last four quarterly reports, not taken from Yahoo's summary fields. Yahoo's ready-made
  number is kept as a cross-check, and differences above 10% are shown in the data quality report.
- **Enterprise value** is calculated as market cap + debt − cash, so every step of the
  bridge is visible. Yahoo's own EV is used only as a cross-check.
- **Leases are excluded from debt** (switch: `EXCLUDE_LEASES_FROM_DEBT` in `config.py`).
  Under US GAAP (ASC 842) store rent is an operating expense, so EBITDA is already after rent;
  counting lease liabilities as debt as well would count rent twice. Yahoo does not separate
  finance from operating leases, so small finance leases are excluded too.
- **Risk-free rate**: the 10-year US Treasury yield (`^TNX`), downloaded live for USD; for other
  currencies it is entered by hand. The country risk premium defaults to 0 for the US. The LBO
  also uses the 13-week Treasury bill yield (`^IRX`) as a proxy for SOFR.
- **Leases missing from Yahoo**: some balance sheets (e.g. Apple's) have no lease line. If
  Yahoo's debt lines with and without leases are equal, its total debt holds no leases and 0 is
  subtracted (with a note); otherwise the value stays N/A. Fields Yahoo does not deliver are
  listed in a warning at the top of the app, with what shows N/A because of them.
- **Manual overrides**: any value can be replaced in `data/manual_overrides.csv`
  (with a source note). Every value is tagged as `auto`, `manual`, `derived` or `missing`.
  TTM values adjusted for a one-off record the quarter that contains the one-off and the
  TTM window they were calculated for; the data quality report flags the override as stale
  once that quarter leaves the window or a newer quarter is reported.
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
  Default assumptions (all editable, each shown next to the history it comes from):
  - D&A, capex (% of revenue) and tax rate: **average of the last 3 fiscal years**
    (`DEFAULT_HISTORY_YEARS`). The all-years average and the latest year are shown for comparison.
  - EBITDA margin: Year 1 = **current TTM margin with one-offs removed**, moving in a
    **straight line to the last 3 years' average** in Year 5. Start and end margin are
    edited separately in the app.
  - Increase in net working capital: **0% of revenue** (`DEFAULT_NWC_PCT`), because the history
    swings by 2-3% of revenue with a single inventory build.
  - Revenue growth: Year 1 = TTM revenue vs the last full fiscal year (growth already visible
    in the reported quarters), then a **straight line to the terminal growth rate** in Year 5.
    Falls back to the historical CAGR if TTM revenue is missing.
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
- **Reverse DCF**: the WACC, terminal growth and exit multiple at which the model value equals
  today's share price.
- **Sensitivity tables**: WACC ±2% (0.5% steps) × terminal growth ±1% (0.5% steps), and
  WACC × exit multiple ±2x (1x steps), shown as heatmaps colored around the current price.
- A warning is shown when the terminal value is more than 75% of enterprise value.

### App, football field and Excel export (Stage 4)
- **Streamlit app** (`app.py`) with seven tabs: Summary, DCF, LBO, Scenarios, Comps, Precedent
  Transactions, Data & quality. Every DCF and
  WACC assumption is in the sidebar, with its default and the history it comes from in the help
  text. Revenue growth and the EBITDA margin are set by a start (Year 1) and an end (Year 5)
  value; an optional year-by-year table overrides individual years.
- **Football field**, top to bottom: 52-week trading range, comps EV/EBITDA, P/E and EV/Sales
  (peer 25th-75th percentile, tick at the median), precedent transactions EV/EBITDA (when they
  apply, see Stage C), LBO (maximum price for a 25% to 15% IRR, tick at 20%), DCF exit multiple and DCF Gordon growth (WACC ±0.5% x exit multiple ±1x or
  terminal growth ±0.5%, tick at the base case, triangles at the Bear and Bull values), with the
  current price as a dashed vertical line (the axis always includes the price). **P/B is left out** of the chart (it stays in the
  comps tables with a note): a retailer's value comes from its cash flows, not from the
  assets on its balance sheet, and share buybacks shrink book equity and inflate P/B.
- **Reverse DCF box**: the WACC, terminal growth and exit multiple implied by today's price.
- **Excel export** (`export.py`, also the download button in the app). The **DCF, WACC,
  Scenarios and LBO sheets are live formulas**: inputs are blue on yellow, and changing one updates
  the value per share. The other sheets (Summary, Sensitivity, Comps, Bridge, Precedents, Market
  data, Data quality, History) hold values from the moment of export. A test recalculates the workbook with
  LibreOffice and checks that Excel and Python agree to 9 significant digits.

### Scenarios (Stage B)
- **Bear / Base / Bull**, each with its own revenue growth path (Year 1 -> Year 5), EBITDA margin
  path, capex, terminal growth and exit multiple. Base is the sidebar input. The WACC, D&A,
  working capital and tax are shared: scenarios describe performance, the WACC describes risk.
- **Default Bear / Bull** (all editable): halfway from Base to the most extreme year in the
  company's own history (growth, Year 5 margin, capex), terminal growth -/+ 0.5 points, and an
  exit multiple halfway between the peer median and the peer low / high. Moving all the way to
  the historical extremes stacks every worst (best) year at once: for Target it gave a negative
  Bear equity value. The Bull exit multiple is capped at 1.35x the target's own EV/EBITDA,
  because assuming it trades like the group's most premium members (Walmart, Costco) within
  five years is too optimistic.
- **Probability-weighted value** (default 25% / 50% / 25%): N/A unless the weights add up to 100%.
- Excel: a switch cell on the DCF sheet (1 = Bear, 2 = Base, 3 = Bull) drives the DCF through
  `INDEX`, and a Scenarios sheet values all three side by side with live formulas.

### Precedent transactions (Stage C)
What acquirers actually paid for comparable US retailers (`data/precedents.csv`). Every figure is
traced to an SEC filing: the deal press release (8-K Ex.99.1), the merger proxy or tender offer
statement (DEFM14A / DEFM14C / SC 14D-9) and the precedent-transaction tables in fairness opinions.
The `source_url` and `notes` columns name the document and section for each number; empty means
it was not found in a primary source.

| Announced | Target / Acquirer | Status | Tier | EV ($m) | EV / LTM EBITDA |
|---|---|---|---|---|---|
| 2016-03-14 | The Fresh Market / Apollo funds | completed 2016-04-27 | core | 1,360 | 7.1x |
| 2017-06-16 | Whole Foods Market / Amazon | completed 2017-08-28 | core | 13,700 | 10.6x |
| 2019-04-16 | Smart & Final / Apollo funds | completed 2019-06-20 | core | 1,120 | 6.5x |
| 2024-12-23 | Nordstrom / Nordstrom family and Liverpool | completed 2025-05-20 | core | 6,250 | 5.7x |
| 2017-06-28 | Staples / Sycamore Partners | completed 2017-09-12 | second tier | 6,600 | 5.0x |
| 2021-05-06 | At Home / Hellman & Friedman | completed 2021-07-23 | second tier | 2,830 | 7.9x |
| 2022-10-14 | Albertsons / Kroger | terminated 2024-12-10 | core | 24,600 | 5.4x |

- **Default calculation**: the four completed core deals (second-tier and terminated deals can be
  switched on in the app). The range uses the comps rule: 25th-75th percentile of the deals'
  EV/EBITDA with four or more deals, min-max with fewer, median as mid; then the same bridge to
  value per share. For the default four deals (5.7x, 6.5x, 7.1x, 10.6x) that is 6.3x / 6.8x /
  8.0x, or $92 / $101 / $122 per Target share. With fewer than five deals the app warns that the
  sample is small.
- **Only EV/EBITDA** is used: it is the standard yardstick in acquisitions, and EV/Sales would
  compare businesses with very different margins. LTM revenue was not in the proxies either.
- **Premiums are shown for information only**: they are measured against different reference
  prices (a single close, 10- to 30-day averages, or a price months before the announcement).
- Considered but left out: deals before 2016, sales in bankruptcy, loss-making e-commerce
  targets, unsigned approaches, private targets without disclosed figures, distributors and
  non-US deals; Family Dollar (distressed carve-out), Walgreens (loss-making, international),
  Michaels and Foot Locker (business models too far from Target).
- **Commentary.** Precedent values are usually expected above the comps, because buyers pay a
  control premium. Not here: the precedent multiples (median about 6.8x) sit below Target's own
  EV/EBITDA (9.7x) and the comps median (11.3x). Most deals involve smaller grocery chains, where
  margins are typically thin, or companies under pressure; half of them were leveraged buyouts by
  private equity funds, which can only pay what the debt and their return targets allow; and they
  come from different market periods. Amazon's purchase of Whole Foods, the only deal by an
  outside operating company seeking synergies, has the highest multiple (10.6x).
- **Two independent methods, one answer.** The precedent median (about $101 per share) and the
  Gordon growth DCF (about $101) arrive at a similar value by different routes: one from what
  acquirers paid, the other from Target's own cash flows. The trading comps (EV/EBITDA median
  about $182) and the DCF exit multiple (about $167) point higher, because both rest on today's
  peer multiples, which Walmart and Costco pull up. The match should not be over-read: the deals
  come from different periods (2016-2024) and mostly involve different kinds of companies
  (smaller grocers, retailers under pressure, leveraged buyouts).
- The precedent row appears only when the target's sub-sector (from Yahoo's country and industry)
  matches the deals in the calculation, or for Target, for whom the list was selected. Other
  companies can upload their own CSV in the app (template provided; used for that session only).

### LBO analysis (Stage L)
**Question:** if a private equity fund (the *sponsor*) bought Target with mostly borrowed money,
what is the most it could pay per share and still earn its target return? The fund puts in
equity, borrows the rest against EBITDA, uses the cash flow to repay debt and sells after five
years. Code: `lbo.py`; app: LBO tab; Excel: LBO sheet (live formulas, IRR via Excel's `IRR()`).

- **Operations** come from the DCF projection of the chosen scenario (default Base): no second
  set of operating assumptions. Taxes are recalculated after interest.
- **Entry**: current price + 25% premium (or an entry EV/EBITDA multiple). Entry EV = offer price
  x shares + net debt. For Target: $192.05 per share, EV $97.7bn = 11.8x TTM EBITDA ($8.27bn).
- **Sources and uses** (Target, $bn): uses = purchase of equity 87.25, repay existing debt 15.86,
  transaction fees 1.47, financing fees 0.83 (total 105.40); sources = Term Loan B 24.80,
  Senior Notes 16.53, existing cash above the minimum 3.26, sponsor equity 60.81 (58%).
- **Debt** (all terms are *illustrative assumptions*, editable; no market source is claimed):

  | Tranche | Size | Rate | Repayment | Maturity |
  |---|---|---|---|---|
  | Term Loan B | 3.0x EBITDA | floating: `^IRX` 4.05% (SOFR proxy) + 3.25% | 1% a year + cash sweep | 7 years |
  | Senior Notes | 2.0x EBITDA | fixed: 10-year Treasury 5.24% + 3.50% | at maturity | 8 years |

  Fees: 1.5% of entry EV (transaction), 2% of new debt (financing). Minimum cash: 2% of revenue.
- **Cash flow**: free cash flow after interest and tax pays the required repayments; cash above
  the minimum repays the Term Loan B early (*cash sweep*). Interest is charged on the debt at the
  start of each year, so there is no circular reference in Excel.
- **Interest deduction (US section 163(j))**, simplified: deductible interest is capped at 30% of
  EBITDA and the excess is ignored (in reality it carries forward).
- **Exit**: Year 5 at Target's **current trading EV/EBITDA (9.7x)**, not the entry multiple. The
  entry multiple includes the 25% premium, so exiting there assumes the fund sells above today's
  market value; sponsors usually underwrite more cautiously. Exit at the entry multiple is shown
  in the sensitivity table.
- **Returns**: MOIC = equity out / equity in; IRR = the yearly return that turns one into the other.
- **Maximum price**: with the exit multiple fixed, nothing after closing depends on the price (the
  debt is sized on EBITDA), so the price only changes the sponsor's cheque and can be solved
  directly: equity needed = exit equity / (1 + IRR)^5. The exit multiple stays fixed here on
  purpose: tying it to the entry price would let a higher price raise its own sale price.
- **Credit checks**: a warning when EBITDA / interest falls below 2x or total debt is above 6x
  EBITDA (the level the US bank regulators' *Interagency Guidance on Leveraged Lending*, 2013,
  says raises concerns for most industries), and when interest exceeds the 163(j) cap.
- **Feasibility**: compared with the largest sponsor buyouts on record: Electronic Arts (2025,
  EV ~$55bn, equity ~$36bn, $20bn debt; [EA 8-K Ex.99.1](https://www.sec.gov/Archives/edgar/data/712515/000114036125036415/ef20056167_ex99-1.htm))
  and TXU (2007, $45bn; [TXU 8-K Ex.99.1](https://www.sec.gov/Archives/edgar/data/1023291/000095013407004081/d44014exv99w1.htm)).
- **Simplifications**: no revolving credit facility and no interest on cash; taxes never negative
  and no loss carryforward; fees not amortised; no management options, dividends or dividend
  recaps; the sponsor owns 100%; no tax or fees at exit; base rates flat for five years.

**Target results.** At a 25% premium the fund would earn an IRR of **0.5%** (MOIC 1.02x): the
debt falls only from $41.3bn to $35.8bn in five years, because capex (~$4bn a year) and interest
(~$3bn) absorb most of the cash flow, and the exit at 9.7x is below the 11.8x paid. The most a
fund could pay is **$104 / $114 / $127 per share for a 25% / 20% / 15% IRR**, all below today's
$153.64. Interest is 30-38% of EBITDA, so the 163(j) cap applies every year. The deal would be
1.8x the largest sponsor buyout on record: read the result as a valuation yardstick (what a
financial buyer could justify), not as a realistic deal.

*Interest rates.* Two points lower base rates raise the 20% IRR price only from $114 to $118:
debt is sized as a multiple of EBITDA, so cheaper debt saves interest (~$0.65bn a year after
tax) but does not change how much is borrowed. The larger effect works through *debt capacity*:
lenders size debt by interest coverage. At today's rates 6x debt leaves coverage of 2.2x, close
to the 2x floor; with rates two points lower the same 6x leaves 3.0x. More cheap debt in place
of 20%-return equity is what lifts the price ($122 at 6x today, $126 at 6x with lower rates).

*Leverage cuts both ways.* At the 25% premium, more debt *lowers* the IRR (4x: 1.3%, 7x: -2.4% at
a 9.7x exit): leverage only helps when the business returns more than the debt costs, and buying
at 11.8x and selling at 9.7x does not. At the lower maximum price it does, which is why 6x
debt raises that price.

### Data quality checks
Missing fields, zero or negative EBITDA, losses (P/E excluded), negative equity, price outside
the 52-week range, price × shares vs market cap, our EV vs Yahoo's EV, reporting currency,
and stale annual statements.

## Valuation commentary
*Figures from the data downloaded on 12 October 2026 (price $153.64 at the 9 October close, 10-year
Treasury 5.24%, 13-week T-bill 4.05%); they move with the data.*

### One-off items (manual overrides)
Two one-offs distort the trailing twelve months, and neither is removed by Yahoo's
"normalized" figures:
- **Kroger**: a $2,585m impairment and related charges for its automated fulfillment network
  (Q3 FY2025, quarter ending Nov 2025). It is non-cash and inside operating expenses, so the
  sum of four quarters gives TTM EBITDA of only $5.8bn and P/E of 34x. Added back: EBITDA
  $8.4bn (EV/EBITDA 6.0x), net income $3.0bn (P/E 12.0x). This lowers the peer median P/E
  from 33.8x to 20.9x; the median EV/EBITDA (11.3x) does not change.
- **Target**: $994m of IEEPA tariff refunds booked as lower cost of sales in Q2 2026. Removed:
  TTM EBITDA $8.27bn instead of $9.26bn (margin 7.7% instead of 8.6%), net income $3.64bn.

Both are in `data/manual_overrides.csv` with the SEC source and will be flagged as stale when
the TTM window moves (Kroger's charge drops out once Q3 FY2026 is reported).

### Why the default assumptions changed
The first version averaged all four years Yahoo provides. The oldest year (fiscal year ending
January 2023) was Target's inventory glut year: 6.0% EBITDA margin, 5.1% capex and a 2.2%
working capital build. Averaged in, it produced a model that was internally inconsistent:
revenue shrinking 1.3% a year forever while capex stayed above D&A and the company kept
investing in working capital, and then a Gordon terminal value growing that depressed cash
flow at 2.5% forever. Projected FCFF was about $2.2bn a year, against an actual free cash
flow (operating cash flow - capex) of $3.7bn a year over the last three years.
The defaults now use the last three years for D&A, capex (3.6%) and tax, 0% working capital and
a growth path that starts from the growth already visible in 2026 (+2.8%) and fades to 2.5%.

The EBITDA margin starts from where Target is today and moves to where it has been: Year 1 uses
the current TTM margin with the tariff refund removed (7.7%), rising in a straight line to the
last three years' average (8.0%) in Year 5. Starting at 8.0% straight away would assume the
recovery has already happened; staying at 7.7% would ignore that 2023-2025 margins were
steadily around 8%. Guidance supports a gradual path: in its Q2 2026 results Target guided its underlying 2026
operating margin (excluding the refunds) to about 0.5 points above 2025's adjusted 4.6%, not a jump back to earlier levels.
Effect on the Gordon value per share: $47 -> $101.

### Why the Gordon and exit multiple results differ
- **Gordon growth: $101 (-34%)**. With WACC 9.2% and g 2.5%, the terminal value equals
  6.3x final-year EBITDA.
- **Exit multiple: $167 (+9%)**. The peer median EV/EBITDA of 11.3x implies a perpetual
  growth rate of 5.4%, which no mature retailer can sustain forever.

**Read the exit multiple result as an optimistic upper bound.** The 11.3x is the peer median,
and the peer group includes Walmart and Costco, which trade at premium multiples. Target's own
EV/EBITDA is 8.7x on reported TTM EBITDA (9.7x once the tariff refund is removed), and today's
price implies an exit multiple of 10.3x. Applying 11.3x assumes Target will be valued more
richly in five years than it is today, which is why this method lands above the price.

The two methods ask different questions. Gordon asks what the cash flows are worth at
*our* discount rate; the exit multiple asks what buyers pay *today* for similar companies.
The peer median is pulled up by Walmart (19.8x) and Costco (28.1x), which trade at premium
multiples for their growth and membership models; Target itself trades at 9.7x. The truth
probably sits in between, and the gap is shown rather than hidden.

### What the current price implies (reverse DCF)
With every other assumption unchanged, today's price of $153.64 implies:
- a **WACC of 7.2%** (the model uses 9.2%), or
- a **terminal growth rate of 4.8%** at the model WACC, or
- an **exit multiple of 10.3x**.

The WACC is high because the risk-free rate is high (5.24%): cost of equity = 5.24% + 0.98 x 5.0%
= 10.1%. We do not lower it to "fit" the price; the live rate is market data, and the
sensitivity tables show how the value moves with WACC. The honest reading is that, at
today's interest rates, the market price needs either a lower required return or more
growth/margin recovery than Target's recent history shows.

### Four methods side by side
| Method | Value per share | Based on |
|---|---|---|
| DCF, Gordon growth | ~$101 | Target's own cash flows at our WACC |
| Precedent transactions (median) | ~$101 | what acquirers paid for comparable retailers |
| LBO (20% IRR) | ~$114 | what a financial buyer could pay with today's financing costs |
| DCF, exit multiple | ~$167 | today's peer multiples, applied in five years |
| Comps EV/EBITDA (median) | ~$182 | today's peer multiples |

The three methods that rest on Target's own cash flow or on what buyers actually paid meet in a
cautious zone around $100-$115, about a quarter or more below the $153.64 price. The two that rest
on today's peer multiples sit above the price, because Walmart and Costco pull the peer median up.
So the market price only makes sense if Target is valued more like its premium peers than its own
cash flows justify at today's interest rates, or if margins and growth recover beyond its recent
history.

### Why leases are excluded from enterprise value (ASC 842)
Under US GAAP (ASC 842) operating lease costs (store rent) stay in operating expenses, so
EBITDA is already *after* rent. Lease liabilities are on the balance sheet and Yahoo includes
them in total debt. If we put them in EV but use post-rent EBITDA, rent is counted twice:
once as a cost that lowers EBITDA and once as debt that raises EV. EV/EBITDA would then be
overstated, and more so for companies that lease more stores (Dollar General's lease
liabilities are $11.2bn against $4.6bn of financial debt). So EV = market cap + financial debt
- cash, consistently for the target and every peer. The alternative (EV including leases with
EBITDAR, i.e. before rent) is also consistent, but Yahoo does not report rent for every company.

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
- Apart from revenue growth and the EBITDA margin (which move in a straight line), the DCF uses the same assumption
  for all 5 years by default. Per-year inputs are supported.
- Manual overrides are fixed numbers. They are checked for staleness but must be updated by hand.
- The equity risk premium, credit spread and terminal growth are judgement calls, not data.
- A company that reports in one currency but trades in another (e.g. Ferrari on the NYSE, `RACE`,
  reports in EUR and trades in USD) has its statement figures converted at today's exchange rate,
  and its DCF is then discounted with the trading currency's risk-free rate. Its cash flows are
  really earned in the reporting currency, so this mixes two currencies' interest rates and
  inflation: on 12 Oct 2026 `RACE` gave a Gordon value of $155 against EUR 200 for `RACE.MI`
  (US 10-year yield 5.24% vs Bund 3.53%). The app warns about this; analyse the home listing
  (`RACE.MI`) when there is one.
- **Scenarios**: Bear and Bull are built from the company's own history and the peer range, not
  from forecasts; the weights (25/50/25) are a judgement call, and the WACC is the same in all three.
- **Precedent transactions**: only four core deals, from 2016 to 2024 and from different market
  and interest rate conditions. LTM EBITDA comes from different advisors' fairness opinion
  tables, some on an adjusted basis, and advisors do not always agree (Staples: 5.0x at Morgan
  Stanley, 6.0x at Goldman Sachs). Whether the deals apply is
  decided from Yahoo's industry label, which is coarse (Target is "Discount Stores").
- **LBO**: the debt terms, fees and minimum cash are illustrative assumptions, not market quotes;
  `^IRX` is only a proxy for SOFR, and rates are held flat. See the simplifications in the LBO
  section.
- **Peer suggestions**: GICS does not always give the best peer group (Apple is classified with
  hardware makers but is valued like the platform companies; Ferrari as an automaker but trades
  like a luxury brand). Suggestions cover the S&P 500 only, and the market caps used to rank them
  are from one date (13 October 2026). On the live app, Yahoo may block some of the downloads for a
  larger group; the app then lists the missing fields.
- **Cyclical sectors**: the DCF defaults use the last three years' averages, which can mislead in
  cyclical industries such as energy, where those years may sit at a high or low point of the
  commodity cycle. For ExxonMobil the default Gordon value was $299 against a $172 price, helped by
  a low beta (0.21) and so a low WACC (6.2%). Review the margin, capex and WACC inputs by hand.
- **Banks and insurers**: Yahoo reports no EBITDA, lease or capex lines for them, so EV multiples,
  the DCF and the LBO stay N/A; P/E and P/B are shown instead.

## Project structure
```
config.py              tickers, settings and thresholds
data_loader.py         download, cache, overrides, data quality report
comps.py               trading multiples, peer statistics, implied value bridge
dcf.py                 projection, FCFF, WACC, terminal value, sensitivity tables
summary.py             football field data, reverse DCF, app inputs -> model
charts.py              Plotly charts (football field, sensitivity heatmap, scenarios)
scenarios.py           Bear / Base / Bull scenarios and the probability-weighted value
precedents.py          precedent transactions: loading, filters, EV/EBITDA range
lbo.py                 LBO: sources & uses, debt schedule, IRR / MOIC, maximum price
peers.py               peer suggestions: GICS sub-industry, sector fallback, size, curated groups
build_universe.py      downloads the S&P 500 list (Wikipedia) and market caps (Yahoo)
export.py              Excel export (DCF, WACC, Scenarios and LBO as live formulas)
app.py                 Streamlit app
data/manual_overrides.csv
data/precedents.csv    precedent transactions with SEC sources
data/sp500_universe.csv  S&P 500 constituents, GICS and market caps (source and dates in its header)
data/curated_peers.csv analyst-chosen peer groups with their rationale
data/cache/            downloaded data (not in git)
data/snapshot/         saved copy of the data, used only if the live download fails
.streamlit/config.toml app theme and settings (also used by Streamlit Community Cloud)
requirements.txt       runtime dependencies (pinned); requirements-dev.txt adds pytest
tests/                 pytest tests (offline: small hand-checkable numbers and a made-up data set)
docs/screenshot.png    app screenshot
```

## How to run
Needs **Python 3.11 to 3.14**. (Python 3.15 does not work yet: Streamlit needs `pyarrow`,
which has no ready-made package for 3.15, so the installation fails.)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # or requirements.txt if you don't need the tests

python data_loader.py            # snapshot, annual history and data quality report
python data_loader.py --refresh  # ignore the 24h cache and download again
python data_loader.py --refresh --save-snapshot   # also update the committed fallback data
python build_universe.py         # refresh the S&P 500 list and market caps (~10 min)
python comps.py                  # comparable companies analysis
python dcf.py                    # DCF valuation in the terminal
python dcf.py --html             # also save sensitivity heatmaps to output/
python export.py                 # Excel model with default assumptions -> output/TGT_valuation.xlsx
streamlit run app.py             # the app (http://localhost:8501)
pytest                           # run the tests (the Excel formula test needs LibreOffice)
```

If you download SEC filings with your own scripts, set your contact in an environment
variable rather than in the code (the SEC rejects requests without one):
`export SEC_USER_AGENT="Your Name you@example.com"` (read as `config.SEC_USER_AGENT`).

## Deploying on Streamlit Community Cloud
- Main file: `app.py`. In **Advanced settings** choose **Python 3.12** (Community Cloud ignores
  `runtime.txt`; the version can only be changed by deleting and redeploying the app).
- No secrets or API keys are needed.
- Yahoo Finance often rate-limits shared cloud servers. If the live download fails, the app
  opens with the saved data in `data/snapshot/` and shows a warning saying so. Refresh the
  snapshot from time to time with `python data_loader.py --refresh --save-snapshot` and push it.

## Picking a company and its peers (Stage S)
The sidebar offers two ways in: **Pick from S&P 500** (searchable by name or ticker) or **Enter
tickers manually** (any Yahoo Finance symbol, e.g. companies outside the S&P 500 such as `RACE.MI`).
The app opens on Target and its usual peer group, as before.

- **Universe** (`data/sp500_universe.csv`): the S&P 500 constituents from Wikipedia's
  [List of S&P 500 companies](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies)
  (revision 1379247819, downloaded 13 October 2026: 503 share classes, 500 companies) with their
  GICS sector and sub-industry, headquarters and SEC CIK, plus a one-off Yahoo Finance market cap
  (13 October 2026), so the app does not send 500 requests on start-up. GICS (Global Industry
  Classification Standard) is the MSCI / S&P system that sorts companies into 11 sectors and
  about 160 sub-industries; GICS classifications are maintained by MSCI and S&P, and this list is
  taken from Wikipedia for educational use. Refresh it with `python build_universe.py` (about ten
  minutes, one slow request per company).
- **Suggestion rules** (`peers.py`):
  1. never the target's other share classes (same CIK, e.g. GOOGL / GOOG), and one class per peer;
  2. the target's GICS sub-industry first;
  3. if fewer than 3 other companies share it, fill up from the same sector (flagged as a
     fallback); with 3 or more, a small but similar group is preferred;
  4. closest in size first (log difference of market caps, so twice and half as big are equally
     far); 6 peers by default, up to 10 (each peer is one more download from Yahoo).
- **Curated groups** (`data/curated_peers.csv`) come first when the target is listed, labelled
  *Curated peer group (analyst judgment)* with their rationale: Apple against the large platform
  companies (MSFT, GOOGL, META, AMZN) rather than its GICS hardware peers, and Target's usual group.
  The GICS suggestion is one click away.
- The group stays editable (remove, add from the S&P 500 or type other tickers; *Use suggested
  peers* restores it), and a table shows each peer's sub-industry, market cap, size relative to the
  target and why it was chosen.
- **Warnings**: sector fallback; peers that are all below 1/10 or above 10x the target's market
  cap ("multiples may not be comparable"); and, for any company, a WACC less than 2 points above
  the risk-free rate, which usually comes from a low beta (ExxonMobil: WACC 6.2%, beta 0.21).
- Examples from the 13 October data: Coca-Cola gets PepsiCo, Monster and Keurig Dr Pepper; JPMorgan
  the other diversified banks (with the financial-company warning); ExxonMobil shares its
  sub-industry only with Chevron, so refiners and pipelines fill the group; Garmin is alone in
  *Consumer Electronics* and gets eBay, Nike and Ford, which shows why the fallback is flagged.
  54 of the 126 sub-industries have at most two companies.

## Analysing other companies
Pick a company from the S&P 500 or type any Yahoo Finance ticker and a comma-separated peer group
(up to 10) in the app's sidebar; the default set is in `config.py`. Things to know:
- Outside the US use the exchange suffix (e.g. `RACE.MI` Milan, `MC.PA` Paris, `CFR.SW` Zurich).
- Amounts are shown in the target's trading currency. Companies that report in a different
  currency than they trade in (e.g. Ferrari on the NYSE) are converted at the latest exchange rate.
- The risk-free rate is downloaded only for USD; for other currencies the app asks you to enter
  the 10-year government bond yield (for the euro: the German Bund).
- One-off items were checked only for the default set; banks and insurers get a warning, as
  EV/EBITDA and an FCFF DCF do not fit them, and their football field adds P/B (book value is
  how financial companies are usually valued). Yahoo's saved fallback data exists only for the
  default set.
- Example tested: Ferrari (`RACE.MI`) against luxury peers (LVMH, Hermès, Moncler, Richemont)
  rather than carmakers, because the market prices Ferrari as a luxury brand for its business
  model (scarcity, pricing power, margins), not as a car manufacturer.

*Disclaimer: for educational purposes only, not investment advice.*
