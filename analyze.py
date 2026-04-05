"""
RBI Analysis Pipeline -- Claude-powered KPI extraction + expert commentary.

Uses claude-sonnet-4-6 with:
  - web_search + web_fetch server-side tools to get fresh RBI data
  - adaptive thinking for deep analysis
  - streaming to avoid timeouts
  - structured JSON output for the dashboard

Extraction is split into 3 batches to stay within 40k TPM rate limits.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

MODEL = "claude-sonnet-4-6"
BATCH_PAUSE_SECONDS = 65  # pause between batches to reset TPM window

# -------------------------------------------------------------------------
# SYSTEM PROMPTS
# -------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a senior macroeconomic analyst specialising in the Reserve Bank of India (RBI) and India's economy.

Your job is to search for and extract precise KPI values with exact dates, sources, and units.

SOURCES TO USE (in priority order):
- rbi.org.in press releases, MPC resolution, Governor's statement
- mospi.gov.in for CPI/IIP/GDP
- commerce.gov.in for trade data
- cga.nic.in for fiscal data
- Reputable financial media (Bloomberg, Business Standard, Mint) only to fill gaps

Return ONLY valid JSON matching the schema in the user message.
Never hallucinate data -- if you cannot find a value, set it to null."""

COMMENTARY_SYSTEM = """You are a senior India macro analyst writing for sophisticated institutional investors.
Provide expert commentary grounded in RBI's own stated reasoning. Use exact verbatim quotes from MPC resolutions where possible.
Return ONLY valid JSON matching the schema in the user message."""

# -------------------------------------------------------------------------
# BATCH 1: Monetary Policy + Prices + Currency
# -------------------------------------------------------------------------
BATCH1_PROMPT = """Search for and extract these India KPIs for {period} (data from {start_date} to {today}):

1. RBI repo rate (latest MPC decision)
2. CPI inflation (MoSPI, monthly)
3. WPI inflation (DPIIT, monthly)
4. INR/USD exchange rate (RBI reference rate)

Search queries to use:
- "RBI repo rate MPC {period} site:rbi.org.in"
- "India CPI inflation {period} MoSPI"
- "India WPI inflation {period}"
- "RBI reference rate INR USD {period}"

Return ONLY this JSON:
{{
  "repo_rate": {{
    "current_value": <number or null>,
    "unit": "%",
    "as_of": "<date>",
    "change_from_prev": <number or null>,
    "series": [{{"date": "YYYY-MM", "value": <number>}}],
    "source_url": "<url>",
    "source_name": "RBI MPC Resolution"
  }},
  "cpi_inflation": {{
    "current_value": <number or null>,
    "unit": "% YoY",
    "as_of": "<date>",
    "change_from_prev": <number or null>,
    "series": [{{"date": "YYYY-MM", "value": <number>}}],
    "source_url": "<url>",
    "source_name": "MoSPI"
  }},
  "wpi_inflation": {{
    "current_value": <number or null>,
    "unit": "% YoY",
    "as_of": "<date>",
    "change_from_prev": <number or null>,
    "series": [{{"date": "YYYY-MM", "value": <number>}}],
    "source_url": "<url>",
    "source_name": "DPIIT"
  }},
  "inr_usd": {{
    "current_value": <number or null>,
    "unit": "INR per USD",
    "as_of": "<date>",
    "month_high": <number or null>,
    "month_low": <number or null>,
    "series": [{{"date": "YYYY-MM", "value": <number>}}],
    "source_url": "<url>",
    "source_name": "RBI Reference Rate"
  }}
}}"""

