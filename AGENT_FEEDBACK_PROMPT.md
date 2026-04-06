# RBI KPI Dashboard — Agent Feedback & Development Brief
**Prepared by:** Senior Review (IT Architecture · Quantitative Finance · Data Science)
**For:** AI Agent continuing development of this codebase
**Project:** India Macroeconomic KPI Dashboard (rbi-dashboard)
**Priority principle:** Accuracy over completeness. Money is involved. Never guess.

---

## 1. Who You Are Receiving This From

This feedback comes from a multi-disciplinary review combining three perspectives:

- **IT/Software Engineering** — concerned with reliability, observability, encoding correctness, error propagation, and whether the system fails gracefully or silently
- **Quantitative Finance** — concerned with data provenance, timestamp accuracy, source attribution, stale-data risk, and the danger of presenting unverified figures as fact to financial decision-makers
- **Data Science** — concerned with pipeline reproducibility, structured output integrity, signal logic validity, and separation between raw data and interpretation

You are building a prototype that touches real financial indicators. The user will make real judgements from this dashboard. **The cost of a wrong number presented confidently is higher than the cost of an honest "data unavailable."**

---

## 2. Current State — Honest Assessment

### What is working well
- Clean architectural separation: `fetch_data.py` → `analyze.py` → `render.py` → `pipeline.py`. This is the right instinct and must be preserved.
- The KPI schema in `analyze.py` is well-structured and covers the right indicators for India macro.
- Caching with JSON files is appropriate for a prototype. Do not over-engineer this yet.
- The verbatim RBI quote feature is excellent — keep it and protect it.
- The What / Why / Outlook / Investor Note commentary structure is professionally sound.
- Using Claude with `web_search` + `web_fetch` as the live data extractor is smart — it avoids brittle scrapers.

### What is broken or missing at base level (fix these first)
1. **No logging** — `print()` is not a logging system. If this runs unattended or is extended, you are blind. Add Python `logging` module with at minimum INFO and WARNING levels.
2. **No data timestamp validation** — the dashboard can render stale cached data with no visible warning to the reader. Every KPI card must show its `as_of` date clearly, and if that date is >7 days old the UI must flag it visually.
3. **File encoding** — all file writes must use `encoding='utf-8'` explicitly. The null byte corruption that already occurred once will recur on Windows without this.
4. **No retry logic in `fetch_data.py`** — a single network timeout fails silently. Add at minimum one retry with a short wait before giving up.
5. **Signal classification is undefended** — the `bullish / bearish / neutral` signal currently comes from Claude's subjective judgement with no quantitative basis. This is a hallucination risk. See Section 4.

---

## 3. What NOT to Build Yet — Future Features

The following are legitimate future features but **must not be attempted in the base prototype** because they require capabilities, data sources, or model reasoning that cannot be reliably delivered right now without hallucination risk:

- **Automated market commentary** (e.g., "this means buy duration bonds") — requires position context, portfolio awareness, and real-time market prices. Claude cannot do this reliably from public data alone without fabricating specifics.
- **Cross-KPI correlation analysis** — valid idea, but requires sufficient historical series data before any correlation is meaningful. Do not fake trends from 3 data points.
- **Predictive / forward-looking probability statements** — e.g., "70% chance of rate cut in June." Claude is not a probability calibrator. This will hallucinate.
- **FII/DII flows, UPI volumes, GST collections** — good indicators but sourcing them reliably requires either paid data or scraping fragile government portals. Add to a backlog, not to this sprint.
- **PDF parsing of RBI documents** — viable but complex. Defer unless a PDF is directly accessible via URL and the fetch succeeds cleanly.
- **Automated scheduling / daily runs** — only after the base pipeline runs cleanly and validates its own output.

---

## 4. The Single Most Important Instruction — Anti-Hallucination

**This dashboard involves financial figures. Hallucinated or misattributed numbers cause real harm.**

Apply the following rules without exception:

### Rule 1 — Null over guess
If a KPI value cannot be found from a verified source, set `current_value` to `null`. Do not estimate, interpolate, or use "approximately." A null rendered as "N/A" on the dashboard is honest. A fabricated 6.2% presented as CPI is dangerous.

### Rule 2 — Source URL is mandatory
Every non-null KPI value must have a `source_url` that actually resolves to the page where the number was found. If you cannot provide a real URL, the value must be null. Do not invent URLs or use homepage URLs as proxies.

### Rule 3 — State your confidence explicitly
The commentary JSON must include a `data_confidence` field per KPI: `"high"` (directly read from official source), `"medium"` (inferred from a related release or secondary source), or `"low"` (could not verify, using best available proxy). Render this visibly in the UI so the reader knows what to trust.

### Rule 4 — Date precision matters
`as_of` must be the actual publication date of the data, not today's date. CPI for December is released in January — the `as_of` should be the MoSPI release date, not the date you fetched it.

### Rule 5 — Verbatim quotes only
The `rbi_verbatim` field must contain text actually present in an RBI document. If you cannot find a direct quote, leave the field empty or null. Do not paraphrase and present it as a quote.

### Rule 6 — Flag workarounds
If you used a workaround (e.g., a secondary news source because the primary source was unavailable), note it explicitly in a `data_notes` field on the KPI. The reader needs to know.

---

