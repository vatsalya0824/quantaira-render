# streamlit_app/fetcher.py
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

# If used inside Streamlit, we'll optionally cache
try:
    import streamlit as st
    cache_fn = st.cache_data
except Exception:  # running outside Streamlit
    def cache_fn(*args, **kwargs):
        def deco(f): return f
        return deco

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
def _backend_base() -> str:
    """
    Resolve backend base URL from:
      1) env var BACKEND_URL
      2) st.secrets['BACKEND_URL']
    Raises if not found.
    """
    env_url = os.getenv("BACKEND_URL")
    if env_url and env_url.strip():
        base = env_url.strip()
    else:
        try:
            base = st.secrets.get("BACKEND_URL", "").strip()  # type: ignore[name-defined]
        except Exception:
            base = ""
    if not base:
        raise RuntimeError(
            "BACKEND_URL not configured. Set env var BACKEND_URL or add it to .streamlit/secrets.toml"
        )
    return base[:-1] if base.endswith("/") else base


_session = requests.Session()
_DEFAULT_TIMEOUT = 12  # seconds
_MAX_RETRIES = 3


def _request_json(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Any:
    """
    Small JSON helper with retries + exponential backoff.
    """
    url = f"{_backend_base()}{path}"
    last_err: Optional[Exception] = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = _session.request(method, url, params=params, timeout=timeout)
            resp.raise_for_status()
            # Some providers return text/plain JSON; force parse
            return resp.json()
        except Exception as e:
            last_err = e
            if attempt == _MAX_RETRIES:
                break
            time.sleep(0.8 * attempt)  # backoff
    # If we got here, we failed
    raise RuntimeError(f"Request failed for {url}: {last_err}")


# ─────────────────────────────────────────────
# Public API used by your Streamlit pages
# ─────────────────────────────────────────────
@cache_fn(ttl=20)
def fetch_patients() -> pd.DataFrame:
    """
    GET /patients  → DataFrame with columns like: id, name, age, gender
    """
    data = _request_json("GET", "/patients")
    if not isinstance(data, list):
        data = []
    df = pd.DataFrame(data)
    # normalize types a bit
    if "id" in df.columns:
        df["id"] = df["id"].astype(str)
    return df


@cache_fn(ttl=8)  # vitals change more often
def fetch_data(hours: int = 24, patient_id: str = "todd") -> pd.DataFrame:
    """
    GET /api/v1/vitals?hours=H&patient_id=PID
    Expected schema from backend:
      - timestamp_utc: ISO8601 UTC string
      - metric: "pulse" | "spo2" | "blood_pressure" | "pillbox_opened"
      - value: number for pulse/spo2; "SYS/DIA" string for blood_pressure; 1 for pill events
    Returns a tidy DataFrame with a parsed UTC timestamp column `timestamp_utc`.
    """
    params = {"hours": int(hours), "patient_id": str(patient_id)}
    data = _request_json("GET", "/api/v1/vitals", params=params)

    if not isinstance(data, list) or not data:
        return pd.DataFrame(columns=["timestamp_utc", "metric", "value"])

    df = pd.DataFrame(data)

    # Ensure expected columns exist
    for col in ("timestamp_utc", "metric", "value"):
        if col not in df.columns:
            df[col] = pd.NA

    # Parse timestamps as UTC (errors → NaT, filtered out by caller if needed)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")

    # Light normalization
    df["metric"] = df["metric"].astype(str).str.strip().str.lower()

    return df


@cache_fn(ttl=10)
def health() -> Dict[str, Any]:
    """GET /health for quick diagnostics."""
    try:
        return _request_json("GET", "/health")
    except Exception as e:
        return {"status": "error", "detail": str(e)}