# -------------------------------------------------------------------------
# BATCH 2: Real Economy
# -------------------------------------------------------------------------
BATCH2_PROMPT = """Search for and extract these India KPIs for {period} (data from {start_date} to {today}):

1. GDP growth rate (MoSPI quarterly estimates)
2. IIP industrial production (MoSPI monthly)
3. Bank credit growth (RBI, non-food credit)
4. M3 money supply growth (RBI)

Search queries to use:
- "India GDP growth Q3 FY26 MoSPI advance estimate"
- "India IIP industrial production {period} MoSPI"
- "India bank credit growth {period} RBI"
- "India M3 money supply {period} RBI"

Return ONLY this JSON:
{{
  "gdp_growth": {{
    "current_value": <number or null>,
    "unit": "% YoY",
    "as_of": "<quarter e.g. Q3 FY26>",
    "fy_projection": <number or null>,
    "series": [{{"period": "<quarter>", "value": <number>}}],
    "source_url": "<url>",
    "source_name": "MoSPI"
  }},
  "iip": {{
    "current_value": <number or null>,
    "unit": "% YoY",
    "as_of": "<month>",
    "sectors": {{
      "mining": <number or null>,
      "manufacturing": <number or null>,
      "electricity": <number or null>
    }},
    "use_based": {{
      "capital_goods": <number or null>,
      "infrastructure": <number or null>,
      "consumer_durables": <number or null>,
      "consumer_nondurables": <number or null>
    }},
    "source_url": "<url>",
    "source_name": "MoSPI IIP"
  }},
  "bank_credit_growth": {{
    "current_value": <number or null>,
    "unit": "% YoY",
    "as_of": "<date>",
    "segments": {{
      "retail": <number or null>,
      "industry": <number or null>,
      "agriculture": <number or null>,
      "services": <number or null>
    }},
    "source_url": "<url>",
    "source_name": "RBI Sectoral Deployment of Credit"
  }},
  "m3_growth": {{
    "current_value": <number or null>,
    "unit": "% YoY",
    "as_of": "<date>",
    "absolute_value_trn_inr": <number or null>,
    "source_url": "<url>",
    "source_name": "RBI Money Supply"
  }}
}}"""

# -------------------------------------------------------------------------
# BATCH 3: External + Fiscal
# -------------------------------------------------------------------------
BATCH3_PROMPT = """Search for and extract these India KPIs for {period} (data from {start_date} to {today}):

1. Foreign exchange reserves (RBI weekly)
2. Trade balance and current account deficit
3. Fiscal deficit (CGA monthly accounts)

Search queries to use:
- "India forex reserves {period} RBI weekly"
- "India trade deficit {period} merchandise commerce ministry"
- "India current account deficit Q3 FY26 RBI"
- "India fiscal deficit CGA {period} monthly accounts"

Return ONLY this JSON:
{{
  "forex_reserves": {{
    "current_value": <number or null>,
    "unit": "USD Billion",
    "as_of": "<date>",
    "change_from_prev_week": <number or null>,
    "series": [{{"date": "YYYY-MM-DD", "value": <number>}}],
    "source_url": "<url>",
    "source_name": "RBI Weekly Statistical Supplement"
  }},
  "trade_balance": {{
    "merchandise_deficit": <number or null>,
    "overall_deficit": <number or null>,
    "unit": "USD Billion",
    "as_of": "<month>",
    "exports_goods": <number or null>,
    "imports_goods": <number or null>,
    "services_surplus": <number or null>,
    "cad_latest_quarter": <number or null>,
    "cad_pct_gdp": <number or null>,
    "source_url": "<url>",
    "source_name": "Ministry of Commerce / RBI BOP"
  }},
  "fiscal_deficit": {{
    "cumulative_value_lakh_cr": <number or null>,
    "pct_of_target": <number or null>,
    "full_year_target_lakh_cr": <number or null>,
    "full_year_target_pct_gdp": <number or null>,
    "period_covered": "<e.g. Apr-Jan FY26>",
    "source_url": "<url>",
    "source_name": "CGA Monthly Accounts"
  }}
}}"""

