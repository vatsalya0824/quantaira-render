import os, requests, pandas as pd

API_BASE = os.getenv("BACKEND_URL", "http://localhost:8000")  # Render injects BACKEND_URL

def _get(path, **kwargs):
    r = requests.get(f"{API_BASE}{path}", timeout=15, **kwargs)
    r.raise_for_status()
    return r.json()

def _post(path, json):
    r = requests.post(f"{API_BASE}{path}", json=json, timeout=15)
    r.raise_for_status()
    return r.json()

# -------- Vitals --------
def fetch_data(hours=24, patient_id=None, metric=None, limit=1000) -> pd.DataFrame:
    params = {"hours": hours, "limit": limit}
    if patient_id: params["patient_id"] = patient_id
    if metric: params["metric"] = metric
    try:
        j = _get("/vitals", params=params)
        return pd.DataFrame(j.get("items", []))
    except Exception:
        return pd.DataFrame()

# -------- Patients --------
def fetch_patients():
    try:
        return _get("/patients").get("patients", [])
    except Exception:
        return []

# -------- Meals --------
def fetch_meals(patient_id: str) -> pd.DataFrame:
    try:
        j = _get("/meals", params={"patient_id": patient_id})
        return pd.DataFrame(j.get("items", []))
    except Exception:
        return pd.DataFrame()

def add_meal(patient_id: str, **kwargs):
    payload = {"patient_id": patient_id, **kwargs}
    return _post("/meals", payload)

# -------- Notes --------
def fetch_notes(patient_id: str) -> pd.DataFrame:
    try:
        j = _get("/notes", params={"patient_id": patient_id})
        return pd.DataFrame(j.get("items", []))
    except Exception:
        return pd.DataFrame()

def add_note(patient_id: str, note: str, timestamp_utc=None):
    payload = {"patient_id": patient_id, "note": note}
    if timestamp_utc: payload["timestamp_utc"] = timestamp_utc
    return _post("/notes", payload)

# -------- Limits --------
def fetch_limits(patient_id: str | None = None) -> pd.DataFrame:
    try:
        params = {"patient_id": patient_id} if patient_id else {}
        j = _get("/limits", params=params)
        return pd.DataFrame(j.get("items", []))
    except Exception:
        return pd.DataFrame()

def set_limit(metric: str, lsl: float | None, usl: float | None, patient_id: str | None = None):
    payload = {"metric": metric, "lsl": lsl, "usl": usl}
    if patient_id: payload["patient_id"] = patient_id
    return _post("/limits", payload)
