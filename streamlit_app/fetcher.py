# streamlit_app/fetcher.py
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import pandas as pd
import requests
import streamlit as st

# ─────────────────────────────────────────────
# Config: where is the backend?
# Priority: env BACKEND_URL → st.secrets["BACKEND_URL"] → localhost
# Example: BACKEND_URL="https://quantaira-backend.onrender.com"
# ─────────────────────────────────────────────
def _get_base_url() -> str:
    url = (
        os.getenv("BACKEND_URL")
        or (st.secrets.get("BACKEND_URL") if hasattr(st, "secrets") else None)
        or "http://localhost:8000"
    )
    return url.rstrip("/")

BASE_URL = _get_base_url()

# Toggle to allow local demo if backend is down (optional).
_FAKE_MODE = os.getenv("FAKE_MODE", "").lower() in {"1", "true", "yes"}

# ─────────────────────────────────────────────
# HTTP client helpers (retries + timeouts)
# ─────────────────────────────────────────────
_DEFAULT_TIMEOUT = 20

def _url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return BASE_URL + path

def _request_json(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    retries: int = 2,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Any:
    """
    Issue an HTTP request and return r.json().
    Retries a couple of times on transient 5xx/connection errors.
    Raises on final failure.
    """
    session = requests.Session()
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = session.request(
                method.upper(),
                _url(path),
                params=params,
                json=json,
                timeout=timeout,
            )
            # Raise on HTTP 4xx/5xx to enter except branch
            resp.raise_for_status()
            # Try to parse JSON; if it fails, raise a clean error
            try:
                return resp.json()
            except ValueError as e:
                raise RuntimeError(f"{method} {_url(path)} did not return JSON") from e
        except (requests.RequestException, RuntimeError) as e:
            last_exc = e
            # Only retry on the first N attempts
            if attempt < retries:
                # small backoff
                time.sleep(0.6 * (attempt + 1))
                continue
            # Exhausted
            break
    # If we're here, we failed
    if _FAKE_MODE and path.strip("/") in {"patients", "vitals"}:
        return _fake_response(path, params or {})
    raise last_exc  # type: ignore[misc]

# ─────────────────────────────────────────────
# Cache wrapper (Streamlit 1.20+)
# ─────────────────────────────────────────────
def cache_fn(ttl: int = 20):
    def deco(fn):
        return st.cache_data(show_spinner=False, ttl=ttl)(fn)
    return deco

# ─────────────────────────────────────────────
# Public API used by the Streamlit pages
# ─────────────────────────────────────────────
@cache_fn(ttl=20)
def fetch_patients() -> pd.DataFrame:
    """
    GET /patients  → returns a list[dict]
    Frontend expects a DataFrame with at least: id, name
    """
    data = _request_json("GET", "/patients")
    if not isinstance(data, list):
        # Old shapes like {"items":[...]} are tolerated
        if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
            data = data["items"]
        else:
            data = []
    df = pd.DataFrame(data)
    if "id" in df.columns:
        df["id"] = df["id"].astype(str)
    return df


@cache_fn(ttl=8)
def fetch_data(*, hours: int = 24, patient_id: Optional[str] = None) -> pd.DataFrame:
    """
    GET /vitals?hours=H[&patient_id=X]
    Returns a list of measurements. We normalize to columns:
      - timestamp_utc (ISO string or pandas ts)
      - metric        (str)
      - value         (numeric/str)
    Optional pass-through columns (if backend provides them):
      - device_name, value_1, value_2, unit, source
    """
    params: Dict[str, Any] = {"hours": int(hours)}
    if patient_id:
        params["patient_id"] = str(patient_id)

    data = _request_json("GET", "/vitals", params=params)

    # Accept list or dict{"items":[...]}
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        data = data["items"]
    if not isinstance(data, list):
        data = []

    df = pd.DataFrame(data)

    # Normalize critical columns
    if "timestamp_utc" not in df.columns:
        # try common alternatives
        for cand in ("ts", "timestamp", "time_utc", "created_at_utc"):
            if cand in df.columns:
                df["timestamp_utc"] = df[cand]
                break
    # Ensure datetime (UTC)
    if "timestamp_utc" in df.columns:
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")

    if "metric" in df.columns:
        df["metric"] = df["metric"].astype(str).str.strip().str.lower()

    # If backend sends nested BP (e.g., "120/80" in 'value'), that's handled later
    # by common.split_blood_pressure(). Here we just keep 'value' raw and try numeric fallback.
    if "value" in df.columns:
        # Keep original, but also try numeric where possible; pages can re-cast as needed
        df["value"] = df["value"]

    # Keep only rows with timestamp
    if "timestamp_utc" in df.columns:
        df = df.dropna(subset=["timestamp_utc"]).sort_values("timestamp_utc")

    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
# Optional: tiny fake data for local demos (when FAKE_MODE=1)
# ─────────────────────────────────────────────
def _fake_response(path: str, params: Dict[str, Any]) -> Any:
    if path.strip("/") == "patients":
        return [
            {"id": "todd", "name": "Todd Carter", "age": 47, "gender": "Male"},
            {"id": "jane", "name": "Jane Wilson", "age": 53, "gender": "Female"},
        ]
    if path.strip("/") == "vitals":
        import numpy as np
        from datetime import datetime, timedelta, timezone
        hours = int(params.get("hours", 24))
        pid = params.get("patient_id") or "todd"
        now = datetime.now(timezone.utc)
        ts = [now - timedelta(minutes=15 * i) for i in range(hours * 4)]
        ts = list(reversed(ts))
        hr = 72 + 8 * np.sin(np.linspace(0, 8, len(ts)))
        spo2 = 97 + np.sin(np.linspace(0, 6, len(ts))) * 0.6
        sbp = 120 + 10 * np.sin(np.linspace(0, 5, len(ts)))
        dbp = 78 + 6 * np.cos(np.linspace(0, 5, len(ts)))
        out = []
        for i, t in enumerate(ts):
            out.append({"patient_id": pid, "timestamp_utc": t.isoformat(), "metric": "pulse", "value": float(hr[i])})
            out.append({"patient_id": pid, "timestamp_utc": t.isoformat(), "metric": "spo2", "value": float(spo2[i])})
            # Some backends may send a combined BP string; we’ll send split metrics here
            out.append({"patient_id": pid, "timestamp_utc": t.isoformat(), "metric": "systolic_bp", "value": float(sbp[i])})
            out.append({"patient_id": pid, "timestamp_utc": t.isoformat(), "metric": "diastolic_bp", "value": float(dbp[i])})
        return out
    return {"ok": False, "error": "unknown path in fake mode"}


# ─────────────────────────────────────────────
# Simple health check (optional)
# ─────────────────────────────────────────────
@cache_fn(ttl=10)
def backend_health() -> dict:
    try:
        data = _request_json("GET", "/")
        return {"ok": True, "base_url": BASE_URL, "data": data}
    except Exception as e:
        return {"ok": False, "base_url": BASE_URL, "error": str(e)}
