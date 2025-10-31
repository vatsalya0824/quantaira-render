# pages/Patient.py — Vitals + Meals + Notes + Limits (iOS teal, Render-ready)

from __future__ import annotations
import json
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

from fetcher import (
    fetch_data, fetch_meals, add_meal,
    fetch_notes, add_note,
    fetch_limits, set_limit
)
import common
from importlib import reload
common = reload(common)
from common import best_ts_col, convert_tz

st.set_page_config(page_title="Patient Detail", layout="wide")
BUILD_TAG = "patient-ios-teal-tabs v1"
st.markdown(
    f"<div style='opacity:.45;font:12px/1.2 ui-sans-serif,system-ui'>build {BUILD_TAG}</div>",
    unsafe_allow_html=True,
)

# ---------- Colors / labels ----------
P = {
    "bg": "#F6FBFD", "ink": "#0F172A", "muted": "#667085",
    "chip": "#F3F6F8", "chipBrd": "rgba(2,6,23,.08)",
    "tealA": "#48C9C3", "segGreen": "#10B981", "segYellow": "#FACC15", "segRed": "#EF4444",
}
UNITS = {"pulse": "bpm", "systolic_bp": "mmHg", "diastolic_bp": "mmHg", "spo2": "%"}
METRIC_LABELS = {"pulse": "Heart Rate", "systolic_bp": "Systolic BP", "diastolic_bp": "Diastolic BP", "spo2": "SpO₂"}
HOURS_LOOKUP = {"24h": 24, "3d": 72, "7d": 7*24, "30d": 30*24}

# ---------- Params / state ----------
def _qp(key: str, default: str = "") -> str:
    try:
        qp = st.query_params
        v = qp.get(key)
        if isinstance(v, list): return (v[0] if v else default)
        return v if v is not None else default
    except Exception:
        return default

pid  = (_qp("pid", "todd") or "todd").strip().lower()
name = (_qp("name", pid) or "Patient").strip()

if "win" not in st.session_state: st.session_state.win = "24h"
if "metric_sel" not in st.session_state: st.session_state.metric_sel = "pulse"

# ---------- Sidebar ----------
st.sidebar.header("Settings")
tz_choice   = st.sidebar.selectbox("Timezone", ["UTC","America/New_York","Europe/London","Asia/Kolkata"], index=0, key="tz_sel")
line_w      = st.sidebar.slider("Line width", 1, 6, 4)
marker_size = st.sidebar.slider("Marker size (dots)", 6, 20, 10)
show_ref    = st.sidebar.checkbox("Show LSL/USL dashed lines", True)

st.markdown(f"""
<style>
  .stApp{{background:{P['bg']};color:{P['ink']};}}
  section[data-testid="stSidebar"]{{ background:#ECF7F6; border-right:1px solid rgba(2,6,23,.06); }}
  .h-title{{font-weight:900;font-size:34px;margin:2px 0;color:{P['ink']};}}
  .h-sub{{color:{P['muted']};margin:0 0 12px;}}
  .pillrow{{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin:6px 0 14px; }}
  .stButton > button {{
    border:1px solid {P['chipBrd']} !important;
    background:{P['chip']} !important;
    color:{P['ink']} !important;
    border-radius:999px !important;
    padding:12px 20px !important;
    font-weight:900 !important; font-size:15px !important;
    box-shadow:0 10px 24px rgba(17,24,39,.08) !important;
    transition:transform .18s, box-shadow .22s ease, filter .18s linear;
  }}
  .chart-wrap{{background:#fff;border-radius:18px;padding:12px 14px;box-shadow:0 18px 44px rgba(17,24,39,.10)}}
  .stats{{background:#fff;border-radius:14px;padding:12px 14px;box-shadow:0 10px 26px rgba(0,0,0,.08); width:280px}}
  .data-card{{background:#fff;border-radius:14px;padding:14px;box-shadow:0 10px 26px rgba(0,0,0,.08)}}
</style>
""", unsafe_allow_html=True)

st.markdown(f"<div class='h-title'>{name}</div>", unsafe_allow_html=True)
st.markdown("<div class='h-sub'>Green ≥ USL • Yellow normal • Red < LSL</div>", unsafe_allow_html=True)

# ---------- Tabs ----------
tab_vitals, tab_meals, tab_notes, tab_limits = st.tabs(["Vitals", "Meals", "Notes", "Limits"])

