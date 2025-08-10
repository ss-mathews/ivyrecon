# app.py — IvyRecon (Polished UI)
import os
from datetime import datetime
import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth

from reconcile import reconcile_two, reconcile_three
from excel_export import export_errors_multitab

# ---------------- Page setup & global style ----------------
st.set_page_config(page_title="IvyRecon", page_icon="🪄", layout="wide")

# Fonts: Raleway for headings, Roboto for body
st.markdown(
    '''
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Raleway:wght@500;600;700&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
    :root { --teal: #18CCAA; --navy: #2F455C; --bg2: #F6F8FA; }
    html, body, [class*="css"] { font-family: "Roboto", system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; color: var(--navy); }
    h1, h2, h3, h4, h5, h6 { font-family: "Raleway", sans-serif; color: var(--navy); letter-spacing: 0.2px; }
    .block-container { padding-top: 1.25rem; }
    /* Buttons */
    .stButton>button { background: var(--teal); color: #0F2A37; border: 0; padding: 0.6rem 1.0rem; border-radius: 12px; font-weight: 600; }
    .stButton>button:hover { filter: brightness(0.95); }
    /* Chips */
    .chip { display:inline-flex; align-items:center; gap:.5rem; padding:.35rem .6rem; border-radius:999px; background:var(--bg2); color:var(--navy); border:1px solid #E5E7EB; font-size:0.9rem; }
    .chip b { color: var(--navy); }
    .chip.red { background:#FFF5F5; border-color:#FEE2E2; }
    .chip.yellow { background:#FFFBEB; border-color:#FEF3C7; }
    .chip.blue { background:#EFF6FF; border-color:#DBEAFE; }
    .chip.green { background:#ECFDF5; border-color:#D1FAE5; }
    /* Dataframe tweaks */
    .stDataFrame { border-radius: 12px; }
    </style>
    ''',
    unsafe_allow_html=True,
)

# ---------------- Auth (email + password admin) ----------------
# Prefer reading from Secrets; fallback to env for portability
ADMIN_EMAIL = st.secrets.get("ADMIN_EMAIL") or os.environ.get("ADMIN_EMAIL", "admin@example.com")
ADMIN_NAME = st.secrets.get("ADMIN_NAME") or os.environ.get("ADMIN_NAME", "Admin")
ADMIN_PASSWORD_HASH = st.secrets.get("ADMIN_PASSWORD_HASH") or os.environ.get("ADMIN_PASSWORD_HASH", "$2b$12$PLACEHOLDER")

credentials = {
    "usernames": {
        ADMIN_EMAIL: {
            "email": ADMIN_EMAIL,
            "name": ADMIN_NAME,
            "password": ADMIN_PASSWORD_HASH,
        }
    }
}

authenticator = stauth.Authenticate(
    credentials=credentials,
    cookie_name="ivyrecon_cookies",
    key="ivyrecon_key",
    cookie_expiry_days=1,
)

# Render login & read session state (new API)
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

# Signed-in sidebar w/ logout
with st.sidebar:
    st.markdown(f"**Signed in as:** {name or username}")
    authenticator.logout(location="sidebar")
    st.markdown("---")
    st.caption("IvyRecon • Clean reconciliation for Payroll ↔ Carrier ↔ BenAdmin")

# ---------------- Helpers ----------------
REQUIRED_COLS = ["SSN", "First Name", "Last Name", "Plan Name", "Employee Cost", "Employer Cost"]

def load_any(uploaded) -> pd.DataFrame | None:
    if not uploaded:
        return None
    try:
        if uploaded.name.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(uploaded)
        return pd.read_csv(uploaded)
    except Exception as e:
        st.error(f"Failed to read {uploaded.name}: {e}")
        return None

# Color-row styling based on Error Type
def style_errors(df: pd.DataFrame):
    if df is None or df.empty:
        return df
    def _row_style(row):
        et = str(row.get("Error Type", ""))
        if et.startswith("Missing in"):
            return ["background-color: #FFFBEB"] * len(row)  # yellow soft
        if "Mismatch" in et:
            return ["background-color: #FFF5F5"] * len(row)  # red soft
        if "Duplicate SSN" in et:
            return ["background-color: #EFF6FF"] * len(row)  # blue soft
        return [""] * len(row)
    return df.style.apply(_row_style, axis=1)

# Quick stats block

def quick_stats(df: pd.DataFrame, label: str):
    if df is None or df.empty:
        st.metric(f"{label} Rows", 0, help="No file uploaded yet")
        return
    c1, c2, c3 = st.columns(3)
    with c1: st.metric(f"{label} Rows", len(df))
    with c2: st.metric(f"{label} Unique Employees", df["SSN"].astype(str).nunique() if "SSN" in df.columns else 0)
    with c3: st.metric(f"{label} Plans", df["Plan Name"].astype(str).nunique() if "Plan Name" in df.columns else 0)

# Error summary chips

