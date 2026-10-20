#!/usr/bin/env python3
"""
Fetch daily closes for Dow Jones, Nifty 50 and 10 Nifty sectoral indices from
Yahoo Finance and write dashboard.csv for the dashboard's "Upload combined CSV".

Setup:  pip install requests
Run:    python fetch_yahoo.py --range 1y        (e.g. 3mo, 6mo, 1y, 2y, 5y, max)
"""
import argparse, csv, io, json, time
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

# Index -> official NSE constituent list (fetched live, so it stays current)
NSE_LISTS = {
    "Nifty 50": "ind_nifty50list", "Nifty Bank": "ind_niftybanklist", "Nifty IT": "ind_niftyitlist",
    "Nifty Auto": "ind_niftyautolist", "Nifty FMCG": "ind_niftyfmcglist", "Nifty Pharma": "ind_niftypharmalist",
    "Nifty Metal": "ind_niftymetallist", "Nifty Energy": "ind_niftyenergylist",
    "Nifty Realty": "ind_niftyrealtylist", "Nifty PSU Bank": "ind_niftypsubanklist",
    "Nifty Fin Service": "ind_niftyfinancelist",
}
# Dow 30 is typed in by hand (no official CSV). Check it against the current list occasionally.
DOW30 = {
    "AAPL": "Apple", "AMGN": "Amgen", "AMZN": "Amazon", "AXP": "American Express", "BA": "Boeing",
    "CAT": "Caterpillar", "CRM": "Salesforce", "CSCO": "Cisco", "CVX": "Chevron", "DIS": "Disney",
    "GS": "Goldman Sachs", "HD": "Home Depot", "HON": "Honeywell", "IBM": "IBM", "JNJ": "Johnson & Johnson",
    "JPM": "JPMorgan Chase", "KO": "Coca-Cola", "MCD": "McDonald's", "MMM": "3M", "MRK": "Merck",
    "MSFT": "Microsoft", "NKE": "Nike", "NVDA": "Nvidia", "PG": "Procter & Gamble", "SHW": "Sherwin-Williams",
    "TRV": "Travelers", "UNH": "UnitedHealth", "V": "Visa", "VZ": "Verizon", "WMT": "Walmart",
}


def nse_members(index_name):
    url = f"https://www.niftyindices.com/IndexConstituent/{NSE_LISTS[index_name]}.csv"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")))
    return [(row["Symbol"].strip() + ".NS", row["Company Name"].strip()) for row in rows]


def build_stocks():
    members, names = {}, {}
    for idx in NSE_LISTS:
        try:
            lst = nse_members(idx)
            members[idx] = [t for t, _ in lst]
            names.update(dict(lst))
            print(f"{idx:18} {len(lst)} constituents")
        except Exception as e:
            print(f"!! {idx} constituents failed: {e}")
    members["Dow Jones"] = list(DOW30)
    names.update(DOW30)
    out = {}
    for tk in sorted({t for v in members.values() for t in v}):
        try:
            d = fetch(tk, "6mo")
            i = fetch_intraday(tk)
            out[tk] = {"name": names[tk], "tz": i["tz"], "prev": i["prev"],
                       "d": [[k, round(v, 2)] for k, v in sorted(d.items())], "i": i["pts"]}
        except Exception as e:
            print(f"!! {tk} failed: {e}")
        time.sleep(0.25)
    print(f"Stocks fetched: {len(out)}")
    with open("stocks.json", "w") as f:
        json.dump({"members": {k: [t for t in v if t in out] for k, v in members.items()}, "stocks": out},
                  f, separators=(",", ":"))


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


def fetch_intraday(ticker):
    """Latest session in 5-minute steps, plus previous close and exchange timezone."""
    for host in ("query1", "query2"):
        try:
            r = requests.get(
                f"https://{host}.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(ticker)}",
                params={"range": "1d", "interval": "5m"}, headers=HEADERS, timeout=30)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            m = res["meta"]
            pts = [[t, round(c, 2)] for t, c in
                   zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
            return {"tz": m.get("exchangeTimezoneName"), "prev": m.get("chartPreviousClose"), "pts": pts}
        except Exception as e:
            err = e
    raise err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="1y")
    ap.add_argument("--out", default="dashboard.csv")
    ap.add_argument("--stocks", action="store_true", help="also fetch every constituent stock (slow)")
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
    intraday = {}
    for name, tk in TICKERS.items():
        try:
            intraday[name] = fetch_intraday(tk)
            print(f"{name:18} intraday {len(intraday[name]['pts'])} points")
        except Exception as e:
            print(f"!! {name} intraday failed: {e}")
    with open("intraday.json", "w") as f:
        json.dump(intraday, f)

    if a.stocks:
        build_stocks()

    print(f"\nWrote {a.out} ({len(dates)} days). Upload it via 'Upload combined CSV' on the dashboard.")


if __name__ == "__main__":
    main()
