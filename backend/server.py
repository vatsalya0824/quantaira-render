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

# Where to persist data (Render free plan cannot write to /data; use repo-relative)
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

VITALS_FILE = Path(os.getenv("VITALS_JSONL_PATH", str(DATA_DIR / "vitals.jsonl")))
SEEN_FILE   = Path(os.getenv("VITALS_SEEN_PATH",  str(DATA_DIR / "seen_ids.jsonl")))

TENOVI_EXPECTED_KEY = os.getenv("TENOVI_EXPECTED_KEY", "")
TENOVI_API_KEY      = os.getenv("TENOVI_API_KEY", "").strip()  # optional for proxy

# Optional pretty names (JSON):  {"54321":"Todd Gross","99999":"Andy Miller"}
try:
    PATIENT_NAMES: Dict[str, str] = json.loads(os.getenv("PATIENT_NAMES", "{}"))
except Exception:
    PATIENT_NAMES = {}

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
# Small helpers
# ────────────────────────────────────────────────────────────────────────────────
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

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

def _parse_ts(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Accept both "2025-11-02T12:00:00Z" and ISO with offset
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

# store de-dup ids if vendor sends event ids (we’ll de-dup by (pid, metric, ts, value) otherwise)
def _mark_seen(eid: str) -> None:
    if not eid:
        return
    _ensure_parent(SEEN_FILE)
    with SEEN_FILE.open("a", encoding="utf-8") as f:
        f.write(eid + "\n")

# ────────────────────────────────────────────────────────────────────────────────
# Health & root
# ────────────────────────────────────────────────────────────────────────────────
@app.get("/")
def root() -> Dict[str, Any]:
    return {"ok": True, "service": APP_NAME}

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

# ────────────────────────────────────────────────────────────────────────────────
# Webhook: /webhooks/tenovi
# Accepts:
#  - single object OR array
#  - keys: patient_id, type|metric or bp, value, unit, timestamp|time, id (optional)
#  - requires header: X-Webhook-Key  (must match TENOVI_EXPECTED_KEY)
# Writes a normalized row to VITALS_FILE (JSONL)
# ────────────────────────────────────────────────────────────────────────────────
def _normalize_event(ev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert a Tenovi-ish payload into one or more normalized records:
    {
      "timestamp_utc": "...",
      "metric": "pulse|spo2|systolic_bp|diastolic_bp|pillbox_opened|...",
      "value": number or string,
      "unit": "...",
      "source": "tenovi",
      "patient_id": "..."
    }
    """
    out: List[Dict[str, Any]] = []
    patient_id = str(ev.get("patient_id") or ev.get("patient") or "").strip()
    if not patient_id:
        return out

    # map time keys
    ts = ev.get("timestamp") or ev.get("time") or ev.get("created_at")
    dt = _parse_ts(ts) or datetime.now(timezone.utc)

    # blood pressure packaged formats
    if "bp" in ev and isinstance(ev["bp"], str) and "/" in ev["bp"]:
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
            pass

    # generic metric/value
    metric = (ev.get("metric") or ev.get("type") or "").strip().lower()
    unit   = (ev.get("unit") or "").strip()
    value  = ev.get("value")

    if metric:
        out.append({
            "timestamp_utc": dt.isoformat(),
            "metric": metric, "value": value, "unit": unit,
            "source": "tenovi", "patient_id": patient_id
        })

    # pillbox open boolean or special field names → normalize
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
    x_webhook_key: Optional[str] = Header(None, convert_underscores=False)
) -> Dict[str, Any]:
    # auth
    if TENOVI_EXPECTED_KEY and x_webhook_key != TENOVI_EXPECTED_KEY:
        raise HTTPException(status_code=401, detail="Invalid X-Webhook-Key")

    # body
    try:
        payload: Union[Dict[str, Any], List[Dict[str, Any]]] = await request.json()
    except Exception:
        # Tenovi sometimes tests with empty body — just ack
        return {"ok": True, "inserted": 0, "note": "empty or invalid json"}

    # accept single dict or list
    events: List[Dict[str, Any]] = payload if isinstance(payload, list) else [payload]
    inserted = 0
    for ev in events:
        rows = _normalize_event(ev)
        for r in rows:
            try:
                _append_jsonl(VITALS_FILE, r)
                inserted += 1
            except Exception as e:
                print(f"[webhook] failed to write row: {e}")

        # mark seen if vendor id present
        eid = str(ev.get("id") or "")
        if eid:
            _mark_seen(eid)

    return {"ok": True, "inserted": inserted}

# ────────────────────────────────────────────────────────────────────────────────
# Vitals: GET /vitals?hours=24&patient_id=54321
# Returns list[dict] sorted by timestamp_utc (ISO UTC).
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
            # keep a stable schema
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
# Patients: derived from vitals file; returns {"items":[{id,name},...]}
# ────────────────────────────────────────────────────────────────────────────────
def load_patients_from_db() -> List[Dict[str, Any]]:
    if not VITALS_FILE.exists():
        return []
    seen = OrderedDict()
    for rec in _iter_jsonl(VITALS_FILE):
        pid = str(rec.get("patient_id") or "").strip()
        if not pid:
            continue
        if pid not in seen:
            seen[pid] = {
                "id": pid,
                "name": PATIENT_NAMES.get(pid) or f"Patient {pid}",
            }
    return list(seen.values())

@app.get("/patients")
def get_patients() -> Dict[str, Any]:
    patients = load_patients_from_db()
    return {"items": patients}

# ────────────────────────────────────────────────────────────────────────────────
# Optional: proxy to Tenovi patient API (needs TENOVI_API_KEY)
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
