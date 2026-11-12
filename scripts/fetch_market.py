"""Pull end-of-day prices (Tiingo) and macro series (FRED) into data/market.json.

Runs in GitHub Actions on a schedule. Needs two repository secrets:
TIINGO_API_KEY and FRED_API_KEY. Standard library only, no installs.
"""
import json, os, sys, time, datetime as dt, urllib.request, urllib.parse

TIINGO = os.environ.get("TIINGO_API_KEY", "").strip()
FRED = os.environ.get("FRED_API_KEY", "").strip()
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "market.json")
YEARS = 5

# group, ticker as Tiingo spells it, display symbol, name
BASKET = [
    ("Core", "BRK-B", "BRK.B", "Berkshire Hathaway"),
    ("Core", "VTI", "VTI", "Total US stock market"),
    ("Core", "VOO", "VOO", "S&P 500"),
    ("Core", "QQQ", "QQQ", "Nasdaq 100"),
    ("Core", "DIA", "DIA", "Dow Jones 30"),
    ("Core", "IWM", "IWM", "Russell 2000 small caps"),
    ("Core", "AVUV", "AVUV", "US small-cap value"),
    ("Core", "SCHD", "SCHD", "US dividend payers"),
    ("Core", "VXUS", "VXUS", "Total international"),
    ("Core", "VEA", "VEA", "Developed international"),
    ("Core", "VWO", "VWO", "Emerging markets"),
    ("Safety", "GLD", "GLD", "Gold"),
    ("Safety", "SLV", "SLV", "Silver"),
    ("Safety", "SGOV", "SGOV", "0-3 month T-bills"),
    ("Safety", "SHY", "SHY", "1-3 year Treasuries"),
    ("Safety", "IEF", "IEF", "7-10 year Treasuries"),
    ("Safety", "TLT", "TLT", "20+ year Treasuries"),
    ("Safety", "TIP", "TIP", "Inflation-protected Treasuries"),
    ("Themes", "XLK", "XLK", "Technology"),
    ("Themes", "SMH", "SMH", "Semiconductors"),
    ("Themes", "URA", "URA", "Uranium and nuclear"),
    ("Themes", "CCJ", "CCJ", "Cameco (uranium miner)"),
    ("Themes", "XLE", "XLE", "Energy"),
    ("Themes", "XLU", "XLU", "Utilities"),
    ("Themes", "ITA", "ITA", "Aerospace and defense"),
    ("Themes", "PAVE", "PAVE", "US infrastructure"),
    ("Themes", "VNQ", "VNQ", "REITs"),
    ("Themes", "PLD", "PLD", "Prologis (industrial REIT)"),
]
CRYPTO = [("Crypto", "btcusd", "BTC", "Bitcoin"), ("Crypto", "ethusd", "ETH", "Ethereum")]

# id, label, units, group
MACRO = [
    ("VIXCLS", "VIX volatility index", "index", "Markets"),
    ("DGS3MO", "3-month Treasury yield", "%", "Rates"),
    ("DGS2", "2-year Treasury yield", "%", "Rates"),
    ("DGS10", "10-year Treasury yield", "%", "Rates"),
    ("DGS30", "30-year Treasury yield", "%", "Rates"),
    ("BAA10Y", "Corporate credit spread (Baa minus 10-yr)", "%", "Rates"),
    ("MORTGAGE30US", "30-year mortgage rate", "%", "Housing"),
    ("HOUST", "Housing starts", "thous. units, annual rate", "Housing"),
    ("PERMIT", "Building permits", "thous. units, annual rate", "Housing"),
    ("CSUSHPINSA", "Case-Shiller national home price index", "index", "Housing"),
    ("TTLCONS", "Total construction spending", "$M, annual rate", "Construction"),
    ("TLNRESCONS", "Non-residential construction spending", "$M, annual rate", "Construction"),
    ("TLRESCONS", "Residential construction spending", "$M, annual rate", "Construction"),
    ("PCU332321332321", "Producer prices: metal doors and frames", "index", "Construction"),
]


def get(url, headers=None, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "banyan-market-job"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # retry transient failures
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def tiingo_daily(ticker, start):
    q = urllib.parse.urlencode({"startDate": start, "resampleFreq": "daily"})
    rows = get(f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?{q}",
               {"Authorization": f"Token {TIINGO}", "Content-Type": "application/json"})
    # adjClose includes dividends and splits, so returns are total returns
    return [[r["date"][:10], round(r.get("adjClose") or r["close"], 4)] for r in rows]


def tiingo_crypto(ticker, start):
    q = urllib.parse.urlencode({"tickers": ticker, "startDate": start, "resampleFreq": "1day"})
    data = get(f"https://api.tiingo.com/tiingo/crypto/prices?{q}",
               {"Authorization": f"Token {TIINGO}", "Content-Type": "application/json"})
    rows = data[0]["priceData"] if data else []
    return [[r["date"][:10], round(r["close"], 2)] for r in rows]


def fred(series, start):
    q = urllib.parse.urlencode({"series_id": series, "api_key": FRED, "file_type": "json",
                                "observation_start": start})
    data = get(f"https://api.stlouisfed.org/fred/series/observations?{q}")
    return [[o["date"], float(o["value"])] for o in data["observations"] if o["value"] not in (".", "")]


def main():
    if not TIINGO or not FRED:
        sys.exit("Missing TIINGO_API_KEY or FRED_API_KEY secret.")
    today = dt.date.today()
    start = (today - dt.timedelta(days=365 * YEARS + 10)).isoformat()
    out = {"updated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "assets": [], "macro": [], "errors": []}

    for group, tk, sym, name in BASKET + CRYPTO:
        try:
            series = tiingo_crypto(tk, start) if group == "Crypto" else tiingo_daily(tk, start)
            if not series:
                raise ValueError("no rows returned")
            out["assets"].append({"group": group, "symbol": sym, "name": name, "series": series})
        except Exception as e:
            out["errors"].append(f"{sym}: {e}")
        time.sleep(1.2)  # stay well inside Tiingo's free-tier rate limit

    for sid, label, units, group in MACRO:
        try:
            out["macro"].append({"id": sid, "label": label, "units": units, "group": group,
                                 "series": fred(sid, start)})
        except Exception as e:
            out["errors"].append(f"{sid}: {e}")

    # never overwrite good data with an empty run
    if len(out["assets"]) < len(BASKET) // 2:
        sys.exit("Too many failures, keeping the previous file: " + "; ".join(out["errors"][:5]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Wrote {len(out['assets'])} assets, {len(out['macro'])} macro series, {len(out['errors'])} errors")
    for e in out["errors"]:
        print("  error:", e)


if __name__ == "__main__":
    main()
