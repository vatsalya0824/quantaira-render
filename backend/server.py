# server.py
# Quantaira backend — FastAPI
# Endpoints:
#   GET/HEAD /              → ok banner
#   GET/HEAD /health        → health probe 200
#   GET      /patients      → list of known patients (from env map and/or data)
#   GET      /vitals        → recent vitals (hours=24, patient_id=..., limit=...)
#   POST     /webhooks/tenovi → accepts Tenovi measurement posts (array or single)
#
# Storage:
#   - JSONL file for vitals (env VITALS_JSONL_PATH, default /tmp/vitals.jsonl)
#   - JSON file for dedupe of Tenovi measurement ids (env VITALS_SEEN_PATH)
#   - Optional local data dir (env DATA_DIR, default ./data)
#
# Auth:
#   - Webhook requires header X-Webhook-Key: <TENOVI_EXPECTED_KEY>
#     (also accepts Authorization: <TENOVI_EXPECTED_KEY>)
#
# Env knobs:
#   APP_NAME=quantaira-backend
#   DATA_DIR=./data
#   TENOVI_API_KEY=<optional; not used by this server>
#   TENOVI_EXPECTED_KEY=quantaira_data_123
#   VITALS_JSONL_PATH=/tmp/vitals.jsonl
#   VITALS_SEEN_PATH=/tmp/seen_ids.jsonl
#   GATEWAY_TO_PATIENT='{"26CC-31EB-65DF":"54321","4770-07E8-08DC":"54321"}'
#   PATIENT_NAMES='{"54321":"Todd Gross","99999":"Andrew Miller"}'

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─────────────────────────────────────────────
# Config / env
# ─────────────────────────────────────────────
APP_NAME = os.getenv("APP_NAME", "quantaira-backend")
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

VITALS_JSONL_PATH = Path(os.getenv("VITALS_JSONL_PATH", "/tmp/vitals.jsonl"))
VITALS_SEEN_PATH = Path(os.getenv("VITALS_SEEN_PATH", "/tmp/seen_ids.jsonl"))
TENOVI_EXPECTED_KEY = os.getenv("TENOVI_EXPECTED_KEY", "").strip()

def _parse_json_env(name: str) -> Dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}
GATEWAY_TO_PATIENT: Dict[str, str] = _parse_json_env("GATEWAY_TO_PATIENT")
PATIENT_NAMES: Dict[str, str] = _parse_json_env("PATIENT_NAMES")


