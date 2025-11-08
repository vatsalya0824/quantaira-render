# db.py
from __future__ import annotations
import os
import sqlalchemy as sa
from sqlalchemy import text

DATABASE_URL = os.environ.get("DATABASE_URL", "")

engine = sa.create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

def init_db() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS measurements (
      id           BIGSERIAL PRIMARY KEY,
      created_utc  TIMESTAMPTZ NOT NULL,
      patient_id   TEXT NOT NULL,
      metric       TEXT NOT NULL,
      value_1      DOUBLE PRECISION,
      value_2      DOUBLE PRECISION,
      unit         TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_meas_created ON measurements (created_utc);
    CREATE INDEX IF NOT EXISTS idx_meas_pid      ON measurements (patient_id);
    CREATE INDEX IF NOT EXISTS idx_meas_metric   ON measurements (metric);

    -- to dedupe body deliveries
    CREATE TABLE IF NOT EXISTS webhook_bodies (
      body_sha     TEXT PRIMARY KEY,
      received_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    with engine.begin() as conn:
        conn.execute(text(schema))
