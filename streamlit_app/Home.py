# streamlit_app/Home.py
import streamlit as st
import pandas as pd
from fetcher import fetch_patients

st.set_page_config(page_title="Quantaira Dashboard — Patients", layout="wide")

# ─────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────
st.markdown("## 🩺 Quantaira Dashboard — Patients List")
st.markdown(
    "Displays all registered patients currently in the system. "
    "Each patient card links to their detailed vitals page."
)

# ─────────────────────────────────────────────
# Fetch patients dynamically
# ─────────────────────────────────────────────
df = fetch_patients()

if df.empty:
    st.info("No patients found yet. Data will appear automatically when Tenovi sends the first measurement.")
else:
    # Sort alphabetically
    df = df.sort_values("name", ascending=True).reset_index(drop=True)

    # Make nice patient cards
    for _, row in df.iterrows():
        pid = str(row["id"])
        name = row.get("name", f"Patient {pid}")
        with st.container(border=True):
            st.markdown(f"### 👤 {name}")
            st.markdown(f"**Patient ID:** `{pid}`")
            st.page_link(
                "pages/Patient.py",
                label="Open Patient Dashboard",
                icon="🫀",
                use_container_width=True,
                params={"pid": pid, "name": name},
            )
