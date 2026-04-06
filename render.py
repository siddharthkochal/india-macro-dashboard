"""
Dashboard Renderer — generic, data-driven, zero hardcoded KPI names.

Adding a new KPI requires:
  1. Add entry to kpis_config.json
  2. Drop data/{kpi_id}.json
  -> Appears on dashboard automatically. No changes here.
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
    if not events_for_kpi:
        return "null"
    anns = []
    for ev in events_for_kpi:
        color = {"negative": "#dc2626", "positive": "#16a34a",
                 "neutral": "#94a3b8"}.get(ev.get("impact_direction", "neutral"), "#94a3b8")
        label = ev.get("label", "")[:18]
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
            backgroundColor: '{color}18',
            color: '{color}',
            font: {{ size: 9 }}
          }}
        }}""")
    return "{ annotations: [" + ",\n".join(anns) + "] }"


# KPIs that are directly impacted by oil prices
OIL_IMPACT_KPIS = {
    "trade_balance":    "India's merchandise trade deficit widens ~$1.5B for every $10/barrel rise in Brent.",
    "cpi_inflation":    "Every $10/barrel Brent rise adds ~25–30bps to India CPI via fuel and transport costs.",
    "inr_usd":          "Higher oil raises India's import bill, pressuring INR through wider current account deficit.",
    "fiscal_deficit":   "Oil above $85 raises fuel subsidy burden and import taxes, affecting fiscal math.",
    "gdp_growth":       "India is 85% import-dependent on crude — sustained high oil is a structural GDP drag.",
    "forex_reserves":   "RBI uses reserves to defend INR when oil-driven import demand spikes.",
}


def _oil_impact_html(kpi_id: str, market_data: dict) -> str:
    """Inject live oil context into cards affected by oil prices."""
    if kpi_id not in OIL_IMPACT_KPIS:
        return ""
    prices = (market_data or {}).get("prices", {})
    alert  = (market_data or {}).get("india_oil_alert", {})
    brent  = prices.get("brent", {}).get("current_value")
    inr    = prices.get("inr",   {}).get("current_value")
    ovx    = prices.get("ovx",   {}).get("current_value")
    if not brent:
        return ""

    severity = alert.get("severity", "normal")
    color = {"critical": "#dc2626", "warning": "#d97706", "normal": "#16a34a"}.get(severity, "#6b7280")
    bg    = {"critical": "#fef2f2", "warning": "#fffbeb", "normal": "#f0fdf4"}.get(severity, "#f8fafc")
    border= {"critical": "#fecaca", "warning": "#fde68a", "normal": "#bbf7d0"}.get(severity, "#e2e8f0")

    parts = [f"Brent <strong>${brent:.0f}/bbl</strong>"]
    if inr:
        parts.append(f"USD/INR <strong>{inr:.2f}</strong>")
    if ovx:
        parts.append(f"Oil Vol (OVX) <strong>{ovx:.0f}</strong>")
    live_str = " &nbsp;|&nbsp; ".join(parts)

    impact_note = OIL_IMPACT_KPIS[kpi_id]

    return f"""
    <div class="oil-impact" style="background:{bg};border:1px solid {border};border-left:3px solid {color};">
      <div class="oil-impact-header">
        <span class="oil-dot" style="background:{color};"></span>
        <span class="oil-label" style="color:{color};">Live Market Impact ({severity.upper()})</span>
        <span class="oil-prices">{live_str}</span>
      </div>
      <p class="oil-note">{impact_note}</p>
    </div>"""


# ── KPI Panel ─────────────────────────────────────────────────────────────────

