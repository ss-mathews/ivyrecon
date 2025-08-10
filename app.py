# app.py — IvyRecon (Sales-Polished + Copy Insights)
import os
from io import BytesIO
from datetime import datetime
import json

import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth
import streamlit.components.v1 as components

from reconcile import reconcile_two, reconcile_three
from excel_export import export_errors_multitab

# ---------------- Page setup & global style ----------------
st.set_page_config(page_title="IvyRecon", page_icon="🪄", layout="wide")

# Fonts: Raleway for headings, Roboto for body; sticky header + cards
st.markdown(
    """
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Raleway:wght@500;600;700&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
      :root { --teal:#18CCAA; --navy:#2F455C; --bg2:#F6F8FA; --line:#E5E7EB; }
      html, body, [class*="css"] { font-family:"Roboto",system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; color:var(--navy); }
      h1, h2, h3, h4, h5, h6 { font-family:"Raleway",sans-serif; letter-spacing:.2px; color:var(--navy); }
      .block-container { padding-top: 0; }
      /* Sticky header bar */
      .ivy-header { position: sticky; top: 0; z-index: 50; background: #fff; border-bottom: 1px solid var(--line); }
      .ivy-header .wrap { display:flex; align-items:center; justify-content:space-between; padding: 12px 4px; }
      .ivy-brand { font-weight:700; font-size:18px; letter-spacing:.3px; }
      .ivy-badge { font-size:12px; padding:.2rem .5rem; border-radius:999px; background:var(--bg2); border:1px solid var(--line); }
      /* Buttons */
      .stButton>button { background: var(--teal); color:#0F2A37; border:0; padding:.6rem 1rem; border-radius:12px; font-weight:600; }
      .stButton>button:hover { filter:brightness(0.97); }
      /* Cards */
      .card { border:1px solid var(--line); border-radius:16px; background:#fff; padding:16px; margin: 8px 0 16px; }
      .card h3, .card h4 { margin-top: 0; }
      /* Chips */
      .chip { display:inline-flex; align-items:center; gap:.5rem; padding:.35rem .6rem; border-radius:999px; background:var(--bg2); color:var(--navy); border:1px solid var(--line); font-size:0.9rem; }
      .chip b { color: var(--navy); }
      .chip.red { background:#FFF5F5; border-color:#FEE2E2; }
      .chip.yellow { background:#FFFBEB; border-color:#FEF3C7; }
      .chip.blue { background:#EFF6FF; border-color:#DBEAFE; }
      .chip.green { background:#ECFDF5; border-color:#D1FAE5; }
      /* Impact line */
      .impact { font-family:"Raleway",sans-serif; font-size:18px; font-weight:600; margin:.25rem 0 0; }
      .muted { color:#64748B; font-size:12px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------- Auth (email + password admin) ----------------
ADMIN_EMAIL = st.secrets.get("ADMIN_EMAIL") or os.environ.get("ADMIN_EMAIL", "admin@example.com")
ADMIN_NAME = st.secrets.get("ADMIN_NAME") or os.environ.get("ADMIN_NAME", "Admin")
ADMIN_PASSWORD_HASH = st.secrets.get("ADMIN_PASSWORD_HASH") or os.environ.get("ADMIN_PASSWORD_HASH", "$2b$12$PLACEHOLDER")

credentials = {"usernames": {ADMIN_EMAIL: {"email": ADMIN_EMAIL, "name": ADMIN_NAME, "password": ADMIN_PASSWORD_HASH}}}

authenticator = stauth.Authenticate(
    credentials=credentials,
    cookie_name="ivyrecon_cookies",
    key="ivyrecon_key",
    cookie_expiry_days=1,
)

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

# Sticky header
st.markdown(
    f"""
    <div class="ivy-header">
      <div class="wrap">
        <div class="ivy-brand">IvyRecon</div>
        <div class="ivy-badge">Signed in as: <b>{(name or username) or "User"}</b></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    authenticator.logout(location="sidebar")
    st.markdown("---")
    st.caption("IvyRecon • Clean reconciliation for Payroll ↔ Carrier ↔ BenAdmin")

# ---------------- Helpers & State ----------------
if "upl_ver" not in st.session_state:
    st.session_state["upl_ver"] = 0
if "ran" not in st.session_state:
    st.session_state["ran"] = False
if "use_demo" not in st.session_state:
    st.session_state["use_demo"] = False

def load_any(uploaded) -> pd.DataFrame | None:
    if uploaded is None:
        return None
    try:
        if uploaded.name.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(uploaded)
        return pd.read_csv(uploaded)
    except Exception as e:
        st.error(f"Failed to read {uploaded.name}: {e}")
        return None

def load_demo_samples():
    try:
        p = pd.read_csv("samples/payroll_sample.csv")
        c = pd.read_csv("samples/carrier_sample.csv")
        b = pd.read_csv("samples/benadmin_sample.csv")
        return p, c, b
    except Exception as e:
        st.error(f"Could not load demo files from /samples: {e}")
        return None, None, None

def style_errors(df: pd.DataFrame):
    if df is None or df.empty:
        return df
    def _row_style(row):
        et = str(row.get("Error Type", ""))
        if et.startswith("Missing in"):      return ["background-color: #FFFBEB"] * len(row)  # yellow soft
        if "Mismatch" in et:                 return ["background-color: #FFF5F5"] * len(row)  # red soft
        if "Duplicate SSN" in et:            return ["background-color: #EFF6FF"] * len(row)  # blue soft
        return [""] * len(row)
    return df.style.apply(_row_style, axis=1)

def quick_stats(df: pd.DataFrame, label: str):
    if df is None or df.empty:
        st.metric(f"{label} Rows", 0, help="No file uploaded yet"); return
    c1, c2, c3 = st.columns(3)
    with c1: st.metric(f"{label} Rows", len(df))
    with c2: st.metric(f"{label} Unique Employees", df["SSN"].astype(str).nunique() if "SSN" in df.columns else 0)
    with c3: st.metric(f"{label} Plans", df["Plan Name"].astype(str).nunique() if "Plan Name" in df.columns else 0)

def render_error_chips(summary_df: pd.DataFrame):
    if summary_df is None or summary_df.empty:
        st.markdown('<div class="chip green"><b>No Errors</b></div>', unsafe_allow_html=True); return
    total = 0; chips = []
    for _, r in summary_df.iterrows():
        et, cnt = str(r["Error Type"]), int(r["Count"])
        if et.lower() == "total": total = cnt; continue
        color = "blue"
        if et.startswith("Missing in"): color = "yellow"
        elif "Mismatch" in et: color = "red"
        elif "Duplicate" in et: color = "blue"
        chips.append(f'<div class="chip {color}"><b>{cnt}</b> {et}</div>')
    if total: chips.append(f'<div class="chip"><b>Total:</b> {total}</div>')
    st.markdown(" ".join(chips), unsafe_allow_html=True)

def _unique_keys_count(*dfs: pd.DataFrame) -> int:
    keys = set()
    for df in dfs:
        if df is None or df.empty: continue
        cols = {c.lower(): c for c in df.columns}
        ssn_col = cols.get("ssn"); plan_col = cols.get("plan name") or cols.get("plan")
        if ssn_col and plan_col:
            ssn_series = df[ssn_col].astype(str).str.replace(r"\D", "", regex=True)
            plan_series = df[plan_col].astype(str).str.strip().str.lower()
            for ssn, plan in zip(ssn_series, plan_series):
                keys.add(f"{ssn}||{plan}")
        else:
            keys.update([f"row-{i}-df-{id(df)}" for i in range(len(df))])
    return len(keys)

def compute_insights(summary_df: pd.DataFrame, errors_df: pd.DataFrame, compared_lines: int, minutes_per_line: float, hourly_rate: float):
    total_errors = 0; most_common = "—"; mismatch_pct = 0.0
    if summary_df is not None and not summary_df.empty:
        for _, r in summary_df.iterrows():
            if str(r["Error Type"]).lower() == "total":
                total_errors = int(r["Count"]); break
        filt = summary_df[summary_df["Error Type"].str.lower() != "total"] if "Error Type" in summary_df.columns else summary_df
        if not filt.empty:
            top = filt.sort_values("Count", ascending=False).iloc[0]
            most_common = f"{top['Error Type']} ({int(top['Count'])})"
    if errors_df is not None and not errors_df.empty and compared_lines:
        mismatch_count = (errors_df["Error Type"] == "Plan Name Mismatch").sum()
        mismatch_pct = mismatch_count / compared_lines

    error_rate = (total_errors / max(1, compared_lines))
    minutes_saved = compared_lines * minutes_per_line
    hours_saved = minutes_saved / 60.0
    dollars_saved = hours_saved * hourly_rate

    return {
        "total_errors": total_errors,
        "most_common": most_common,
        "mismatch_pct": mismatch_pct,
        "error_rate": error_rate,
        "compared_lines": compared_lines,
        "minutes_saved": minutes_saved,
        "hours_saved": hours_saved,
        "dollars_saved": dollars_saved,
    }

def render_quick_insights(ins):
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1: st.metric("Lines Reconciled", f"{ins['compared_lines']:,}")
    with m2: st.metric("Error Rate", f"{ins['error_rate']:.1%}", help="Total errors / lines compared")
    with m3: st.metric("Plan Mismatch %", f"{ins['mismatch_pct']:.1%}")
    with m4: st.metric("Most Common Error", ins["most_common"])
    with m5: st.metric("Time Saved (hrs)", f"{ins['hours_saved']:,.1f}")
    st.markdown(
        f'<div class="impact">{ins["compared_lines"]:,} records • {ins["error_rate"]:.1%} error rate • '
        f'saved {ins["hours_saved"]:,.1f} hrs (~${ins["dollars_saved"]:,.0f})</div>',
        unsafe_allow_html=True,
    )
    st.caption("Assumptions configurable in Options (mins/line, hourly $).")

def download_insights_button(ins, mode, group_name, period):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "IvyRecon — Reconciliation Insights",
        f"Generated: {ts}",
        f"Mode: {mode}",
        f"Group: {group_name or '-'}   Period: {period or '-'}",
        "",
        f"Lines reconciled: {ins['compared_lines']:,}",
        f"Error rate: {ins['error_rate']:.1%}",
        f"Most common error: {ins['most_common']}",
        f"Plan name mismatches: {ins['mismatch_pct']:.1%}",
        f"Time saved (hrs): {ins['hours_saved']:.1f}",
        f"Estimated $ saved: ${ins['dollars_saved']:,.0f}",
        "",
        "Generated by IvyRecon"
    ]
    data = "\n".join(lines).encode("utf-8")
    st.download_button(
        "Download Insights (.txt)",
        data=data,
        file_name=f"ivyrecon_insights_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        mime="text/plain",
    )

