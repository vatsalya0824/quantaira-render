# streamlit_app/Home.py
from __future__ import annotations
import streamlit as st
import pandas as pd
from fetcher import fetch_patients, backend_health

st.set_page_config(page_title="Quantaira Dashboard", layout="wide")

st.title("Quantaira Dashboard")
health = backend_health()
st.caption(f"Backend: {health.get('base_url')} — ok={health.get('ok')}")

df = fetch_patients()
if df.empty:
    st.info("No patients returned by backend.")
    st.stop()

st.subheader("Patients")
for _, r in df.iterrows():
    pid = str(r.get("id", ""))
    name = str(r.get("name", pid))
    col1, col2 = st.columns([6, 1])
    with col1:
        st.write(f"**{name}**  \n_id: {pid}_")
    with col2:
        if st.button("Open", key=f"open_{pid}"):
            # WRITE query params with the new API (no experimental calls)
            st.query_params.update({"pid": pid, "name": name})
            # jump to the patient page
            st.switch_page("pages/Patient.py")
