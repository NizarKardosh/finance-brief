"""Fetch market data from TradingView's public scanner endpoint.

Usage: python fetch_markets.py [YYYY-MM-DD]
Writes data/<date>/markets.json
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FinanceBrief/1.0",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}

STOCK_COLS = [
    "name", "description", "close", "change", "volume", "market_cap_basic",
    "sector", "Value.Traded", "premarket_change", "premarket_close", "relative_volume_10d_calc",
]


def scan(market: str, body: dict, retries: int = 3) -> dict:
    url = f"https://scanner.tradingview.com/{market}/scan"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.load(resp)
        except Exception as exc:  # network hiccup, retry with backoff
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"TradingView scan failed: {last}")


def rows(result: dict, cols: list[str]) -> list[dict]:
    out = []
    for item in result.get("data", []):
        rec = dict(zip(cols, item["d"]))
        rec["symbol"] = item["s"]
        out.append(rec)
    return out


def clean_stock(r: dict) -> dict:
    return {
        "symbol": r["symbol"],
        "ticker": r["name"],
        "name": r["description"],
        "price": r["close"],
        "change": r["change"],
        "volume": r["volume"],
        "value_traded": r.get("Value.Traded"),
        "market_cap": r["market_cap_basic"],
        "sector": r.get("sector") or "Miscellaneous",
        "premarket_change": r.get("premarket_change"),
        "premarket_price": r.get("premarket_close"),
        "rel_volume": r.get("relative_volume_10d_calc"),
    }


def fetch_benchmarks() -> list[dict]:
    bench = CONFIG["benchmarks"]
    cols = ["name", "description", "close", "change"]
    res = scan("global", {"columns": cols, "symbols": {"tickers": [b["symbol"] for b in bench]}})
    by_sym = {r["symbol"]: r for r in rows(res, cols)}
    out = []
    for b in bench:
        r = by_sym.get(b["symbol"])
        if r:
            out.append({"label": b["label"], "symbol": b["symbol"], "price": r["close"],
                        "change": r["change"], "kind": b.get("kind", "index")})
    return out


def fetch_sp500() -> list[dict]:
    res = scan("america", {
        "columns": STOCK_COLS,
        "symbols": {"symbolset": ["SYML:SP;SPX"]},
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 600],
        "markets": ["america"],
    })
    return [clean_stock(r) for r in rows(res, STOCK_COLS) if r.get("close") is not None]


def fetch_us_movers(sort_by: str, order: str, n: int, extra_filter: list | None = None) -> list[dict]:
    flt = [
        {"left": "type", "operation": "equal", "right": "stock"},
        {"left": "is_primary", "operation": "equal", "right": True},
        {"left": "market_cap_basic", "operation": "greater", "right": 2_000_000_000},
        {"left": "close", "operation": "greater", "right": 3},
    ] + (extra_filter or [])
    res = scan("america", {
        "columns": STOCK_COLS,
        "filter": flt,
        "sort": {"sortBy": sort_by, "sortOrder": order},
        "range": [0, n],
        "markets": ["america"],
    })
    return [clean_stock(r) for r in rows(res, STOCK_COLS)]


def build(date: str) -> dict:
    n = CONFIG.get("movers_count", 10)
    sp = fetch_sp500()

    sectors: dict[str, dict] = {}
    for s in sp:
        sec = sectors.setdefault(s["sector"], {"name": s["sector"], "stocks": [], "market_cap": 0.0})
        sec["stocks"].append(s)
        sec["market_cap"] += s["market_cap"] or 0
    for sec in sectors.values():
        cap = sec["market_cap"] or 1
        sec["change"] = sum((x["change"] or 0) * (x["market_cap"] or 0) for x in sec["stocks"]) / cap
        sec["count"] = len(sec["stocks"])
        sec["advancers"] = sum(1 for x in sec["stocks"] if (x["change"] or 0) > 0)
        sec["leaders"] = sec["stocks"][: CONFIG.get("leaders_per_sector", 3)]
    sector_list = sorted(sectors.values(), key=lambda x: -x["market_cap"])

    # Watchlist: configured tickers, resolved against S&P data (fallback scan for others)
    by_ticker = {s["ticker"]: s for s in sp}
    watch = []
    missing = []
    for group in CONFIG["watchlist"]:
        items = []
        for t in group["tickers"]:
            if t in by_ticker:
                items.append(by_ticker[t])
            else:
                missing.append(t)
                items.append({"ticker": t, "missing": True})
        watch.append({"sector": group["sector"], "items": items})
    if missing:
        res = scan("america", {"columns": STOCK_COLS, "filter": [
            {"left": "name", "operation": "in_range", "right": missing},
            {"left": "is_primary", "operation": "equal", "right": True}], "range": [0, 100]})
        found = {r["name"]: clean_stock(r) for r in rows(res, STOCK_COLS)}
        for g in watch:
            g["items"] = [found.get(i["ticker"], i) if i.get("missing") else i for i in g["items"]]

    adv = sum(1 for s in sp if (s["change"] or 0) > 0)
    dec = sum(1 for s in sp if (s["change"] or 0) < 0)

    return {
        "date": date,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "TradingView scanner",
        "benchmarks": fetch_benchmarks(),
        "breadth": {"advancers": adv, "decliners": dec, "total": len(sp)},
        "sectors": [{k: v for k, v in s.items() if k != "stocks"} for s in sector_list],
        "heatmap": [
            {"name": s["name"], "change": s["change"], "market_cap": s["market_cap"],
             "stocks": [{"t": x["ticker"], "n": x["name"], "c": x["change"], "m": x["market_cap"], "p": x["price"]}
                        for x in s["stocks"]]}
            for s in sector_list
        ],
        "watchlist": watch,
        "most_active": fetch_us_movers("Value.Traded", "desc", n),
        "gainers": fetch_us_movers("change", "desc", n),
        "losers": fetch_us_movers("change", "asc", n),
        "premarket_movers": fetch_us_movers(
            "premarket_change", "desc", 6,
            [{"left": "premarket_volume", "operation": "greater", "right": 50_000}]),
    }


def main() -> int:
    date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    out_dir = ROOT / "data" / date
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build(date)
    (out_dir / "markets.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    b = {x["label"]: round(x["change"], 2) for x in data["benchmarks"]}
    print(f"OK markets.json: {len(data['heatmap'])} sectors, {data['breadth']['total']} S&P stocks; {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
