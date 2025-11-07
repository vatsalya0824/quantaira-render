# streamlit_app/Home.py
import streamlit as st
import pandas as pd

from fetcher import fetch_patients, backend_health

st.set_page_config(page_title="Quantaira Dashboard", layout="wide")

st.markdown("# Quantaira Dashboard")

bh = backend_health()
if bh.get("ok"):
    st.caption(f"Backend: [{bh['base_url']}]({bh['base_url']}) — ok=True")
else:
    st.caption(f"Backend: {bh.get('base_url','?')} — ok=False")

patients = fetch_patients()
if patients.empty:
    st.info("No patients found.")
else:
    st.markdown("## Patients ↪️")

    def go_patient(pid: str, name: str):
        # Use ONLY modern API to avoid deprecation conflicts
        st.query_params["pid"] = pid
        st.query_params["name"] = name
        st.switch_page("pages/Patient.py")

    for _, row in patients.iterrows():
        name = str(row.get("name", "Patient"))
        pid  = str(row.get("id",  "unknown"))
        with st.container(border=True):
            left, right = st.columns([6,1])
            with left:
                st.markdown(f"**{name}**\n\n*id: {pid}*")
            with right:
                if st.button("Open", key=f"open_{pid}"):
                    go_patient(pid, name)
