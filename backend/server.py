
# server.py — Quantaira Webhook & API (Render-ready)
import os, json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, Request, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

# ─────────────────────────────────────────────────────────
# Config / DB
# ─────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/tenovi.db")
if DATABASE_URL.startswith("sqlite:///"):
    os.makedirs(os.path.dirname(DATABASE_URL.replace("sqlite:///", "")), exist_ok=True)

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
TENOVI_WEBHOOK_KEY = (os.getenv("TENOVI_WEBHOOK_KEY") or "").strip()

# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────
def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def to_iso_utc(ts: Union[str, datetime, None]) -> str:
    if isinstance(ts, datetime):
        d = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if ts:
        try:
            d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return now_iso_utc()
    return now_iso_utc()

def norm_gateway(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    s = str(s).strip().upper()
    keep = "".join(ch for ch in s if ch.isalnum())
    return keep or None

# ─────────────────────────────────────────────────────────
# Schema / Init  (Postgres & SQLite)
# ─────────────────────────────────────────────────────────
def init_db() -> None:
    DIALECT = engine.url.get_backend_name()
    IS_PG = DIALECT.startswith("postgres")

    if IS_PG:
        vitals_sql = """
        CREATE TABLE IF NOT EXISTS vitals (
            id BIGSERIAL PRIMARY KEY,
            patient_id TEXT NOT NULL,
            metric TEXT NOT NULL,
            value DOUBLE PRECISION,
            timestamp_utc TIMESTAMPTZ NOT NULL,
            device_name TEXT,
            gateway_raw TEXT,
            gateway_norm TEXT,
            raw JSONB
        );
        """
        meals_sql = """
        CREATE TABLE IF NOT EXISTS meals (
            id BIGSERIAL PRIMARY KEY,
            patient_id TEXT NOT NULL,
            timestamp_utc TIMESTAMPTZ NOT NULL,
            food TEXT,
            kcal INTEGER,
            protein_g DOUBLE PRECISION,
            carbs_g DOUBLE PRECISION,
            fat_g DOUBLE PRECISION,
            sodium_mg INTEGER,
            fdc_id TEXT
        );
        """
        notes_sql = """
        CREATE TABLE IF NOT EXISTS notes (
            id BIGSERIAL PRIMARY KEY,
            patient_id TEXT NOT NULL,
            timestamp_utc TIMESTAMPTZ NOT NULL,
            note TEXT
        );
        """
        limits_sql = """
        CREATE TABLE IF NOT EXISTS limits (
            id BIGSERIAL PRIMARY KEY,
            patient_id TEXT,
            metric TEXT NOT NULL,
            lsl DOUBLE PRECISION,
            usl DOUBLE PRECISION,
            UNIQUE (patient_id, metric)
        );
        """
        gateway_sql = """
        CREATE TABLE IF NOT EXISTS gateway_map (
            id BIGSERIAL PRIMARY KEY,
            gateway_raw TEXT NOT NULL,
            gateway_norm TEXT UNIQUE,
            patient_id TEXT
        );
        """
    else:
        vitals_sql = """
        CREATE TABLE IF NOT EXISTS vitals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            timestamp_utc TEXT NOT NULL,
            device_name TEXT,
            gateway_raw TEXT,
            gateway_norm TEXT,
            raw TEXT
        );
        """
        meals_sql = """
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            food TEXT,
            kcal INTEGER,
            protein_g REAL,
            carbs_g REAL,
            fat_g REAL,
            sodium_mg INTEGER,
            fdc_id TEXT
        );
        """
        notes_sql = """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            note TEXT
        );
        """
        limits_sql = """
        CREATE TABLE IF NOT EXISTS limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT,
            metric TEXT NOT NULL,
            lsl REAL,
            usl REAL,
            UNIQUE (patient_id, metric)
        );
        """
        gateway_sql = """
        CREATE TABLE IF NOT EXISTS gateway_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gateway_raw TEXT NOT NULL,
            gateway_norm TEXT UNIQUE,
            patient_id TEXT
        );
        """

    with engine.begin() as conn:
        conn.execute(text(vitals_sql))
        conn.execute(text(meals_sql))
        conn.execute(text(notes_sql))
        conn.execute(text(limits_sql))
        conn.execute(text(gateway_sql))

        # Normalize + dedupe gateway_map
        rows = conn.execute(text("SELECT id, gateway_raw FROM gateway_map")).fetchall()
        for rid, raw_val in rows:
            n = norm_gateway(raw_val)
            if not n:
                continue
            try:
                conn.execute(text(
                    "UPDATE gateway_map SET gateway_norm=:n WHERE id=:i"
                ), {"n": n, "i": rid})
            except IntegrityError:
                pass

        dups = conn.execute(text("""
            WITH ranked AS (
              SELECT id, gateway_norm,
                     ROW_NUMBER() OVER (PARTITION BY gateway_norm ORDER BY id) rn
              FROM gateway_map
              WHERE gateway_norm IS NOT NULL
            )
            SELECT id FROM ranked WHERE rn > 1
        """)).fetchall()
        for (dup_id,) in dups:
            conn.execute(text("DELETE FROM gateway_map WHERE id=:i"), {"i": dup_id})

# ─────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────
app = FastAPI(title="Quantaira Webhook & API", version="1.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

@app.on_event("startup")
def _startup():
    init_db()

# ─────────────────────────────────────────────────────────
# Health / Root (Render checks)
# ─────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"ok": True, "service": "Quantaira Webhook & API"}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/ping")
def ping():
    return {"ok": True, "ts": now_iso_utc()}

# ─────────────────────────────────────────────────────────
# Admin / Testing helpers
# ─────────────────────────────────────────────────────────
@app.post("/admin/migrate")
def admin_migrate():
    init_db()
    return {"ok": True, "migrated": True}

@app.post("/debug/seed")
def debug_seed():
    """Insert a couple demo vital rows (remove in prod)."""
    ts1 = now_iso_utc()
    ts2 = to_iso_utc(datetime.now(timezone.utc) - timedelta(minutes=3))
    rows = [
        {"patient_id": "demo-001", "metric": "pulse",        "value": 77,  "ts": ts1, "device": "HWI Pulse", "gwr": None, "gwn": None, "raw": json.dumps({"seed": True})},
        {"patient_id": "demo-001", "metric": "systolic_bp",  "value": 120, "ts": ts2, "device": "HWI BP",    "gwr": None, "gwn": None, "raw": json.dumps({"seed": True})},
    ]
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO vitals (patient_id, metric, value, timestamp_utc, device_name, gateway_raw, gateway_norm, raw)
            VALUES (:patient_id, :metric, :value, :ts, :device, :gwr, :gwn, :raw)
        """), rows)
    return {"ok": True, "inserted": len(rows)}

# ─────────────────────────────────────────────────────────
# Core data routes
# ─────────────────────────────────────────────────────────
@app.get("/patients")
def patients():
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT patient_id
            FROM (
                SELECT patient_id FROM vitals
                UNION ALL
                SELECT patient_id FROM meals
                UNION ALL
                SELECT patient_id FROM notes
            ) t
            WHERE patient_id IS NOT NULL AND patient_id <> ''
            ORDER BY patient_id
        """)).fetchall()
    return {"patients": [r[0] for r in rows]}

