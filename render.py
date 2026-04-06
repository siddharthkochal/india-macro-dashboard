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


def _fmt_val(v, decimals=None, prefix="", suffix="", null_str="N/A"):
    if v is None:
        return null_str
    try:
        fv = float(v)
        if decimals is None:
            # Smart decimals: large integers → 0, small % → 2, tiny → 3
            if abs(fv) >= 10000:
                decimals = 0
            elif fv == int(fv) and abs(fv) >= 100:
                decimals = 0
            elif abs(fv) < 0.1 and fv != 0:
                decimals = 3
            else:
                decimals = 2
        return f"{prefix}{fv:,.{decimals}f}{suffix}"
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
        display_val = _fmt_val(pct_of_target, decimals=1)
        unit_display = "% of target"
    else:
        display_val = _fmt_val(current)
        unit_display = unit

    is_na = (current is None and pct_of_target is None)
    val_class = "kpi-value-na" if is_na else "kpi-value"

    change_html = ""
    if change is not None:
        arrow = "▲" if direction == "up" else ("▼" if direction == "down" else "—")
        chg_class = "chg-up" if direction == "up" else ("chg-down" if direction == "down" else "chg-flat")
        change_html = f'<span class="kpi-change {chg_class}">{arrow} {_fmt_val(abs(change))}</span>'

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

    cat_color = CAT_COLORS.get(category, "#3b82f6")

    return f"""
    <div class="kpi-card" id="kpi_{kpi_id}" data-category="{category}"
         style="border-top: 3px solid {cat_color}22; border-top-color: {cat_color}55;">
      <div class="kpi-card-top">
        <div class="kpi-title-row">
          <h3 class="kpi-name">{name}</h3>
          <span class="signal-badge {_signal_css(signal)}">{signal.upper()}</span>
        </div>
        <div class="kpi-value-row">
          <span class="{val_class}">{display_val}</span>
          {change_html}
        </div>
        <div style="display:flex; align-items:center; gap:6px; margin-bottom:8px;">
          <span class="kpi-unit">{unit_display}</span>
        </div>
        <div class="kpi-meta-row">
          <span class="conf-badge {conf_css}">{conf_label}</span>
          {stale_badge}{prov_badge}{warn_badge}
          <span class="kpi-asof {stale_css}">as of {as_of}</span>
          <a href="{data_source_url}" target="_blank" class="source-link">{source_auth} ↗</a>
        </div>
      </div>
      <div class="kpi-chart-wrap">
        <canvas id="chart_{kpi_id}"></canvas>
      </div>
      <div class="kpi-commentary">
        <div class="com-section">
          <span class="com-label">Analysis</span>
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
      <div class="section-header">
        <h2 class="section-title">Macro Events Timeline</h2>
        <span class="section-count">{len(events)} events</span>
      </div>
      <div class="events-scroll">{''.join(cards)}</div>
    </section>"""


# ── Category accent colours ───────────────────────────────────────────────────
CAT_COLORS = {
    "monetary_policy": "#3b82f6",
    "prices":          "#f59e0b",
    "growth":          "#22c55e",
    "external":        "#a855f7",
    "fiscal":          "#f87171",
    "financial":       "#06b6d4",
    "digital_economy": "#818cf8",
    "real_economy":    "#fb923c",
}

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:        #080d19;
  --surface:   #0f1829;
  --card:      #141e30;
  --border:    #1e2d45;
  --border-hi: #2d4060;
  --text:      #e2e8f0;
  --muted:     #64748b;
  --subtle:    #94a3b8;
  --accent:    #3b82f6;
}

html { scroll-behavior: smooth; }

body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg); color: var(--text);
  font-size: 14px; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; opacity: 0.85; }

/* ── Scrollbar ── */
::-webkit-scrollbar { height: 4px; width: 4px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border-hi); border-radius: 4px; }

/* ── Layout ── */
.container { max-width: 1440px; margin: 0 auto; padding: 0 24px 48px; }

