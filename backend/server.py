# backend/server.py
from __future__ import annotations

import errno
import hashlib
import json
import os
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import requests
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

# ────────────────────────────────────────────────────────────────────────────────
# Config (env)
# ────────────────────────────────────────────────────────────────────────────────
APP_NAME = os.getenv("APP_NAME", "quantaira-backend")

# Render free plan can't write to /data in some setups → auto-fallback handled below
VITALS_JSONL_PATH = os.getenv("VITALS_JSONL_PATH", "/data/vitals.jsonl")
VITALS_SEEN_PATH  = os.getenv("VITALS_SEEN_PATH",  "/data/seen_ids.jsonl")

# Webhook key expected from Tenovi (“Authorization Header Value” you set in Tenovi UI)
TENOVI_EXPECTED_KEY = (os.getenv("TENOVI_EXPECTED_KEY", "quantaira_data_123") or "").strip()

# Optional: for /tenovi/patients proxy
TENOVI_API_KEY = (os.getenv("TENOVI_API_KEY") or "").strip()

# Optional: pretty names mapping, e.g. {"54321":"Todd Gross","99999":"Andy Miller"}
try:
    PATIENT_NAMES: Dict[str, str] = json.loads(os.getenv("PATIENT_NAMES", "{}"))
except Exception:
    PATIENT_NAMES = {}

# Optional: gateway→patient mapping if Tenovi events carry gateway_id instead of patient_id
# Example: {"4770-07E8-08DC":"54321","26CC-31EB-65DF":"99999"}
try:
    GATEWAY_TO_PATIENT: Dict[str, str] = json.loads(os.getenv("GATEWAY_TO_PATIENT", "{}"))
except Exception:
    GATEWAY_TO_PATIENT = {}

