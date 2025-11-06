# backend/store.py
from __future__ import annotations
import json, os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

PATIENTS_PATH = DATA_DIR / "patients.json"
GATEWAYS_PATH = DATA_DIR / "gateways.json"
VITALS_PATH   = DATA_DIR / "vitals.jsonl"    # append-only json lines

def _load_json(path: Path) -> list:
    if not path.exists(): return []
    return json.loads(path.read_text(encoding="utf-8"))

def _save_json(path: Path, rows: list) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

# ───────────────────────── patients ─────────────────────────
def upsert_patient_row(pid: str, name: str) -> None:
    rows = _load_json(PATIENTS_PATH)
    found = next((r for r in rows if r["id"] == pid), None)
    if found:
        found["name"] = name
    else:
        rows.append({"id": pid, "name": name, "created_at": datetime.now(timezone.utc).isoformat()})
    _save_json(PATIENTS_PATH, rows)

def list_all_patients() -> List[Dict[str, Any]]:
    return _load_json(PATIENTS_PATH)

def find_patient(pid: str) -> bool:
    return any(r["id"] == pid for r in _load_json(PATIENTS_PATH))

def ensure_unassigned_patient() -> str:
    pid = "UNASSIGNED"
    if not find_patient(pid):
        upsert_patient_row(pid, "Unassigned")
    return pid

# ───────────────────────── gateways ─────────────────────────
def upsert_gateway_binding(gateway_id: str, patient_id: str) -> None:
    rows = _load_json(GATEWAYS_PATH)
    found = next((r for r in rows if r["gateway_id"] == gateway_id), None)
    if found:
        found["patient_id"] = patient_id
        found["active"] = True
    else:
        rows.append({"gateway_id": gateway_id, "patient_id": patient_id, "active": True})
    _save_json(GATEWAYS_PATH, rows)

def find_binding_by_gateway(gateway_id: str) -> Optional[str]:
    rows = _load_json(GATEWAYS_PATH)
    rec = next((r for r in rows if r["gateway_id"] == gateway_id and r.get("active")), None)
    return rec["patient_id"] if rec else None

def list_unassigned_gateways() -> List[Dict[str, Any]]:
    rows = _load_json(GATEWAYS_PATH)
    return [r for r in rows if r.get("patient_id") in (None, "UNASSIGNED") or not r.get("active", True)]

# ───────────────────────── vitals ─────────────────────────
def append_vital_row(row: Dict[str, Any]) -> None:
    """Append one normalized vital to JSONL."""
    with VITALS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

def iter_vitals() -> Dict[str, Any]:
    if not VITALS_PATH.exists():
        return
    with VITALS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                yield json.loads(line)
            except Exception:
                continue

def query_vitals(patient_id: Optional[str], since_iso: str) -> List[Dict[str, Any]]:
    t0 = datetime.fromisoformat(since_iso.replace("Z","+00:00"))
    out = []
    for r in iter_vitals() or []:
        if patient_id and str(r.get("patient_id")) != str(patient_id):
            continue
        ts = r.get("timestamp_utc")
        if not ts: continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z","+00:00"))
            if dt >= t0:
                out.append(r)
        except Exception:
            continue
    out.sort(key=lambda x: x.get("timestamp_utc"))
    return out
