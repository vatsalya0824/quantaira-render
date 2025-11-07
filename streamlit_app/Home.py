
# streamlit_app/Home.py
import streamlit as st
import pandas as pd

from fetcher import fetch_patients, backend_health

st.set_page_config(page_title="Quantaira — Home", layout="wide")

st.title("Quantaira Dashboard")
st.caption("Choose a patient to open the detail view.")

# Small health card (verifies BACKEND_URL)
health = backend_health()
with st.container(border=True):
    st.subheader("Backend status")
    if health.get("ok"):
        st.success(f"Connected to {health.get('base_url')}")
    else:
        st.error(f"Backend unreachable → {health.get('base_url')}\n\n{health.get('error')}")

# Helper to navigate without page_link(params=...)
def go_patient(pid: str, name: str):
    st.session_state["pid"] = pid
    st.session_state["name"] = name
    # put them in the URL so pages/Patient.py can read them
    try:
        st.experimental_set_query_params(pid=pid, name=name)
    except Exception:
        pass
    st.switch_page("pages/Patient.py")

# Patients list
df = fetch_patients()
if df.empty:
    st.info("No patients returned yet.")
else:
    st.write("### Patients")
    for _, row in df.iterrows():
        pid = str(row.get("id", "unknown"))
        name = str(row.get("name", f"Patient {pid}"))
        cols = st.columns([6, 2])
        with cols[0]:
            st.markdown(f"**{name}**  \n<span style='opacity:.7'>id: {pid}</span>", unsafe_allow_html=True)
        with cols[1]:
            if st.button(f"Open", key=f"open_{pid}"):
                go_patient(pid, name)