@app.get("/gateways")
def gateways():
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT id, gateway_raw, gateway_norm, COALESCE(patient_id,'') AS patient_id
            FROM gateway_map
            ORDER BY gateway_norm, id
        """)).fetchall()
    out = [{"id": rid, "gateway_raw": raw, "gateway_norm": normed, "patient_id": pid or None}
           for rid, raw, normed, pid in rows]
    return {"gateways": out}

@app.post("/map-gateway")
async def map_gateway(payload: Dict[str, Any]):
    gateway_raw = (payload.get("gateway_raw") or "").strip()
    patient_id = (payload.get("patient_id") or "").strip().lower()
    if not gateway_raw or not patient_id:
        raise HTTPException(status_code=400, detail="gateway_raw and patient_id are required")

    gateway_norm = norm_gateway(gateway_raw)
    if not gateway_norm:
        raise HTTPException(status_code=400, detail="Invalid gateway_raw")

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM gateway_map WHERE gateway_norm=:n"), {"n": gateway_norm}
        ).fetchone()
        if existing:
            conn.execute(text(
                "UPDATE gateway_map SET gateway_raw=:r, patient_id=:p WHERE gateway_norm=:n"
            ), {"r": gateway_raw, "p": patient_id, "n": gateway_norm})
        else:
            try:
                conn.execute(text(
                    "INSERT INTO gateway_map (gateway_raw, gateway_norm, patient_id) VALUES (:r, :n, :p)"
                ), {"r": gateway_raw, "n": gateway_norm, "p": patient_id})
            except IntegrityError:
                conn.execute(text(
                    "UPDATE gateway_map SET gateway_raw=:r, patient_id=:p WHERE gateway_norm=:n"
                ), {"r": gateway_raw, "p": patient_id, "n": gateway_norm})
    return {"ok": True, "gateway_norm": gateway_norm, "patient_id": patient_id}

# Accept several possible header names used by Tenovi/sample tools
def _get_webhook_key(x_webhook_key: str, x_auth_key: str, auth_key: str) -> str:
    return (x_webhook_key or x_auth_key or auth_key or "").strip()

@app.post("/webhook")
async def webhook(
    req: Request,
    x_webhook_key: str = Header(default=""),
    x_auth_key: str = Header(default=""),
    auth_key: str = Header(default="")
):
    # Protect endpoint if key configured
    sent = _get_webhook_key(x_webhook_key, x_auth_key, auth_key)
    if TENOVI_WEBHOOK_KEY and sent != TENOVI_WEBHOOK_KEY:
        raise HTTPException(status_code=401, detail="Invalid webhook key")

    payload = await req.json()
    events: List[Dict[str, Any]] = payload if isinstance(payload, list) else [payload]

    to_insert: List[Dict[str, Any]] = []
    with engine.begin() as conn:
        for r in events:
            patient = (r.get("patient_id") or r.get("patient") or "").strip().lower()
            metric  = (r.get("metric") or r.get("type") or "").strip().lower()

            # Value mapping: prefer value, then value_1, else None
            value = r.get("value")
            if value is None:
                value = r.get("value_1")

            ts      = r.get("timestamp_utc") or r.get("timestamp") or r.get("time")
            device  = r.get("device_name") or r.get("device")
            gw_raw  = r.get("gateway_id") or r.get("gateway") or r.get("gateway_mac") or r.get("mac")
            gw_norm = norm_gateway(gw_raw)

            if not patient and gw_norm:
                mapped = conn.execute(
                    text("SELECT patient_id FROM gateway_map WHERE gateway_norm=:n"),
                    {"n": gw_norm}
                ).fetchone()
                if mapped and mapped[0]:
                    patient = (mapped[0] or "").strip().lower()
            if not patient:
                patient = "unknown"

            to_insert.append({
                "patient_id": patient,
                "metric": metric,
                "value": value,
                "ts": to_iso_utc(ts),
                "device": device,
                "gateway_raw": gw_raw,
                "gateway_norm": gw_norm,
                "raw": json.dumps(r),
            })

        if to_insert:
            conn.execute(text("""
                INSERT INTO vitals (patient_id, metric, value, timestamp_utc, device_name, gateway_raw, gateway_norm, raw)
                VALUES (:patient_id, :metric, :value, :ts, :device, :gateway_raw, :gateway_norm, :raw)
            """), to_insert)

    return {"ok": True, "inserted": len(to_insert)}

@app.get("/vitals")
def get_vitals(
    patient_id: Optional[str] = Query(default=None),
    metric: Optional[str] = Query(default=None),
    hours: Optional[int] = Query(default=None),
    limit: int = Query(default=500)
):
    params: Dict[str, Any] = {}
    clauses: List[str] = []

    if patient_id:
        clauses.append("LOWER(patient_id)=:pid")
        params["pid"] = patient_id.strip().lower()
    if metric:
        clauses.append("metric=:m")
        params["m"] = metric.strip().lower()
    if hours and hours > 0:
        cut = datetime.now(timezone.utc) - timedelta(hours=hours)
        clauses.append("timestamp_utc >= :cut")
        params["cut"] = to_iso_utc(cut)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT patient_id, metric, value, timestamp_utc, device_name, gateway_norm
        FROM vitals
        {where}
        ORDER BY timestamp_utc DESC
        LIMIT :lim
    """
    params["lim"] = max(1, min(limit, 5000))

    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    return {
        "count": len(rows),
        "items": [
            {"patient_id": pid, "metric": m, "value": v, "timestamp_utc": ts,
             "device_name": dev, "gateway_norm": gwn}
            for (pid, m, v, ts, dev, gwn) in rows
        ],
    }

