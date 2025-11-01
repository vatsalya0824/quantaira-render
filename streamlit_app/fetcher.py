# streamlit_app/fetcher.py
from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Iterable

import pandas as pd
import requests
import streamlit as st

# ─────────────────────────────────────────────
# Config: where is the backend?
# Priority: env BACKEND_URL → st.secrets["BACKEND_URL"] (if dict-like) → localhost
# Example: BACKEND_URL="https://quantaira-backend.onrender.com"
# ─────────────────────────────────────────────
def _get_base_url() -> str:
    # 1) Prefer plain environment variable (works on Render)
    env_val = os.getenv("BACKEND_URL")

    # 2) Fall back to Streamlit secrets ONLY if it behaves like a dict
    secrets_val = None
    try:
        secrets_obj = getattr(st, "secrets", None)
        if isinstance(secrets_obj, dict) and "BACKEND_URL" in secrets_obj:
            secrets_val = secrets_obj["BACKEND_URL"]
    except Exception:
        secrets_val = None

    url = env_val or secrets_val or "http://localhost:8000"
    return str(url).rstrip("/")

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

def _should_retry(status: Optional[int]) -> bool:
    # Retry on typical transient server/network states
    return status is None or 500 <= status < 600 or status in {408, 429}

def _request_json(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    retries: int = 2,
    timeout: int = _DEFAULT_TIMEOUT,
    extra_ok_statuses: Iterable[int] = (),
) -> Any:
    """
    Issue an HTTP request and return r.json().
    Retries a couple of times on transient 5xx/connection errors.
    Raises on final failure.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "quantaira-dashboard/1.0 (+streamlit)",
            "Accept": "application/json",
        }
    )

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        status_for_retry: Optional[int] = None
        try:
            resp = session.request(
                method.upper(),
                _url(path),
                params=params,
                json=json,
                timeout=timeout,
            )
            status_for_retry = resp.status_code

            if resp.status_code >= 400 and resp.status_code not in set(extra_ok_statuses):
                # Raise to enter except and maybe retry
                resp.raise_for_status()

            # Parse JSON safely
            try:
                return resp.json()
            except ValueError as e:
                raise RuntimeError(f"{method} {_url(path)} did not return JSON") from e

        except (requests.RequestException, RuntimeError) as e:
            last_exc = e
            if attempt < retries and _should_retry(status_for_retry):
                # Exponential-ish backoff
                time.sleep(0.6 * (attempt + 1))
                continue
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
        # Tolerate {"items":[...]} or empty/malformed responses
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
      - timestamp_utc (datetime64[ns, UTC])
      - metric        (str)
      - value         (any; keep raw)
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

    # Normalize timestamp column
    if "timestamp_utc" not in df.columns:
        for cand in ("ts", "timestamp", "time_utc", "created_at_utc"):
            if cand in df.columns:
                df["timestamp_utc"] = df[cand]
                break

    if "timestamp_utc" in df.columns:
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")

    if "metric" in df.columns:
        df["metric"] = df["metric"].astype(str).str.strip().str.lower()

    # Keep value raw; pages can parse/convert (e.g., BP "120/80")
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
            iso = t.isoformat()
            out.append({"patient_id": pid, "timestamp_utc": iso, "metric": "pulse", "value": float(hr[i])})
            out.append({"patient_id": pid, "timestamp_utc": iso, "metric": "spo2", "value": float(spo2[i])})
            out.append({"patient_id": pid, "timestamp_utc": iso, "metric": "systolic_bp", "value": float(sbp[i])})
            out.append({"patient_id": pid, "timestamp_utc": iso, "metric": "diastolic_bp", "value": float(dbp[i])})
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
