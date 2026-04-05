"""
Deterministic signal classification for RBI KPI Dashboard.

Rules are threshold-based — same input always produces same output.
No AI reasoning involved. Thresholds live in config.py.

Usage:
    from signals import classify_signal
    signal = classify_signal("cpi_inflation", value=4.5, prev_value=5.1)
    # Returns: "neutral"
"""

import logging
from config import SIGNAL_THRESHOLDS

logger = logging.getLogger(__name__)


def classify_signal(kpi_id: str, value=None, prev_value=None,
                    week_change=None, pct_of_target=None) -> str:
    """
    Returns "bullish", "bearish", or "neutral".
    Returns "neutral" if value is None or kpi_id not in thresholds.
    Never raises — always returns a string.
    """
    if value is None:
        logger.warning("classify_signal: null value for %s — defaulting to neutral", kpi_id)
        return "neutral"

    thresholds = SIGNAL_THRESHOLDS.get(kpi_id)
    if not thresholds:
        logger.warning("classify_signal: no thresholds defined for %s — defaulting to neutral", kpi_id)
        return "neutral"

    try:
        # ── Directional KPIs (change-based) ────────────────────────────────
        if kpi_id == "repo_rate":
            if prev_value is None:
                return "neutral"
            change = value - prev_value
            if change < 0:
                return "bullish"
            if change > 0:
                return "bearish"
            return "neutral"

        if kpi_id == "forex_reserves":
            if week_change is None:
                return "neutral"
            if week_change > 0:
                return "bullish"
            if week_change < 0:
                return "bearish"
            return "neutral"

        if kpi_id == "fiscal_deficit":
            v = pct_of_target if pct_of_target is not None else value
            bmax = thresholds.get("bullish_max")
            bmin = thresholds.get("bearish_min")
            if bmax is not None and v <= bmax:
                return "bullish"
            if bmin is not None and v >= bmin:
                return "bearish"
            return "neutral"

        # ── Absolute value KPIs ─────────────────────────────────────────────
        bullish_min = thresholds.get("bullish_min")
        bullish_max = thresholds.get("bullish_max")
        bearish_min = thresholds.get("bearish_min")
        bearish_max = thresholds.get("bearish_max")

        if bullish_min is not None and value >= bullish_min:
            return "bullish"
        if bullish_max is not None and value <= bullish_max:
            return "bullish"
        if bearish_min is not None and value >= bearish_min:
            return "bearish"
        if bearish_max is not None and value <= bearish_max:
            return "bearish"
        return "neutral"

    except Exception as exc:
        logger.error("classify_signal error for %s: %s", kpi_id, exc)
        return "neutral"


def classify_all(kpis: dict) -> dict:
    """
    Run classify_signal over every KPI in the kpis dict.
    Returns {kpi_id: signal_string}.
    """
    results = {}
    for kpi_id, kpi in kpis.items():
        if not isinstance(kpi, dict):
            results[kpi_id] = "neutral"
            continue
        results[kpi_id] = classify_signal(
            kpi_id=kpi_id,
            value=kpi.get("current_value"),
            prev_value=kpi.get("prev_value"),
            week_change=kpi.get("change_from_prev_week"),
            pct_of_target=kpi.get("pct_of_target"),
        )
    return results


if __name__ == "__main__":
    # Quick smoke test
    import json
    tests = [
        ("repo_rate",          {"current_value": 5.75, "prev_value": 6.0}),
        ("cpi_inflation",      {"current_value": 3.8,  "prev_value": 4.1}),
        ("cpi_inflation",      {"current_value": 6.5,  "prev_value": 5.9}),
        ("gdp_growth",         {"current_value": 7.2,  "prev_value": 6.8}),
        ("inr_usd",            {"current_value": 88.5, "prev_value": 87.0}),
        ("forex_reserves",     {"current_value": 635,  "change_from_prev_week": -1.2}),
        ("fiscal_deficit",     {"pct_of_target": 52.5}),
        ("upi_transactions",   {"current_value": 22.6, "prev_value": 20.1}),
    ]
    print("\nSignal classification smoke test:")
    print("-" * 40)
    for kpi_id, data in tests:
        sig = classify_signal(
            kpi_id=kpi_id,
            value=data.get("current_value") or data.get("pct_of_target"),
            prev_value=data.get("prev_value"),
            week_change=data.get("change_from_prev_week"),
            pct_of_target=data.get("pct_of_target"),
        )
        print(f"  {kpi_id:<30} → {sig}")
    print()