/* ── Hero Header ── */
.dash-hero {
  background: linear-gradient(135deg, #0a1628 0%, #0f2040 50%, #0a1628 100%);
  border-bottom: 1px solid var(--border);
  padding: 32px 24px 28px;
  margin-bottom: 32px;
  position: relative;
  overflow: hidden;
}
.dash-hero::before {
  content: '';
  position: absolute; inset: 0;
  background: radial-gradient(ellipse 60% 100% at 80% 50%, #3b82f615 0%, transparent 70%);
  pointer-events: none;
}
.dash-hero-inner { max-width: 1440px; margin: 0 auto; position: relative; }
.dash-flag {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 0.7rem; font-weight: 600; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--accent);
  background: #3b82f610; border: 1px solid #3b82f630;
  padding: 4px 10px; border-radius: 20px; margin-bottom: 14px;
}
.dash-title {
  font-size: 2rem; font-weight: 800; color: #f8fafc;
  letter-spacing: -0.02em; line-height: 1.2; margin-bottom: 6px;
}
.dash-subtitle { color: var(--subtle); font-size: 0.9rem; }
.dash-stats {
  display: flex; gap: 24px; margin-top: 20px; flex-wrap: wrap;
}
.stat-pill {
  display: flex; align-items: center; gap: 8px;
  background: #ffffff08; border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 14px;
}
.stat-pill-val { font-size: 1.1rem; font-weight: 700; color: #f1f5f9; font-family: 'JetBrains Mono', monospace; }
.stat-pill-lbl { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
.stat-pill-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }

/* ── Macro Narrative ── */
.macro-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 24px 28px;
  margin-bottom: 36px;
  position: relative;
  overflow: hidden;
}
.macro-card::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #3b82f6, #a855f7, #22c55e);
}
.macro-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
.macro-headline { font-size: 1.1rem; font-weight: 700; color: #f1f5f9; line-height: 1.4; max-width: 800px; }
.risk-badge {
  flex-shrink: 0; font-size: 0.7rem; font-weight: 700;
  padding: 4px 12px; border-radius: 20px; letter-spacing: 0.06em; text-transform: uppercase;
}
.macro-summary { color: var(--subtle); font-size: 0.875rem; line-height: 1.7; margin-bottom: 14px; }
.macro-quote {
  padding: 14px 18px;
  background: #ffffff05; border-radius: 10px;
  border-left: 3px solid #64748b;
  font-style: italic; color: #94a3b8; font-size: 0.82rem; line-height: 1.6;
}
.macro-quote cite { display: block; margin-top: 6px; font-style: normal; color: var(--muted); font-size: 0.75rem; }

/* ── Category Section ── */
.category-group { margin-bottom: 44px; }
.category-header {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 18px; padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.cat-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.category-title {
  font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--subtle);
}
.cat-count {
  margin-left: auto; font-size: 0.68rem; color: var(--muted);
  background: var(--surface); border: 1px solid var(--border);
  padding: 2px 8px; border-radius: 10px;
}

/* ── KPI Grid ── */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
}

