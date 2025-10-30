# streamlit_app/fetcher.py — handles API requests (Render-compatible)

import requests
import pandas as pd

API_BASE = "https://hurtlingly-insurable-crysta.ngrok-free.dev"  # replace with your Render backend URL

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