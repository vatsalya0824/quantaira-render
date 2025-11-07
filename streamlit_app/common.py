# streamlit_app/common.py — shared helpers
import pandas as pd
import pytz

def best_ts_col(df: pd.DataFrame):
    for c in ["timestamp_utc", "timestamp", "time", "datetime"]:
        if c in df.columns:
            return c
    return None

def convert_tz(ts_col, tz_name="UTC"):
    if ts_col is None:
        return []
    try:
        return pd.to_datetime(ts_col, utc=True, errors="coerce").dt.tz_convert(pytz.timezone(tz_name))
    except Exception:
        return pd.to_datetime(ts_col, errors="coerce")

def split_blood_pressure(df: pd.DataFrame):
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
