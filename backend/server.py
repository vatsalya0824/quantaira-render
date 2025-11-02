# backend/server.py
from __future__ import annotations

import json
import os
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

# ────────────────────────────────────────────────────────────────────────────────
# Config (env)
# ────────────────────────────────────────────────────────────────────────────────
APP_NAME = os.getenv("APP_NAME", "quantaira-backend")

# Persist under repo dir (Render free plan cannot write to /data)
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

VITALS_FILE = Path(os.getenv("VITALS_JSONL_PATH", str(DATA_DIR / "vitals.jsonl")))
SEEN_FILE   = Path(os.getenv("VITALS_SEEN_PATH",  str(DATA_DIR / "seen_ids.jsonl")))

TENOVI_EXPECTED_KEY = os.getenv("TENOVI_EXPECTED_KEY", "").strip()
TENOVI_API_KEY      = os.getenv("TENOVI_API_KEY", "").strip()   # optional (proxy)

# Optional: pretty names for the /patients list (JSON string)
#   PATIENT_NAMES='{"54321":"Todd Gross","99999":"Andrew Miller"}'
try:
    PATIENT_NAMES: Dict[str, str] = json.loads(os.getenv("PATIENT_NAMES", "{}"))
except Exception:
    PATIENT_NAMES = {}

# Map Gateway IDs → Patient IDs when Tenovi doesn’t send patient_id
# You can override with env:
#   GATEWAY_TO_PATIENT='{"26CC-31EB-65DF":"54321","4770-07E8-08DC":"99999"}'
DEFAULT_GATEWAY_MAP = {
    "26CC-31EB-65DF": "54321",   # Todd Gross
    "4770-07E8-08DC": "99999",   # Andrew Miller
}
try:
    GATEWAY_TO_PATIENT = {**DEFAULT_GATEWAY_MAP, **json.loads(os.getenv("GATEWAY_TO_PATIENT", "{}"))}
except Exception:
    GATEWAY_TO_PATIENT = DEFAULT_GATEWAY_MAP

# ────────────────────────────────────────────────────────────────────────────────
# App & CORS
# ────────────────────────────────────────────────────────────────────────────────
app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────
def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

