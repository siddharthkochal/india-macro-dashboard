"""
Market price fetcher — Yahoo Finance (no API key, no authentication)

Fetches: Brent crude, WTI, Dubai crude proxy, OVX (oil vol), Gold, INR/USD
Writes:  data/market_prices.json

Run:  python fetchers/fetch_market.py
Auto: called by pipeline.py before render step

Yahoo Finance v8 chart API: free, no key, returns JSON
URL pattern: https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
             ?interval=1mo&range=13mo
"""

import json
import logging
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATA_DIR

logger = logging.getLogger(__name__)

SYMBOLS = {
    "brent":  {"symbol": "BZ=F",    "name": "Brent Crude",        "unit": "USD/barrel",     "india_impact": "high_negative_if_above_85"},
    "wti":    {"symbol": "CL=F",    "name": "WTI Crude",          "unit": "USD/barrel",     "india_impact": "high_negative_if_above_80"},
    "gold":   {"symbol": "GC=F",    "name": "Gold",               "unit": "USD/troy oz",    "india_impact": "negative_via_import_demand"},
    "inr":    {"symbol": "USDINR=X","name": "USD/INR",            "unit": "INR per USD",    "india_impact": "higher_is_bearish_for_india"},
    "ovx":    {"symbol": "^OVX",    "name": "CBOE Oil Volatility","unit": "index",          "india_impact": "above_40_signals_supply_risk"},
}

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1mo&range=13mo"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _fetch_symbol(symbol: str) -> dict | None:
    url = YAHOO_URL.format(symbol=urllib.request.quote(symbol))
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", symbol, e)
        return None


def _parse_series(raw: dict) -> tuple[float | None, list]:
    """Extract current price and monthly series from Yahoo v8 response."""
    try:
        result = raw["chart"]["result"][0]
        meta = result.get("meta", {})
        current = meta.get("regularMarketPrice") or meta.get("previousClose")

        timestamps = result.get("timestamp", [])
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])

        series = []
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            series.append({
                "date": dt.strftime("%b-%y"),
                "value": round(close, 2),
            })

        return (round(current, 2) if current else None), series

    except (KeyError, IndexError, TypeError) as e:
        logger.warning("Parse error: %s", e)
        return None, []


def _signal(key: str, value: float | None) -> str:
    if value is None:
        return "neutral"
    rules = {
        "brent": lambda v: "bearish" if v > 85 else ("bullish" if v < 65 else "neutral"),
        "wti":   lambda v: "bearish" if v > 80 else ("bullish" if v < 60 else "neutral"),
        "gold":  lambda v: "bearish" if v > 2800 else "neutral",
        "inr":   lambda v: "bearish" if v > 87 else ("bullish" if v < 84 else "neutral"),
        "ovx":   lambda v: "bearish" if v > 40 else "neutral",
    }
    fn = rules.get(key)
    return fn(value) if fn else "neutral"


def fetch_all_market_prices() -> dict:
    results = {}
    for key, meta in SYMBOLS.items():
        logger.info("Fetching %s (%s)...", meta["name"], meta["symbol"])
        raw = _fetch_symbol(meta["symbol"])
        if raw:
            current, series = _parse_series(raw)
            prev = series[-2]["value"] if len(series) >= 2 else None
            change = round(current - prev, 2) if current and prev else None
            direction = ("up" if change and change > 0
                         else "down" if change and change < 0 else "flat")
            results[key] = {
                "symbol": meta["symbol"],
                "name": meta["name"],
                "unit": meta["unit"],
                "india_impact": meta["india_impact"],
                "current_value": current,
                "change_from_prev": change,
                "direction": direction,
                "signal": _signal(key, current),
                "series": series,
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            }
            logger.info("  %s = %s %s (signal: %s)",
                        meta["name"], current, meta["unit"], results[key]["signal"])
        else:
            results[key] = {
                "symbol": meta["symbol"],
                "name": meta["name"],
                "unit": meta["unit"],
                "current_value": None,
                "signal": "neutral",
                "error": "fetch_failed",
                "series": [],
            }
        time.sleep(0.5)   # be polite to Yahoo

    return {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source": "Yahoo Finance (free, no API key)",
        "prices": results,
        "india_oil_alert": _india_oil_alert(results),
    }


def _india_oil_alert(results: dict) -> dict:
    """Generate a plain-language alert for the dashboard."""
    brent = results.get("brent", {}).get("current_value")
    inr   = results.get("inr",   {}).get("current_value")
    ovx   = results.get("ovx",   {}).get("current_value")

    alerts = []
    severity = "normal"

    if brent and brent > 100:
        alerts.append(f"Brent at ${brent:.0f}/barrel — severe import bill pressure for India")
        severity = "critical"
    elif brent and brent > 85:
        alerts.append(f"Brent at ${brent:.0f}/barrel — above India comfort zone ($65–85)")
        severity = "warning"
    elif brent and brent < 65:
        alerts.append(f"Brent at ${brent:.0f}/barrel — low oil is a macro tailwind for India")

    if ovx and ovx > 40:
        alerts.append(f"Oil volatility index (OVX) at {ovx:.0f} — elevated supply disruption risk (Strait of Hormuz?)")
        if severity != "critical":
            severity = "warning"

    if inr and inr > 87:
        alerts.append(f"USD/INR at {inr:.2f} — weak rupee amplifies oil import cost in INR terms")

    return {
        "severity": severity,
        "alerts": alerts,
        "brent": brent,
        "inr": inr,
        "ovx": ovx,
    }


def run(output_path: Path = None) -> dict:
    data = fetch_all_market_prices()
    path = output_path or (DATA_DIR / "market_prices.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Market prices saved: %s", path)
    except Exception as e:
        logger.error("Could not write %s: %s", path, e)
    return data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    data = run()
    alert = data["india_oil_alert"]
    print(f"\nIndia Oil Alert — severity: {alert['severity'].upper()}")
    for a in alert["alerts"]:
        print(f"  !! {a}")
    print("\nAll prices:")
    for k, v in data["prices"].items():
        print(f"  {v['name']:<25} {str(v.get('current_value','N/A')):<10} {v['unit']}  [{v['signal']}]")
