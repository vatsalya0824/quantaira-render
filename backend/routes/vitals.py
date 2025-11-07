# backend/routes/vitals.py
from fastapi import APIRouter, Query
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone

from store import query_vitals

router = APIRouter(tags=["vitals"])

@router.get("/vitals")
def get_vitals(
    hours: int = Query(24, ge=1, le=24*31),
    patient_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    return query_vitals(patient_id, since.isoformat())
