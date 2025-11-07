# backend/routes/webhooks_tenovi.py
from fastapi import APIRouter, Request, HTTPException
from typing import Any, Dict, List
from datetime import datetime, timezone
import os

from store import (
    upsert_patient_row, find_patient, ensure_unassigned_patient,
    upsert_gateway_binding, find_binding_by_gateway,
    append_vital_row
)

router = APIRouter(tags=["tenovi"])

EXPECTED_KEY = os.getenv("TENOVI_EXPECTED_KEY", "")

def _check_secret(headers):
    # Optional: you configured Tenovi with header key "X-Webhook-Key"
    val = headers.get("X-Webhook-Key") or headers.get("Authorization") or ""
    if not EXPECTED_KEY:
        return  # accept (dev)
    if val.strip() != EXPECTED_KEY.strip():
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

def _normalize_one(r: Dict[str, Any], resolved_pid: str) -> List[Dict[str, Any]]:
    """
    Map Tenovi payload → normalized rows:
      required: patient_id, timestamp_utc, metric, value
      optional: device_name, unit, source, value_1/value_2 (for BP), raw
    """
    ts = r.get("timestamp_utc") or r.get("timestamp") or r.get("created_at") or r.get("dateTime")
    if ts:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z","+00:00")).astimezone(timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)

    device = r.get("device") or r.get("deviceName") or r.get("device_name") or ""
    source = "tenovi"

    out: List[Dict[str, Any]] = []

    # BP special case (sometimes arrives as systolic/diastolic fields or "120/80")
    if "systolic" in r and "diastolic" in r:
        out.append({
            "patient_id": resolved_pid, "timestamp_utc": dt.isoformat(),
            "metric": "systolic_bp", "value": float(r["systolic"]),
            "unit": "mmHg", "device_name": device, "source": source, "raw": r
        })
        out.append({
            "patient_id": resolved_pid, "timestamp_utc": dt.isoformat(),
            "metric": "diastolic_bp", "value": float(r["diastolic"]),
            "unit": "mmHg", "device_name": device, "source": source, "raw": r
        })
        return out

    # Generic numeric value
    if "metric" in r and "value" in r:
        out.append({
            "patient_id": resolved_pid, "timestamp_utc": dt.isoformat(),
            "metric": str(r["metric"]).strip().lower(),
            "value": float(r["value"]) if str(r["value"]).replace('.','',1).isdigit() else r["value"],
            "unit": r.get("unit"), "device_name": device, "source": source, "raw": r
        })
        return out

    # Device type mapping examples
    measurement_type = (r.get("type") or r.get("measurementType") or "").lower()
    if measurement_type in {"spo2","pulse","heart_rate"}:
        metric = "spo2" if "spo2" in measurement_type else "pulse"
        val = r.get("spo2") or r.get("pulse") or r.get("heart_rate") or r.get("value")
        if val is not None:
            out.append({
                "patient_id": resolved_pid, "timestamp_utc": dt.isoformat(),
                "metric": metric, "value": float(val),
                "unit": "%" if metric=="spo2" else "bpm", "device_name": device, "source": source, "raw": r
            })
    return out

@router.post("/webhooks/tenovi")
async def webhook_tenovi(request: Request):
    _check_secret(request.headers)
    body = await request.json()
    rows = body if isinstance(body, list) else [body]

    inserted = 0
    for r in rows:
        gw = r.get("gatewayId") or r.get("gateway_id") or r.get("deviceId") or r.get("device_id")
        pid = r.get("patientId") or r.get("patient_id")

        # Resolve patient
        resolved_pid: str
        if pid:
            if not find_patient(str(pid)):
                upsert_patient_row(str(pid), f"Patient {pid}")
            resolved_pid = str(pid)
        elif gw:
            bound = find_binding_by_gateway(str(gw))
            if bound:
                resolved_pid = bound
            else:
                resolved_pid = ensure_unassigned_patient()
                upsert_gateway_binding(str(gw), resolved_pid)
        else:
            resolved_pid = ensure_unassigned_patient()

        # Normalize → 1..N rows; append
        norm = _normalize_one(r, resolved_pid)
        for row in norm:
            append_vital_row(row)
            inserted += 1

    return {"ok": True, "inserted": inserted}
