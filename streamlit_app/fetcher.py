# streamlit_app/fetcher.py — handles API requests (Render-compatible)

import requests
import pandas as pd

import os
API_BASE = os.getenv("BACKEND_URL", "https://quantaira-render.onrender.com")

def fetch_data(hours=24, patient_id=None, metric=None):
    """Fetch vitals from backend with optional filters."""
    params = {"hours": hours}
    if patient_id:
        params["patient_id"] = patient_id
    if metric:
        params["metric"] = metric

    try:
        r = requests.get(f"{API_BASE}/vitals", params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("items", [])
        return pd.DataFrame(data)
    except Exception as e:
        print("Fetch error:", e)
        return pd.DataFrame()