@app.get("/meals")
def get_meals(patient_id: str):
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT timestamp_utc, food, kcal, protein_g, carbs_g, fat_g, sodium_mg, fdc_id
            FROM meals
            WHERE LOWER(patient_id)=:p
            ORDER BY timestamp_utc DESC
            LIMIT 200
        """), {"p": patient_id.strip().lower()}).fetchall()
    items = []
    for ts, food, kcal, pr, cb, ft, na, fdc in rows:
        items.append({
            "timestamp_utc": ts, "food": food, "kcal": kcal,
            "protein_g": pr, "carbs_g": cb, "fat_g": ft, "sodium_mg": na, "fdc_id": fdc
        })
    return {"count": len(items), "items": items}

@app.post("/meals")
async def add_meal(payload: Dict[str, Any]):
    pid = (payload.get("patient_id") or "").strip().lower()
    if not pid:
        raise HTTPException(status_code=400, detail="patient_id required")
    ts = to_iso_utc(payload.get("timestamp_utc"))
    row = {
        "patient_id": pid, "timestamp_utc": ts,
        "food": payload.get("food"), "kcal": payload.get("kcal"),
        "protein_g": payload.get("protein_g"), "carbs_g": payload.get("carbs_g"),
        "fat_g": payload.get("fat_g"), "sodium_mg": payload.get("sodium_mg"),
        "fdc_id": payload.get("fdc_id"),
    }
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO meals (patient_id, timestamp_utc, food, kcal, protein_g, carbs_g, fat_g, sodium_mg, fdc_id)
            VALUES (:patient_id, :timestamp_utc, :food, :kcal, :protein_g, :carbs_g, :fat_g, :sodium_mg, :fdc_id)
        """), row)
    return {"ok": True}

