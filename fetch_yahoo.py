#!/usr/bin/env python3
"""
Fetch daily closes for Dow Jones, Nifty 50 and 10 Nifty sectoral indices from
Yahoo Finance and write dashboard.csv for the dashboard's "Upload combined CSV".

Setup:  pip install requests
Run:    python fetch_yahoo.py --range 1y        (e.g. 3mo, 6mo, 1y, 2y, 5y, max)
"""
import argparse, csv
from datetime import datetime, timezone
import requests

# Dashboard card name -> Yahoo ticker
TICKERS = {
    "Dow Jones":        "^DJI",
    "Nifty 50":         "^NSEI",
    "Nifty Bank":       "^NSEBANK",
    "Nifty IT":         "^CNXIT",
    "Nifty Auto":       "^CNXAUTO",
    "Nifty FMCG":       "^CNXFMCG",
    "Nifty Pharma":     "^CNXPHARMA",
    "Nifty Metal":      "^CNXMETAL",
    "Nifty Energy":     "^CNXENERGY",
    "Nifty Realty":     "^CNXREALTY",
    "Nifty PSU Bank":   "^CNXPSUBANK",
    "Nifty Fin Service": "^CNXFIN",   # if empty, try NIFTY_FIN_SERVICE.NS
}

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(ticker, rng):
    for host in ("query1", "query2"):
        try:
            r = requests.get(
                f"https://{host}.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ticker)}",
                params={"range": rng, "interval": "1d"}, headers=HEADERS, timeout=30)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            off = res["meta"].get("gmtoffset", 0)
            closes = res["indicators"]["quote"][0]["close"]
            return {datetime.fromtimestamp(t + off, tz=timezone.utc).date().isoformat(): c
                    for t, c in zip(res["timestamp"], closes) if c is not None}
        except Exception as e:
            err = e
    raise err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="1y")
    ap.add_argument("--out", default="dashboard.csv")
    a = ap.parse_args()

    series = {}
    for name, tk in TICKERS.items():
        try:
            series[name] = fetch(tk, a.range)
            print(f"{name:18} {tk:14} {len(series[name])} rows")
        except Exception as e:
            print(f"!! {name} ({tk}) failed: {e}")

    dates = sorted({d for s in series.values() for d in s})
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date"] + list(series))
        for d in dates:
            w.writerow([d] + [round(series[k][d], 2) if d in series[k] else "" for k in series])
    print(f"\nWrote {a.out} ({len(dates)} days). Upload it via 'Upload combined CSV' on the dashboard.")


if __name__ == "__main__":
    main()