def build_insights_blurb(ins, mode, group_name, period):
    return (
        f"IvyRecon — Reconciliation Insights • "
        f"{ins['compared_lines']:,} lines • {ins['error_rate']:.1%} error rate • "
        f"top issue: {ins['most_common']} • "
        f"saved ~{ins['hours_saved']:.1f} hrs (~${ins['dollars_saved']:,.0f}) • "
        f"Mode: {mode} • Group: {group_name or '-'} • Period: {period or '-'}"
    )

def copy_to_clipboard_button(label: str, text: str):
    # Safely escape text for JS using JSON
    text_js = json.dumps(text)
    components.html(
        f"""
        <button onclick='navigator.clipboard.writeText({text_js});'
                style="background:#18CCAA;color:#0F2A37;border:0;padding:8px 12px;
                       border-radius:12px;font-weight:600;cursor:pointer;">
            {label}
        </button>
        """,
        height=46,
    )

# ---------------- Tabs ----------------
st.title("IvyRecon")
st.caption("Modern, tech-forward reconciliation for Payroll • Carrier • BenAdmin")

run_tab, dashboard_tab, help_tab = st.tabs(["Run Reconciliation", "Summary Dashboard", "Help & Formatting"])

with run_tab:
    # Section: Uploads & Options (Cards)
    wrap = st.container()
    with wrap:
        up_col, opt_col = st.columns([2, 1])

        with up_col:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown("### Upload Files")
            u1, u2, u3 = st.columns(3)
            with u1:
                payroll_file = st.file_uploader(
                    "Payroll (CSV/XLSX)", type=["csv", "xlsx"], key=f"payroll_{st.session_state.upl_ver}",
                    help="CSV or Excel; header row required"
                )
            with u2:
                carrier_file = st.file_uploader(
                    "Carrier (CSV/XLSX)", type=["csv", "xlsx"], key=f"carrier_{st.session_state.upl_ver}"
                )
            with u3:
                benadmin_file = st.file_uploader(
                    "BenAdmin (CSV/XLSX)", type=["csv", "xlsx"], key=f"benadmin_{st.session_state.upl_ver}"
                )
            st.caption("Required Columns: SSN, First Name, Last Name, Plan Name, Employee Cost, Employer Cost")
            load_demo = st.button("Load Demo Files")
            st.markdown('</div>', unsafe_allow_html=True)

        with opt_col:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown("### Options")
            threshold = st.slider("Plan Name Match Threshold", 0.5, 1.0, 0.90, 0.01,
                                  help="Lower = more tolerant fuzzy matches")
            group_name = st.text_input("Group Name (export header)", value="")
            period = st.text_input("Reporting Period", value="")
            st.markdown("**ROI Assumptions**")
            minutes_per_line = st.slider("Manual mins per line", 0.5, 3.0, 1.2, 0.1,
                                         help="Estimated manual review time per employee/plan line")
            hourly_rate = st.slider("Hourly cost ($)", 15, 150, 40, 5)
            c1, c2 = st.columns(2)
            with c1:
                run_clicked = st.button("Run Reconciliation", type="primary")
            with c2:
                if st.button("Clear All"):
                    st.session_state.upl_ver += 1
                    st.session_state.ran = False
                    st.session_state.use_demo = False
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    # Load dataframes (demo or uploads)
    if load_demo:
        p_df, c_df, b_df = load_demo_samples()
        st.session_state.use_demo = True
    else:
        p_df = c_df = b_df = None

    if not st.session_state.use_demo:
        p_df = p_df or load_any(payroll_file)
        c_df = c_df or load_any(carrier_file)
        b_df = b_df or load_any(benadmin_file)

    if run_clicked:
        st.session_state.ran = True

    # Section: Previews (hidden after run)
    if not st.session_state.ran:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Previews")
        pc, cc, bc = st.columns(3)
        with pc:
            st.markdown("#### Payroll")
            st.dataframe((p_df.head(12) if p_df is not None else pd.DataFrame()), use_container_width=True)
            quick_stats(p_df, "Payroll")
        with cc:
            st.markdown("#### Carrier")
            st.dataframe((c_df.head(12) if c_df is not None else pd.DataFrame()), use_container_width=True)
            quick_stats(c_df, "Carrier")
        with bc:
            st.markdown("#### BenAdmin")
            st.dataframe((b_df.head(12) if b_df is not None else pd.DataFrame()), use_container_width=True)
            quick_stats(b_df, "BenAdmin")
        st.markdown('</div>', unsafe_allow_html=True)

    # Section: Results
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### Results")

    if st.session_state.ran:
        try:
            if p_df is not None and c_df is not None and b_df is not None:
                errors_df, summary_df = reconcile_three(p_df, c_df, b_df, plan_match_threshold=threshold)
                mode = "Three-way (Payroll vs Carrier vs BenAdmin)"
                compared_lines = _unique_keys_count(p_df, c_df, b_df)
            elif p_df is not None and c_df is not None:
                errors_df, summary_df = reconcile_two(p_df, c_df, "Payroll", "Carrier", plan_match_threshold=threshold)
                mode = "Two-way (Payroll vs Carrier)"
                compared_lines = _unique_keys_count(p_df, c_df)
            elif p_df is not None and b_df is not None:
                errors_df, summary_df = reconcile_two(p_df, b_df, "Payroll", "BenAdmin", plan_match_threshold=threshold)
                mode = "Two-way (Payroll vs BenAdmin)"
                compared_lines = _unique_keys_count(p_df, b_df)
            elif c_df is not None and b_df is not None:
                errors_df, summary_df = reconcile_two(c_df, b_df, "Carrier", "BenAdmin", plan_match_threshold=threshold)
                mode = "Two-way (Carrier vs BenAdmin)"
                compared_lines = _unique_keys_count(c_df, b_df)
            else:
                st.warning("Please upload at least two files to reconcile.")
                st.markdown('</div>', unsafe_allow_html=True)
                st.stop()

            st.success(f"Completed: {mode}")

            # Insights
            ins = compute_insights(summary_df, errors_df, compared_lines, minutes_per_line, hourly_rate)
            render_quick_insights(ins)

            # Chips + Tables
            render_error_chips(summary_df)
            left, right = st.columns([1, 2])
            with left:
                st.markdown("**Summary**")
                st.dataframe(summary_df, use_container_width=True)
            with right:
                st.markdown("**Errors**")
                if errors_df is not None and not errors_df.empty:
                    st.dataframe(style_errors(errors_df), use_container_width=True, height=520)
                else:
                    st.info("No errors found.")

            # Exports + Copy Insights
            st.markdown("#### Export")
            xlsx = export_errors_multitab(errors_df, summary_df, group_name=group_name, period=period)
            c1, c2, c3 = st.columns(3)
            with c1:
                st.download_button(
                    "Download Error Report (Excel)",
                    data=xlsx,
                    file_name=f"ivyrecon_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with c2:
                download_insights_button(ins, mode, group_name, period)
            with c3:
                blurb = build_insights_blurb(ins, mode, group_name, period)
                copy_to_clipboard_button("Copy Insights", blurb)
                st.caption("Quick blurb copied")

        except Exception as e:
            st.error(f"Error: {e}")
            st.exception(e)
    else:
        st.info("Upload 2 or 3 files and click **Run Reconciliation**.")

    st.markdown('</div>', unsafe_allow_html=True)

with dashboard_tab:
    st.subheader("Summary Dashboard")
    st.caption("High-level view of discrepancies once you run a reconciliation in the first tab.")
    st.info("Future: persist run snapshots to power trends and client reporting.")

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





