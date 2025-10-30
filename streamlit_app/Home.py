# streamlit_app/Home.py — Patient Overview Dashboard (Render-ready)

import streamlit as st
import requests
import pandas as pd
from datetime import datetime

API_BASE = "https://hurtlingly-insurable-crysta.ngrok-free.dev"  # replace with Render backend URL later

st.set_page_config(page_title="Quantaira Dashboard — Home", layout="wide")

st.title("👩‍⚕️ Quantaira Dashboard — Patient Overview")

st.caption("Displays all registered patients and their mean vital statistics.")

# Fetch patient list
@st.cache_data(ttl=60)
def get_patients():
    try:
        r = requests.get(f"{API_BASE}/patients", timeout=10)
        r.raise_for_status()
        return r.json().get("patients", [])
    except Exception as e:
        st.error(f"Error fetching patients: {e}")
        return []

patients = get_patients()

if not patients:
    st.info("No patients found yet. Add data via Tenovi or webhook.")
    st.stop()

# Layout grid
cols = st.columns(3, gap="large")
for i, pid in enumerate(patients):
    with cols[i % 3]:
        st.markdown(f"### 🩺 {pid.title()}")
        # Get last 24h vitals
        try:
            r = requests.get(f"{API_BASE}/vitals", params={"patient_id": pid, "hours": 24, "limit": 1000}, timeout=10)
            data = r.json().get("items", [])
            df = pd.DataFrame(data)
            if not df.empty:
                mean_pulse = df[df["metric"] == "pulse"]["value"].mean()
                mean_spo2 = df[df["metric"] == "spo2"]["value"].mean()
                mean_sys = df[df["metric"] == "systolic_bp"]["value"].mean()
                mean_dia = df[df["metric"] == "diastolic_bp"]["value"].mean()

                st.metric("Pulse (avg)", f"{mean_pulse:.1f} bpm" if not pd.isna(mean_pulse) else "—")
                st.metric("SpO₂ (avg)", f"{mean_spo2:.1f} %" if not pd.isna(mean_spo2) else "—")
                st.metric("BP (avg)", f"{mean_sys:.0f}/{mean_dia:.0f} mmHg" if not pd.isna(mean_sys) else "—")
            else:
                st.text("No vitals recorded.")
        except Exception:
            st.text("—")

        if st.button("Open Details", key=f"btn_{pid}"):
            # Navigate to Patient.py page with params
            st.query_params["pid"] = pid
            st.query_params["name"] = pid.title()
            st.switch_page("pages/Patient.py")