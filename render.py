"""
Dashboard Renderer — generic, data-driven, zero hardcoded KPI names.

Adding a new KPI requires:
  1. Add entry to kpis_config.json
  2. Drop data/{kpi_id}.json
  → Appears on dashboard automatically. No changes here.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import STALENESS_WARNING_DAYS, STALENESS_ERROR_DAYS
from signals import classify_signal

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe(data, *keys, default=None):
    cur = data
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


def _fmt_val(v, decimals=2, prefix="", suffix="", null_str="N/A"):
    if v is None:
        return null_str
    try:
        return f"{prefix}{float(v):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def _signal_css(signal: str) -> str:
    return {"bullish": "signal-bullish", "bearish": "signal-bearish",
            "neutral": "signal-neutral"}.get(signal, "signal-neutral")


def _confidence_css(confidence: str) -> str:
    return {"high": "conf-high", "medium": "conf-medium",
            "low": "conf-low"}.get((confidence or "").lower(), "conf-medium")


def _staleness_css(release_date_str: str) -> str:
    if not release_date_str:
        return ""
    try:
        release = datetime.strptime(release_date_str[:10], "%Y-%m-%d")
        age = (datetime.now() - release).days
        if age > STALENESS_ERROR_DAYS:
            return "stale-error"
        if age > STALENESS_WARNING_DAYS:
            return "stale-warn"
        return ""
    except ValueError:
        return ""


def _series_to_js(series: list, value_key: str = "value",
                  date_key: str = "date") -> tuple:
    if not series:
        return "[]", "[]"
    pts = [(d.get(date_key, ""), d.get(value_key)) for d in series
           if d.get(value_key) is not None]
    labels = json.dumps([p[0] for p in pts])
    values = json.dumps([p[1] for p in pts])
    return labels, values


def _events_for_kpi(kpi_id: str, events: list) -> list:
    return [e for e in events if kpi_id in e.get("impact_kpis", [])]


def _event_annotation_js(events_for_kpi: list, labels_js: str) -> str:
    """Generates Chart.js annotation plugin config for event markers."""
    if not events_for_kpi:
        return "null"
    anns = []
    for ev in events_for_kpi:
        color = {"negative": "#ef4444", "positive": "#22c55e",
                 "neutral": "#94a3b8"}.get(ev.get("impact_direction", "neutral"), "#94a3b8")
        label = ev.get("label", "")[:20]
        date_label = ev.get("date", "")
        anns.append(f"""{{
          type: 'line',
          xMin: '{date_label}',
          xMax: '{date_label}',
          borderColor: '{color}',
          borderWidth: 1.5,
          borderDash: [4, 3],
          label: {{
            content: '{label}',
            enabled: true,
            position: 'start',
            backgroundColor: '{color}22',
            color: '{color}',
            font: {{ size: 9 }}
          }}
        }}""")
    return "{ annotations: [" + ",\n".join(anns) + "] }"


# ── KPI Panel ─────────────────────────────────────────────────────────────────

def _render_kpi_panel(kpi_cfg: dict, kpi_data: dict | None,
                      kpi_events: list, commentary_kpi: dict) -> str:
    kpi_id   = kpi_cfg["id"]
    name     = kpi_cfg["name"]
    unit     = kpi_cfg.get("unit", "")
    category = kpi_cfg.get("category", "")
    source_url  = kpi_cfg.get("source_base_url", "#")
    source_auth = kpi_cfg.get("source_authority", "")
    chart_type  = kpi_cfg.get("chart_type", "line")

    # Data values
    current  = _safe(kpi_data, "current_value")
    prev     = _safe(kpi_data, "prev_value")
    change   = _safe(kpi_data, "change_from_prev")
    direction = _safe(kpi_data, "direction", default="")
    as_of    = _safe(kpi_data, "as_of", default="")
    release_date = _safe(kpi_data, "as_of_release_date", default="")
    is_prov  = _safe(kpi_data, "is_provisional", default=False)
    confidence = _safe(kpi_data, "data_confidence", default="medium")
    data_notes = _safe(kpi_data, "data_notes")
    week_change = _safe(kpi_data, "change_from_prev_week")
    pct_of_target = _safe(kpi_data, "pct_of_target")
    series   = _safe(kpi_data, "series", default=[])
    data_source_url = _safe(kpi_data, "source_url", default=source_url)

    # Signal — deterministic
    signal = classify_signal(
        kpi_id=kpi_id,
        value=current if current is not None else pct_of_target,
        prev_value=prev,
        week_change=week_change,
        pct_of_target=pct_of_target,
    )

    # Display values
    if current is None and pct_of_target is not None:
        display_val = _fmt_val(pct_of_target, decimals=1, suffix="%")
    else:
        display_val = _fmt_val(current, decimals=2, suffix=f" {unit}".rstrip())

    is_na = (current is None and pct_of_target is None)
    val_class = "kpi-value-na" if is_na else "kpi-value"

    change_html = ""
    if change is not None:
        arrow = "▲" if direction == "up" else ("▼" if direction == "down" else "")
        chg_class = "chg-up" if direction == "up" else ("chg-down" if direction == "down" else "chg-flat")
        change_html = f'<span class="kpi-change {chg_class}">{arrow} {_fmt_val(abs(change), decimals=2)}</span>'

    stale_css = _staleness_css(release_date)
    stale_badge = ""
    if stale_css:
        stale_badge = '<span class="stale-badge">⏰ Stale</span>'

    prov_badge = '<span class="prov-badge">Provisional</span>' if is_prov else ""
    warn_badge = f'<span class="warn-badge" title="{data_notes}">⚠</span>' if data_notes else ""

    conf_label = (confidence or "medium").upper()
    conf_css   = _confidence_css(confidence)

    # Chart
    labels_js, values_js = _series_to_js(series)
    chart_color = {"bullish": "#22c55e", "bearish": "#ef4444",
                   "neutral": "#60a5fa"}.get(signal, "#60a5fa")
    annotations_js = _event_annotation_js(kpi_events, labels_js)

    chart_js = f"""
    (function() {{
      var ctx = document.getElementById('chart_{kpi_id}');
      if (!ctx) return;
      new Chart(ctx, {{
        type: '{chart_type}',
        data: {{
          labels: {labels_js},
          datasets: [{{
            label: '{name}',
            data: {values_js},
            borderColor: '{chart_color}',
            backgroundColor: '{chart_color}22',
            borderWidth: 2,
            pointRadius: 3,
            fill: {'true' if chart_type == 'line' else 'false'},
            tension: 0.3
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }},
            annotation: {annotations_js}
          }},
          scales: {{
            x: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }},
                   grid: {{ color: '#1e293b' }} }},
            y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }},
                   grid: {{ color: '#1e293b' }} }}
          }}
        }}
      }});
    }})();"""

    # Commentary — support both old keys (why/outlook) and new (why_it_moved/forward_outlook)
    why    = (_safe(commentary_kpi, "why_it_moved")
              or (_safe(commentary_kpi, "what", default="") + " " + _safe(commentary_kpi, "why", default="")).strip()
              or "")
    outlook = _safe(commentary_kpi, "forward_outlook") or _safe(commentary_kpi, "outlook", default="")
    inv_note = _safe(commentary_kpi, "investor_note", default="")

    why_html    = f'<p class="com-text">{why}</p>'     if why    else '<p class="com-na">Commentary not yet available.</p>'
    out_html    = f'<p class="com-text">{outlook}</p>' if outlook else ""
    inv_html    = f'<div class="inv-note"><strong>Investor note:</strong> {inv_note}</div>' if inv_note else ""

    return f"""
    <div class="kpi-card" id="kpi_{kpi_id}" data-category="{category}">
      <div class="kpi-header">
        <div class="kpi-title-row">
          <h3 class="kpi-name">{name}</h3>
          <span class="signal-badge {_signal_css(signal)}">{signal.upper()}</span>
        </div>
        <div class="kpi-meta-row">
          <span class="conf-badge {conf_css}">{conf_label}</span>
          {stale_badge}{prov_badge}{warn_badge}
          <span class="kpi-asof {stale_css}">as of {as_of}</span>
          <a href="{data_source_url}" target="_blank" class="source-link">{source_auth} ↗</a>
        </div>
      </div>
      <div class="kpi-value-row">
        <span class="{val_class}">{display_val}</span>
        {change_html}
      </div>
      <div class="kpi-chart-wrap">
        <canvas id="chart_{kpi_id}" height="140"></canvas>
      </div>
      <div class="kpi-commentary">
        <div class="com-section">
          <span class="com-label">Why it moved</span>
          {why_html}
        </div>
        {'<div class="com-section"><span class="com-label">Outlook</span>' + out_html + '</div>' if out_html else ''}
        {inv_html}
      </div>
    </div>
    <script>{chart_js}</script>"""


# ── Events Timeline ───────────────────────────────────────────────────────────

def _render_events_timeline(events: list) -> str:
    if not events:
        return ""
    cards = []
    for ev in sorted(events, key=lambda e: e.get("date", "")):
        direction = ev.get("impact_direction", "neutral")
        color_class = {"negative": "ev-neg", "positive": "ev-pos",
                       "neutral": "ev-neu"}.get(direction, "ev-neu")
        mag = ev.get("magnitude", "")
        mag_html = f'<span class="ev-mag">{mag.upper()}</span>' if mag else ""
        kpis_affected = ", ".join(ev.get("impact_kpis", []))
        src_url = ev.get("source_url", "")
        src_html = f'<a href="{src_url}" target="_blank" class="ev-src">source ↗</a>' if src_url else ""
        cards.append(f"""
        <div class="ev-card {color_class}">
          <div class="ev-date">{ev.get('date','')}</div>
          <div class="ev-label">{ev.get('label','')} {mag_html}</div>
          <div class="ev-desc">{ev.get('description','')}</div>
          <div class="ev-kpis">Affects: {kpis_affected} {src_html}</div>
        </div>""")
    return f"""
    <section class="events-section">
      <h2 class="section-title">Global Macro Events</h2>
      <div class="events-scroll">{''.join(cards)}</div>
    </section>"""


# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: #0f172a; color: #e2e8f0; font-size: 14px; line-height: 1.5;
}
a { color: #60a5fa; text-decoration: none; }
a:hover { text-decoration: underline; }

/* Layout */
.container { max-width: 1400px; margin: 0 auto; padding: 24px 16px; }

/* Header */
.dash-header { margin-bottom: 32px; }
.dash-title { font-size: 1.6rem; font-weight: 700; color: #f1f5f9; }
.dash-subtitle { color: #94a3b8; font-size: 0.85rem; margin-top: 4px; }
.dash-meta { display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap; }
.meta-item { font-size: 0.78rem; color: #64748b; }

/* Macro narrative */
.macro-card {
  background: #1e293b; border-radius: 12px; padding: 20px 24px;
  margin-bottom: 32px; border-left: 4px solid #3b82f6;
}
.macro-headline { font-size: 1.05rem; font-weight: 600; color: #f1f5f9; margin-bottom: 8px; }
.macro-summary { color: #94a3b8; font-size: 0.88rem; line-height: 1.6; }
.macro-quote {
  margin-top: 12px; padding: 12px 16px;
  background: #0f172a; border-radius: 8px; border-left: 3px solid #64748b;
  font-style: italic; color: #94a3b8; font-size: 0.83rem;
}

/* Category group */
.category-group { margin-bottom: 40px; }
.category-title {
  font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.1em; color: #64748b; margin-bottom: 16px;
  padding-bottom: 6px; border-bottom: 1px solid #1e293b;
}

/* KPI Grid */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

/* KPI Card */
.kpi-card {
  background: #1e293b; border-radius: 12px; padding: 18px;
  border: 1px solid #334155; transition: border-color 0.2s;
}
.kpi-card:hover { border-color: #475569; }
.kpi-header { margin-bottom: 10px; }
.kpi-title-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.kpi-name { font-size: 0.88rem; font-weight: 600; color: #cbd5e1; }
.kpi-meta-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.kpi-asof { font-size: 0.72rem; color: #64748b; }
.source-link { font-size: 0.72rem; color: #475569; margin-left: auto; }

/* Value */
.kpi-value-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 12px; }
.kpi-value { font-size: 1.8rem; font-weight: 700; color: #f1f5f9; }
.kpi-value-na { font-size: 1.8rem; font-weight: 700; color: #475569; font-style: italic; }
.kpi-change { font-size: 0.8rem; padding: 2px 6px; border-radius: 4px; }
.chg-up   { color: #22c55e; background: #14532d22; }
.chg-down { color: #ef4444; background: #7f1d1d22; }
.chg-flat { color: #94a3b8; }

/* Badges */
.signal-badge {
  font-size: 0.65rem; font-weight: 700; padding: 2px 7px;
  border-radius: 4px; text-transform: uppercase; letter-spacing: 0.05em;
}
.signal-bullish { background: #14532d; color: #22c55e; }
.signal-bearish { background: #7f1d1d; color: #f87171; }
.signal-neutral { background: #1e3a5f; color: #60a5fa; }

.conf-badge {
  font-size: 0.62rem; font-weight: 600; padding: 1px 5px;
  border-radius: 3px; text-transform: uppercase;
}
.conf-high   { background: #14532d44; color: #4ade80; border: 1px solid #14532d; }
.conf-medium { background: #78350f44; color: #fbbf24; border: 1px solid #78350f; }
.conf-low    { background: #7f1d1d44; color: #f87171; border: 1px solid #7f1d1d; }

.stale-badge { font-size: 0.62rem; color: #f59e0b; }
.stale-warn  { color: #f59e0b; }
.stale-error { color: #ef4444; }
.prov-badge  { font-size: 0.62rem; color: #94a3b8; background: #1e293b; border: 1px solid #334155; padding: 1px 5px; border-radius: 3px; }
.warn-badge  { font-size: 0.75rem; color: #f59e0b; cursor: help; }

/* Chart */
.kpi-chart-wrap { height: 140px; margin-bottom: 14px; }

/* Commentary */
.kpi-commentary { border-top: 1px solid #1e293b; padding-top: 12px; }
.com-section { margin-bottom: 10px; }
.com-label { font-size: 0.68rem; font-weight: 600; text-transform: uppercase;
             letter-spacing: 0.08em; color: #475569; display: block; margin-bottom: 3px; }
.com-text  { font-size: 0.8rem; color: #94a3b8; line-height: 1.55; }
.com-na    { font-size: 0.78rem; color: #334155; font-style: italic; }
.inv-note  {
  font-size: 0.78rem; color: #93c5fd; background: #1e3a5f22;
  border-left: 2px solid #3b82f6; padding: 8px 10px; border-radius: 4px; margin-top: 8px;
}

/* Events timeline */
.events-section { margin-top: 40px; }
.section-title {
  font-size: 0.75rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.1em; color: #64748b; margin-bottom: 16px;
  padding-bottom: 6px; border-bottom: 1px solid #1e293b;
}
.events-scroll {
  display: flex; gap: 12px; overflow-x: auto; padding-bottom: 12px;
  scrollbar-width: thin; scrollbar-color: #334155 transparent;
}
.ev-card {
  min-width: 220px; max-width: 240px; flex-shrink: 0;
  background: #1e293b; border-radius: 8px; padding: 12px;
  border-top: 3px solid #334155;
}
.ev-neg { border-top-color: #ef4444; }
.ev-pos { border-top-color: #22c55e; }
.ev-neu { border-top-color: #94a3b8; }
.ev-date  { font-size: 0.68rem; color: #64748b; margin-bottom: 4px; }
.ev-label { font-size: 0.8rem; font-weight: 600; color: #e2e8f0; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
.ev-mag   { font-size: 0.6rem; background: #334155; color: #94a3b8; padding: 1px 4px; border-radius: 3px; }
.ev-desc  { font-size: 0.75rem; color: #94a3b8; line-height: 1.4; margin-bottom: 6px; }
.ev-kpis  { font-size: 0.68rem; color: #475569; }
.ev-src   { color: #475569; margin-left: 4px; }

/* Footer */
.dash-footer {
  margin-top: 48px; padding-top: 20px; border-top: 1px solid #1e293b;
  color: #334155; font-size: 0.75rem;
}
"""