def _append_jsonl(path: Path, rec: Dict[str, Any]) -> None:
    _ensure_parent(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Accept ...Z or any ISO with offset
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def _mark_seen(eid: str) -> None:
    if not eid:
        return
    _ensure_parent(SEEN_FILE)
    with SEEN_FILE.open("a", encoding="utf-8") as f:
        f.write(eid + "\n")

def _first_nonempty(*vals) -> str:
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

# ────────────────────────────────────────────────────────────────────────────────
# Health
# ────────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": APP_NAME}

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

# ────────────────────────────────────────────────────────────────────────────────
# Webhook: /webhooks/tenovi
# Accepts single dict or array of dicts.
# Fields we normalize:
#   patient_id | patient (fallback from gateway id)
#   metric | type   | bp ("120/80")
#   timestamp | time | created_at
#   unit | value | pillbox_opened
# ────────────────────────────────────────────────────────────────────────────────
def _normalize_event(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    # Try direct patient id first
    patient_id = _first_nonempty(str(ev.get("patient_id") or ev.get("patient") or ""))

    # If missing or a placeholder, attempt to map from gateway id
    if not patient_id or patient_id.lower().startswith("demo"):
        gwid = _first_nonempty(
            str(ev.get("gateway_id") or ""),
            str(ev.get("gatewayId") or ""),
            str(ev.get("gateway") or ""),
            str(ev.get("gateway_imei") or ""),
        )
        if gwid in GATEWAY_TO_PATIENT:
            patient_id = GATEWAY_TO_PATIENT[gwid]

    if not patient_id:
        # Can't attribute this record – drop it
        return out

    ts = _first_nonempty(
        ev.get("timestamp"), ev.get("time"), ev.get("created_at")
    )
    dt = _parse_ts(ts) or datetime.now(timezone.utc)

    # BP packed as "120/80"
    if isinstance(ev.get("bp"), str) and "/" in ev["bp"]:
        try:
            sbp_s, dbp_s = ev["bp"].split("/", 1)
            out.append({
                "timestamp_utc": dt.isoformat(),
                "metric": "systolic_bp", "value": float(sbp_s), "unit": "mmHg",
                "source": "tenovi", "patient_id": patient_id
            })
            out.append({
                "timestamp_utc": dt.isoformat(),
                "metric": "diastolic_bp", "value": float(dbp_s), "unit": "mmHg",
                "source": "tenovi", "patient_id": patient_id
            })
            return out
        except Exception:
            # fall back to generic handling below
            pass

    metric = _first_nonempty(
        str(ev.get("metric") or ""),
        str(ev.get("type") or "")
    ).lower()
    unit  = str(ev.get("unit") or "")
    value = ev.get("value")

    if metric:
        out.append({
            "timestamp_utc": dt.isoformat(),
            "metric": metric, "value": value, "unit": unit,
            "source": "tenovi", "patient_id": patient_id
        })

    # explicit pillbox flag
    if ev.get("pillbox_opened") is True:
        out.append({
            "timestamp_utc": dt.isoformat(),
            "metric": "pillbox_opened", "value": 1, "unit": "",
            "source": "tenovi", "patient_id": patient_id
        })

    return out

@app.post("/webhooks/tenovi")
async def tenovi_webhook_plural(
    request: Request,
    x_webhook_key: Optional[str] = Header(None, convert_underscores=False),
) -> Dict[str, Any]:
    # auth (Render env → TENOVI_EXPECTED_KEY, Tenovi “Authorization Header Key” → X-Webhook-Key)
    if TENOVI_EXPECTED_KEY and (x_webhook_key or "").strip() != TENOVI_EXPECTED_KEY:
        raise HTTPException(status_code=401, detail="Invalid X-Webhook-Key")

    # parse body (array or single)
    try:
        payload: Union[Dict[str, Any], List[Dict[str, Any]]] = await request.json()
    except Exception:
        # some Tenovi tests send empty body – ack so they show Code 200
        return {"ok": True, "inserted": 0, "note": "empty or invalid json"}

    events: List[Dict[str, Any]] = payload if isinstance(payload, list) else [payload]

    inserted = 0
    for ev in events:
        rows = _normalize_event(ev)
        for r in rows:
            try:
                _append_jsonl(VITALS_FILE, r)
                inserted += 1
            except Exception as e:
                print(f"[webhook] write failed: {e}")

        # de-dup marker (if vendor sends event ids)
        eid = str(ev.get("id") or "")
        if eid:
            _mark_seen(eid)

    return {"ok": True, "inserted": inserted}

# ────────────────────────────────────────────────────────────────────────────────
# Vitals API: /vitals?hours=24&patient_id=54321
# ────────────────────────────────────────────────────────────────────────────────
@app.get("/vitals")
def get_vitals(hours: int = 24, patient_id: Optional[str] = None) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    out: List[Dict[str, Any]] = []
    for rec in _iter_jsonl(VITALS_FILE):
        try:
            ts = _parse_ts(rec.get("timestamp_utc") or rec.get("timestamp") or "")
            if not ts or ts < cutoff:
                continue
            if patient_id and str(rec.get("patient_id")) != str(patient_id):
                continue
            out.append({
                "timestamp_utc": ts.isoformat(),
                "metric": str(rec.get("metric") or "").strip().lower(),
                "value": rec.get("value"),
                "unit": rec.get("unit") or "",
                "source": rec.get("source") or "tenovi",
                "patient_id": str(rec.get("patient_id") or ""),
                # pass-throughs if present:
                "device_name": rec.get("device_name"),
                "value_1": rec.get("value_1"),
                "value_2": rec.get("value_2"),
            })
        except Exception:
            continue
    out.sort(key=lambda r: r["timestamp_utc"])
    return out

# ────────────────────────────────────────────────────────────────────────────────
# Patients list derived from vitals (what your Streamlit expects)
# ────────────────────────────────────────────────────────────────────────────────
def _patients_from_vitals() -> List[Dict[str, Any]]:
    if not VITALS_FILE.exists():
        return []
    seen: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for rec in _iter_jsonl(VITALS_FILE):
        pid = str(rec.get("patient_id") or "").strip()
        if not pid or pid in seen:
            continue
        seen[pid] = {"id": pid, "name": PATIENT_NAMES.get(pid) or f"Patient {pid}"}
    return list(seen.values())

@app.get("/patients")
def get_patients() -> Dict[str, Any]:
    return {"items": _patients_from_vitals()}

# ────────────────────────────────────────────────────────────────────────────────
# Optional: proxy to Tenovi patients (needs TENOVI_API_KEY)
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