# =============================================================================
# VITALS TAB
# =============================================================================
with tab_vitals:
    c1, c2 = st.columns([3,2])
    with c1:
        st.markdown('<div class="pillrow">', unsafe_allow_html=True)
        for lbl in ["24h", "3d", "7d", "30d"]:
            if st.button(lbl, key=f"tw_{lbl}"):
                st.session_state.win = lbl
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="pillrow">', unsafe_allow_html=True)
        for m, label in METRIC_LABELS.items():
            if st.button(label, key=f"metric_{m}"):
                st.session_state.metric_sel = m
        st.markdown('</div>', unsafe_allow_html=True)

        hours = HOURS_LOOKUP[st.session_state.win]
        metric = st.session_state.metric_sel

        # Load vitals for patient
        df = fetch_data(hours=hours, patient_id=pid)
        if df.empty:
            st.info("No vitals yet.")
        else:
            ts_col = best_ts_col(df) or "timestamp_utc"
            df["timestamp_utc"] = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
            df["local_time"] = convert_tz(df["timestamp_utc"], tz_choice)
            d = df[df["metric"].str.lower() == metric.lower()].copy()

            if d.empty:
                st.info(f"No {metric} measurements in this window.")
            else:
                d["value"] = pd.to_numeric(d["value"], errors="coerce")
                d = d.dropna(subset=["value", "local_time"])

                y = d["value"]
                if y.empty:
                    st.info("No numeric values to plot.")
                else:
                    mu = float(y.mean()); sd = float(y.std(ddof=0))
                    lsl = mu - 0.5*sd; usl = mu + 0.5*sd

                    labels = [pd.to_datetime(t).strftime("%b %d %H:%M") for t in d["local_time"]]
                    values = [float(v) for v in y]

                    colors=[]
                    for v in values:
                        if v < lsl: colors.append(P["segRed"])
                        elif v >= usl: colors.append(P["segGreen"])
                        else: colors.append(P["segYellow"])

                    ref_js = ""
                    if show_ref:
                        ref_js = f"""
                        const refPlugin = {{
                          id: 'refPlugin',
                          afterDraw(chart) {{
                            const {{ctx, chartArea:{{top,bottom,left,right}}, scales:{{y}}}} = chart;
                            ctx.save(); ctx.setLineDash([6,6]); ctx.strokeStyle='rgba(15,23,42,.45)';
                            let yL = y.getPixelForValue({lsl}); ctx.beginPath(); ctx.moveTo(left,yL); ctx.lineTo(right,yL); ctx.stroke();
                            let yU = y.getPixelForValue({usl}); ctx.beginPath(); ctx.moveTo(left,yU); ctx.lineTo(right,yU); ctx.stroke();
                            ctx.restore();
                          }}
                        }};
                        """
                    else:
                        ref_js = "const refPlugin={id:'refPlugin',afterDraw(){}};"

                    html = f"""
                    <div class="chart-wrap" style="height:460px"><canvas id="chart"></canvas></div>
                    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
                    <script>
                      {ref_js}
                      const ctx = document.getElementById('chart').getContext('2d');
                      const data = {{
                        labels: {json.dumps(labels)},
                        datasets: [{{
                          label: '{METRIC_LABELS.get(metric, metric)}',
                          data: {json.dumps(values)},
                          borderColor: '{P['tealA']}',
                          backgroundColor: 'rgba(72,201,195,0.15)',
                          pointBackgroundColor: {json.dumps(colors)},
                          pointBorderColor: {json.dumps(colors)},
                          pointRadius: {int(marker_size)},
                          tension: 0.5,
                          borderWidth: {int(line_w)}
                        }}]
                      }};
                      const options = {{
                        responsive: true, maintainAspectRatio: false,
                        plugins: {{ legend: {{ display:false }} }},
                        scales: {{
                          x: {{ grid: {{ color:'rgba(0,0,0,.05)'}} }},
                          y: {{ grid: {{ color:'rgba(0,0,0,.05)'}} }}
                        }}
                      }};
                      new Chart(ctx, {{ type:'line', data, options, plugins:[refPlugin] }});
                    </script>
                    """
                    st_html(html, height=460, scrolling=False)

    with c2:
        st.markdown("### Stats")
        df_stats = fetch_data(hours=HOURS_LOOKUP[st.session_state.win], patient_id=pid, metric=st.session_state.metric_sel)
        if not df_stats.empty:
            df_stats["value"] = pd.to_numeric(df_stats["value"], errors="coerce")
            s = df_stats["value"].dropna()
            if not s.empty:
                st.markdown(
                    f"""
                    <div class='stats'>
                    <div><b>Latest:</b> {s.iloc[-1]:.1f} {UNITS.get(st.session_state.metric_sel,'')}</div>
                    <div><b>Mean:</b> {s.mean():.1f}</div>
                    <div><b>Std:</b> {s.std(ddof=0):.1f}</div>
                    <div><b>Min:</b> {s.min():.1f}</div>
                    <div><b>Max:</b> {s.max():.1f}</div>
                    </div>
                    """, unsafe_allow_html=True
                )
        st.divider()
        st.markdown("#### Recent points")
        if not df_stats.empty:
            st.dataframe(
                df_stats[["timestamp_utc","value","device_name","gateway_norm"]]
                .rename(columns={"timestamp_utc":"UTC time","value":"Value","device_name":"Device","gateway_norm":"Gateway"})
                .tail(20),
                use_container_width=True, height=260
            )

