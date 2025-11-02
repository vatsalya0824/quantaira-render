# streamlit_app/common.py — shared helpers for Patient.py & Home.py

import pandas as pd
from datetime import datetime
import pytz

# ─────────────────────────────────────────────
# Pick the most relevant timestamp column
# ─────────────────────────────────────────────
def best_ts_col(df: pd.DataFrame):
    """Return the best timestamp column name if present."""
    for c in ["timestamp_utc", "timestamp", "time", "datetime"]:
        if c in df.columns:
            return c
    return None

# ─────────────────────────────────────────────
# Convert UTC timestamps → local timezone
# ─────────────────────────────────────────────
def convert_tz(ts_col, tz_name="UTC"):
    """Convert a Series or list of UTC timestamps into the chosen timezone."""
    if ts_col is None:
        return []
    try:
        tz = pytz.timezone(tz_name)
        return pd.to_datetime(ts_col, utc=True, errors="coerce").dt.tz_convert(tz)
    except Exception:
        return pd.to_datetime(ts_col, errors="coerce")

# ─────────────────────────────────────────────
# Split combined BP into systolic/diastolic rows
# ─────────────────────────────────────────────
def split_blood_pressure(df: pd.DataFrame):
    """
    If a 'blood_pressure' or combined BP metric appears (e.g. '120/80'),
    split it into two metrics: systolic_bp and diastolic_bp.
    """
    if df is None or df.empty:
        return df

    df = df.copy()
    new_rows = []
    for _, row in df.iterrows():
        metric = str(row.get("metric", "")).lower()
        val = row.get("value")
        if "blood" in metric and isinstance(val, str) and "/" in val:
            parts = val.split("/")
            if len(parts) == 2:
                try:
                    systolic = float(parts[0])
                    diastolic = float(parts[1])
                    for name, v in [("systolic_bp", systolic), ("diastolic_bp", diastolic)]:
                        nr = row.copy()
                        nr["metric"] = name
                        nr["value"] = v
                        new_rows.append(nr)
                except Exception:
                    pass
        else:
            new_rows.append(row)
    return pd.DataFrame(new_rows)
