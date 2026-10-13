"""Save the committed fallback data (data/snapshot/) that the app uses when
Yahoo Finance blocks the live download, e.g. on Streamlit Community Cloud.

    python build_snapshot.py --all          # every S&P 500 company + the default set (~30-40 min)
    python build_snapshot.py TSLA ABBV      # only these tickers
    python build_snapshot.py --all --missing-only   # only companies without a snapshot yet

Only the statement rows the model reads are kept (data_loader.snapshot_rows()),
so the files stay small. Requests are slowed down; failures are retried once at
the end and listed.
"""

import argparse
import shutil
import time

import config
import data_loader as dl


def save_one(ticker: str) -> None:
    data = dl.fetch_from_yahoo(ticker)
    data["meta"]["data_source"] = "live"
    dl.save_company_cache(ticker, data, config.SNAPSHOT_DIR, trim=True)


def run(tickers: list, pause: float) -> list:
    failed = []
    for i, t in enumerate(tickers, 1):
        try:
            save_one(t)
        except Exception as e:
            failed.append(t)
            print(f"  {t}: {str(e)[:100]}")
        time.sleep(pause)
        if i % 50 == 0:
            print(f"  {i} / {len(tickers)}")
    return failed


def save_rates() -> None:
    """The US 10-year Treasury and the 13-week T-bill (LBO base rate)."""
    dl.get_risk_free_rate("USD", refresh=True)
    dl.get_short_rate(refresh=True)
    for name in ("risk_free_rate.json", "short_rate.json"):
        if (config.CACHE_DIR / name).exists():
            shutil.copy2(config.CACHE_DIR / name, config.SNAPSHOT_DIR / name)


def snapshot_size() -> str:
    files = list(config.SNAPSHOT_DIR.glob("*"))
    return f"{len(files)} files, {sum(f.stat().st_size for f in files) / 1e6:.1f} MB"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tickers", nargs="*", help="Yahoo tickers (default: --all)")
    parser.add_argument("--all", action="store_true", help="every S&P 500 company plus the default set")
    parser.add_argument("--missing-only", action="store_true", help="skip companies that already have a snapshot")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between companies")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers]
    if args.all or not tickers:
        tickers = list(dict.fromkeys(config.ALL_TICKERS + sorted(dl.known_tickers())))
    if args.missing_only:
        have = set(dl.snapshot_tickers())
        tickers = [t for t in tickers if t not in have]
    print(f"Saving {len(tickers)} companies to {config.SNAPSHOT_DIR}")
    failed = run(tickers, args.pause)
    if failed:
        print(f"Retrying {len(failed)} after a pause: {' '.join(failed)}")
        time.sleep(30)
        failed = run(failed, args.pause * 3)
    save_rates()
    print(f"Done. Failed: {' '.join(failed) or 'none'}. Snapshot: {snapshot_size()}")


if __name__ == "__main__":
    main()
