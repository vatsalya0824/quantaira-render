# backend/server.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.patients import router as patients_router
from routes.gateways import router as gateways_router
from routes.vitals import router as vitals_router
from routes.webhooks_tenovi import router as tenovi_router

app = FastAPI(title="quantaira-backend")

# CORS: allow your Streamlit origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten if you want
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"ok": True, "service": "quantaira-backend"}

app.include_router(patients_router, prefix="")
app.include_router(gateways_router, prefix="")
app.include_router(vitals_router,    prefix="")
app.include_router(tenovi_router,    prefix="")
