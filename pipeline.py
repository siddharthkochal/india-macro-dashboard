"""
RBI KPI Dashboard - Main Pipeline Orchestrator

Usage:
  python pipeline.py                          # render from cached data
  python pipeline.py --period "Jan 2026"      # specific period label
  python pipeline.py --refresh               # force re-fetch (requires API credits)
  python pipeline.py --commentary-only       # regenerate commentary from cached KPIs

Requires: ANTHROPIC_API_KEY in environment or .env file (only for --refresh)
"""

import argparse
import io
import json
import os
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import (
    DATA_DIR, OUTPUT_DIR, LOG_FILE,
    PERIOD_LABEL, STALENESS_WARNING_DAYS
)

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("pipeline")


# ── Helpers ────────────────────────────────────────────────────────────────────

def check_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key or not key.startswith("sk-"):
        logger.error("ANTHROPIC_API_KEY not set or invalid.")
        logger.error("Set it in a .env file: ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)
    logger.info("API key: %s...%s", key[:12], key[-4:])


def load_kpis_config() -> list:
    """Load the KPI registry from kpis_config.json."""
    config_path = Path(__file__).parent / "kpis_config.json"
    if not config_path.exists():
        logger.error("kpis_config.json not found at %s", config_path)
        return []
    with open(config_path, encoding="utf-8") as f:
        return json.load(f).get("kpis", [])


def load_per_kpi_data(kpis_config: list) -> dict:
    """
    Load each KPI's data from data/{kpi_id}.json.
    Returns merged dict: {"kpis": {kpi_id: data, ...}}
    Missing files are skipped with a warning — never crashes.
    """
    kpis = {}
    for kpi in kpis_config:
        kpi_id = kpi["id"]
        path = DATA_DIR / f"{kpi_id}.json"
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    kpis[kpi_id] = json.load(f)
            except Exception as e:
                logger.warning("Could not load %s: %s", path, e)
        else:
            logger.warning("No data file for %s (expected %s) — will render as N/A", kpi_id, path)
    return {"kpis": kpis}


def load_commentary() -> dict:
    path = DATA_DIR / "commentary.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Could not load commentary.json: %s", e)
    return {}


def load_events() -> list:
    path = DATA_DIR / "events.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                # Support both {"events": [...]} wrapper and bare list
                return data.get("events", data) if isinstance(data, dict) else data
        except Exception as e:
            logger.warning("Could not load events.json: %s", e)
    return []


def check_data_freshness(kpi_data: dict) -> list:
    """
    Returns a list of stale KPI ids (as_of_release_date older than threshold).
    Emits warnings to logger.
    """
    stale = []
    threshold = timedelta(days=STALENESS_WARNING_DAYS)
    now = datetime.now()
    for kpi_id, kpi in kpi_data.get("kpis", {}).items():
        release_date_str = kpi.get("as_of_release_date")
        if not release_date_str:
            continue
        try:
            release_date = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
            age = now - release_date
            if age > threshold:
                logger.warning(
                    "Stale data: %s last released %s (%d days ago)",
                    kpi_id, release_date_str, age.days
                )
                stale.append(kpi_id)
        except ValueError:
            pass
    return stale


def load_legacy_cache(period: str) -> tuple:
    """
    Backward-compatible: load old monolithic kpi_data_{slug}.json and commentary_{slug}.json.
    Returns (kpi_data_dict, commentary_dict) or (None, None).
    """
    slug = period.replace(" ", "_").lower()
    kpi_path = DATA_DIR / f"kpi_data_{slug}.json"
    com_path = DATA_DIR / f"commentary_{slug}.json"
    kpi_data = None
    commentary = None
    if kpi_path.exists():
        try:
            with open(kpi_path, encoding="utf-8") as f:
                kpi_data = json.load(f)
        except Exception as e:
            logger.warning("Legacy KPI cache load failed: %s", e)
    if com_path.exists():
        try:
            with open(com_path, encoding="utf-8") as f:
                commentary = json.load(f)
        except Exception as e:
            logger.warning("Legacy commentary cache load failed: %s", e)
    return kpi_data, commentary


def update_progress(kpi_id: str, status: str = "complete"):
    path = DATA_DIR / "progress.json"
    try:
        with open(path, encoding="utf-8") as f:
            prog = json.load(f)
        if kpi_id in prog.get("kpis", {}):
            prog["kpis"][kpi_id]["status"] = status
            prog["kpis"][kpi_id]["fetched_at"] = datetime.now().isoformat()
            prog["last_updated"] = datetime.now().isoformat()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prog, f, indent=2)
    except Exception as e:
        logger.warning("Could not update progress.json: %s", e)


# ── Pipeline ───────────────────────────────────────────────────────────────────