/* ── KPI Card ── */
.kpi-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 0;
  overflow: hidden;
  transition: border-color 0.2s, box-shadow 0.2s;
  display: flex; flex-direction: column;
}
.kpi-card:hover {
  border-color: var(--border-hi);
  box-shadow: 0 8px 32px #00000040;
}
.kpi-card-top {
  padding: 16px 18px 14px;
  border-bottom: 1px solid var(--border);
}
.kpi-title-row {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 8px; margin-bottom: 8px;
}
.kpi-name { font-size: 0.85rem; font-weight: 600; color: #cbd5e1; }
.signal-badge {
  font-size: 0.62rem; font-weight: 700; padding: 3px 8px;
  border-radius: 5px; text-transform: uppercase; letter-spacing: 0.06em;
  flex-shrink: 0; white-space: nowrap;
}
.signal-bullish { background: #14532d; color: #4ade80; border: 1px solid #166534; }
.signal-bearish { background: #450a0a; color: #fca5a5; border: 1px solid #7f1d1d; }
.signal-neutral { background: #0c1a2e; color: #93c5fd; border: 1px solid #1e3a5f; }

.kpi-value-row { display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px; }
.kpi-value {
  font-size: 2rem; font-weight: 800; color: #f8fafc;
  font-family: 'JetBrains Mono', monospace; letter-spacing: -0.02em;
  line-height: 1.1;
}
.kpi-value-na {
  font-size: 2rem; font-weight: 700; color: #334155;
  font-style: italic; font-family: 'JetBrains Mono', monospace;
}
.kpi-unit { font-size: 0.72rem; color: var(--muted); align-self: flex-end; padding-bottom: 4px; }
.kpi-change {
  font-size: 0.75rem; font-weight: 600; padding: 3px 7px;
  border-radius: 5px; display: inline-flex; align-items: center; gap: 3px;
}
.chg-up   { color: #4ade80; background: #14532d33; }
.chg-down { color: #fca5a5; background: #45090933; }
.chg-flat { color: var(--subtle); background: var(--surface); }

.kpi-meta-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.kpi-asof { font-size: 0.7rem; color: var(--muted); }
.source-link { font-size: 0.68rem; color: #475569; margin-left: auto; transition: color 0.15s; }
.source-link:hover { color: var(--accent); text-decoration: none; }

/* Badges */
.conf-badge {
  font-size: 0.6rem; font-weight: 700; padding: 2px 6px;
  border-radius: 4px; text-transform: uppercase; letter-spacing: 0.04em;
}
.conf-high   { background: #052e1644; color: #4ade80; border: 1px solid #14532d88; }
.conf-medium { background: #451a0344; color: #fcd34d; border: 1px solid #78350f88; }
.conf-low    { background: #45090944; color: #fca5a5; border: 1px solid #7f1d1d88; }
.stale-badge { font-size: 0.65rem; color: #fbbf24; }
.stale-warn  { color: #fbbf24; }
.stale-error { color: #f87171; }
.prov-badge  {
  font-size: 0.6rem; color: var(--muted);
  background: var(--surface); border: 1px solid var(--border);
  padding: 1px 5px; border-radius: 3px;
}
.warn-badge  { font-size: 0.78rem; color: #fbbf24; cursor: help; }

/* ── Chart ── */
.kpi-chart-wrap {
  padding: 4px 4px 0;
  height: 170px;
}

/* ── Commentary ── */
.kpi-commentary {
  padding: 14px 18px 16px;
  flex: 1;
}
.com-section { margin-bottom: 10px; }
.com-label {
  font-size: 0.62rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: #3d5470; display: block; margin-bottom: 4px;
}
.com-text  { font-size: 0.79rem; color: #7a8fa8; line-height: 1.6; }
.com-na    { font-size: 0.77rem; color: #1e2d45; font-style: italic; }
.inv-note  {
  font-size: 0.77rem; color: #93c5fd;
  background: linear-gradient(135deg, #0c1a2e, #111e35);
  border: 1px solid #1e3a5f;
  border-left: 3px solid #3b82f6;
  padding: 10px 12px; border-radius: 8px; margin-top: 10px;
  line-height: 1.55;
}

/* ── Events Timeline ── */
.events-section { margin-top: 48px; }
.section-header {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 20px; padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.section-title {
  font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--subtle);
}
.section-count {
  font-size: 0.68rem; color: var(--muted);
  background: var(--surface); border: 1px solid var(--border);
  padding: 2px 8px; border-radius: 10px;
}
.events-scroll {
  display: flex; gap: 12px; overflow-x: auto; padding-bottom: 16px;
  scrollbar-width: thin; scrollbar-color: var(--border-hi) transparent;
}
.ev-card {
  min-width: 230px; max-width: 250px; flex-shrink: 0;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px; padding: 14px;
  border-top: 3px solid var(--border);
  transition: border-color 0.2s;
}
.ev-card:hover { border-color: var(--border-hi); }
.ev-neg { border-top-color: #ef4444; }
.ev-pos { border-top-color: #22c55e; }
.ev-neu { border-top-color: #64748b; }
.ev-date  { font-size: 0.67rem; color: var(--muted); margin-bottom: 5px; font-family: 'JetBrains Mono', monospace; }
.ev-label {
  font-size: 0.79rem; font-weight: 600; color: var(--text);
  margin-bottom: 7px; display: flex; align-items: flex-start; gap: 6px; line-height: 1.3;
}
.ev-mag {
  font-size: 0.58rem; background: var(--surface);
  color: var(--muted); padding: 2px 5px; border-radius: 3px;
  white-space: nowrap; margin-top: 2px; flex-shrink: 0;
}
.ev-desc  { font-size: 0.73rem; color: #5a7090; line-height: 1.45; margin-bottom: 8px; }
.ev-kpis  { font-size: 0.66rem; color: #3d5470; }
.ev-src   { color: #3d5470; margin-left: 4px; transition: color 0.15s; }
.ev-src:hover { color: var(--accent); }

/* ── Stale Banner ── */
.stale-banner {
  background: #451a0322; border: 1px solid #78350f;
  border-radius: 10px; padding: 10px 16px;
  margin-bottom: 20px; font-size: 0.82rem; color: #fcd34d;
  display: flex; align-items: center; gap: 8px;
}

/* ── Footer ── */
.dash-footer {
  margin-top: 48px; padding: 20px 0;
  border-top: 1px solid var(--border);
  color: #253447; font-size: 0.73rem; line-height: 1.8;
}

/* ── Responsive ── */
@media (max-width: 640px) {
  .dash-title { font-size: 1.4rem; }
  .kpi-grid { grid-template-columns: 1fr; }
  .dash-stats { gap: 12px; }
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
            dot_color = CAT_COLORS.get(cat_id, "#3b82f6")
            sections_html += f"""
            <div class="category-group" id="cat_{cat_id}">
              <div class="category-header">
                <span class="cat-dot" style="background:{dot_color};box-shadow:0 0 6px {dot_color}88;"></span>
                <h2 class="category-title">{cat_label}</h2>
                <span class="cat-count">{len(panels)} indicator{'s' if len(panels)!=1 else ''}</span>
              </div>
              <div class="kpi-grid">{''.join(panels)}</div>
            </div>"""

    # ── Events timeline ──
    events_html = _render_events_timeline(events)

    # ── Stale warning banner ──
    stale_banner = ""
    if stale_kpis:
        stale_banner = f"""
        <div class="stale-banner">
          ⚠ Some data may not reflect the latest releases: {', '.join(stale_kpis)}
        </div>"""

    # ── Summary stats for hero ──
    bullish_count = sum(1 for kpi in kpis_config
                        if classify_signal(kpi["id"],
                            value=kpis.get(kpi["id"],{}).get("current_value"),
                            prev_value=kpis.get(kpi["id"],{}).get("prev_value"),
                            week_change=kpis.get(kpi["id"],{}).get("change_from_prev_week"),
                            pct_of_target=kpis.get(kpi["id"],{}).get("pct_of_target")) == "bullish")
    bearish_count = sum(1 for kpi in kpis_config
                        if classify_signal(kpi["id"],
                            value=kpis.get(kpi["id"],{}).get("current_value"),
                            prev_value=kpis.get(kpi["id"],{}).get("prev_value"),
                            week_change=kpis.get(kpi["id"],{}).get("change_from_prev_week"),
                            pct_of_target=kpis.get(kpi["id"],{}).get("pct_of_target")) == "bearish")

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

  <header class="dash-hero">
    <div class="dash-hero-inner">
      <div class="dash-flag">🇮🇳 India Macro Intelligence</div>
      <h1 class="dash-title">India Macro Dashboard</h1>
      <p class="dash-subtitle">{period_label} &nbsp;·&nbsp; {len(kpis_config)} KPIs &nbsp;·&nbsp; {len(events)} Events &nbsp;·&nbsp; Primary official sources only</p>
      <div class="dash-stats">
        <div class="stat-pill">
          <span class="stat-pill-dot" style="background:#4ade80;box-shadow:0 0 6px #4ade8066;"></span>
          <div>
            <div class="stat-pill-val" style="color:#4ade80;">{bullish_count}</div>
            <div class="stat-pill-lbl">Bullish signals</div>
          </div>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-dot" style="background:#fca5a5;box-shadow:0 0 6px #fca5a566;"></span>
          <div>
            <div class="stat-pill-val" style="color:#fca5a5;">{bearish_count}</div>
            <div class="stat-pill-lbl">Bearish signals</div>
          </div>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-dot" style="background:#93c5fd;"></span>
          <div>
            <div class="stat-pill-val" style="color:#93c5fd;">{len(kpis_config)-bullish_count-bearish_count}</div>
            <div class="stat-pill-lbl">Neutral</div>
          </div>
        </div>
        <div class="stat-pill">
          <span class="stat-pill-dot" style="background:#64748b;"></span>
          <div>
            <div class="stat-pill-val" style="color:#94a3b8;">{generated_at.split(',')[0]}</div>
            <div class="stat-pill-lbl">Last generated</div>
          </div>
        </div>
      </div>
    </div>
  </header>

<div class="container">
  {stale_banner}
  {macro_html}
  {sections_html}
  {events_html}

  <footer class="dash-footer">
    <p>Data sourced from primary official releases: RBI · MoSPI/NSO · NPCI · SIAM · CEA · DGCA · CGA · Ministry of Commerce · Ministry of Finance</p>
    <p>Signals are rule-based deterministic thresholds — same input always produces same output. Commentary is analytical only, not investment advice.</p>
    <p>Confidence badges: HIGH = value read directly from official primary source · MEDIUM = secondary official source · LOW = proxy/estimated</p>
  </footer>
</div>
</body>
</html>"""

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html, encoding="utf-8")
        logger.info("Dashboard saved: %s", output_path)

    return html
