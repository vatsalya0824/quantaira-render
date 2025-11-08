# server.py
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, Request, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text

from db import engine, init_db

APP_NAME = "quantaira-backend"

# ── Render env ─────────────────────────────────────────────────────────────
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "quantaira_data_123")  # matches your Tenovi config
DEBUG_LAST_PAYLOAD = "/tmp/last_payload.bin"

# ── App ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Quantaira Backend (Realtime)", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ── Lifecycle ─────────────────────────────────────────────────────────────
@app.on_event("startup")
def _startup() -> None:
    init_db()
    print("🚀 started; WEBHOOK_SECRET =", WEBHOOK_SECRET)

# ── Health / root ─────────────────────────────────────────────────────────
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": APP_NAME}

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok"}

# ── Models ────────────────────────────────────────────────────────────────
class TenoviLike(BaseModel):
    metric: Optional[str] = None
    type: Optional[str] = None         # tolerate "type"
    value: Optional[Union[str, float, int]] = None
    value_1: Optional[float] = None
    value_2: Optional[float] = None
    unit: Optional[str] = None
    timestamp: Optional[str] = None
    time: Optional[str] = None
    created: Optional[str] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    patient_id: Optional[str] = None
    user_id: Optional[str] = None

# ── Helpers ───────────────────────────────────────────────────────────────
def _utc(ts: Optional[str]) -> datetime:
    if not ts:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)

def _metric_name(m: TenoviLike) -> str:
    return (m.metric or m.type or "unknown").strip().lower()

def _split_bp_string(val: str) -> Optional[tuple[float, float]]:
    try:
        s, d = [float(x.strip()) for x in val.split("/", 1)]
        return s, d
    except Exception:
        return None

def _save_last_payload(raw: bytes) -> None:
    try:
        with open(DEBUG_LAST_PAYLOAD, "wb") as f:
            f.write(raw)
    except Exception:
        pass

def _auth_from_headers(h: Dict[str, str]) -> Optional[str]:
    # Accept X-Webhook-Key or Authorization; tolerate "Key: value" pattern too
    s = h.get("X-Webhook-Key") or h.get("x-webhook-key") or h.get("Authorization") or h.get("authorization")
    if s and ":" in s and not s.lower().startswith(("bearer ", "basic ")):
        s = s.split(":", 1)[1].strip()
    return s

# ── Webhook (plural + singular) ───────────────────────────────────────────
async def _handle_tenovi(request: Request) -> Dict[str, Any]:
    # 1) auth
    secret = _auth_from_headers(request.headers)
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    # 2) body & idempotency
    raw = await request.body()
    _save_last_payload(raw)
    body_sha = hashlib.sha256(raw).hexdigest()

    # short-circuit if we’ve already seen this body exactly
    with engine.begin() as conn:
        done = conn.execute(
            text("SELECT 1 FROM webhook_bodies WHERE body_sha = :s LIMIT 1"),
            {"s": body_sha},
        ).first()
        if done:
            return {"ok": True, "duplicate": True, "inserted": 0}

    # 3) parse
    try:
        payload: Union[List[Dict[str, Any]], Dict[str, Any]] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")

    # 4) normalize → rows
    items: List[Dict[str, Any]] = []
    def push_one(obj: Dict[str, Any]) -> None:
        m = TenoviLike(**obj)
        ts = _utc(m.created or m.timestamp or m.time)
        pid = (m.patient_id or m.user_id or "todd")  # default 'todd' for your demo

        name = _metric_name(m)
        v = m.value

        # BP as "122/78"
        if isinstance(v, str) and "/" in v and ("bp" in name or "blood_pressure" in name or name == "bp"):
            bp = _split_bp_string(v)
            if bp:
                s, d = bp
                items.append({"created_utc": ts, "patient_id": str(pid), "metric": "systolic_bp", "value_1": s, "unit": "mmHg"})
                items.append({"created_utc": ts, "patient_id": str(pid), "metric": "diastolic_bp", "value_1": d, "unit": "mmHg"})
                return

        # numeric values (value_1/value_2 supported)
        if m.value_1 is not None or m.value_2 is not None:
            items.append({"created_utc": ts, "patient_id": str(pid), "metric": name, "value_1": m.value_1, "value_2": m.value_2, "unit": m.unit})
        else:
            items.append({"created_utc": ts, "patient_id": str(pid), "metric": name, "value_1": v if isinstance(v, (int, float)) else None, "unit": m.unit})

    if isinstance(payload, dict):
        push_one(payload)
    elif isinstance(payload, list):
        for obj in payload:
            if isinstance(obj, dict):
                push_one(obj)
    else:
        raise HTTPException(status_code=400, detail="unsupported payload type")

    # 5) persist
    inserted = 0
    with engine.begin() as conn:
        # record body hash (idempotency)
        conn.execute(text("INSERT INTO webhook_bodies(body_sha, received_utc) VALUES(:s, NOW() AT TIME ZONE 'UTC') ON CONFLICT DO NOTHING"), {"s": body_sha})

        for r in items:
            conn.execute(
                text("""
                    INSERT INTO measurements
                      (created_utc, patient_id, metric, value_1, value_2, unit)
                    VALUES
                      (:created_utc, :patient_id, :metric, :value_1, :value_2, :unit)
                """),
                r,
            )
            inserted += 1

    return {"ok": True, "inserted": inserted}