# =============================================================================
# MEALS TAB
# =============================================================================
with tab_meals:
    st.markdown("### Meals")
    dfm = fetch_meals(pid)
    if dfm.empty:
        st.info("No meals logged.")
    else:
        st.dataframe(
            dfm.rename(columns={
                "timestamp_utc":"UTC time","food":"Food","kcal":"kcal",
                "protein_g":"Protein (g)","carbs_g":"Carbs (g)","fat_g":"Fat (g)","sodium_mg":"Sodium (mg)","fdc_id":"USDA FDC"
            }),
            use_container_width=True, height=360
        )

    with st.expander("➕ Add meal"):
        c1,c2,c3,c4 = st.columns(4)
        food = c1.text_input("Food", placeholder="Chicken salad")
        kcal = c2.number_input("kcal", min_value=0, step=10)
        protein_g = c3.number_input("Protein (g)", min_value=0.0, step=0.5)
        carbs_g   = c4.number_input("Carbs (g)", min_value=0.0, step=0.5)
        fat_g     = st.number_input("Fat (g)", min_value=0.0, step=0.5)
        sodium_mg = st.number_input("Sodium (mg)", min_value=0, step=10)
        fdc_id    = st.text_input("USDA FDC ID (optional)")
        if st.button("Save meal"):
            try:
                add_meal(pid,
                         food=food or None, kcal=int(kcal) if kcal else None,
                         protein_g=float(protein_g) if protein_g else None,
                         carbs_g=float(carbs_g) if carbs_g else None,
                         fat_g=float(fat_g) if fat_g else None,
                         sodium_mg=int(sodium_mg) if sodium_mg else None,
                         fdc_id=fdc_id or None)
                st.success("Meal saved. Refreshing…")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to save meal: {e}")

# =============================================================================
# NOTES TAB
# =============================================================================
with tab_notes:
    st.markdown("### Notes")
    dfn = fetch_notes(pid)
    if dfn.empty:
        st.info("No notes yet.")
    else:
        st.dataframe(
            dfn.rename(columns={"timestamp_utc":"UTC time","note":"Note"}),
            use_container_width=True, height=360
        )

    with st.expander("📝 Add note"):
        note_txt = st.text_area("Note", placeholder="Patient reported a morning walk of 30 minutes…")
        if st.button("Save note"):
            if not note_txt.strip():
                st.warning("Please enter a note.")
            else:
                try:
                    add_note(pid, note_txt.strip())
                    st.success("Note saved. Refreshing…")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to save note: {e}")

# =============================================================================
# LIMITS TAB
# =============================================================================
with tab_limits:
    st.markdown("### Limits (LSL/USL)")
    dfl = fetch_limits(pid)
    if dfl.empty:
        st.info("No patient-specific limits. You can add some below.")
    else:
        st.dataframe(
            dfl.rename(columns={"patient_id":"Patient","metric":"Metric","lsl":"LSL","usl":"USL"}),
            use_container_width=True, height=320
        )

    st.divider()
    st.markdown("#### Set / Update a limit")
    m = st.selectbox("Metric", list(METRIC_LABELS.keys()), index=0)
    c1, c2 = st.columns(2)
    lsl_in = c1.number_input("LSL", value=0.0, step=0.5, format="%.1f")
    usl_in = c2.number_input("USL", value=0.0, step=0.5, format="%.1f")
    if st.button("Save limit"):
        try:
            set_limit(metric=m, lsl=lsl_in, usl=usl_in, patient_id=pid)
            st.success("Limit saved. Refreshing…")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to save limit: {e}")