# ────────────────────────────────────────────────────────────────────────────────
# App & CORS
# ────────────────────────────────────────────────────────────────────────────────
app = FastAPI(title=APP_NAME, version="1.6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ────────────────────────────────────────────────────────────────────────────────
# File utilities (with /tmp fallback)
# ────────────────────────────────────────────────────────────────────────────────
def _ensure_parent_dir(path: str) -> str:
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        return path
    except OSError as e:
        if e.errno == errno.EACCES:
            alt = os.path.join("/tmp", os.path.basename(path))
            os.makedirs("/tmp", exist_ok=True)
            return alt
        raise

def _append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    path = _ensure_parent_dir(path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    path = _ensure_parent_dir(path)
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
    if not eid:
        return
    path = _ensure_parent_dir(VITALS_SEEN_PATH)
    with open(path, "a", encoding="utf-8") as f:
        f.write(eid + "\n")

def _seen_before(eid: str) -> bool:
    if not eid:
        return False
    path = _ensure_parent_dir(VITALS_SEEN_PATH)
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == eid:
                    return True
    except FileNotFoundError:
        pass
    return False

# ────────────────────────────────────────────────────────────────────────────────
# Time helpers
# ────────────────────────────────────────────────────────────────────────────────
def _utc_iso(ts: Any | None) -> str:
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

# ────────────────────────────────────────────────────────────────────────────────
# Normalization helpers
# ────────────────────────────────────────────────────────────────────────────────
def _split_bp(val: str) -> List[Dict[str, Any]]:
    try:
        s, d = [float(x.strip()) for x in str(val).split("/", 1)]
        return [
            {"metric": "systolic_bp", "value": s, "unit": "mmHg"},
            {"metric": "diastolic_bp", "value": d, "unit": "mmHg"},
        ]
    except Exception:
        return [{"metric": "blood_pressure", "value": str(val), "unit": ""}]

def _get_patient_id(payload: Dict[str, Any]) -> str:
    pid = (
        payload.get("patient_id")
        or payload.get("user_id")
        or (payload.get("patient") or {}).get("id")
        or (payload.get("user") or {}).get("id")
        or ""
    )
    if pid:
        return str(pid)

    # try gateway mapping
    gw = (
        payload.get("gateway_id")
        or (payload.get("gateway") or {}).get("id")
        or (payload.get("device") or {}).get("gateway_id")
        or ""
    )
    gw = str(gw).strip()
    if gw and gw in GATEWAY_TO_PATIENT:
        return str(GATEWAY_TO_PATIENT[gw])

    return "unknown"

def _normalize_one(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Accepts multiple Tenovi-ish shapes:
      - flat: {patient_id, metric|type, value, unit, timestamp|time}
      - bp:   {"bp": "120/78"}
      - nested reading: {"reading": {metric|type, value, unit, timestamp}}
      - batch: {"measurements": [ {metric|type, value, unit, time}, ... ]}
      - may carry gateway_id instead of patient_id (mapped via env)
    """
    rows: List[Dict[str, Any]] = []

    patient_id = _get_patient_id(payload)
    base_ts = _utc_iso(
        payload.get("timestamp")
        or payload.get("time")
        or (payload.get("reading") or {}).get("timestamp")
        or (payload.get("reading") or {}).get("time")
    )

    # Combined blood pressure "120/80"
    if "bp" in payload:
        for r in _split_bp(payload["bp"]):
            rows.append({
                "timestamp_utc": base_ts, "patient_id": patient_id,
                "metric": r["metric"], "value": r["value"], "unit": r.get("unit", ""),
                "source": "tenovi",
            })

    # Flat: metric|type + value
    flat_metric = payload.get("metric") or payload.get("type")
    if flat_metric is not None and ("value" in payload):
        rows.append({
            "timestamp_utc": base_ts, "patient_id": patient_id,
            "metric": str(flat_metric).strip().lower(),
            "value": payload.get("value"),
            "unit": str(payload.get("unit", "")),
            "source": "tenovi",
        })

    # Nested: reading:{...}
    if isinstance(payload.get("reading"), dict):
        r = payload["reading"]
        metric = r.get("metric") or r.get("type")
        value = r.get("value")
        unit = r.get("unit", "")
        if metric is not None and value is not None:
            rows.append({
                "timestamp_utc": _utc_iso(r.get("timestamp") or r.get("time") or base_ts),
                "patient_id": patient_id,
                "metric": str(metric).strip().lower(),
                "value": value, "unit": str(unit), "source": "tenovi",
            })

    # Batch: measurements:[...]
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
                        "timestamp_utc": m_ts, "patient_id": patient_id,
                        "metric": r["metric"], "value": r["value"], "unit": r.get("unit", ""), "source": "tenovi",
                    })
            else:
                rows.append({
                    "timestamp_utc": m_ts, "patient_id": patient_id,
                    "metric": str(metric).strip().lower(),
                    "value": value, "unit": str(unit), "source": "tenovi",
                })

    return rows

# ────────────────────────────────────────────────────────────────────────────────
# Root & health
# ────────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": APP_NAME}

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

# ────────────────────────────────────────────────────────────────────────────────
# Webhooks (plural & singular paths)
# ────────────────────────────────────────────────────────────────────────────────
def _ingest_items(items: List[Dict[str, Any]]) -> int:
    inserted = 0
    for obj in items:
        for row in _normalize_one(obj):
            _append_jsonl(VITALS_JSONL_PATH, row)
            inserted += 1
    return inserted

async def _tenovi_handler(
    request: Request,
    x_webhook_key: Optional[str] = Header(default=None, alias="X-Webhook-Key", convert_underscores=False),
    authorization: Optional[str] = Header(default=None),  # allow fallback if Tenovi uses "Authorization"
) -> Dict[str, Any]:
    # header auth (trim to avoid whitespace gotchas)
    provided = (x_webhook_key or authorization or "").strip()
    expected = TENOVI_EXPECTED_KEY
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail="invalid webhook key")

    # Tenovi sometimes fires empty-body tests → ack
    raw = await request.body()
    if not raw.strip():
        return {"ok": True, "message": "empty webhook body (test acknowledged)"}

    # idempotency on raw body
    eid = hashlib.sha256(raw).hexdigest()
    if _seen_before(eid):
        return {"ok": True, "duplicate": True}
    _mark_seen(eid)

    # parse JSON
    try:
        payload: Union[List[Dict[str, Any]], Dict[str, Any]] = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}

    if isinstance(payload, list):
        items = [p for p in payload if isinstance(p, dict)]
        if not items:
            return {"ok": True, "message": "empty array (no measurements)"}
        inserted = _ingest_items(items)
    elif isinstance(payload, dict):
        inserted = _ingest_items([payload])
    else:
        return {"ok": False, "error": f"unsupported payload type {type(payload)}"}

    return {"ok": True, "inserted": inserted}

@app.post("/webhooks/tenovi")
async def tenovi_webhook_plural(
    request: Request,
    x_webhook_key: Optional[str] = Header(default=None, alias="X-Webhook-Key", convert_underscores=False),
    authorization: Optional[str] = Header(default=None),
):
    return await _tenovi_handler(request, x_webhook_key, authorization)

@app.post("/webhook/tenovi")
async def tenovi_webhook_singular(
    request: Request,
    x_webhook_key: Optional[str] = Header(default=None, alias="X-Webhook-Key", convert_underscores=False),
    authorization: Optional[str] = Header(default=None),
):
    return await _tenovi_handler(request, x_webhook_key, authorization)

# ────────────────────────────────────────────────────────────────────────────────
# Vitals API (real if present; otherwise synthetic so UI still renders)
# ────────────────────────────────────────────────────────────────────────────────
def _recent_webhook_vitals(hours: int, patient_id: Optional[str]) -> List[Dict[str, Any]]:
    rows = _read_jsonl(VITALS_JSONL_PATH)
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
            "patient_id": str(r.get("patient_id") or ""),
        })
    return out

def _seed(pid: str) -> int:
    return sum(ord(c) for c in pid) % 7

def _synthetic_vitals(hours: int, patient_id: str) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    s = _seed(patient_id)
    out: List[Dict[str, Any]] = []
    for i in range(hours):
        ts = now - timedelta(hours=hours - i)
        out.extend([
            {"timestamp_utc": ts.isoformat(), "metric": "pulse", "value": 72 + (i + s) % 5 - 2, "unit": "bpm"},
            {"timestamp_utc": ts.isoformat(), "metric": "spo2",  "value": 97 - (1 if (i + s) % 13 == 0 else 0), "unit": "%"},
            {"timestamp_utc": ts.isoformat(), "metric": "blood_pressure",
             "value": f"{120 + (i + s) % 5 - 2}/{78 + ((i + s)//2) % 5 - 2}", "unit": "mmHg"},
        ])
        if (i + s) % 6 == 0:
            out.append({"timestamp_utc": ts.isoformat(), "metric": "pillbox_opened", "value": 1, "unit": ""})
    return out

def _serve_vitals(hours: int, patient_id: str) -> List[Dict[str, Any]]:
    real = _recent_webhook_vitals(hours, patient_id)
    return real if real else _synthetic_vitals(hours, patient_id)

@app.get("/vitals")
def get_vitals(
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

# ────────────────────────────────────────────────────────────────────────────────
# Patients list (derived from webhook file; pretty names via env)
# ────────────────────────────────────────────────────────────────────────────────
@app.get("/patients")
def list_patients() -> Dict[str, Any]:
    rows = _read_jsonl(VITALS_JSONL_PATH)
    seen = OrderedDict()
    for r in rows:
        pid = str(r.get("patient_id") or "").strip()
        if not pid or pid in seen:
            continue
        seen[pid] = {
            "id": pid,
            "name": PATIENT_NAMES.get(pid, pid),
        }
    return {"items": list(seen.values())}

# ────────────────────────────────────────────────────────────────────────────────
# Optional proxy to Tenovi patients (needs TENOVI_API_KEY)
# ────────────────────────────────────────────────────────────────────────────────
@app.get("/tenovi/patients")
def tenovi_patients(search: str = "", page: int = 1, page_size: int = 10):
    if not TENOVI_API_KEY:
        return {"ok": False, "error": "TENOVI_API_KEY not set"}
    try:
        url = "https://api2.tenovi.com/fwi-patients"
        headers = {"Authorization": f"Api-Key {TENOVI_API_KEY}"}
        params = {"search": search, "page": page, "page_size": page_size}
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Tenovi proxy failed: {e}")
