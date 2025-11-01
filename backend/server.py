# backend/server.py
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import requests
from fastapi import FastAPI, Query, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

APP_NAME = os.getenv("APP_NAME", "quantaira-backend")

# ── Tenovi + storage settings (set in Render → Environment) ───────────────
TENOVI_EXPECTED_KEY = os.getenv("TENOVI_EXPECTED_KEY", "quantaira_data_123")  # header value
TENOVI_API_KEY = os.getenv("TENOVI_API_KEY")  # optional, only for /tenovi/patients proxy
DATA_FILE = os.getenv("VITALS_JSONL_PATH", "/data/vitals.jsonl")
SEEN_FILE = os.getenv("VITALS_SEEN_PATH", "/data/seen_ids.jsonl")

app = FastAPI(title="Quantaira Backend", version="1.3.0")

# ── CORS: allow Streamlit UI to call us ───────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Small utils ───────────────────────────────────────────────────────────
def _append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return out

def _mark_seen(eid: str) -> None:
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "a", encoding="utf-8") as f:
        f.write(eid + "\n")

def _seen_before(eid: str) -> bool:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == eid:
                    return True
    except FileNotFoundError:
        pass
    return False

def _utc_iso(ts: Any) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()

def _split_bp(val: str) -> List[Dict[str, Any]]:
    try:
        s, d = [float(x.strip()) for x in str(val).split("/", 1)]
        return [
            {"metric": "systolic_bp", "value": s, "unit": "mmHg"},
            {"metric": "diastolic_bp", "value": d, "unit": "mmHg"},
        ]
    except Exception:
        return [{"metric": "blood_pressure", "value": str(val), "unit": ""}]