# ── Main render function ───────────────────────────────────────────────────────

def render_dashboard(
    kpi_data: dict,
    commentary: dict,
    events: list = None,
    kpis_config: list = None,
    output_path: str = None,
    stale_kpis: list = None,
) -> str:
    """
    Render the full HTML dashboard. Returns HTML string.
    Writes to output_path if provided.
    """
    events = events or []
    stale_kpis = stale_kpis or []

    # Load kpis_config if not passed
    if not kpis_config:
        cfg_path = Path(__file__).parent / "kpis_config.json"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                kpis_config = json.load(f).get("kpis", [])
        else:
            kpis_config = []

    kpis = kpi_data.get("kpis", {})
    kpi_com = commentary.get("kpi_commentary", {})
    macro = commentary.get("macro_narrative", {})
    # Support both inline headline (old) and top-level headline block (new)
    headline_block = commentary.get("headline", {})
    period_label = kpi_data.get("period_label", "Latest")
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")

    # ── Macro narrative card ──
    headline  = macro.get("headline") or headline_block.get("title", "")
    summary   = macro.get("summary") or headline_block.get("summary", "")
    quote     = macro.get("rbi_verbatim_quote", "")
    risk_tone = macro.get("risk_tone") or headline_block.get("risk_tone_label", "")
    risk_badge_color = {"cautiously optimistic": "#22c55e",
                        "cautious": "#f59e0b", "bearish": "#ef4444"}.get(risk_tone, "#60a5fa")

    macro_html = f"""
    <div class="macro-card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
        <h2 class="macro-headline">{headline or 'Macro Narrative'}</h2>
        {'<span style="font-size:0.72rem;padding:3px 8px;border-radius:4px;background:#1e293b;color:' + risk_badge_color + ';border:1px solid ' + risk_badge_color + '44;">' + risk_tone.upper() + '</span>' if risk_tone else ''}
      </div>
      {('<p class="macro-summary">' + summary + '</p>') if summary else ''}
      {('<div class="macro-quote">"' + quote + '"</div>') if quote else ''}
    </div>"""

    # ── KPI panels grouped by category ──
    category_order = [
        ("monetary_policy", "Monetary Policy"),
        ("prices",          "Prices"),
        ("growth",          "Growth"),
        ("external",        "External Sector"),
        ("fiscal",          "Fiscal"),
        ("financial",       "Financial"),
        ("digital_economy", "Digital Economy"),
        ("real_economy",    "Real Economy"),
    ]

    panels_by_cat: dict[str, list] = {cat: [] for cat, _ in category_order}

    for kpi_cfg in kpis_config:
        kpi_id   = kpi_cfg["id"]
        cat      = kpi_cfg.get("category", "other")
        kpi_d    = kpis.get(kpi_id)
        kpi_ev   = _events_for_kpi(kpi_id, events)
        com_kpi  = kpi_com.get(kpi_id, {})
        panel_html = _render_kpi_panel(kpi_cfg, kpi_d, kpi_ev, com_kpi)
        panels_by_cat.setdefault(cat, []).append(panel_html)

    sections_html = ""
    for cat_id, cat_label in category_order:
        panels = panels_by_cat.get(cat_id, [])
        if panels:
            sections_html += f"""
            <div class="category-group">
              <h2 class="category-title">{cat_label}</h2>
              <div class="kpi-grid">{''.join(panels)}</div>
            </div>"""

    # ── Events timeline ──
    events_html = _render_events_timeline(events)

    # ── Stale warning banner ──
    stale_banner = ""
    if stale_kpis:
        stale_banner = f"""
        <div style="background:#78350f22;border:1px solid #78350f;border-radius:8px;
                    padding:10px 16px;margin-bottom:20px;font-size:0.82rem;color:#fbbf24;">
          ⚠ Stale data detected for: {', '.join(stale_kpis)}. Values may not reflect latest releases.
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>India Macro Dashboard — {period_label}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
  <style>{CSS}</style>
</head>
<body>
<div class="container">

  <header class="dash-header">
    <div class="dash-title">India Macro Dashboard</div>
    <div class="dash-subtitle">{period_label}</div>
    <div class="dash-meta">
      <span class="meta-item">Generated: {generated_at}</span>
      <span class="meta-item">KPIs: {len(kpis_config)}</span>
      <span class="meta-item">Events: {len(events)}</span>
      <span class="meta-item">Data: primary official sources only</span>
    </div>
  </header>

  {stale_banner}
  {macro_html}
  {sections_html}
  {events_html}

  <footer class="dash-footer">
    <p>All data sourced from primary official releases (RBI, MoSPI, NPCI, SIAM, CEA, DGCA, CGA, Ministry of Commerce).</p>
    <p>Signals are rule-based (deterministic thresholds). Commentary is analytical, not investment advice.</p>
    <p>Generated {generated_at} | Confidence: HIGH = directly from official source | MEDIUM = secondary official | LOW = proxy/estimated</p>
  </footer>

</div>
</body>
</html>"""

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html, encoding="utf-8")
        logger.info("Dashboard saved: %s", output_path)

    return html
