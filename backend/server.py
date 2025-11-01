# backend/server.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

APP_NAME = os.getenv("APP_NAME", "quantaira-backend")

app = FastAPI(title="Quantaira Backend", version="1.0.0")

# ─────────────────────────────────────────────
# CORS (open so Streamlit can call us)
# ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten later if you prefer
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# Root & health
# ─────────────────────────────────────────────
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": APP_NAME}

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

# ─────────────────────────────────────────────
# Demo patients (mock data for dashboard)
# ─────────────────────────────────────────────
MOCK_PATIENTS: List[Dict[str, Any]] = [
    {"id": "todd", "name": "Todd Carter", "age": 47, "gender": "Male"},
    {"id": "jane", "name": "Jane Wilson", "age": 53, "gender": "Female"},
    {"id": "alex", "name": "Alex Kim", "age": 29, "gender": "Male"},
]

@app.get("/patients")
def get_patients() -> List[Dict[str, Any]]:
    """
    Return a small list of demo patients. Replace with your DB or Tenovi registry
    later if desired.
    """
    return MOCK_PATIENTS

# ─────────────────────────────────────────────
# Synthetic vitals generator
# ─────────────────────────────────────────────
def _seed_for_patient(pid: str) -> int:
    # cheap, stable per-patient seed
    return sum(ord(c) for c in pid) % 7

def _bp_for_index(i: int, base_sys: int = 120, base_dia: int = 78) -> str:
    # slightly bouncy blood pressure, encoded as "SYS/DIA"
    sys = base_sys + (i % 5) - 2
    dia = base_dia + ((i // 2) % 5) - 2
    return f"{sys}/{dia}"

def _hr_for_index(i: int, base: int = 72) -> int:
    return base + (i % 5) - 2  # 70–74ish

def _spo2_for_index(i: int, base: int = 97) -> int:
    return base - (1 if i % 13 == 0 else 0)

def _make_point(ts: datetime, metric: str, value: Any) -> Dict[str, Any]:
    return {
        "timestamp_utc": ts.replace(tzinfo=timezone.utc).isoformat(),
        "metric": metric,
        "value": value,
    }

# ─────────────────────────────────────────────
# Vitals API (called by fetcher.fetch_data)
# ─────────────────────────────────────────────
@app.get("/api/v1/vitals")
def get_vitals(
    hours: int = Query(24, ge=1, le=24 * 30, description="Window size in hours"),
    patient_id: str = Query("todd", description="Patient id (string)"),
) -> List[Dict[str, Any]]:
    """
    Returns synthetic vitals for the last `hours` hours for `patient_id`.
    Schema matches what your Streamlit app expects:

      - timestamp_utc (ISO8601, UTC)
      - metric: 'pulse' | 'spo2' | 'blood_pressure' | 'pillbox_opened'
      - value:  number for pulse/spo2, 'SYS/DIA' for blood_pressure, 1 for pillbox events
    """
    now = datetime.now(timezone.utc)
    seed = _seed_for_patient(patient_id)
    out: List[Dict[str, Any]] = []

    # generate one sample per hour per metric
    for i in range(hours):
        ts = now - timedelta(hours=hours - i)

        # pulse
        out.append(_make_point(ts, "pulse", _hr_for_index(i + seed)))

        # spo2
        out.append(_make_point(ts, "spo2", _spo2_for_index(i + seed)))

        # blood pressure as "SYS/DIA"
        out.append(_make_point(ts, "blood_pressure", _bp_for_index(i + seed)))

        # sprinkle pillbox opened events ~ every 6 hours
        if (i + seed) % 6 == 0:
            out.append(_make_point(ts, "pillbox_opened", 1))

    # ensure at least one pill event in last few hours
    out.append(_make_point(now - timedelta(hours=3), "pillbox_opened", 1))

    return out


# ─────────────────────────────────────────────
# (Optional) simple echo to help debugging from the UI
# ─────────────────────────────────────────────
@app.get("/api/v1/echo")
def echo(q: str = "") -> Dict[str, Any]:
    return {"ok": True, "echo": q}