def _normalize_one(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize ONE Tenovi-like object to our rows."""
    rows: List[Dict[str, Any]] = []

    patient_id = (
        payload.get("patient_id")
        or payload.get("user_id")
        or (payload.get("patient") or {}).get("id")
        or (payload.get("user") or {}).get("id")
        or "unknown"
    )

    base_ts = _utc_iso(
        payload.get("timestamp")
        or payload.get("time")
        or (payload.get("reading") or {}).get("timestamp")
        or (payload.get("reading") or {}).get("time")
    )

    # BP "120/80"
    if "bp" in payload:
        for r in _split_bp(payload["bp"]):
            rows.append({
                "timestamp_utc": base_ts, "patient_id": str(patient_id),
                "metric": r["metric"], "value": r["value"], "unit": r.get("unit", ""), "source": "tenovi",
            })

    # Flat {type,value}
    if "type" in payload and "value" in payload:
        rows.append({
            "timestamp_utc": base_ts, "patient_id": str(patient_id),
            "metric": str(payload["type"]).strip().lower(),
            "value": payload["value"], "unit": str(payload.get("unit", "")), "source": "tenovi",
        })

    # Nested {reading:{...}}
    if isinstance(payload.get("reading"), dict):
        r = payload["reading"]
        metric = r.get("metric") or r.get("type")
        value = r.get("value")
        unit = r.get("unit", "")
        if metric is not None and value is not None:
            rows.append({
                "timestamp_utc": _utc_iso(r.get("timestamp") or r.get("time") or base_ts),
                "patient_id": str(patient_id), "metric": str(metric).strip().lower(),
                "value": value, "unit": str(unit), "source": "tenovi",
            })

    # measurements: [...]
    if isinstance(payload.get("measurements"), list):
        for m in payload["measurements"]:
            metric = m.get("metric") or m.get("type")
            value = m.get("value")
            unit = m.get("unit", "")
            m_ts = _utc_iso(m.get("timestamp") or m.get("time") or base_ts)
            if metric is None or value is None:
                continue
            if str(value).count("/") == 1 and ("bp" in str(metric).lower() or metric == "blood_pressure"):
                for r in _split_bp(str(value)):
                    rows.append({
                        "timestamp_utc": m_ts, "patient_id": str(patient_id),
                        "metric": r["metric"], "value": r["value"], "unit": r.get("unit", ""), "source": "tenovi",
                    })
            else:
                rows.append({
                    "timestamp_utc": m_ts, "patient_id": str(patient_id),
                    "metric": str(metric).strip().lower(), "value": value, "unit": str(unit), "source": "tenovi",
                })
    return rows

# ── Root & health ─────────────────────────────────────────────────────────
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": APP_NAME}

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

# ── Patients (demo) ──────────────────────────────────────────────────────
MOCK_PATIENTS: List[Dict[str, Any]] = [
    {"id": "todd", "name": "Todd Carter", "age": 47, "gender": "Male"},
    {"id": "jane", "name": "Jane Wilson", "age": 53, "gender": "Female"},
    {"id": "alex", "name": "Alex Kim", "age": 29, "gender": "Male"},
]

@app.get("/patients")
def get_patients() -> List[Dict[str, Any]]:
    return MOCK_PATIENTS

# (Optional) pass-through to Tenovi patient API if you set TENOVI_API_KEY
@app.get("/tenovi/patients")
def tenovi_patients(search: str = "", page: int = 1, page_size: int = 10):
    if not TENOVI_API_KEY:
        return {"ok": False, "error": "TENOVI_API_KEY not set"}
    try:
        r = requests.get(
            "https://api2.tenovi.com/hwi-patients/",
            headers={"Authorization": f"Api-Key {TENOVI_API_KEY}"},
            params={"search": search, "page": page, "page_size": page_size},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Tenovi webhook (header auth, array or object, idempotent) ────────────
def _ingest_payload_items(items: List[Dict[str, Any]]) -> int:
    inserted = 0
    for obj in items:
        for row in _normalize_one(obj):
            _append_jsonl(DATA_FILE, row)
            inserted += 1
    return inserted

async def _tenovi_handler(
    request: Request,
    x_webhook_key: Optional[str] = Header(default=None, alias="X-Webhook-Key"),
) -> Dict[str, Any]:
    # 1) header check
    if TENOVI_EXPECTED_KEY and x_webhook_key != TENOVI_EXPECTED_KEY:
        raise HTTPException(status_code=401, detail="invalid webhook key")

    # 2) idempotency
    body = await request.body()
    eid = hashlib.sha256(body).hexdigest()
    if _seen_before(eid):
        return {"ok": True, "duplicate": True}
    _mark_seen(eid)

    # 3) parse JSON (array or object)
    payload: Union[List[Dict[str, Any]], Dict[str, Any]] = await request.json()
    if isinstance(payload, list):
        inserted = _ingest_payload_items([p for p in payload if isinstance(p, dict)])
    elif isinstance(payload, dict):
        inserted = _ingest_payload_items([payload])
    else:
        raise HTTPException(status_code=400, detail="unsupported payload type")

    return {"ok": True, "inserted": inserted}

async def _tenovi_handler(
    request: Request,
    x_webhook_key: Optional[str] = Header(default=None, alias="X-Webhook-Key"),
) -> Dict[str, Any]:
    # 1) header check
    if TENOVI_EXPECTED_KEY and x_webhook_key != TENOVI_EXPECTED_KEY:
        raise HTTPException(status_code=401, detail="invalid webhook key")

    # 2) read body (handle empty or invalid)
    try:
        body = await request.body()
        eid = hashlib.sha256(body).hexdigest()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"unable to read body: {e}")

    if not body.strip():
        return {"ok": True, "message": "empty webhook body (test ping acknowledged)"}

    # 3) idempotency
    if _seen_before(eid):
        return {"ok": True, "duplicate": True}
    _mark_seen(eid)

    # 4) parse JSON safely
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}

    # handle both object and list
    inserted = 0
    if isinstance(payload, list):
        items = [p for p in payload if isinstance(p, dict)]
        if items:
            inserted = _ingest_payload_items(items)
        else:
            return {"ok": True, "message": "empty array (no measurements)"}
    elif isinstance(payload, dict):
        inserted = _ingest_payload_items([payload])
    else:
        return {"ok": False, "error": f"unsupported payload type {type(payload)}"}

    return {"ok": True, "inserted": inserted}

@app.post("/webhooks/tenovi")
async def tenovi_webhook_plural(
    request: Request,
    x_webhook_key: Optional[str] = Header(default=None, alias="X-Webhook-Key"),
):
    return await _tenovi_handler(request, x_webhook_key)

# ── Synthetic vitals (fallback) ───────────────────────────────────────────
def _seed_for_patient(pid: str) -> int:
    return sum(ord(c) for c in pid) % 7
def _bp_for_index(i: int, base_sys: int = 120, base_dia: int = 78) -> str:
    return f"{base_sys + (i % 5) - 2}/{base_dia + ((i // 2) % 5) - 2}"
def _hr_for_index(i: int, base: int = 72) -> int:
    return base + (i % 5) - 2
def _spo2_for_index(i: int, base: int = 97) -> int:
    return base - (1 if i % 13 == 0 else 0)
def _make_point(ts: datetime, metric: str, value: Any) -> Dict[str, Any]:
    return {"timestamp_utc": ts.replace(tzinfo=timezone.utc).isoformat(), "metric": metric, "value": value}

def _synthetic_vitals(hours: int, patient_id: str) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    seed = _seed_for_patient(patient_id)
    out: List[Dict[str, Any]] = []
    for i in range(hours):
        ts = now - timedelta(hours=hours - i)
        out.append(_make_point(ts, "pulse", _hr_for_index(i + seed)))
        out.append(_make_point(ts, "spo2", _spo2_for_index(i + seed)))
        out.append(_make_point(ts, "blood_pressure", _bp_for_index(i + seed)))
        if (i + seed) % 6 == 0:
            out.append(_make_point(ts, "pillbox_opened", 1))
    out.append(_make_point(now - timedelta(hours=3), "pillbox_opened", 1))
    return out

def _recent_webhook_vitals(hours: int, patient_id: Optional[str]) -> List[Dict[str, Any]]:
    rows = _read_jsonl(DATA_FILE)
    if not rows:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r.get("timestamp_utc")).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        if dt < cutoff:
            continue
        if patient_id is not None and str(r.get("patient_id")) != str(patient_id):
            continue
        out.append({
            "timestamp_utc": dt.isoformat(),
            "metric": str(r.get("metric", "")).strip().lower(),
            "value": r.get("value"),
            "unit": r.get("unit", ""),
            "source": r.get("source", "tenovi"),
            "patient_id": r.get("patient_id"),
        })
    return out

def _serve_vitals(hours: int, patient_id: str) -> List[Dict[str, Any]]:
    real = _recent_webhook_vitals(hours, patient_id)
    return real if real else _synthetic_vitals(hours, patient_id)

@app.get("/vitals")
def get_vitals_compat(
    hours: int = Query(24, ge=1, le=24 * 30),
    patient_id: str = Query("todd"),
) -> List[Dict[str, Any]]:
    return _serve_vitals(hours, patient_id)

@app.get("/api/v1/vitals")
def get_vitals_v1(
    hours: int = Query(24, ge=1, le=24 * 30),
    patient_id: str = Query("todd"),
) -> List[Dict[str, Any]]:
    return _serve_vitals(hours, patient_id)

@app.get("/api/v1/echo")
def echo(q: str = "") -> Dict[str, Any]:
    return {"ok": True, "echo": q}