@app.get("/notes")
def get_notes(patient_id: str):
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT timestamp_utc, note
            FROM notes
            WHERE LOWER(patient_id)=:p
            ORDER BY timestamp_utc DESC
            LIMIT 200
        """), {"p": patient_id.strip().lower()}).fetchall()
    return {"count": len(rows), "items": [{"timestamp_utc": r[0], "note": r[1]} for r in rows]}

@app.post("/notes")
async def add_note(payload: Dict[str, Any]):
    pid = (payload.get("patient_id") or "").strip().lower()
    if not pid:
        raise HTTPException(status_code=400, detail="patient_id required")
    ts = to_iso_utc(payload.get("timestamp_utc"))
    note = (payload.get("note") or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="note required")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO notes (patient_id, timestamp_utc, note) VALUES (:p, :t, :n)"),
                     {"p": pid, "t": ts, "n": note})
    return {"ok": True}

@app.get("/limits")
def get_limits(patient_id: Optional[str] = None):
    q = "SELECT patient_id, metric, lsl, usl FROM limits"
    params: Dict[str, Any] = {}
    if patient_id:
        q += " WHERE LOWER(COALESCE(patient_id,'')) = :p"
        params["p"] = patient_id.strip().lower()
    with engine.begin() as conn:
        rows = conn.execute(text(q), params).fetchall()
    items = [{"patient_id": pid, "metric": m, "lsl": l, "usl": u} for pid, m, l, u in rows]
    return {"count": len(items), "items": items}

@app.post("/limits")
async def set_limit(payload: Dict[str, Any]):
    metric = (payload.get("metric") or "").strip().lower()
    if not metric:
        raise HTTPException(status_code=400, detail="metric required")
    pid = (payload.get("patient_id") or None)
    if pid:
        pid = pid.strip().lower() or None
    lsl = payload.get("lsl")
    usl = payload.get("usl")
    with engine.begin() as conn:
        try:
            conn.execute(text("""
                INSERT INTO limits (patient_id, metric, lsl, usl)
                VALUES (:p, :m, :l, :u)
            """), {"p": pid, "m": metric, "l": lsl, "u": usl})
        except IntegrityError:
            conn.execute(text("""
                UPDATE limits SET lsl=:l, usl=:u
                WHERE ( (patient_id IS NULL AND :p IS NULL) OR LOWER(COALESCE(patient_id,'')) = COALESCE(:p,'') )
                  AND metric=:m
            """), {"p": (pid or None), "m": metric, "l": lsl, "u": usl})
    return {"ok": True}

# ─────────────────────────────────────────────────────────
# Local dev entry
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)