# ─────────────────────────────────────────────
# App
# ─────────────────────────────────────────────
app = FastAPI(title=APP_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in prod
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────
class Vital(BaseModel):
    # canonical internal representation
    id: str
    timestamp_utc: datetime
    patient_id: str
    metric: str
    value: Any | None = None
    value_1: Any | None = None
    value_2: Any | None = None
    unit: str | None = None
    device_name: str | None = None
    source: str | None = None
    raw: Dict[str, Any] | None = None


# ─────────────────────────────────────────────
# Utilities: JSONL storage
# ─────────────────────────────────────────────
def _read_seen_ids() -> set[str]:
    if not VITALS_SEEN_PATH.exists():
        return set()
    try:
        return set(json.loads(VITALS_SEEN_PATH.read_text()))
    except Exception:
        return set()

def _write_seen_ids(ids: Iterable[str]) -> None:
    VITALS_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    VITALS_SEEN_PATH.write_text(json.dumps(sorted(set(ids))))

def append_jsonl(record: Dict[str, Any]) -> None:
    VITALS_JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with VITALS_JSONL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")

def iter_jsonl_reverse(path: Path) -> Iterable[Dict[str, Any]]:
    """Read JSONL from end to start (fast recent scan)."""
    if not path.exists():
        return
    with path.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        buf = b""
        while pos > 0:
            step = min(8192, pos)
            pos -= step
            f.seek(pos)
            chunk = f.read(step)
            buf = chunk + buf
            while True:
                nl = buf.rfind(b"\n")
                if nl == -1:
                    break
                line = buf[nl + 1 :]
                buf = buf[:nl]
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
        if buf.strip():
            try:
                yield json.loads(buf.strip())
            except Exception:
                pass


# ─────────────────────────────────────────────
# Probes (GET + HEAD)
# ─────────────────────────────────────────────
@app.get("/")
def root():
    return {"ok": True, "service": APP_NAME}

@app.head("/")
def root_head():
    return Response(status_code=200)

@app.get("/health")
def health():
    return {"ok": True}

@app.head("/health")
def health_head():
    return Response(status_code=200)


# ─────────────────────────────────────────────
# Patients & vitals APIs
# ─────────────────────────────────────────────
@app.get("/patients")
def list_patients() -> List[Dict[str, Any]]:
    """Return patients discovered in env maps or from stored vitals."""
    ids: set[str] = set(PATIENT_NAMES.keys())

    # include all PATIENT_IDS referenced by GATEWAY_TO_PATIENT
    ids.update(GATEWAY_TO_PATIENT.values())

    # include anyone found in the JSONL file (recent first for speed)
    for rec in iter_jsonl_reverse(VITALS_JSONL_PATH):
        pid = str(rec.get("patient_id") or "").strip()
        if pid:
            ids.add(pid)
        if len(ids) >= 256:
            break

    out = []
    for pid in sorted(ids):
        name = PATIENT_NAMES.get(pid) or f"Patient {pid}"
        out.append({"id": str(pid), "name": name})
    return out


@app.get("/vitals")
def get_vitals(hours: int = 24, patient_id: Optional[str] = None, limit: int = 5000):
    """
    Return measurements within the last `hours` (default 24).
    Optional filter by patient_id. Results newest→oldest.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    rows: List[Dict[str, Any]] = []

    for rec in iter_jsonl_reverse(VITALS_JSONL_PATH):
        try:
            ts = datetime.fromisoformat(rec["timestamp_utc"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < since:
            # we scan newest→oldest, can stop once past window
            break
        if patient_id and str(rec.get("patient_id")) != str(patient_id):
            continue
        rows.append(rec)
        if len(rows) >= limit:
            break

    return {"items": rows}  # front-end accepts dict{"items":[...]}


# ─────────────────────────────────────────────
# Tenovi webhook
# ─────────────────────────────────────────────
def _auth_ok(x_webhook_key: Optional[str], authorization: Optional[str]) -> bool:
    if not TENOVI_EXPECTED_KEY:
        # if not set, accept anything (dev only)
        return True
    # Accept either header
    if (x_webhook_key or "").strip() == TENOVI_EXPECTED_KEY:
        return True
    if (authorization or "").strip() == TENOVI_EXPECTED_KEY:
        return True
    return False

def _normalize_tenovi_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Map Tenovi payload to our Vital schema.
    Expected Tenovi fields (examples, may vary by device):
      - id (optional) a unique measurement id
      - device_id / gateway_id
      - patient_id (may be missing; we derive via GATEWAY_TO_PATIENT if so)
      - timestamp (ISO) or created_at
      - metric / measurement_type
      - value / value_1 / value_2 / unit
    """
    # 1) timestamp
    ts = (
        item.get("timestamp")
        or item.get("timestamp_utc")
        or item.get("created_at")
        or item.get("created_at_utc")
        or item.get("time")
    )
    try:
        ts_utc = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        ts_utc = datetime.now(timezone.utc)

    # 2) patient id
    pid = str(item.get("patient_id") or "").strip()
    if not pid:
        # derive via gateway or device
        gw = str(item.get("gateway_id") or item.get("device_id") or "").strip()
        if gw and gw in GATEWAY_TO_PATIENT:
            pid = str(GATEWAY_TO_PATIENT[gw])

    if not pid:
        # if we truly cannot map → drop
        return None

    # 3) metric & values
    metric = str(item.get("metric") or item.get("measurement_type") or "").strip().lower()
    v = item.get("value")
    v1 = item.get("value_1")
    v2 = item.get("value_2")
    unit = item.get("unit")

    # common transforms
    if not metric and "systolic" in item or "diastolic" in item:
        metric = "blood_pressure"
    device_name = str(item.get("device_name") or item.get("device_type") or "").strip()

    # 4) id / dedupe key
    mid = str(item.get("id") or item.get("measurement_id") or uuid.uuid4().hex)

    return {
        "id": mid,
        "timestamp_utc": ts_utc.isoformat(),
        "patient_id": pid,
        "metric": metric,
        "value": v,
        "value_1": v1,
        "value_2": v2,
        "unit": unit,
        "device_name": device_name,
        "source": "tenovi",
        "raw": item,
    }

@app.post("/webhooks/tenovi")
async def tenovi_webhook(
    request: Request,
    x_webhook_key: Optional[str] = Header(None, convert_underscores=False),
    authorization: Optional[str] = Header(None),
):
    if not _auth_ok(x_webhook_key, authorization):
        raise HTTPException(status_code=401, detail="Invalid webhook key")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Tenovi can POST a single object or an array
    items: List[Dict[str, Any]]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            items = payload["items"]
        else:
            items = [payload]
    else:
        raise HTTPException(status_code=400, detail="Unsupported payload type")

    seen = _read_seen_ids()
    inserted = 0
    new_seen: set[str] = set()

    for it in items:
        norm = _normalize_tenovi_item(it)
        if not norm:
            continue
        mid = str(norm["id"])
        if mid in seen or mid in new_seen:
            continue
        append_jsonl(norm)
        new_seen.add(mid)
        inserted += 1

    if new_seen:
        seen.update(new_seen)
        _write_seen_ids(seen)

    return {"ok": True, "inserted": inserted}
