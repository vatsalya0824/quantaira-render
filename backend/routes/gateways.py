# backend/routes/gateways.py
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Dict, Any, List
from store import upsert_gateway_binding, list_unassigned_gateways

router = APIRouter(tags=["gateways"])

class BindReq(BaseModel):
    gateway_id: str
    patient_id: str

@router.post("/gateways/bind")
def bind_gateway(body: BindReq) -> Dict[str, Any]:
    upsert_gateway_binding(body.gateway_id, body.patient_id)
    return {"ok": True}

@router.get("/gateways/unassigned")
def unassigned_gateways() -> List[Dict[str, Any]]:
    return list_unassigned_gateways()