def _render_kpi_panel(kpi_cfg: dict, kpi_data: dict | None,
                      kpi_events: list, commentary_kpi: dict,
                      market_data: dict = None) -> str:
    kpi_id   = kpi_cfg["id"]
    name     = kpi_cfg["name"]
    unit     = kpi_cfg.get("unit", "")
    category = kpi_cfg.get("category", "")
    source_url  = kpi_cfg.get("source_base_url", "#")
    source_auth = kpi_cfg.get("source_authority", "")
    chart_type  = kpi_cfg.get("chart_type", "line")

    current      = _safe(kpi_data, "current_value")
    prev         = _safe(kpi_data, "prev_value")
    change       = _safe(kpi_data, "change_from_prev")
    direction    = _safe(kpi_data, "direction", default="")
    as_of        = _safe(kpi_data, "as_of", default="")
    release_date = _safe(kpi_data, "as_of_release_date", default="")
    is_prov      = _safe(kpi_data, "is_provisional", default=False)
    confidence   = _safe(kpi_data, "data_confidence", default="medium")
    data_notes   = _safe(kpi_data, "data_notes")
    week_change  = _safe(kpi_data, "change_from_prev_week")
    pct_of_target= _safe(kpi_data, "pct_of_target")
    series       = _safe(kpi_data, "series", default=[])
    data_source_url = _safe(kpi_data, "source_url", default=source_url)

    signal = classify_signal(
        kpi_id=kpi_id,
        value=current if current is not None else pct_of_target,
        prev_value=prev,
        week_change=week_change,
        pct_of_target=pct_of_target,
    )

    if current is None and pct_of_target is not None:
        display_val  = _fmt_val(pct_of_target, decimals=1)
        unit_display = "% of target"
    else:
        display_val  = _fmt_val(current)
        unit_display = unit

    is_na     = (current is None and pct_of_target is None)
    val_class = "kpi-value-na" if is_na else "kpi-value"

    change_html = ""
    if change is not None:
        arrow     = "▲" if direction == "up" else ("▼" if direction == "down" else "—")
        chg_class = "chg-up" if direction == "up" else ("chg-down" if direction == "down" else "chg-flat")
        change_html = f'<span class="kpi-change {chg_class}">{arrow} {_fmt_val(abs(change))}</span>'

    stale_css   = _staleness_css(release_date)
    stale_badge = '<span class="stale-badge">Stale</span>' if stale_css else ""
    prov_badge  = '<span class="prov-badge">Provisional</span>' if is_prov else ""
    warn_badge  = f'<span class="warn-badge" title="{data_notes}">!</span>' if data_notes else ""
    conf_label  = (confidence or "medium").upper()
    conf_css    = _confidence_css(confidence)

    # Chart colours — adapted for light theme
    chart_color = {"bullish": "#16a34a", "bearish": "#dc2626",
                   "neutral": "#2563eb"}.get(signal, "#2563eb")
    chart_bg    = {"bullish": "#16a34a18", "bearish": "#dc262618",
                   "neutral": "#2563eb12"}.get(signal, "#2563eb12")
    annotations_js = _event_annotation_js(kpi_events, _series_to_js(series)[0])
    labels_js, values_js = _series_to_js(series)

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
            backgroundColor: '{chart_bg}',
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: '{chart_color}',
            fill: {'true' if chart_type == 'line' else 'false'},
            tension: 0.35
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
                   grid: {{ color: '#f1f5f9' }} }},
            y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }},
                   grid: {{ color: '#f1f5f9' }} }}
          }}
        }}
      }});
    }})();"""

    why     = (_safe(commentary_kpi, "why_it_moved")
               or (_safe(commentary_kpi, "what", default="") + " " + _safe(commentary_kpi, "why", default="")).strip()
               or "")
    outlook = _safe(commentary_kpi, "forward_outlook") or _safe(commentary_kpi, "outlook", default="")
    inv_note= _safe(commentary_kpi, "investor_note", default="")

    why_html = f'<p class="com-text">{why}</p>' if why else '<p class="com-na">Analysis not available.</p>'
    out_html = f'<p class="com-text">{outlook}</p>' if outlook else ""
    inv_html = f'<div class="inv-note"><strong>Investor view:</strong> {inv_note}</div>' if inv_note else ""

    cat_color = CAT_COLORS.get(category, "#2563eb")
    oil_html  = _oil_impact_html(kpi_id, market_data)

    return f"""
    <div class="kpi-card" id="kpi_{kpi_id}" data-category="{category}">
      <div class="kpi-card-accent" style="background:{cat_color};"></div>
      <div class="kpi-card-body">
        <div class="kpi-title-row">
          <h3 class="kpi-name">{name}</h3>
          <span class="signal-badge {_signal_css(signal)}">{signal.upper()}</span>
        </div>
        <div class="kpi-value-row">
          <span class="{val_class}">{display_val}</span>
          {change_html}
        </div>
        <div class="kpi-unit-row">
          <span class="kpi-unit">{unit_display}</span>
          <span class="kpi-asof {stale_css}">as of {as_of}</span>
        </div>
        <div class="kpi-meta-row">
          <span class="conf-badge {conf_css}">{conf_label}</span>
          {stale_badge}{prov_badge}{warn_badge}
          <a href="{data_source_url}" target="_blank" class="source-link">{source_auth} ↗</a>
        </div>
      </div>
      <div class="kpi-chart-wrap">
        <canvas id="chart_{kpi_id}"></canvas>
      </div>
      <div class="kpi-commentary">
        {oil_html}
        <div class="com-section">
          <span class="com-label">Analysis</span>
          {why_html}
        </div>
        {'<div class="com-section"><span class="com-label">Outlook</span>' + out_html + '</div>' if out_html else ''}
        {inv_html}
      </div>
    </div>
    <script>{chart_js}</script>"""


# ── Ticker bar ────────────────────────────────────────────────────────────────

def _render_ticker(market_data: dict, geo_data: dict) -> str:
    prices   = (market_data or {}).get("prices", {})
    alert    = (market_data or {}).get("india_oil_alert", {})
    headlines= (geo_data or {}).get("india_headlines", [])
    fetch_at = (market_data or {}).get("fetched_at", "")

    if not prices:
        return ""

    severity      = alert.get("severity", "normal")
    alert_color   = {"critical": "#dc2626", "warning": "#d97706", "normal": "#16a34a"}.get(severity, "#6b7280")
    alert_bg      = {"critical": "#fef2f2", "warning": "#fffbeb", "normal": "#f0fdf4"}.get(severity, "#f8fafc")
    alert_border  = {"critical": "#fecaca", "warning": "#fde68a", "normal": "#bbf7d0"}.get(severity, "#e2e8f0")
    alert_msgs    = alert.get("alerts", [])

    # Price pills
    pills = []
    for key, p in prices.items():
        v = p.get("current_value")
        if v is None:
            continue
        sig   = p.get("signal", "neutral")
        dirn  = p.get("direction", "")
        arrow = "▲" if dirn == "up" else ("▼" if dirn == "down" else "")
        val_color = {"bullish": "#16a34a", "bearish": "#dc2626", "neutral": "#374151"}.get(sig, "#374151")
        arr_color = "#16a34a" if dirn == "up" else ("#dc2626" if dirn == "down" else "#9ca3af")
        pills.append(f"""<div class="tick-pill">
          <span class="tick-name">{p['name']}</span>
          <span class="tick-val" style="color:{val_color};">{v:,.2f}</span>
          <span class="tick-arrow" style="color:{arr_color};">{arrow}</span>
          <span class="tick-unit">{p['unit']}</span>
        </div>""")

    # Top headlines (compact)
    hl_html = ""
    if headlines:
        items = []
        for h in headlines[:5]:
            sev = h.get("severity", "medium")
            dot_c = {"critical": "#dc2626", "high": "#d97706", "medium": "#6b7280"}.get(sev, "#6b7280")
            items.append(f"""<div class="hl-item">
              <span class="hl-dot" style="background:{dot_c}"></span>
              <span class="hl-time">{h.get('date_display','')}</span>
              <span class="hl-text">{h.get('title','')[:110]}</span>
            </div>""")
        hl_html = f'<div class="hl-feed">{"".join(items)}</div>'

    alert_bar = ""
    if alert_msgs:
        alert_bar = f"""<div class="alert-bar" style="background:{alert_bg};border-color:{alert_border};color:{alert_color};">
          <span class="alert-dot" style="background:{alert_color};"></span>
          <strong>India Alert &mdash; {severity.upper()}:</strong>&nbsp;
          {" &nbsp;|&nbsp; ".join(alert_msgs)}
        </div>"""

    return f"""<section class="live-section">
      <div class="live-top">
        <div class="live-label">
          <span class="live-pulse"></span>
          <span>Live Markets &amp; Geopolitics</span>
          <span class="live-src">Yahoo Finance + Spectator Index &middot; {fetch_at}</span>
        </div>
        <div class="tick-strip">{"".join(pills)}</div>
      </div>
      {alert_bar}
      {hl_html}
    </section>"""


# ── Events Timeline ───────────────────────────────────────────────────────────

def _render_events_timeline(events: list) -> str:
    if not events:
        return ""
    cards = []
    for ev in sorted(events, key=lambda e: e.get("date", "")):
        direction   = ev.get("impact_direction", "neutral")
        accent      = {"negative": "#dc2626", "positive": "#16a34a",
                       "neutral": "#94a3b8"}.get(direction, "#94a3b8")
        bg          = {"negative": "#fef2f2", "positive": "#f0fdf4",
                       "neutral": "#f8fafc"}.get(direction, "#f8fafc")
        mag         = ev.get("magnitude", "")
        mag_html    = f'<span class="ev-mag">{mag}</span>' if mag else ""
        kpis_str    = ", ".join(ev.get("impact_kpis", []))
        src_url     = ev.get("source_url", "")
        src_html    = f'<a href="{src_url}" target="_blank" class="ev-src">source ↗</a>' if src_url else ""
        cards.append(f"""<div class="ev-card" style="border-top-color:{accent};background:{bg};">
          <div class="ev-date">{ev.get('date','')}</div>
          <div class="ev-label" style="color:{accent};">{ev.get('label','')} {mag_html}</div>
          <div class="ev-desc">{ev.get('description','')}</div>
          <div class="ev-kpis">Affects: {kpis_str} {src_html}</div>
        </div>""")
    return f"""<section class="events-section">
      <div class="section-header">
        <h2 class="section-title">Macro Events Timeline</h2>
        <span class="section-count">{len(events)} events</span>
      </div>
      <div class="events-scroll">{"".join(cards)}</div>
    </section>"""


# ── Category accent colours ───────────────────────────────────────────────────

CAT_COLORS = {
    "monetary_policy": "#2563eb",
    "prices":          "#d97706",
    "growth":          "#16a34a",
    "external":        "#7c3aed",
    "fiscal":          "#dc2626",
    "financial":       "#0891b2",
    "digital_economy": "#6366f1",
    "real_economy":    "#ea580c",
}

# ── CSS ────────────────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:       #f4f6f9;
  --surface:  #ffffff;
  --border:   #e2e8f0;
  --border-hi:#c7d2e0;
  --text:     #0f172a;
  --body:     #374151;
  --muted:    #6b7280;
  --subtle:   #9ca3af;
  --accent:   #2563eb;
  --shadow:   0 1px 4px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04);
  --shadow-hover: 0 4px 12px rgba(0,0,0,0.08), 0 8px 32px rgba(0,0,0,0.06);
}

html { scroll-behavior: smooth; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg); color: var(--text);
  font-size: 14px; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
::-webkit-scrollbar { height: 5px; width: 5px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border-hi); border-radius: 4px; }

/* ── Layout ── */
.page-wrap { max-width: 1480px; margin: 0 auto; padding: 0 24px 56px; }

/* ── Top nav bar ── */
.top-nav {
  background: #ffffff;
  border-bottom: 1px solid var(--border);
  padding: 0 24px;
  position: sticky; top: 0; z-index: 100;
  box-shadow: 0 1px 0 var(--border);
}
.top-nav-inner {
  max-width: 1480px; margin: 0 auto;
  display: flex; align-items: center; gap: 24px; height: 52px;
}
.nav-brand {
  display: flex; align-items: center; gap: 10px;
  font-weight: 700; font-size: 0.9rem; color: var(--text);
  letter-spacing: -0.01em;
}
.nav-flag { font-size: 1.1rem; }
.nav-period {
  font-size: 0.72rem; color: var(--muted);
  background: var(--bg); border: 1px solid var(--border);
  padding: 3px 10px; border-radius: 20px;
}
.nav-links { display: flex; gap: 4px; margin-left: auto; }
.nav-link {
  font-size: 0.72rem; color: var(--muted); padding: 4px 10px;
  border-radius: 6px; transition: all 0.15s; cursor: pointer;
}
.nav-link:hover { background: var(--bg); color: var(--text); }

/* ── Hero ── */
.dash-hero {
  background: linear-gradient(120deg, #1e3a5f 0%, #1a3557 40%, #0f2744 100%);
  padding: 40px 24px 36px;
  position: relative; overflow: hidden;
}
.dash-hero::after {
  content: '';
  position: absolute; inset: 0;
  background: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.02'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
  pointer-events: none;
}
.dash-hero-inner { max-width: 1480px; margin: 0 auto; position: relative; }
.hero-eyebrow {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 0.68rem; font-weight: 600; letter-spacing: 0.14em;
  text-transform: uppercase; color: #93c5fd;
  background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12);
  padding: 4px 12px; border-radius: 20px; margin-bottom: 16px;
}
.dash-title {
  font-size: 2.2rem; font-weight: 800; color: #f8fafc;
  letter-spacing: -0.025em; line-height: 1.15; margin-bottom: 8px;
}
.dash-sub { color: #94a3b8; font-size: 0.88rem; margin-bottom: 24px; }
.hero-stats { display: flex; gap: 16px; flex-wrap: wrap; }
.hstat {
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.1);
  border-radius: 10px; padding: 12px 18px;
  display: flex; align-items: center; gap: 12px;
}
.hstat-icon { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.hstat-val {
  font-size: 1.3rem; font-weight: 800;
  font-family: 'JetBrains Mono', monospace; line-height: 1;
}
.hstat-lbl { font-size: 0.68rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 2px; }

/* ── Live Section ── */
.live-section {
  background: #ffffff;
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  margin-bottom: 24px;
  box-shadow: var(--shadow);
}
.live-top {
  display: flex; align-items: center; gap: 16px;
  padding: 10px 16px; flex-wrap: wrap;
  border-bottom: 1px solid var(--bg);
  background: #fafbfc;
}
.live-label {
  display: flex; align-items: center; gap: 8px;
  font-size: 0.7rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--muted); white-space: nowrap;
}
.live-pulse {
  width: 7px; height: 7px; border-radius: 50%;
  background: #dc2626; flex-shrink: 0;
  box-shadow: 0 0 6px #dc262688;
  animation: pulse 2s infinite;
}
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
.live-src { font-weight: 400; color: var(--subtle); font-size: 0.67rem; }
.tick-strip { display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }
.tick-pill {
  display: flex; align-items: center; gap: 6px;
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 7px; padding: 5px 12px;
}
.tick-name { font-size: 0.67rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.tick-val  { font-size: 0.88rem; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
.tick-arrow{ font-size: 0.7rem; }
.tick-unit { font-size: 0.62rem; color: var(--subtle); }

.alert-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 16px; font-size: 0.8rem;
  border-top: 1px solid transparent;
}
.alert-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; animation: pulse 1.5s infinite; }

.hl-feed { padding: 0 16px 4px; }
.hl-item {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 7px 0; border-bottom: 1px solid var(--bg);
}
.hl-item:last-child { border-bottom: none; }
.hl-dot { width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; margin-top: 6px; }
.hl-time { font-size: 0.65rem; color: var(--muted); white-space: nowrap; flex-shrink: 0;
           font-family: 'JetBrains Mono', monospace; margin-top: 2px; min-width: 110px; }
.hl-text { font-size: 0.78rem; color: var(--body); line-height: 1.45; }

/* ── Macro Card ── */
.macro-card {
  background: #ffffff;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 24px 28px;
  margin-bottom: 28px;
  box-shadow: var(--shadow);
  position: relative; overflow: hidden;
}
.macro-card::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #2563eb, #7c3aed, #16a34a);
}
.macro-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }
.macro-headline { font-size: 1.05rem; font-weight: 700; color: var(--text); line-height: 1.45; max-width: 820px; }
.risk-badge {
  flex-shrink: 0; font-size: 0.68rem; font-weight: 700;
  padding: 4px 12px; border-radius: 20px; letter-spacing: 0.05em; text-transform: uppercase;
}
.macro-summary { color: var(--body); font-size: 0.875rem; line-height: 1.75; margin-bottom: 14px; }
.macro-quote {
  padding: 14px 18px;
  background: #f8fafc; border-radius: 10px;
  border-left: 3px solid var(--border-hi);
  font-style: italic; color: var(--muted); font-size: 0.82rem; line-height: 1.65;
}
.macro-quote cite { display: block; margin-top: 6px; font-style: normal; color: var(--subtle); font-size: 0.73rem; }

/* ── Stale Banner ── */
.stale-banner {
  background: #fffbeb; border: 1px solid #fde68a;
  border-radius: 10px; padding: 10px 16px;
  margin-bottom: 20px; font-size: 0.8rem; color: #92400e;
  display: flex; align-items: center; gap: 8px;
}

/* ── Category Group ── */
.category-group { margin-bottom: 40px; }
.category-header {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 16px; padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.cat-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.category-title {
  font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--muted);
}
.cat-count {
  margin-left: auto; font-size: 0.67rem; color: var(--subtle);
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
  background: #ffffff;
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  display: flex; flex-direction: column;
  transition: box-shadow 0.2s, border-color 0.2s;
  box-shadow: var(--shadow);
}
.kpi-card:hover {
  border-color: var(--border-hi);
  box-shadow: var(--shadow-hover);
}
.kpi-card-accent { height: 3px; width: 100%; flex-shrink: 0; }
.kpi-card-body { padding: 16px 18px 12px; }
.kpi-title-row {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 8px; margin-bottom: 10px;
}
.kpi-name { font-size: 0.82rem; font-weight: 600; color: var(--body); }
.signal-badge {
  font-size: 0.6rem; font-weight: 700; padding: 3px 8px;
  border-radius: 5px; text-transform: uppercase; letter-spacing: 0.06em;
  flex-shrink: 0; white-space: nowrap;
}
.signal-bullish { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
.signal-bearish { background: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; }
.signal-neutral { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }

.kpi-value-row { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }
.kpi-value {
  font-size: 2rem; font-weight: 800; color: var(--text);
  font-family: 'JetBrains Mono', monospace; letter-spacing: -0.02em; line-height: 1.1;
}
.kpi-value-na {
  font-size: 2rem; font-weight: 600; color: var(--subtle);
  font-style: italic; font-family: 'JetBrains Mono', monospace;
}
.kpi-change {
  font-size: 0.72rem; font-weight: 600; padding: 3px 7px;
  border-radius: 5px; display: inline-flex; align-items: center; gap: 3px;
}
.chg-up   { color: #15803d; background: #dcfce7; }
.chg-down { color: #b91c1c; background: #fee2e2; }
.chg-flat { color: var(--muted); background: var(--bg); }

.kpi-unit-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.kpi-unit  { font-size: 0.7rem; color: var(--muted); }
.kpi-asof  { font-size: 0.68rem; color: var(--subtle); }
.stale-warn  { color: #d97706; }
.stale-error { color: #dc2626; }

.kpi-meta-row { display: flex; gap: 5px; align-items: center; flex-wrap: wrap; }
.source-link { font-size: 0.67rem; color: var(--subtle); margin-left: auto; transition: color 0.15s; }
.source-link:hover { color: var(--accent); text-decoration: none; }

.conf-badge {
  font-size: 0.59rem; font-weight: 700; padding: 2px 6px;
  border-radius: 4px; text-transform: uppercase; letter-spacing: 0.04em;
}
.conf-high   { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
.conf-medium { background: #fef9c3; color: #a16207; border: 1px solid #fde68a; }
.conf-low    { background: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; }
.stale-badge { font-size: 0.59rem; color: #d97706; background: #fffbeb; border: 1px solid #fde68a; padding: 1px 5px; border-radius: 3px; }
.prov-badge  { font-size: 0.59rem; color: var(--muted); background: var(--bg); border: 1px solid var(--border); padding: 1px 5px; border-radius: 3px; }
.warn-badge  { font-size: 0.72rem; color: #d97706; cursor: help; font-weight: 700; }

/* ── Chart ── */
.kpi-chart-wrap { height: 165px; padding: 4px 4px 0; border-top: 1px solid var(--bg); }

/* ── Commentary ── */
.kpi-commentary { padding: 14px 18px 16px; flex: 1; border-top: 1px solid var(--bg); }
.com-section { margin-bottom: 10px; }
.com-label {
  font-size: 0.6rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--subtle); display: block; margin-bottom: 4px;
}
.com-text { font-size: 0.78rem; color: var(--body); line-height: 1.65; }
.com-na   { font-size: 0.76rem; color: var(--subtle); font-style: italic; }
.inv-note {
  font-size: 0.76rem; color: #1e40af;
  background: #eff6ff; border: 1px solid #bfdbfe;
  border-left: 3px solid #2563eb;
  padding: 10px 12px; border-radius: 8px; margin-top: 10px; line-height: 1.55;
}

/* ── Oil Impact Box ── */
.oil-impact {
  border-radius: 8px; padding: 10px 12px;
  margin-bottom: 12px;
}
.oil-impact-header {
  display: flex; align-items: center; gap: 8px; margin-bottom: 5px; flex-wrap: wrap;
}
.oil-dot   { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; animation: pulse 2s infinite; }
.oil-label { font-size: 0.62rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; }
.oil-prices{ font-size: 0.72rem; font-family: 'JetBrains Mono', monospace; color: var(--body); margin-left: auto; }
.oil-note  { font-size: 0.75rem; color: var(--body); line-height: 1.5; }

/* ── Events ── */
.events-section { margin-top: 40px; }
.section-header {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 16px; padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}
.section-title {
  font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--muted);
}
.section-count {
  font-size: 0.67rem; color: var(--subtle);
  background: var(--surface); border: 1px solid var(--border);
  padding: 2px 8px; border-radius: 10px;
}
.events-scroll {
  display: flex; gap: 12px; overflow-x: auto; padding-bottom: 12px;
  scrollbar-width: thin; scrollbar-color: var(--border-hi) transparent;
}
.ev-card {
  min-width: 230px; max-width: 250px; flex-shrink: 0;
  border: 1px solid var(--border);
  border-top: 3px solid var(--border);
  border-radius: 10px; padding: 14px;
  transition: box-shadow 0.2s;
}
.ev-card:hover { box-shadow: var(--shadow-hover); }
.ev-date  { font-size: 0.67rem; color: var(--muted); margin-bottom: 5px; font-family: 'JetBrains Mono', monospace; }
.ev-label { font-size: 0.78rem; font-weight: 700; margin-bottom: 6px; display: flex; align-items: flex-start; gap: 6px; }
.ev-mag   { font-size: 0.58rem; background: rgba(0,0,0,0.06); color: var(--muted); padding: 2px 5px; border-radius: 3px; white-space: nowrap; flex-shrink: 0; text-transform: uppercase; }
.ev-desc  { font-size: 0.72rem; color: var(--muted); line-height: 1.45; margin-bottom: 8px; }
.ev-kpis  { font-size: 0.65rem; color: var(--subtle); }
.ev-src   { color: var(--accent); margin-left: 4px; }

/* ── Footer ── */
.dash-footer {
  margin-top: 48px; padding: 20px 0;
  border-top: 1px solid var(--border);
  color: var(--subtle); font-size: 0.72rem; line-height: 1.9;
}

/* ── Responsive ── */
@media (max-width: 768px) {
  .dash-title { font-size: 1.5rem; }
  .kpi-grid { grid-template-columns: 1fr; }
  .hero-stats { gap: 10px; }
  .tick-strip { display: none; }
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
    market_data: dict = None,
    geo_data: dict = None,
) -> str:
    events     = events or []
    stale_kpis = stale_kpis or []

    if not kpis_config:
        cfg_path = Path(__file__).parent / "kpis_config.json"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                kpis_config = json.load(f).get("kpis", [])
        else:
            kpis_config = []

    kpis          = kpi_data.get("kpis", {})
    kpi_com       = commentary.get("kpi_commentary", {})
    macro         = commentary.get("macro_narrative", {})
    headline_block= commentary.get("headline", {})
    period_label  = kpi_data.get("period_label", "Latest")
    generated_at  = datetime.now().strftime("%d %b %Y, %H:%M")

    # Macro card
    headline  = macro.get("headline") or headline_block.get("title", "")
    summary   = macro.get("summary") or headline_block.get("summary", "")
    quote     = macro.get("rbi_verbatim_quote", "")
    risk_tone = macro.get("risk_tone") or headline_block.get("risk_tone_label", "")
    rt_colors = {
        "cautiously_bullish": ("#15803d", "#dcfce7", "#bbf7d0"),
        "cautiously bullish": ("#15803d", "#dcfce7", "#bbf7d0"),
        "cautious":           ("#a16207", "#fef9c3", "#fde68a"),
        "bearish":            ("#b91c1c", "#fee2e2", "#fecaca"),
    }
    rt_col, rt_bg, rt_border = rt_colors.get(risk_tone.lower() if risk_tone else "", ("#1d4ed8","#eff6ff","#bfdbfe"))

    macro_html = f"""
    <div class="macro-card">
      <div class="macro-top">
        <h2 class="macro-headline">{headline or 'Macro Narrative'}</h2>
        {'<span class="risk-badge" style="color:'+rt_col+';background:'+rt_bg+';border:1px solid '+rt_border+';">'+risk_tone.upper()+'</span>' if risk_tone else ''}
      </div>
      {('<p class="macro-summary">'+summary+'</p>') if summary else ''}
      {('<div class="macro-quote">"'+quote+'"<cite>— RBI Governor</cite></div>') if quote else ''}
    </div>"""

    # Category sections
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
        kpi_id  = kpi_cfg["id"]
        cat     = kpi_cfg.get("category", "other")
        kpi_d   = kpis.get(kpi_id)
        kpi_ev  = _events_for_kpi(kpi_id, events)
        com_kpi = kpi_com.get(kpi_id, {})
        panel   = _render_kpi_panel(kpi_cfg, kpi_d, kpi_ev, com_kpi, market_data)
        panels_by_cat.setdefault(cat, []).append(panel)

    sections_html = ""
    for cat_id, cat_label in category_order:
        panels = panels_by_cat.get(cat_id, [])
        if panels:
            dot_color = CAT_COLORS.get(cat_id, "#2563eb")
            sections_html += f"""
            <div class="category-group" id="cat_{cat_id}">
              <div class="category-header">
                <span class="cat-dot" style="background:{dot_color};"></span>
                <h2 class="category-title">{cat_label}</h2>
                <span class="cat-count">{len(panels)} indicator{'s' if len(panels)!=1 else ''}</span>
              </div>
              <div class="kpi-grid">{"".join(panels)}</div>
            </div>"""

    events_html = _render_events_timeline(events)
    ticker_html = _render_ticker(market_data, geo_data)

    # Signal counts for hero
    def _sig(kpi):
        return classify_signal(kpi["id"],
            value=kpis.get(kpi["id"],{}).get("current_value"),
            prev_value=kpis.get(kpi["id"],{}).get("prev_value"),
            week_change=kpis.get(kpi["id"],{}).get("change_from_prev_week"),
            pct_of_target=kpis.get(kpi["id"],{}).get("pct_of_target"))

    bullish = sum(1 for k in kpis_config if _sig(k) == "bullish")
    bearish = sum(1 for k in kpis_config if _sig(k) == "bearish")
    neutral = len(kpis_config) - bullish - bearish

    stale_banner = ""
    if stale_kpis:
        stale_banner = f'<div class="stale-banner">Data may be outdated for: {", ".join(stale_kpis)}</div>'

    # Nav links for category jump
    nav_links = "".join(
        f'<a class="nav-link" href="#cat_{cat_id}">{label}</a>'
        for cat_id, label in category_order
        if panels_by_cat.get(cat_id)
    )

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

<nav class="top-nav">
  <div class="top-nav-inner">
    <div class="nav-brand"><span class="nav-flag">&#127470;&#127475;</span> India Macro Dashboard</div>
    <span class="nav-period">{period_label}</span>
    <div class="nav-links">{nav_links}</div>
  </div>
</nav>

<header class="dash-hero">
  <div class="dash-hero-inner">
    <div class="hero-eyebrow">&#127470;&#127475; India Macro Intelligence &middot; Primary Sources Only</div>
    <h1 class="dash-title">India Macro Dashboard</h1>
    <p class="dash-sub">{period_label} &nbsp;&middot;&nbsp; {len(kpis_config)} KPIs &nbsp;&middot;&nbsp; {len(events)} Events &nbsp;&middot;&nbsp; Generated {generated_at}</p>
    <div class="hero-stats">
      <div class="hstat">
        <span class="hstat-icon" style="background:#4ade80;box-shadow:0 0 8px #4ade8066;"></span>
        <div><div class="hstat-val" style="color:#4ade80;">{bullish}</div><div class="hstat-lbl">Bullish</div></div>
      </div>
      <div class="hstat">
        <span class="hstat-icon" style="background:#f87171;box-shadow:0 0 8px #f8717166;"></span>
        <div><div class="hstat-val" style="color:#f87171;">{bearish}</div><div class="hstat-lbl">Bearish</div></div>
      </div>
      <div class="hstat">
        <span class="hstat-icon" style="background:#93c5fd;"></span>
        <div><div class="hstat-val" style="color:#93c5fd;">{neutral}</div><div class="hstat-lbl">Neutral</div></div>
      </div>
      <div class="hstat">
        <span class="hstat-icon" style="background:#fbbf24;box-shadow:0 0 8px #fbbf2444;"></span>
        <div><div class="hstat-val" style="color:#fbbf24;">{len(kpis_config)}</div><div class="hstat-lbl">Indicators</div></div>
      </div>
    </div>
  </div>
</header>

<div class="page-wrap">
  {stale_banner}
  {ticker_html}
  {macro_html}
  {sections_html}
  {events_html}
  <footer class="dash-footer">
    <p>Data sourced exclusively from primary official releases: RBI &middot; MoSPI/NSO &middot; NPCI &middot; SIAM &middot; CEA &middot; DGCA &middot; CGA &middot; Ministry of Commerce &middot; Ministry of Finance</p>
    <p>Live market prices: Yahoo Finance (free, no key). Geopolitical feed: Spectator Index RSS. Signals: deterministic rule-based thresholds &mdash; no AI guessing.</p>
    <p>Commentary is analytical only and does not constitute investment advice &middot; Confidence: HIGH = official primary source &middot; MEDIUM = secondary official &middot; LOW = proxy</p>
  </footer>
</div>
</body>
</html>"""

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html, encoding="utf-8")
        logger.info("Dashboard saved: %s", output_path)

    return html
