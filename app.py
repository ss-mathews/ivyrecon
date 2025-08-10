# app.py — IvyRecon (Streamlit)
import os
import io
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth

from reconcile import reconcile_two, reconcile_three
from excel_export import export_errors_multitab

# ---------------- Page setup & branding ----------------
st.set_page_config(page_title="IvyRecon", page_icon="🪄", layout="wide")
st.markdown('''
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&family=Raleway:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root { --primary: #18CCAA; --navy: #2F455C; }
html, body, [class*="css"]  {
  font-family: "Montserrat", "Raleway", system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
  color: var(--navy);
}
h1, h2, h3, h4 { color: var(--navy); }
.block-container { padding-top: 2rem; }
</style>
''', unsafe_allow_html=True)

# ---------------- Auth (email + password admin) ----------------
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Admin")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "$2b$12$PLACEHOLDERHASH")

# streamlit-authenticator expects a credentials dict with hashed password
credentials = {
    "usernames": {
        ADMIN_EMAIL: {
            "email": ADMIN_EMAIL,
            "name": ADMIN_NAME,
            "password": ADMIN_PASSWORD_HASH,  # argon/bcrypt hash string
        }
    }
}

# Create authenticator object (newer API)
authenticator = stauth.Authenticate(
    credentials=credentials,
    cookie_name="ivyrecon_cookies",
    key="ivyrecon_key",
    cookie_expiry_days=1,
)

# Render login UI, then read values from session_state (new API style)
authenticator.login(location="main")
auth_status = st.session_state.get("authentication_status")
name = st.session_state.get("name")
username = st.session_state.get("username")

if auth_status is False:
    st.error("Invalid credentials")
    st.stop()
elif auth_status is None:
    st.info("Enter your email and password to continue.")
    st.stop()

# Authenticated — show sidebar + logout
with st.sidebar:
    st.write(f"**Signed in as:** {name or username}")
    authenticator.logout(location="sidebar")

# ---------------- App UI ----------------
st.title("IvyRecon")
st.caption("Payroll ↔ Carrier ↔ BenAdmin reconciliation — clean, fast, and accurate.")

# Uploads (in-memory only; no disk writes)
cols = st.columns(3)
with cols[0]:
    payroll_file = st.file_uploader("Payroll (CSV/XLSX)", type=["csv", "xlsx"], key="payroll")
with cols[1]:
    carrier_file = st.file_uploader("Carrier (CSV/XLSX)", type=["csv", "xlsx"], key="carrier")
with cols[2]:
    benadmin_file = st.file_uploader("BenAdmin (CSV/XLSX)", type=["csv", "xlsx"], key="benadmin")

st.markdown("**Required Columns:** `SSN, First Name, Last Name, Plan Name, Employee Cost, Employer Cost`")

ctl1, ctl2, ctl3, ctl4 = st.columns([1,1,1,1])
with ctl1:
    threshold = st.slider("Plan Name Match Threshold", 0.5, 1.0, 0.90, 0.01)
with ctl2:
    group_name = st.text_input("Group Name (for export header)", value="")
with ctl3:
    period = st.text_input("Reporting Period", value="")
with ctl4:
    run = st.button("Run Reconciliation", type="primary")

def load_any(file) -> pd.DataFrame | None:
    if not file:
        return None
    try:
        if file.name.lower().endswith(('.xlsx', '.xls')):
            return pd.read_excel(file)
        return pd.read_csv(file)
    except Exception as e:
        st.error(f"Failed to read {file.name}: {e}")
        return None

def quick_stats(df: pd.DataFrame, label: str):
    if df is None: return
    c1, c2, c3 = st.columns(3)
    with c1: st.metric(f"{label} Rows", len(df))
    with c2: st.metric(f"{label} Unique Employees", df['SSN'].astype(str).nunique() if 'SSN' in df.columns else 0)
    with c3: st.metric(f"{label} Plans", df['Plan Name'].astype(str).nunique() if 'Plan Name' in df.columns else 0)

# Previews
p_df = load_any(payroll_file)
c_df = load_any(carrier_file)
b_df = load_any(benadmin_file)

colp, colc, colb = st.columns(3)
with colp:
    st.subheader("Payroll")
    st.dataframe(p_df.head(15) if p_df is not None else pd.DataFrame(), use_container_width=True)
    quick_stats(p_df, "Payroll")
with colc:
    st.subheader("Carrier")
    st.dataframe(c_df.head(15) if c_df is not None else pd.DataFrame(), use_container_width=True)
    quick_stats(c_df, "Carrier")
with colb:
    st.subheader("BenAdmin")
    st.dataframe(b_df.head(15) if b_df is not None else pd.DataFrame(), use_container_width=True)
    quick_stats(b_df, "BenAdmin")

st.markdown("---")
st.subheader("Results")

if run:
    try:
        if p_df is not None and c_df is not None and b_df is not None:
            errors_df, summary_df = reconcile_three(p_df, c_df, b_df, plan_match_threshold=threshold)
            mode = "Three-way (Payroll vs Carrier vs BenAdmin)"
        elif p_df is not None and c_df is not None:
            errors_df, summary_df = reconcile_two(p_df, c_df, "Payroll", "Carrier", plan_match_threshold=threshold)
            mode = "Two-way (Payroll vs Carrier)"
        elif p_df is not None and b_df is not None:
            errors_df, summary_df = reconcile_two(p_df, b_df, "Payroll", "BenAdmin", plan_match_threshold=threshold)
            mode = "Two-way (Payroll vs BenAdmin)"
        elif c_df is not None and b_df is not None:
            errors_df, summary_df = reconcile_two(c_df, b_df, "Carrier", "BenAdmin", plan_match_threshold=threshold)
            mode = "Two-way (Carrier vs BenAdmin)"
        else:
            st.warning("Please upload at least two files to reconcile.")
            st.stop()

        st.success(f"Completed: {mode}")
        left, right = st.columns([1,2])
        with left:
            st.markdown("**Summary**")
            st.dataframe(summary_df, use_container_width=True)
        with right:
            st.markdown("**Errors**")
            st.dataframe(errors_df, use_container_width=True, height=450)

        st.markdown("### Export")
        xlsx = export_errors_multitab(errors_df, summary_df, group_name=group_name, period=period)
        st.download_button(
            "Download Excel (multi-tab)",
            data=xlsx,
            file_name=f"ivyrecon_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        st.error(f"Error: {e}")
        st.exception(e)
else:
    st.info("Upload 2 or 3 files and click **Run Reconciliation**.")