## 5. Signal Logic — Replace Subjectivity With Rules

The current `bullish / bearish / neutral` classification must not come from free-form Claude reasoning. It must come from explicit, deterministic thresholds so it is reproducible and auditable.

Implement a `classify_signal(kpi_key, value, prev_value)` function in a new `signals.py` file. Use rules like:

```
repo_rate:      change < 0 → bullish | change > 0 → bearish | no change → neutral
cpi_inflation:  value < 4.0 → bullish | value > 6.0 → bearish | else → neutral
gdp_growth:     value > 7.0 → bullish | value < 5.5 → bearish | else → neutral
forex_reserves: week_change > 0 → bullish | week_change < 0 → bearish
inr_usd:        value < 84 → bullish | value > 87 → bearish | else → neutral
fiscal_deficit: pct_of_target < 75% by Jan → bullish | > 90% → bearish
```

These thresholds are starting points — document them clearly so the user can adjust them. This makes the signal defensible, not arbitrary.

---

## 6. Infrastructure — Build for Scale at Prototype Stage

Even in a prototype, the following structural choices now will prevent painful rewrites later. They are not over-engineering — they are the minimum viable foundation:

### Logging
```python
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('pipeline.log'),
        logging.StreamHandler()
    ]
)
```
Replace all `print()` calls with `logger.info()`, `logger.warning()`, `logger.error()`.

### Data freshness check
Add a `check_data_freshness(kpi_data)` function that reads each KPI's `as_of` date and emits a warning if it is older than N days. Surface this in the dashboard header.

### Config file
Move hardcoded values (model name, cache TTL hours, date range, signal thresholds) to a `config.py` or `config.json`. This means model switches, threshold changes, and period adjustments happen in one place, not scattered across files.

### Explicit encoding on all writes
Every `open(..., 'w')` must be `open(..., 'w', encoding='utf-8')`. Every `open(..., 'wb')` write of text must encode explicitly. This is non-negotiable on Windows.

---

## 7. Dashboard UX — Minimum Viable Clarity

The reader of this dashboard is making financial judgements. The UI must communicate:

- **Data age** — show `as_of` date prominently on every KPI card, not buried in small text
- **Confidence level** — a small `HIGH / MEDIUM / LOW` badge per KPI (from Rule 3 above)
- **Workaround flag** — if `data_notes` is non-empty, show a ⚠ icon with tooltip
- **Null handling** — "N/A" must be visually distinct (grey, italicised) — not the same style as a real number
- **Last pipeline run timestamp** — show in the header so the reader knows when this was generated

Do not add new visual complexity (new chart types, animations, additional KPI sections) until the above reliability signals are in place. A beautiful dashboard with unreliable data is worse than an ugly one with honest data.

---

## 8. Completing the Prototype — Prioritised Task List

Complete these in order. Do not skip ahead.

**Phase 1 — Reliability foundation (do this first)**
1. Add `logging` throughout, write to `pipeline.log`
2. Fix all file writes to use `encoding='utf-8'`
3. Add `data_confidence` field to KPI schema in the extraction prompt
4. Add `data_notes` field for workaround disclosure
5. Add data freshness warning in dashboard header

**Phase 2 — Signal integrity**
6. Create `signals.py` with deterministic threshold-based classification
7. Replace Claude's free-form signal in commentary with the output of `signals.py`
8. Add `confidence` badge to KPI cards in the renderer

**Phase 3 — Robustness**
9. Add retry logic (1 retry, 5s wait) to `fetch_data.py` HTTP calls
10. Add `check_api_key()` to validate key format before any API call
11. Handle `null` KPI values gracefully in renderer (never crash on missing data)

**Phase 4 — Prototype validation run**
12. Run full pipeline with `--refresh` for current period
13. Manually verify 3 KPI values against their stated source URLs
14. Confirm dashboard renders without errors on empty/partial data

---

## 9. What Success Looks Like at Prototype Stage

A successful prototype is not one that looks impressive. It is one where:

- Every number on the dashboard has a source URL you can click and verify
- Every unavailable number shows "N/A" with a clear reason, not a fabricated value
- The pipeline runs end-to-end without crashing even if some data sources are unavailable
- A reader can look at the confidence badges and data ages and make an informed decision about how much to trust each figure
- The model can be swapped in `config.py` in 30 seconds (currently Sonnet, future Opus for specific tasks)

**Do not declare the prototype complete until these five conditions are met.**

---

## 10. A Note on Motivation and Caution

This is genuinely interesting and well-conceived work. The architecture is sound, the use of AI as a live data agent is ahead of most dashboard tools, and the KPI selection shows domain knowledge. The person building this understands both the technical and financial dimensions.

But precisely because it is sophisticated, the failure modes are sophisticated too. The danger is not that the dashboard breaks visibly — it is that it runs successfully but presents a number that is six weeks old, or slightly wrong, or sourced from a secondary report rather than the primary release, and a decision gets made on that basis.

**Treat accuracy as a hard constraint, not a quality attribute.** Speed of development, visual polish, and feature breadth are all negotiable. Accuracy of financial data is not.

When in doubt: show less, communicate more, mark uncertainty explicitly, and let the user decide. That is the professional standard.

---

*End of feedback brief. Share this document with any AI agent continuing work on this codebase.*
*Review date: April 2026*
