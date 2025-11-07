# backend/routes/patients.py
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any
from store import upsert_patient_row, list_all_patients

router = APIRouter(tags=["patients"])

class Bind(BaseModel):
    gateway_id: str

class PatientUpsert(BaseModel):
    id: str
    name: str
    gateways: list[Bind] = []

@router.get("/patients")
def get_patients() -> List[Dict[str, Any]]:
    return list_all_patients()

@router.post("/patients")
def upsert_patient(body: PatientUpsert):
    upsert_patient_row(body.id, body.name)
    # optional gateway binds handled by the /gateways/bind endpoint by caller
    return {"ok": True}
