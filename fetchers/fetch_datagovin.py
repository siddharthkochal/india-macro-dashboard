"""
data.gov.in REST API fetcher — direct HTTP calls, no third-party package.

API key: register free at https://data.gov.in → My Account → API Key
Set in .env: DATAGOVIN_API_KEY=your_key_here

Usage:
    python fetchers/fetch_datagovin.py --kpi air_passenger_traffic

Writes to: data/{kpi_id}.json
Updates:   data/progress.json
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DATA_DIR

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

BASE_URL = "https://api.data.gov.in/resource/"

# Dataset IDs on data.gov.in for each supported KPI
# Find IDs at: https://data.gov.in/catalog/monthly-air-traffic-statistics
DATASET_IDS = {
    "air_passenger_traffic": "9ef84268-d588-465a-a308-a864a43d0070",
    # Add more as discovered — check data.gov.in catalog for resource IDs
}


def fetch_datagovin(resource_id: str, api_key: str, limit: int = 500) -> list:
    """
    Fetch all records from a data.gov.in resource.
    Returns list of dicts. Raises on HTTP error.
    """
    url = f"{BASE_URL}{resource_id}"
    params = {
        "api-key": api_key,
        "format": "json",
        "limit": limit,
        "offset": 0,
    }
    logger.info("Fetching data.gov.in resource: %s", resource_id)
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    records = data.get("records", [])
    logger.info("  Got %d records", len(records))
    return records


def transform_air_passenger(records: list) -> dict:
    """
    Transform raw data.gov.in air traffic records into our KPI schema.
    Field names vary — inspect actual response and adjust keys below.
    """
    series = []
    for r in records:
        # Common field names in DGCA dataset — adjust if API returns different names
        date_val = r.get("month_year") or r.get("month") or r.get("date", "")
        domestic = r.get("domestic_passengers") or r.get("dom_pax") or r.get("passengers")
        try:
            domestic = float(str(domestic).replace(",", "")) / 1_000_000  # convert to millions
        except (TypeError, ValueError):
            domestic = None
        if domestic is not None:
            series.append({"date": date_val, "value": round(domestic, 2), "is_provisional": False})

    series.sort(key=lambda x: x["date"])
    series_12 = series[-12:] if len(series) > 12 else series
    current = series_12[-1]["value"] if series_12 else None
    prev = series_12[-2]["value"] if len(series_12) >= 2 else None
    change = round(current - prev, 2) if (current and prev) else None

    return {
        "kpi_id": "air_passenger_traffic",
        "fetched_at": datetime.now().isoformat(),
        "source_authority": "DGCA via data.gov.in",
        "source_url": "https://www.data.gov.in/catalog/monthly-air-traffic-statistics",
        "current_value": current,
        "unit": "million passengers",
        "as_of": series_12[-1]["date"] if series_12 else None,
        "as_of_release_date": datetime.now().strftime("%Y-%m-%d"),
        "change_from_prev": change,
        "direction": "up" if (change and change > 0) else ("down" if (change and change < 0) else "flat"),
        "is_provisional": False,
        "data_confidence": "high",
        "data_notes": None,
        "series": series_12,
        "notes": f"Domestic air passenger traffic from DGCA via data.gov.in. {len(series_12)} monthly data points."
    }


def save_kpi(kpi_id: str, kpi_data: dict):
    path = DATA_DIR / f"{kpi_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kpi_data, f, indent=2, ensure_ascii=False)
    logger.info("Saved: %s", path)

    # Update progress.json
    prog_path = DATA_DIR / "progress.json"
    if prog_path.exists():
        try:
            with open(prog_path, encoding="utf-8") as f:
                prog = json.load(f)
            if kpi_id in prog.get("kpis", {}):
                prog["kpis"][kpi_id]["status"] = "complete"
                prog["kpis"][kpi_id]["fetched_at"] = datetime.now().isoformat()
                prog["last_updated"] = datetime.now().isoformat()
            with open(prog_path, "w", encoding="utf-8") as f:
                json.dump(prog, f, indent=2)
        except Exception as e:
            logger.warning("Could not update progress.json: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Fetch KPI data from data.gov.in")
    parser.add_argument("--kpi", required=True, choices=list(DATASET_IDS.keys()),
                        help="KPI to fetch")
    args = parser.parse_args()

    api_key = os.environ.get("DATAGOVIN_API_KEY", "")
    if not api_key:
        logger.error("DATAGOVIN_API_KEY not set in environment or .env file")
        logger.error("Get a free key at: https://data.gov.in (My Account -> API Key)")
        sys.exit(1)

    kpi_id = args.kpi
    resource_id = DATASET_IDS[kpi_id]

    try:
        records = fetch_datagovin(resource_id, api_key)
    except requests.HTTPError as e:
        logger.error("HTTP error fetching %s: %s", kpi_id, e)
        sys.exit(1)
    except requests.RequestException as e:
        logger.error("Network error fetching %s: %s", kpi_id, e)
        # One retry
        logger.info("Retrying in 5s...")
        time.sleep(5)
        try:
            records = fetch_datagovin(resource_id, api_key)
        except Exception as e2:
            logger.error("Retry failed: %s", e2)
            sys.exit(1)

    if kpi_id == "air_passenger_traffic":
        kpi_data = transform_air_passenger(records)
    else:
        logger.error("No transformer for KPI: %s", kpi_id)
        sys.exit(1)

    save_kpi(kpi_id, kpi_data)
    logger.info("Done: %s", kpi_id)


if __name__ == "__main__":
    main()
