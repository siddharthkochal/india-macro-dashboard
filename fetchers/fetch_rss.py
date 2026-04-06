"""
Geopolitical news fetcher — spectator-index.com/api/rss

Free, no API key, no authentication.
Parses RSS XML → filters India-relevant headlines → writes data/geopolitical_news.json

Run:  python fetchers/fetch_rss.py
Auto: called by pipeline.py before render step
"""

import json
import logging
import re
import sys
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATA_DIR

logger = logging.getLogger(__name__)

RSS_URL = "https://spectator-index.com/api/rss"

# Keywords that make a headline relevant to India or Indian macro
INDIA_KEYWORDS = [
    "india", "rupee", "inr", "rbi", "brent", "wti", "crude", "oil", "opec",
    "hormuz", "iran", "iran war", "gulf", "middle east", "saudi", "kuwait",
    "bahrain", "oman", "uae", "tanker", "lng", "refinery", "petrochemical",
    "straits", "strait", "shipping", "tariff", "trade", "fed ", "federal reserve",
    "dollar", "usd", "inflation", "recession", "china", "pakistan",
    "ceasefire", "escalat", "missile", "attack", "strike",
]

# High-severity keywords — always include regardless of India filter
SEVERITY_KEYWORDS = [
    "hormuz", "iran war", "oil price", "crude oil", "brent", "wti", "opec",
]


def _is_relevant(title: str, desc: str) -> bool:
    text = (title + " " + (desc or "")).lower()
    return any(kw in text for kw in INDIA_KEYWORDS)


def _severity(title: str, desc: str) -> str:
    text = (title + " " + (desc or "")).lower()
    if any(kw in text for kw in ["hormuz", "oil price doubl", "brent above", "crude above"]):
        return "critical"
    if "breaking" in title.lower():
        return "high"
    return "medium"


def fetch_geopolitical_news(max_items: int = 30) -> dict:
    """
    Fetch latest headlines from spectator-index.com RSS.
    Returns structured dict ready to write as JSON.
    """
    logger.info("Fetching geopolitical news from %s", RSS_URL)

    try:
        req = urllib.request.Request(
            RSS_URL,
            headers={"User-Agent": "Mozilla/5.0 (India Macro Dashboard/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_bytes = resp.read()
    except Exception as e:
        logger.error("Failed to fetch RSS: %s", e)
        return _empty_result(str(e))

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.error("Failed to parse RSS XML: %s", e)
        return _empty_result(f"XML parse error: {e}")

    items = root.findall(".//item")
    logger.info("RSS returned %d items", len(items))

    all_items = []
    india_items = []

    for item in items:
        title = (item.findtext("title") or "").strip()
        desc  = (item.findtext("description") or "").strip()
        pub   = (item.findtext("pubDate") or "").strip()
        link  = (item.findtext("link") or "").strip()

        # Parse date
        try:
            dt = parsedate_to_datetime(pub)
            date_iso = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            date_display = dt.astimezone(timezone.utc).strftime("%d %b %H:%M UTC")
        except Exception:
            date_iso = ""
            date_display = pub[:16] if pub else ""

        entry = {
            "title": title,
            "description": desc,
            "date": date_iso,
            "date_display": date_display,
            "severity": _severity(title, desc),
            "is_india_relevant": _is_relevant(title, desc),
        }
        all_items.append(entry)
        if entry["is_india_relevant"]:
            india_items.append(entry)

    # Extract oil price signals from headlines
    oil_signals = _extract_oil_signals(all_items)

    result = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source": RSS_URL,
        "source_authority": "The Spectator Index",
        "total_items": len(all_items),
        "india_relevant_items": len(india_items),
        "oil_signals": oil_signals,
        "india_headlines": india_items[:20],
        "all_recent_headlines": all_items[:max_items],
    }

    return result


def _extract_oil_signals(items: list) -> dict:
    """Parse oil price levels and direction signals from headlines."""
    signals = {
        "brent_mentioned": False,
        "hormuz_risk": False,
        "opec_action": False,
        "price_direction": None,   # "up" / "down" / None
        "latest_oil_headline": None,
    }

    for item in items:
        text = (item["title"] + " " + item["description"]).lower()

        if "hormuz" in text:
            signals["hormuz_risk"] = True

        if "opec" in text:
            signals["opec_action"] = True

        if any(w in text for w in ["brent", "crude oil", "wti", "oil price"]):
            signals["brent_mentioned"] = True
            if signals["latest_oil_headline"] is None:
                signals["latest_oil_headline"] = item["title"]
            if any(w in text for w in ["rise", "rises", "up", "higher", "surge", "jump", "double"]):
                signals["price_direction"] = "up"
            elif any(w in text for w in ["fall", "falls", "down", "lower", "drop", "plunge"]):
                signals["price_direction"] = "down"

    return signals


def _empty_result(error: str) -> dict:
    return {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "source": RSS_URL,
        "error": error,
        "india_headlines": [],
        "all_recent_headlines": [],
        "oil_signals": {},
    }


def run(output_path: Path = None) -> dict:
    data = fetch_geopolitical_news()
    path = output_path or (DATA_DIR / "geopolitical_news.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Geopolitical news saved: %s (%d India-relevant items)",
                    path, data.get("india_relevant_items", 0))
    except Exception as e:
        logger.error("Could not write %s: %s", path, e)
    return data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    data = run()
    print(f"\nFetched {data['total_items']} items, "
          f"{data['india_relevant_items']} India-relevant")
    print(f"Oil signals: {data['oil_signals']}")
    print("\nTop India-relevant headlines:")
    for h in data["india_headlines"][:8]:
        print(f"  [{h['date_display']}] {h['title']}")
