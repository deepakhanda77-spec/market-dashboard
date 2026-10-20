#!/usr/bin/env python3
"""
Fetch option-chain data (nearest expiry) for every NSE F&O stock listed in stocks.json
using the DhanHQ Option Chain API, and write options.json for the dashboard.

Needs:  pip install requests pyotp
Env:    DHAN_CLIENT_ID                      (always)
        DHAN_ACCESS_TOKEN                   (paste a fresh 24h token)   -- OR --
        DHAN_PIN + DHAN_TOTP_SECRET         (script generates a token itself)
Run:    python fetch_options.py              (add --limit 5 to test with 5 stocks)

Dhan allows 1 option-chain request per 3 seconds, so a full run takes ~20 minutes.
"""
import argparse, csv, io, json, os, sys, time
from datetime import datetime, timedelta, timezone
import requests

API = "https://api.dhan.co/v2"
MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
WAIT = 3.2          # seconds between option-chain calls (limit is 1 per 3s)
KEEP = 15           # strikes kept each side of the spot price


def get_token(client_id):
    t = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if t:
        return t
    pin, secret = os.environ.get("DHAN_PIN"), os.environ.get("DHAN_TOTP_SECRET")
    if not (pin and secret):
        sys.exit("Set DHAN_ACCESS_TOKEN, or DHAN_PIN + DHAN_TOTP_SECRET.")
    import pyotp
    r = requests.post("https://auth.dhan.co/app/generateAccessToken",
                      params={"dhanClientId": client_id, "pin": pin, "totp": pyotp.TOTP(secret).now()}, timeout=30)
    r.raise_for_status()
    tok = r.json().get("accessToken")
    if not tok:
        sys.exit(f"Token generation failed: {r.text[:200]}")
    return tok


def security_ids(symbols):
    """Map RELIANCE -> Dhan security id using Dhan's public instrument master."""
    want = set(symbols)
    r = requests.get(MASTER, timeout=120)
    r.raise_for_status()
    out = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        if row.get("SEM_EXM_EXCH_ID") != "NSE" or row.get("SEM_INSTRUMENT_NAME") != "EQUITY":
            continue
        if row.get("SEM_SERIES", "EQ") not in ("EQ", ""):
            continue
        sym = row.get("SEM_TRADING_SYMBOL", "").strip()
        if sym in want and sym not in out:
            out[sym] = row["SEM_SMST_SECURITY_ID"].strip()
    return out


def post(path, hdr, body):
    for attempt in range(3):
        r = requests.post(API + path, json=body, headers=hdr, timeout=45)
        if r.status_code == 429:
            time.sleep(6)
            continue
        return r
    return r


def num(x):
    return x if isinstance(x, (int, float)) else None


def leg(d):
    d = d or {}
    oi, prev = num(d.get("oi")), num(d.get("previous_oi"))
    return [num(d.get("last_price")), oi, (oi - prev) if oi is not None and prev is not None else None,
            num(d.get("implied_volatility"))]


def parse_chain(j):
    data = j.get("data") or {}
    spot, oc = num(data.get("last_price")), data.get("oc") or {}
    rows = []
    if isinstance(oc, dict):
        for k, v in oc.items():
            rows.append((float(k), v.get("ce"), v.get("pe")))
    else:
        for v in oc:
            rows.append((float(v.get("strike_price")), v.get("ce"), v.get("pe")))
    rows.sort(key=lambda x: x[0])
    return spot, rows


def summarize(spot, rows):
    ce_tot = sum((r[1] or {}).get("oi") or 0 for r in rows)
    pe_tot = sum((r[2] or {}).get("oi") or 0 for r in rows)
    pain, best = None, None
    for K, _, _ in rows:
        pay = sum(((c or {}).get("oi") or 0) * max(0, K - s) + ((p or {}).get("oi") or 0) * max(0, s - K)
                  for s, c, p in rows)
        if best is None or pay < best:
            best, pain = pay, K
    top_ce = max(rows, key=lambda r: (r[1] or {}).get("oi") or 0)[0] if rows else None
    top_pe = max(rows, key=lambda r: (r[2] or {}).get("oi") or 0)[0] if rows else None
    return {"ceOi": ce_tot, "peOi": pe_tot, "pcr": round(pe_tot / ce_tot, 2) if ce_tot else None,
            "maxPain": pain, "topCe": top_ce, "topPe": top_pe}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    client = os.environ.get("DHAN_CLIENT_ID", "").strip()
    if not client:
        sys.exit("Set DHAN_CLIENT_ID.")
    hdr = {"access-token": get_token(client), "client-id": client, "Content-Type": "application/json"}

    members = json.load(open("stocks.json"))["members"]
    tickers = sorted({t for k, v in members.items() if k != "Dow Jones" for t in v})
    syms = [t[:-3] for t in tickers if t.endswith(".NS")]
    if a.limit:
        syms = syms[:a.limit]
    ids = security_ids(syms)
    print(f"{len(syms)} stocks, {len(ids)} matched in Dhan master")

    today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()
    out, errors, seg_ok = {}, [], None
    for n, sym in enumerate(syms, 1):
        if sym not in ids:
            continue
        sid = int(ids[sym])
        expiries, seg = None, None
        for s in ([seg_ok] if seg_ok else ["NSE_EQ", "NSE_FNO"]):
            r = post("/optionchain/expirylist", hdr, {"UnderlyingScrip": sid, "UnderlyingSeg": s})
            time.sleep(WAIT)
            if r.status_code == 200 and (r.json().get("data") or []):
                expiries, seg = sorted(r.json()["data"]), s
                seg_ok = seg_ok or s
                break
            if r.status_code in (401, 403):
                sys.exit(f"Dhan rejected the token/subscription ({r.status_code}): {r.text[:200]}")
        if not expiries:
            print(f"[{n}/{len(syms)}] {sym}: no options")
            continue
        exp = next((e for e in expiries if e >= today), None)
        if not exp:
            continue
        r = post("/optionchain", hdr, {"UnderlyingScrip": sid, "UnderlyingSeg": seg, "Expiry": exp})
        time.sleep(WAIT)
        if r.status_code != 200:
            errors.append(f"{sym}: chain HTTP {r.status_code} {r.text[:80]}")
            print("!!", errors[-1])
            continue
        spot, rows = parse_chain(r.json())
        if not rows:
            continue
        atm = min(range(len(rows)), key=lambda i: abs(rows[i][0] - (spot or rows[len(rows)//2][0])))
        keep = rows[max(0, atm - KEEP): atm + KEEP + 1]
        out[sym + ".NS"] = {
            "expiry": exp, "expiries": [e for e in expiries if e >= today][:4], "spot": spot,
            "summary": summarize(spot, rows),
            "chain": [[k] + leg(c) + leg(p) for k, c, p in keep],
        }
        print(f"[{n}/{len(syms)}] {sym}: {len(rows)} strikes, expiry {exp}, PCR {out[sym + '.NS']['summary']['pcr']}")

    with open("options.json", "w") as f:
        json.dump({"updated": datetime.now(timezone.utc).isoformat(), "stocks": out, "errors": errors[:10]},
                  f, separators=(",", ":"))
    print(f"Wrote options.json with {len(out)} stocks")


if __name__ == "__main__":
    main()