# -------------------------------------------------------------------------
# COMMENTARY PROMPT
# -------------------------------------------------------------------------
COMMENTARY_PROMPT = """You are a senior India macro analyst. Here is extracted KPI data for {period}:

{kpi_json}

Write expert commentary. For each KPI provide: what the number means, why it moved (use RBI's own language where possible), and forward outlook.
Also write a macro narrative connecting all KPIs.

Return ONLY this JSON:
{{
  "generated_at": "<ISO timestamp>",
  "macro_narrative": {{
    "headline": "<one-line summary>",
    "body": "<3-4 paragraphs>",
    "investor_takeaway": "<2-3 sentence conclusion>",
    "key_risks": ["<risk 1>", "<risk 2>", "<risk 3>"]
  }},
  "kpi_commentary": {{
    "repo_rate":         {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "rbi_verbatim": "", "outlook": "", "investor_note": ""}},
    "cpi_inflation":     {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "rbi_verbatim": "", "outlook": "", "investor_note": ""}},
    "wpi_inflation":     {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "outlook": "", "investor_note": ""}},
    "gdp_growth":        {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "rbi_verbatim": "", "outlook": "", "investor_note": ""}},
    "forex_reserves":    {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "outlook": "", "investor_note": ""}},
    "bank_credit_growth":{{"signal": "bullish|bearish|neutral", "what": "", "why": "", "rbi_verbatim": "", "outlook": "", "investor_note": ""}},
    "m3_growth":         {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "outlook": "", "investor_note": ""}},
    "trade_balance":     {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "outlook": "", "investor_note": ""}},
    "inr_usd":           {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "rbi_verbatim": "", "outlook": "", "investor_note": ""}},
    "iip":               {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "outlook": "", "investor_note": ""}},
    "fiscal_deficit":    {{"signal": "bullish|bearish|neutral", "what": "", "why": "", "outlook": "", "investor_note": ""}}
  }}
}}"""

BATCHES = [
    ("Monetary/Prices/Currency", BATCH1_PROMPT),
    ("Real Economy",             BATCH2_PROMPT),
    ("External/Fiscal",          BATCH3_PROMPT),
]


# -------------------------------------------------------------------------
# CORE STREAMING HELPER
# -------------------------------------------------------------------------
def _stream_and_collect(client, max_retries: int = 2, **kwargs) -> str:
    """Stream and return all text, with rate-limit retry."""
    for attempt in range(max_retries + 1):
        try:
            full_text = ""
            with client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            full_text += event.delta.text
                            print(".", end="", flush=True)
                    elif event.type == "content_block_start":
                        cb = event.content_block
                        if hasattr(cb, "type") and cb.type == "tool_use":
                            print(f"\n  [{getattr(cb, 'name', 'tool')}]", end="", flush=True)
                print()
                final_msg = stream.get_final_message()

            if not full_text.strip():
                for block in final_msg.content:
                    if hasattr(block, "text") and block.text:
                        full_text += block.text

            return full_text

        except anthropic.RateLimitError:
            if attempt < max_retries:
                wait = 65
                print(f"\n  [Rate limit] Waiting {wait}s before retry {attempt+1}/{max_retries}...")
                time.sleep(wait)
            else:
                raise

    return ""