def run_pipeline(
    period: str = PERIOD_LABEL,
    start_date: str = "2025-04-01",
    force_refresh: bool = False,
    commentary_only: bool = False,
) -> str:
    """
    Full pipeline: load data -> render.
    For --refresh mode: also runs AI extraction (requires API credits).
    Returns path to generated HTML dashboard.
    """
    logger.info("=" * 60)
    logger.info("RBI KPI Dashboard Pipeline")
    logger.info("Period : %s", period)
    logger.info("From   : %s", start_date)
    logger.info("Mode   : %s", "commentary-only" if commentary_only else "full")
    logger.info("=" * 60)

    kpis_config = load_kpis_config()
    events = load_events()

    # ── Step 0: Refresh live market + geopolitical data ──────────────────────
    logger.info("[0/3] Refreshing live market prices and geopolitical news...")
    try:
        from fetchers.fetch_market import run as fetch_market
        fetch_market()
    except Exception as e:
        logger.warning("Market fetch failed (non-fatal): %s", e)
    try:
        from fetchers.fetch_rss import run as fetch_rss
        fetch_rss()
    except Exception as e:
        logger.warning("RSS fetch failed (non-fatal): %s", e)

    # ── Step 1: Load KPI data ────────────────────────────────────────────────
    if force_refresh and not commentary_only:
        check_api_key()
        logger.info("[1/3] Re-fetching KPI data (API credits will be used)...")
        from analyze import run_extraction
        kpi_data = run_extraction(period=period, start_date=start_date)
    else:
        logger.info("[1/3] Loading KPI data from cache...")
        # Try new per-KPI files first
        kpi_data = load_per_kpi_data(kpis_config)
        if not kpi_data.get("kpis"):
            # Fall back to legacy monolithic cache
            logger.info("  No per-KPI files found — trying legacy cache...")
            legacy_kpi, _ = load_legacy_cache(period)
            if legacy_kpi:
                kpi_data = legacy_kpi
                logger.info("  Loaded legacy cache for period: %s", period)
            else:
                logger.warning("  No cached data found — dashboard will show N/A values")
                kpi_data = {"kpis": {}}

    kpi_data["period_label"] = period

    # Check freshness
    stale = check_data_freshness(kpi_data)
    if stale:
        logger.warning("Stale KPIs: %s", stale)

    # ── Step 2: Load / generate commentary ──────────────────────────────────
    if force_refresh or commentary_only:
        check_api_key()
        logger.info("[2/3] Generating commentary (API credits will be used)...")
        from analyze import run_commentary
        commentary = run_commentary(kpi_data=kpi_data, period=period)
    else:
        logger.info("[2/3] Loading commentary from cache...")
        commentary = load_commentary()
        if not commentary:
            _, legacy_com = load_legacy_cache(period)
            if legacy_com:
                commentary = legacy_com
                logger.info("  Loaded legacy commentary cache")
            else:
                logger.warning("  No commentary cache — dashboard will show placeholder text")
                commentary = {}

    # ── Load live market + geopolitical data ────────────────────────────────
    def _load_json_safe(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    market_data = _load_json_safe(DATA_DIR / "market_prices.json")
    geo_data    = _load_json_safe(DATA_DIR / "geopolitical_news.json")

    # ── Step 3: Render ───────────────────────────────────────────────────────
    logger.info("[3/3] Rendering dashboard...")
    from render import render_dashboard

    slug = period.replace(" ", "_").lower().replace("-", "_")
    out_path = OUTPUT_DIR / f"dashboard_{slug}.html"

    render_dashboard(
        kpi_data=kpi_data,
        commentary=commentary,
        events=events,
        kpis_config=kpis_config,
        output_path=str(out_path),
        stale_kpis=stale,
        market_data=market_data,
        geo_data=geo_data,
    )

    root_out = Path(__file__).parent / "dashboard.html"
    render_dashboard(
        kpi_data=kpi_data,
        commentary=commentary,
        events=events,
        kpis_config=kpis_config,
        output_path=str(root_out),
        stale_kpis=stale,
        market_data=market_data,
        geo_data=geo_data,
    )

    logger.info("=" * 60)
    logger.info("Dashboard ready!")
    logger.info("Open: %s", out_path)
    logger.info("Also: %s", root_out)
    logger.info("=" * 60)

    headline = (commentary or {}).get("macro_narrative", {}).get("headline")
    if headline:
        logger.info("Macro headline: %s", headline)

    return str(out_path)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="RBI KPI Dashboard — India macro analysis"
    )
    parser.add_argument("--period", default=PERIOD_LABEL)
    parser.add_argument("--start-date", default="2025-04-01")
    parser.add_argument("--refresh", action="store_true",
                        help="Force re-fetch via API (costs credits)")
    parser.add_argument("--commentary-only", action="store_true",
                        help="Regenerate commentary from cached KPIs")
    parser.add_argument("--latest", action="store_true",
                        help="Use current month as period label")
    args = parser.parse_args()

    period = args.period
    if args.latest:
        period = datetime.now().strftime("%B %Y")

    run_pipeline(
        period=period,
        start_date=args.start_date,
        force_refresh=args.refresh,
        commentary_only=args.commentary_only,
    )


if __name__ == "__main__":
    main()
