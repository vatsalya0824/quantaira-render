# pages/Patient.py — iOS teal pills + green-above / yellow-normal / red-below
# Render-ready version

from datetime import datetime
import json
from importlib import reload
from string import Template

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html
import requests

from fetcher import fetch_data
import common
common = reload(common)
from common import best_ts_col, convert_tz

# ─────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────
st.set_page_config(page_title="Patient Detail", layout="wide")
BUILD_TAG = "patient-ios-teal-render v1"
st.markdown(
    f"<div style='opacity:.45;font:12px/1.2 ui-sans-serif,system-ui'>build {BUILD_TAG}</div>",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────
# Constants / colors
# ─────────────────────────────────────────────
P = {
    "bg": "#F6FBFD", "ink": "#0F172A", "muted": "#667085",
    "chip": "#F3F6F8", "chipBrd": "rgba(2,6,23,.08)",
    "tealA": "#48C9C3", "tealB": "#3FB7B2", "glow": "rgba(68,194,189,.32)",
    "segGreen": "#10B981", "segYellow": "#FACC15", "segRed": "#EF4444",
    "refLine": "rgba(15,23,42,.45)",
    "pillDot": "#0F172A", "mealDot": "#f472b6", "noteDot": "#14b8a6"
}
UNITS = {"pulse": "bpm", "systolic_bp": "mmHg", "diastolic_bp": "mmHg", "spo2": "%"}

# ─────────────────────────────────────────────
# Session state setup
# ─────────────────────────────────────────────
def _get_param(key: str, default: str):
    try:
        qp = st.query_params
        if key in qp and qp[key]: return qp[key]
    except Exception:
        pass
    return st.session_state.get(key, default)

pid  = str(_get_param("pid", "todd"))
name = str(_get_param("name", "Patient"))

if "win" not in st.session_state: st.session_state.win = "24h"
if "metric_sel" not in st.session_state: st.session_state.metric_sel = "pulse"
HOURS_LOOKUP = {"24h": 24, "3d": 72, "7d": 7*24, "30d": 30*24}

# ─────────────────────────────────────────────
# Sidebar controls
# ─────────────────────────────────────────────
st.sidebar.header("Settings")
tz_choice   = st.sidebar.selectbox("Timezone", ["UTC","America/New_York","Europe/London","Asia/Kolkata"], index=0, key="tz_sel")
line_w      = st.sidebar.slider("Line width", 1, 6, 4)
marker_size = st.sidebar.slider("Marker size (dots)", 6, 20, 10)
show_ref    = st.sidebar.checkbox("Show LSL/USL dashed lines", True)

# ─────────────────────────────────────────────
# CSS — iOS teal styling
# ─────────────────────────────────────────────
st.markdown(f"""
<style>
  .stApp{{background:{P['bg']};color:{P['ink']};}}
  section[data-testid="stSidebar"]{{ background:#ECF7F6; border-right:1px solid rgba(2,6,23,.06); }}
  .h-title{{font-weight:900;font-size:34px;margin:2px 0;color:{P['ink']};}}
  .h-sub{{color:{P['muted']};margin:0 0 12px;}}
  .pillrow{{ display:flex; gap:12px; flex-wrap:wrap; align-items:center; margin:6px 0 14px; }}
  .pillrow .stButton{{ margin:0 !important; }}
  .stButton > button {{
    appearance:none !important;
    border:1px solid {P['chipBrd']} !important;
    background:{P['chip']} !important;
    color:{P['ink']} !important;
    border-radius:999px !important;
    padding:12px 20px !important;
    font-weight:900 !important; font-size:15px !important; line-height:1 !important;
    box-shadow:0 10px 24px rgba(17,24,39,.08) !important;
    transition:transform .18s, box-shadow .22s ease, filter .18s linear;
  }}
  .stButton > button:hover {{ transform:translateY(-2px); filter:brightness(.99);
    box-shadow:0 14px 30px rgba(17,24,39,.10) !important; }}
  .stButton > button:active {{ transform:translateY(0); box-shadow:0 8px 16px rgba(17,24,39,.10) !important; }}
  .chart-wrap{{background:#fff;border-radius:18px;padding:12px 14px;box-shadow:0 18px 44px rgba(17,24,39,.10)}}
  .stats{{background:#fff;border-radius:14px;padding:12px 14px;box-shadow:0 10px 26px rgba(0,0,0,.08);
         width:260px;font-size:13px;color:#374151}}
  .stats h4{{margin:0 0 6px;font-weight:800;font-size:14px;color:{P['ink']}}}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────
st.markdown(f"<div class='h-title'>{name}</div>", unsafe_allow_html=True)
st.markdown("<div class='h-sub'>Green = Above USL • Yellow = Normal • Red = Below LSL</div>", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# Time + metric pills
# ─────────────────────────────────────────────
st.markdown('<div class="pillrow">', unsafe_allow_html=True)
for lbl in ["24h", "3d", "7d", "30d"]:
    if st.button(lbl, key=f"tw_{lbl}"):
        st.session_state.win = lbl
st.markdown('</div>', unsafe_allow_html=True)

METRIC_LABELS = {
    "pulse": "Heart Rate",
    "systolic_bp": "Systolic BP",
    "diastolic_bp": "Diastolic BP",
    "spo2": "SpO₂",
}
st.markdown('<div class="pillrow">', unsafe_allow_html=True)
for m, label in METRIC_LABELS.items():
    if st.button(label, key=f"metric_{m}"):
        st.session_state.metric_sel = m
st.markdown('</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────
# Load & prepare data
# ─────────────────────────────────────────────
def load_window(hours: int) -> pd.DataFrame:
    data = fetch_data(hours)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    if df.empty:
        return df
    ts_col = best_ts_col(df) or "timestamp_utc"
    df["timestamp_utc"] = pd.to_datetime(df[ts_col], utc=True, errors="coerce")
    df["local_time"] = convert_tz(df["timestamp_utc"], tz_choice)
    return df.dropna(subset=["timestamp_utc"])

raw = load_window(HOURS_LOOKUP[st.session_state.win])
metric = st.session_state.metric_sel

if raw.empty:
    st.info("No data available.")
    st.stop()

# ─────────────────────────────────────────────
# Compute stats and chart
# ─────────────────────────────────────────────
df_metric = raw[raw["metric"].str.lower() == metric.lower()]
if df_metric.empty:
    st.info(f"No {metric} data.")
    st.stop()

x = df_metric["local_time"]
y = pd.to_numeric(df_metric["value"], errors="coerce")

lsl = float(y.mean() - 0.5 * y.std()) if not y.empty else None
usl = float(y.mean() + 0.5 * y.std()) if not y.empty else None

labels = [pd.to_datetime(t).strftime("%b %d %H:%M") for t in x]
values = [None if pd.isna(v) else float(v) for v in y]

html = f"""
<div class="chart-wrap" style="height:460px"><canvas id="chart"></canvas></div>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
  const ctx = document.getElementById('chart').getContext('2d');
  const data = {{
    labels: {json.dumps(labels)},
    datasets: [{{
      label: '{METRIC_LABELS.get(metric, metric)}',
      data: {json.dumps(values)},
      borderColor: '{P['tealA']}',
      backgroundColor: 'rgba(72,201,195,0.15)',
      tension: 0.5,
      borderWidth: {int(line_w)}
    }}]
  }};
  const options = {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: 'rgba(0,0,0,0.05)' }} }},
      y: {{ grid: {{ color: 'rgba(0,0,0,0.05)' }} }}
    }}
  }};
  new Chart(ctx, {{ type: 'line', data, options }});
</script>
"""
st_html(html, height=460, scrolling=False)

# ─────────────────────────────────────────────
# Stats summary
# ─────────────────────────────────────────────
st.markdown("### Stats Summary")
s = y.dropna()
if not s.empty:
    latest = f"{float(s.iloc[-1]):.1f} {UNITS.get(metric,'')}"
    mean = f"{float(s.mean()):.1f}"
    std = f"{float(s.std()):.1f}"
    vmin = f"{float(s.min()):.1f}"
    vmax = f"{float(s.max()):.1f}"
    st.markdown(f"""
    <div class='stats'>
      <h4>Stats</h4>
      <div><b>LSL / USL:</b> {lsl:.1f if lsl else 0} / {usl:.1f if usl else 0}</div>
      <div><b>Latest:</b> {latest}</div>
      <div>μ Mean: <b>{mean}</b></div>
      <div>σ Std: <b>{std}</b></div>
      <div>Min: <b>{vmin}</b></div>
      <div>Max: <b>{vmax}</b></div>
    </div>
    """, unsafe_allow_html=True)
else:
    st.info("No valid numeric data.")