# -------------------------------------------------------------------------
# EXTRACTION (3 batches)
# -------------------------------------------------------------------------
def run_extraction(period: str = "January 2026", start_date: str = "2025-01-01") -> dict:
    """Extract KPIs in 3 batches with 65s pause between each."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"\n[1/3] Extracting KPI data for {period} in 3 batches...")
    print(f"      Model: {MODEL} | Pause between batches: {BATCH_PAUSE_SECONDS}s")

    merged_kpis = {}

    for i, (batch_name, batch_template) in enumerate(BATCHES):
        print(f"\n  Batch {i+1}/3: {batch_name}")
        prompt = batch_template.format(period=period, start_date=start_date, today=today)

        full_text = _stream_and_collect(
            client,
            model=MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=[
                {"type": "web_search_20260209", "name": "web_search"},
                {"type": "web_fetch_20260209",  "name": "web_fetch"},
            ],
            messages=[{"role": "user", "content": prompt}],
        )

        batch_result = _parse_json_response(full_text)
        for key, val in batch_result.items():
            if not key.startswith("_"):
                merged_kpis[key] = val

        if i < len(BATCHES) - 1:
            print(f"  Batch {i+1} done. Pausing {BATCH_PAUSE_SECONDS}s before next batch...")
            time.sleep(BATCH_PAUSE_SECONDS)

    extraction = {
        "extracted_at": datetime.now().isoformat(),
        "period_label": period,
        "data_vintage": today,
        "kpis": merged_kpis,
    }

    out_path = DATA_DIR / f"kpi_data_{period.replace(' ', '_').lower()}.json"
    out_path.write_text(json.dumps(extraction, indent=2), encoding="utf-8")
    print(f"\n  Saved extraction: {out_path}")
    return extraction


# -------------------------------------------------------------------------
# COMMENTARY (single pass, no web tools needed)
# -------------------------------------------------------------------------
def _trim_kpi_for_commentary(kpi_data: dict) -> dict:
    import copy
    trimmed = copy.deepcopy(kpi_data)
    for key in ("_raw_response", "_parse_error"):
        trimmed.pop(key, None)
    for kpi_val in trimmed.get("kpis", {}).values():
        if isinstance(kpi_val, dict):
            if isinstance(kpi_val.get("series"), list) and len(kpi_val["series"]) > 12:
                kpi_val["series"] = kpi_val["series"][-12:]
    return trimmed


def run_commentary(kpi_data: dict, period: str = "January 2026") -> dict:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    kpi_json = json.dumps(_trim_kpi_for_commentary(kpi_data), indent=2)
    prompt = COMMENTARY_PROMPT.format(kpi_json=kpi_json, period=period)

    print(f"\n[2/3] Generating expert commentary...")

    full_text = _stream_and_collect(
        client,
        model=MODEL,
        max_tokens=12000,
        thinking={"type": "adaptive"},
        system=COMMENTARY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    commentary = _parse_json_response(full_text)

    out_path = DATA_DIR / f"commentary_{period.replace(' ', '_').lower()}.json"
    out_path.write_text(json.dumps(commentary, indent=2), encoding="utf-8")
    print(f"  Saved commentary: {out_path}")
    return commentary


# -------------------------------------------------------------------------
# JSON PARSER
# -------------------------------------------------------------------------
def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        inner = "\n".join(lines[1:])
        if inner.rstrip().endswith("```"):
            inner = inner.rstrip()[:-3].rstrip()
        text = inner
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [WARN] JSON parse error: {e}")
        print(f"  [WARN] Raw (first 500): {text[:500]}")
        return {
            "_parse_error": str(e),
            "kpis": {},
            "kpi_commentary": {},
            "macro_narrative": {
                "headline": "Parsing error -- retry pipeline",
                "body": "Analysis pipeline encountered a parsing issue.",
                "investor_takeaway": "Please retry.",
                "key_risks": [],
            },
        }


def load_cached_analysis(period: str) -> tuple:
    slug = period.replace(" ", "_").lower()
    kpi_path = DATA_DIR / f"kpi_data_{slug}.json"
    com_path = DATA_DIR / f"commentary_{slug}.json"
    kpi_data = json.loads(kpi_path.read_text(encoding="utf-8")) if kpi_path.exists() else None
    commentary = json.loads(com_path.read_text(encoding="utf-8")) if com_path.exists() else None
    return kpi_data, commentary


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    period = sys.argv[1] if len(sys.argv) > 1 else "January 2026"
    kpi_data = run_extraction(period=period, start_date="2025-01-01")
    commentary = run_commentary(kpi_data=kpi_data, period=period)
    print("\nDone.")
    print(f"Headline: {commentary.get('macro_narrative', {}).get('headline', 'N/A')}")
