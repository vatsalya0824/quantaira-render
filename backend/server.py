# backend/server.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Quantaira Backend")

# CORS so your Streamlit frontend can call the API from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # lock down to your frontend origin later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Health & root (for Render checks) ---
@app.get("/")
def root():
    return {"ok": True, "service": "quantaira-backend"}

@app.get("/health")
def health():
    return {"status": "ok"}

# --- Example API used by your fetcher ---
# Adjust this to call your real data source.
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

@app.get("/api/v1/vitals")
def vitals(hours: int = 24, patient_id: str = "todd") -> List[Dict[str, Any]]:
    """Return simple mock vitals so the frontend works while you wire real data."""
    now = datetime.now(timezone.utc)
    out = []
    for i in range(hours):
        ts = now - timedelta(hours=hours - i)
        out.append({"timestamp_utc": ts.isoformat(), "metric": "pulse", "value": 72 + (i % 5)})
        out.append({"timestamp_utc": ts.isoformat(), "metric": "spo2", "value": 97})
        # include combined BP; your frontend will split this
        out.append({"timestamp_utc": ts.isoformat(), "metric": "blood_pressure", "value": "120/78"})
    # pillbox “events” sprinkled in
    out.append({"timestamp_utc": (now - timedelta(hours=3)).isoformat(), "metric": "pillbox_opened", "value": 1})
    return out
