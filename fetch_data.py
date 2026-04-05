"""
RBI Data Fetcher
Pulls time-series KPI data from RBI DBIE API and identifies latest releases.
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

DBIE_BASE = "https://dbie.rbi.org.in/DBIE/dbie.rbi"
CACHE_DIR = Path(__file__).parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── DBIE Series IDs for key KPIs ──────────────────────────────────────────────
# These are the standard DBIE series codes used by RBI
DBIE_SERIES = {
    "repo_rate": {
        "series_id": "BSR1:II.3.1",
        "label": "Repo Rate (%)",
        "frequency": "M",
    },
    "cpi_inflation": {
        "series_id": "PRICES:I.2",
        "label": "CPI Inflation YoY (%)",
        "frequency": "M",
    },
    "wpi_inflation": {
        "series_id": "PRICES:II.2",
        "label": "WPI Inflation YoY (%)",
        "frequency": "M",
    },
    "forex_reserves": {
        "series_id": "FOREX:I.2",
        "label": "Foreign Exchange Reserves (USD Bn)",
        "frequency": "W",
    },
    "inr_usd": {
        "series_id": "FEMA:I.4",
        "label": "INR/USD Exchange Rate",
        "frequency": "M",
    },
    "bank_credit_growth": {
        "series_id": "BSR1:II.1",
        "label": "Non-Food Credit Growth YoY (%)",
        "frequency": "F",  # Fortnightly
    },
    "m3_growth": {
        "series_id": "BSR1:I.5",
        "label": "M3 Money Supply Growth YoY (%)",
        "frequency": "F",
    },
}

# ── RBI press release search patterns ─────────────────────────────────────────
RBI_PRESS_RELEASES_URL = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
RBI_PUBLICATIONS_URL = "https://www.rbi.org.in/scripts/Publications.aspx"

# Key RBI publication URLs for direct PDF access
RBI_KEY_URLS = {
    "mpc_statement": "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx",
    "monetary_policy_report": "https://www.rbi.org.in/Scripts/PublicationsView.aspx?id=22695",
    "fsr": "https://www.rbi.org.in/Scripts/PublicationsView.aspx?id=22697",
    "rbi_bulletin": "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx",
    "governors_statement": "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx",
}


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def _load_cache(key: str, max_age_hours: int = 6) -> dict | None:
    p = _cache_path(key)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    fetched_at = datetime.fromisoformat(data.get("_fetched_at", "2000-01-01"))
    if datetime.now() - fetched_at > timedelta(hours=max_age_hours):
        return None
    return data


def _save_cache(key: str, data: dict) -> None:
    data["_fetched_at"] = datetime.now().isoformat()
    _cache_path(key).write_text(json.dumps(data, indent=2))


def fetch_dbie_series(series_id: str, start_date: str = "2025-01-01") -> list[dict]:
    """
    Fetch a time-series from RBI DBIE API.
    Returns list of {date, value} dicts sorted ascending.
    Falls back to empty list if API is unavailable.
    """
    cache_key = f"dbie_{series_id.replace(':', '_')}_{start_date}"
    cached = _load_cache(cache_key, max_age_hours=12)
    if cached:
        return cached.get("series", [])

    try:
        # DBIE REST-like endpoint (public, no auth required)
        url = f"{DBIE_BASE}?seriesid={series_id}&startdate={start_date}"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "RBI-Dashboard/1.0"})
        if resp.status_code == 200:
            raw = resp.json()
            # DBIE returns {data: [{period, value}]}
            series = [
                {"date": item.get("period", item.get("date", "")), "value": item.get("value", None)}
                for item in raw.get("data", [])
                if item.get("value") is not None
            ]
            series.sort(key=lambda x: x["date"])
            _save_cache(cache_key, {"series": series})
            return series
    except Exception as e:
        print(f"  [DBIE] Could not fetch {series_id}: {e}")

    return []


def fetch_rbi_press_releases_list(limit: int = 30) -> list[dict]:
    """
    Fetch recent RBI press release titles and links.
    Returns list of {title, date, url} dicts.
    """
    cache_key = "rbi_press_releases"
    cached = _load_cache(cache_key, max_age_hours=2)
    if cached:
        return cached.get("releases", [])

    releases = []
    try:
        # RBI press releases listing page
        resp = requests.get(
            RBI_PRESS_RELEASES_URL,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        if resp.status_code == 200:
            # Simple regex-free extraction of press release links
            # RBI's page has a consistent pattern in anchor tags
            content = resp.text
            import re
            # Match press release links pattern on RBI site
            pattern = r'<td[^>]*>(\d{2}/\d{2}/\d{4})</td>.*?<a href="([^"]+PressRelease[^"]+)"[^>]*>([^<]+)</a>'
            matches = re.findall(pattern, content, re.DOTALL)
            for date_str, url, title in matches[:limit]:
                releases.append({
                    "date": date_str,
                    "title": title.strip(),
                    "url": f"https://www.rbi.org.in{url}" if url.startswith("/") else url,
                })
    except Exception as e:
        print(f"  [RBI] Could not fetch press releases list: {e}")

    _save_cache(cache_key, {"releases": releases})
    return releases


def fetch_url_text(url: str, timeout: int = 20) -> str:
    """Fetch text content from a URL (HTML/text stripping handled by Claude)."""
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,text/plain",
            },
        )
        if resp.status_code == 200:
            return resp.text[:150_000]  # cap at 150K chars
    except Exception as e:
        print(f"  [HTTP] Could not fetch {url}: {e}")
    return ""


# ── Known data sources per release month ─────────────────────────────────────
RBI_DATA_SOURCES = {
    "mpc": [
        "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx",
        "https://www.rbi.org.in/Scripts/BS_ViewMonetaryPolicy.aspx",
    ],
    "forex": "https://www.rbi.org.in/Scripts/BS_ForeignExchangeReserves.aspx",
    "credit": "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx",
    "cpi_source": "https://mospi.gov.in/web/mospi/download-tables-data/-/reports/view/templateTwo/15301",
    "iip_source": "https://mospi.gov.in/documents/213904/0/IIP+Summary+Table.pdf",
    "trade": "https://www.commerce.gov.in/trade-statistics/",
    "fiscal": "https://cga.nic.in/e-lekha/MonthlyAccountData.aspx",
}


def get_historical_data_manifest(start_month: str = "2025-01") -> dict:
    """
    Returns a structured manifest of what data to fetch and from where,
    for the period from start_month to present.
    """
    return {
        "period_start": start_month,
        "period_end": datetime.now().strftime("%Y-%m"),
        "kpis": {
            kpi: {
                "label": meta["label"],
                "frequency": meta["frequency"],
                "dbie_series": meta["series_id"],
            }
            for kpi, meta in DBIE_SERIES.items()
        },
        "primary_sources": {
            "monetary_policy": "https://www.rbi.org.in/Scripts/BS_ViewMonetaryPolicy.aspx",
            "weekly_stat": "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=22696",
            "press_releases": RBI_PRESS_RELEASES_URL,
            "rbi_bulletin": RBI_KEY_URLS["rbi_bulletin"],
        },
        "fetched_at": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    print("Testing data fetcher...")
    manifest = get_historical_data_manifest()
    print(json.dumps(manifest, indent=2))

    print("\nFetching forex reserves series...")
    forex = fetch_dbie_series("FOREX:I.2")
    if forex:
        print(f"  Got {len(forex)} data points, latest: {forex[-1]}")
    else:
        print("  DBIE offline — Claude will fetch via web_search/web_fetch tools")
