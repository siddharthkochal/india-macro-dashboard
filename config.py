"""
Central configuration for RBI KPI Dashboard.
Change model, thresholds, dates, and cache settings here — nowhere else.
"""

# ── Model ──────────────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"          # swap to "claude-opus-4-6" for higher quality
MAX_TOKENS_EXTRACTION = 8000
MAX_TOKENS_COMMENTARY = 12000

# ── Data pipeline ──────────────────────────────────────────────────────────────
PERIOD_LABEL   = "Apr 2025 - Mar 2026"
START_DATE     = "2025-04-01"
END_DATE       = "2026-03-31"
CACHE_TTL_HOURS = 72                 # re-fetch if cached data older than this

# ── Staleness warning (dashboard UI) ──────────────────────────────────────────
STALENESS_WARNING_DAYS = 7           # show ⚠ badge if as_of_release_date > N days old
STALENESS_ERROR_DAYS   = 30          # show red badge if > N days old

# ── Batch pause (API rate limiting) ───────────────────────────────────────────
BATCH_PAUSE_SECONDS = 65

# ── Signal thresholds ─────────────────────────────────────────────────────────
# All values are inclusive on the boundary stated.
# Change these to tune signal sensitivity — signals.py reads from here.
SIGNAL_THRESHOLDS = {
    "repo_rate": {
        # Directional: change from previous meeting
        "bullish_if": "change < 0",        # rate cut
        "bearish_if": "change > 0",        # rate hike
        "neutral_if": "change == 0",
    },
    "cpi_inflation": {
        # Absolute value vs RBI comfort band (2–6%, target 4%)
        "bullish_max": 4.0,                # below 4% = bullish
        "bearish_min": 6.0,                # above 6% = bearish
        # 4.0 – 6.0 = neutral
    },
    "gdp_growth": {
        "bullish_min": 7.0,                # above 7% = bullish
        "bearish_max": 5.5,                # below 5.5% = bearish
    },
    "inr_usd": {
        "bullish_max": 84.0,               # below 84 = strong INR = bullish
        "bearish_min": 87.0,               # above 87 = weak INR = bearish
    },
    "forex_reserves": {
        # Directional: week-on-week change
        "bullish_if": "week_change > 0",
        "bearish_if": "week_change < 0",
    },
    "iip": {
        "bullish_min": 5.0,
        "bearish_max": 2.0,
    },
    "bank_credit_growth": {
        "bullish_min": 14.0,
        "bearish_max": 10.0,
    },
    "gst_collections": {
        # Monthly GST in crore INR (1.8 lakh crore = 180000 crore)
        "bullish_min": 180000,
        "bearish_max": 150000,
    },
    "trade_balance": {
        # Merchandise deficit in USD bn (negative = deficit; less negative = better)
        # Use bullish_min / bearish_max so >= / <= comparisons work correctly for negatives
        "bullish_min": -20.0,              # deficit ≤ $20bn (value ≥ -20) = manageable
        "bearish_max": -35.0,              # deficit ≥ $35bn (value ≤ -35) = bearish
    },
    "fiscal_deficit": {
        # % of full-year target consumed
        "bullish_max": 75.0,               # < 75% of target used = on track
        "bearish_min": 90.0,               # > 90% of target used = overshooting
    },
    "upi_transactions": {
        # YoY growth %
        "bullish_min": 20.0,
        "bearish_max": 5.0,
    },
    "auto_sales": {
        # YoY growth %
        "bullish_min": 8.0,
        "bearish_max": 0.0,
    },
    "electricity_consumption": {
        # YoY growth %
        "bullish_min": 6.0,
        "bearish_max": 1.0,
    },
    "air_passenger_traffic": {
        # YoY growth %
        "bullish_min": 8.0,
        "bearish_max": 0.0,
    },
}

# ── Directory paths ────────────────────────────────────────────────────────────
from pathlib import Path
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
OUTPUT_DIR  = BASE_DIR / "output"
LOG_FILE    = BASE_DIR / "pipeline.log"

DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