@app.post("/webhook/tenovi")
async def tenovi_webhook(request: Request):
    return await _handle_tenovi(request)

@app.post("/webhooks/tenovi")
async def tenovi_webhooks(request: Request):
    return await _handle_tenovi(request)

# ── Debug: last payload bytes / pretty JSON ───────────────────────────────
@app.get("/debug/last-payload")
def debug_last_payload(pretty: bool = False):
    try:
        with open(DEBUG_LAST_PAYLOAD, "rb") as f:
            raw = f.read()
        if not pretty:
            return {"ok": True, "bytes": len(raw)}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"ok": True, "bytes": len(raw), "note": "not JSON or cannot decode"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Simple patients list (for your Home list) ─────────────────────────────
@app.get("/patients")
def patients():
    return [
        {"id": "andrew", "name": "Andrew"},
        {"id": "jane", "name": "Jane Wilson"},
        {"id": "54321", "name": "Todd Gross"},
    ]

# ── Read API used by the Streamlit frontend ───────────────────────────────
def _query_rows(hours: int, patient_id: Optional[str]) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    sql = """
        SELECT created_utc, patient_id, metric, value_1, value_2, unit
        FROM measurements
        WHERE created_utc >= :cutoff
        {pid_clause}
        ORDER BY created_utc
    """
    pid_clause = ""
    params: Dict[str, Any] = {"cutoff": cutoff}
    if patient_id:
        pid_clause = "AND patient_id = :pid"
        params["pid"] = str(patient_id)
    sql = sql.format(pid_clause=pid_clause)

    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        ts = r["created_utc"].astimezone(timezone.utc).isoformat()
        out.append({
            "timestamp_utc": ts,
            "patient_id": r["patient_id"],
            "metric": r["metric"],
            "value": r["value_1"] if r["value_2"] is None else r["value_1"],  # UI reads 'value'
            "value_1": r["value_1"],
            "value_2": r["value_2"],
            "unit": r["unit"] or "",
            "source": "tenovi",
        })
    return out

@app.get("/vitals")
def vitals(hours: int = Query(24, ge=1, le=30*24),
           patient_id: Optional[str] = Query(None)) -> List[Dict[str, Any]]:
    return _query_rows(hours, patient_id)

@app.get("/api/v1/vitals")
def vitals_v1(hours: int = Query(24, ge=1, le=30*24),
              patient_id: Optional[str] = Query(None)) -> List[Dict[str, Any]]:
    return _query_rows(hours, patient_id)
