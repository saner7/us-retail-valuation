"""Build data/sp500_universe.csv: the S&P 500 constituents with their GICS
sector and sub-industry (from Wikipedia) and a one-off market cap (from Yahoo).

    python build_universe.py                  # list + market caps (~500 Yahoo requests, ~10 min)
    python build_universe.py --no-market-caps  # list only; keeps no market caps

The app reads the saved file, so it never sends 500 requests on start-up.
"""

import argparse
import io
import re
import time
from datetime import date

import pandas as pd
import requests
import yfinance as yf

import config

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HEADERS = {"User-Agent": "us-retail-valuation/1.0 (educational project; python requests)"}
NOTE = "GICS classifications are maintained by MSCI and S&P; this list is taken from Wikipedia for educational use."
COLUMNS = ["ticker", "yahoo_ticker", "name", "gics_sector", "gics_sub_industry", "headquarters", "cik",
           "share_classes", "market_cap", "market_cap_date"]


def yahoo_ticker(ticker: str) -> str:
    """Wikipedia writes share classes with a dot (BRK.B), Yahoo with a dash (BRK-B)."""
    return ticker.strip().replace(".", "-")


def fetch_constituents() -> tuple[pd.DataFrame, str | None]:
    """The constituents table and the page revision id (None if the API does not answer)."""
    html = requests.get(URL, headers=HEADERS, timeout=60)
    html.raise_for_status()
    table = pd.read_html(io.StringIO(html.text), attrs={"id": "constituents"})[0]
    m = re.search(r'"wgRevisionId":(\d+)', html.text)        # the page names its own revision
    return table, m.group(1) if m else None


def tidy(table: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({
        "ticker": table["Symbol"].str.strip(),
        "name": table["Security"].str.strip(),
        "gics_sector": table["GICS Sector"].str.strip(),
        "gics_sub_industry": table["GICS Sub-Industry"].str.strip(),
        "headquarters": table["Headquarters Location"].str.strip(),
        "cik": table["CIK"].astype(int).map(lambda x: f"{x:010d}"),
    })
    df["yahoo_ticker"] = df["ticker"].map(yahoo_ticker)
    # Same CIK = same company (e.g. GOOGL / GOOG): list the other share classes
    groups = df.groupby("cik")["yahoo_ticker"].apply(list)
    df["share_classes"] = [" ".join(t for t in groups[c] if t != y) for c, y in zip(df["cik"], df["yahoo_ticker"])]
    df["market_cap"] = float("nan")
    df["market_cap_date"] = ""
    return df[COLUMNS]


def add_market_caps(df: pd.DataFrame, pause: float = 0.5) -> list:
    """Yahoo's marketCap for every ticker, one request each, slowly. Failures stay empty."""
    failed = []
    today = date.today().isoformat()
    for i, t in enumerate(df["yahoo_ticker"]):
        try:
            cap = yf.Ticker(t).info.get("marketCap")
            if isinstance(cap, (int, float)) and cap > 0:
                df.loc[i, ["market_cap", "market_cap_date"]] = [float(cap), today]
            else:
                failed.append(t)
        except Exception:
            failed.append(t)
        time.sleep(pause)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1} / {len(df)}")
    return failed


def save(df: pd.DataFrame, revid: str | None, path=None) -> None:
    path = path or config.UNIVERSE_PATH
    caps = df["market_cap_date"].replace("", pd.NA).dropna()
    header = [
        f"# Source: {URL}" + (f" (revision {revid})" if revid else ""),
        f"# Downloaded: {date.today().isoformat()}; market caps: Yahoo Finance marketCap"
        + (f", {caps.min()} to {caps.max()}" if not caps.empty else ", not downloaded"),
        f"# {NOTE}",
    ]
    with open(path, "w", newline="") as f:
        f.write("\n".join(header) + "\n")
        df.to_csv(f, index=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-market-caps", action="store_true", help="only refresh the list")
    args = parser.parse_args()
    table, revid = fetch_constituents()
    df = tidy(table)
    print(f"{len(df)} constituents, {df['cik'].nunique()} companies (revision {revid})")
    if not args.no_market_caps:
        failed = add_market_caps(df)
        print(f"Market caps: {df['market_cap'].notna().sum()} found; missing: {' '.join(failed) or 'none'}")
    save(df, revid)
    print(f"Saved {config.UNIVERSE_PATH}")


if __name__ == "__main__":
    main()