def render_error_chips(summary_df: pd.DataFrame):
    if summary_df is None or summary_df.empty:
        st.markdown('<div class="chip green"><b>No Errors</b></div>', unsafe_allow_html=True)
        return
    # Remove Total from per-type chips; show it last
    total = 0
    chips = []
    for _, r in summary_df.iterrows():
        et, cnt = str(r["Error Type"]), int(r["Count"])
        if et.lower() == "total":
            total = cnt
            continue
        color = "blue"
        if et.startswith("Missing in"): color = "yellow"
        elif "Mismatch" in et: color = "red"
        elif "Duplicate" in et: color = "blue"
        chips.append(f'<div class="chip {color}"><b>{cnt}</b> {et}</div>')
    if total:
        chips.append(f'<div class="chip"><b>Total:</b> {total}</div>')
    st.markdown(" ".join(chips), unsafe_allow_html=True)

# ---------------- Tabs ----------------
st.title("IvyRecon")
st.caption("Modern, tech-forward reconciliation for Payroll • Carrier • BenAdmin")

run_tab, dashboard_tab, help_tab = st.tabs(["Run Reconciliation", "Summary Dashboard", "Help & Formatting"])

with run_tab:
    # Upload + Controls
    st.subheader("Upload Files")
    up1, up2, up3 = st.columns(3)
    with up1:
        payroll_file = st.file_uploader("Payroll (CSV/XLSX)", type=["csv", "xlsx"], key="payroll")
    with up2:
        carrier_file = st.file_uploader("Carrier (CSV/XLSX)", type=["csv", "xlsx"], key="carrier")
    with up3:
        benadmin_file = st.file_uploader("BenAdmin (CSV/XLSX)", type=["csv", "xlsx"], key="benadmin")

    st.markdown("**Required Columns:** `SSN, First Name, Last Name, Plan Name, Employee Cost, Employer Cost`")

    ctl1, ctl2, ctl3, ctl4 = st.columns([1,1,1,1])
    with ctl1:
        threshold = st.slider("Plan Name Match Threshold", 0.5, 1.0, 0.90, 0.01, help="Lower = more tolerant fuzzy matches")
    with ctl2:
        group_name = st.text_input("Group Name (for export header)", value="")
    with ctl3:
        period = st.text_input("Reporting Period", value="")
    with ctl4:
        run = st.button("Run Reconciliation", type="primary")

    # Previews & Quick stats
    p_df = load_any(payroll_file)
    c_df = load_any(carrier_file)
    b_df = load_any(benadmin_file)

    pcol, ccol, bcol = st.columns(3)
    with pcol:
        st.markdown("#### Payroll Preview")
        st.dataframe((p_df.head(15) if p_df is not None else pd.DataFrame()), use_container_width=True)
        quick_stats(p_df, "Payroll")
    with ccol:
        st.markdown("#### Carrier Preview")
        st.dataframe((c_df.head(15) if c_df is not None else pd.DataFrame()), use_container_width=True)
        quick_stats(c_df, "Carrier")
    with bcol:
        st.markdown("#### BenAdmin Preview")
        st.dataframe((b_df.head(15) if b_df is not None else pd.DataFrame()), use_container_width=True)
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

            # Error chips
            render_error_chips(summary_df)

            left, right = st.columns([1, 2])
            with left:
                st.markdown("**Summary**")
                st.dataframe(summary_df, use_container_width=True)
            with right:
                st.markdown("**Errors**")
                if errors_df is not None and not errors_df.empty:
                    st.dataframe(style_errors(errors_df), use_container_width=True, height=480)
                else:
                    st.info("No errors found.")

            st.markdown("### Export")
            xlsx = export_errors_multitab(errors_df, summary_df, group_name=group_name, period=period)
            st.download_button(
                "Download Excel (multi-tab)",
                data=xlsx,
                file_name=f"ivyrecon_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except Exception as e:
            st.error(f"Error: {e}")
            st.exception(e)
    else:
        st.info("Upload 2 or 3 files and click **Run Reconciliation**.")

with dashboard_tab:
    st.subheader("Summary Dashboard")
    st.caption("High-level view of discrepancies once you run a reconciliation in the first tab.")
    st.info("Run a reconciliation on the first tab. A future version will persist run snapshots for dashboarding.")

with help_tab:
    st.subheader("How to format your files")
    st.markdown(
        """
        **Required columns** (case-insensitive):
        - `SSN`
        - `First Name`
        - `Last Name`
        - `Plan Name`
        - `Employee Cost`
        - `Employer Cost`

        **File types**: CSV or Excel (first sheet).

        **Plan Name matching**: IvyRecon uses fuzzy matching and normalization. Common aliases like **Medical/Health**, **STD/Short Term Disability**, **LTD/Long Term Disability** are recognized. Adjust the **Plan Name Match Threshold** slider if needed (lower = more tolerant).

        **Exports**: Multi-tab Excel includes a Summary sheet, one sheet per error type, plus an All Errors sheet. Headers are styled to your brand.
        """
    )



