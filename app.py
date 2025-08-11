# app.py — IvyRecon (Sales-Polished + Aliases + Column Mapper + Themed Excel)
import os
from datetime import datetime
import json

import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth
import streamlit.components.v1 as components

from reconcile import reconcile_two, reconcile_three
from excel_export import export_errors_multitab
from aliases import (
    DEFAULT_ALIASES, load_aliases_from_secrets, normalize_alias_dict,
    merge_aliases, apply_aliases_to_df,
)

# ---------------- Page setup & global style ----------------
st.set_page_config(page_title="IvyRecon", page_icon="🪄", layout="wide")

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
      .ivy-header { position: sticky; top: 0; z-index: 50; background: #fff; border-bottom: 1px solid var(--line); }
      .ivy-header .wrap { display:flex; align-items:center; justify-content:space-between; padding: 12px 4px; }
      .ivy-brand { font-weight:700; font-size:18px; letter-spacing:.3px; }
      .ivy-badge { font-size:12px; padding:.2rem .5rem; border-radius:999px; background:var(--bg2); border:1px solid var(--line); }
      .stButton>button { background: var(--teal); color:#0F2A37; border:0; padding:.6rem 1rem; border-radius:12px; font-weight:600; }
      .stButton>button:hover { filter:brightness(0.97); }
      .card { border:1px solid var(--line); border-radius:16px; background:#fff; padding:16px; margin: 8px 0 16px; }
      .card h3, .card h4 { margin-top: 0; }
      .chip { display:inline-flex; align-items:center; gap:.5rem; padding:.35rem .6rem; border-radius:999px; background:var(--bg2); color:var(--navy); border:1px solid var(--line); font-size:0.9rem; }
      .chip b { color: var(--navy); }
      .chip.red { background:#FFF5F5; border-color:#FEE2E2; }
      .chip.yellow { background:#FFFBEB; border-color:#FEF3C7; }
      .chip.blue { background:#EFF6FF; border-color:#DBEAFE; }
      .chip.green { background:#ECFDF5; border-color:#D1FAE5; }
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
    st.error("Invalid credentials"); st.stop()
elif auth_status is None:
    st.info("Enter your email and password to continue."); st.stop()

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

# ---------------- State ----------------
if "upl_ver" not in st.session_state: st.session_state["upl_ver"] = 0
if "ran" not in st.session_state: st.session_state["ran"] = False
if "use_demo" not in st.session_state: st.session_state["use_demo"] = False
if "aliases" not in st.session_state:
    from_secrets = load_aliases_from_secrets(st)
    st.session_state["aliases"] = merge_aliases(DEFAULT_ALIASES, normalize_alias_dict(from_secrets))

REQUIRED = ["SSN", "First Name", "Last Name", "Plan Name", "Employee Cost", "Employer Cost"]

# ---------------- Helpers ----------------
def load_any(uploaded) -> pd.DataFrame | None:
    if uploaded is None: return None
    try:
        if uploaded.name.lower().endswith((".xlsx", ".xls")):
            return pd.read_excel(uploaded)
        return pd.read_csv(uploaded)
    except Exception as e:
        st.error(f"Failed to read {uploaded.name}: {e}"); return None

def style_errors(df: pd.DataFrame):
    if df is None or df.empty: return df
    def _row_style(row):
        et = str(row.get("Error Type", ""))
        if et.startswith("Missing in"): return ["background-color: #FFFBEB"] * len(row)
        if "Mismatch" in et:            return ["background-color: #FFF5F5"] * len(row)
        if "Duplicate SSN" in et:       return ["background-color: #EFF6FF"] * len(row)
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

def _clean_money(x):
    if pd.isna(x): return pd.NA
    s = str(x).strip()
    if s == "": return pd.NA
    s = s.replace("$", "").replace(",", "")
    try:
        return float(s)
    except:
        return pd.NA

def standardize_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize columns used for matching/comparison:
    - SSN -> digits only, keep leading zeros (string)
    - Plan Name -> trimmed/lower, alias-normalized later
    - Names -> trimmed title-case (doesn’t affect matching but keeps tidy)
    - Amounts -> numeric (strip $, ,)
    - Strip surrounding spaces on all string columns
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    # Trim all string-like columns
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()

    # Tolerant column access
    cols = {c.lower(): c for c in df.columns}
    ssn_col = cols.get("ssn")
    plan_col = cols.get("plan name") or cols.get("plan")
    fn_col = cols.get("first name")
    ln_col = cols.get("last name")
    ee_amt_col = cols.get("employee cost") or cols.get("employee amount") or cols.get("ee amount")
    er_amt_col = cols.get("employer cost") or cols.get("employer amount") or cols.get("er amount")

    # SSN -> digits-only, keep leading zeros, as string
    if ssn_col:
        df[ssn_col] = (
            df[ssn_col]
            .astype(str)
            .str.replace(r"\D", "", regex=True)
            .str.zfill(9)
        )

    # Names tidy (optional; not used for joins)
    if fn_col: df[fn_col] = df[fn_col].astype(str).str.strip().str.title()
    if ln_col: df[ln_col] = df[ln_col].astype(str).str.strip().str.title()

    # Plan tidy (alias normalization will run later)
    if plan_col:
        df[plan_col] = df[plan_col].astype(str).str.strip().str.lower()

    # Amounts to numeric
    if ee_amt_col:
        df[ee_amt_col] = df[ee_amt_col].apply(_clean_money)
    if er_amt_col:
        df[er_amt_col] = df[er_amt_col].apply(_clean_money)

    return df

# --- Duplicate handling helpers ---

def _present_cols(df, wanted):
    return [c for c in wanted if c in df.columns]

def dedupe_exact(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Drop exact duplicates on key fields so 2 identical payroll lines vs 1 BenAdmin line
    doesn't create a false 'Missing in BenAdmin'.
    """
    if df is None or df.empty:
        return df, 0
    key_cols = _present_cols(df, ["SSN", "Plan Name", "Employee Cost", "Employer Cost"])
    if not key_cols:
        return df, 0
    before = len(df)
    out = df.drop_duplicates(subset=key_cols, keep="first").reset_index(drop=True)
    return out, before - len(out)

def aggregate_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Collapse duplicates by summing amounts per (SSN, Plan Name).
    Keeps first/last names from the first occurrence.
    """
    if df is None or df.empty:
        return df, 0
    req = _present_cols(df, ["SSN", "Plan Name"])
    if not req:
        return df, 0
    # amounts that may exist
    amt_cols = _present_cols(df, ["Employee Cost", "Employer Cost"])
    name_cols = _present_cols(df, ["First Name", "Last Name"])
    group_cols = req
    before = len(df)
    agg_dict = {c: "sum" for c in amt_cols}
    for nc in name_cols:
        agg_dict[nc] = "first"
    out = (
        df.groupby(group_cols, dropna=False, as_index=False)
          .agg(agg_dict) if agg_dict else df.groupby(group_cols, dropna=False, as_index=False).size()
    )
    return out.reset_index(drop=True), before - len(out)

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

def _to_float_or_none(v):
    try:
        if pd.isna(v): 
            return None
        s = str(v).strip()
        if s == "": 
            return None
        return float(s)
    except Exception:
        return None

def _money_eq(a, b, tolerance_cents: int, blank_is_zero: bool) -> bool:
    A = _to_float_or_none(a)
    B = _to_float_or_none(b)
    if A is None and B is None:
        return True
    if blank_is_zero:
        A = 0.0 if A is None else A
        B = 0.0 if B is None else B
    if A is None or B is None:
        return False
    tol = max(0, int(tolerance_cents)) / 100.0
    return abs(A - B) <= tol

def _columns_for_amount(row, base_name: str):
    """
    errors_df may label sides variously, e.g.:
      'Employer Cost (Payroll)', 'Employer Cost (BenAdmin)'
    Return two values if found; else (None, None).
    """
    vals = []
    for c in row.index:
        lc = c.lower()
        if base_name.lower() in lc and "(" in lc and ")" in lc:
            vals.append(row[c])
    if len(vals) >= 2:
        return vals[0], vals[1]
    return None, None

def postfilter_amount_mismatches(errors_df: pd.DataFrame, summary_df: pd.DataFrame,
                                 tolerance_cents: int, blank_is_zero: bool):
    if errors_df is None or errors_df.empty:
        return errors_df, summary_df, 0
    keep = []
    removed = 0
    for idx, row in errors_df.iterrows():
        et = str(row.get("Error Type", "")).lower()
        if "employer cost mismatch" in et:
            a, b = _columns_for_amount(row, "employer cost")
            if _money_eq(a, b, tolerance_cents, blank_is_zero):
                removed += 1
                continue
        if "employee cost mismatch" in et:
            a, b = _columns_for_amount(row, "employee cost")
            if _money_eq(a, b, tolerance_cents, blank_is_zero):
                removed += 1
                continue
        keep.append(idx)
    filtered = errors_df.loc[keep].reset_index(drop=True)
    # rebuild summary from filtered
    if summary_df is not None and not summary_df.empty and "Error Type" in summary_df.columns:
        new_summary = (
            filtered.groupby("Error Type", dropna=False)
                    .size().reset_index(name="Count")
            if not filtered.empty else pd.DataFrame({"Error Type": ["Total"], "Count": [0]})
        )
        if not filtered.empty:
            total = pd.DataFrame({"Error Type": ["Total"], "Count": [int(new_summary["Count"].sum())]})
            new_summary = pd.concat([new_summary, total], ignore_index=True)
    else:
        new_summary = summary_df
    return filtered, new_summary, removed


def compute_insights(summary_df: pd.DataFrame, errors_df: pd.DataFrame, compared_lines: int, minutes_per_line: float, hourly_rate: float):
    total_errors = 0; most_common = "—"; mismatch_pct = 0.0
    if summary_df is not None and not summary_df.empty:
        for _, r in summary_df.iterrows():
            if str(r["Error Type"]).lower() == "total": total_errors = int(r["Count"]); break
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

    return {"total_errors": total_errors, "most_common": most_common, "mismatch_pct": mismatch_pct,
            "error_rate": error_rate, "compared_lines": compared_lines, "minutes_saved": minutes_saved,
            "hours_saved": hours_saved, "dollars_saved": dollars_saved}

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

run_tab, dashboard_tab, settings_tab, help_tab = st.tabs(
    ["Run Reconciliation", "Summary Dashboard", "Settings", "Help & Formatting"]
)

# ---------- SETTINGS: Alias Manager ----------
with settings_tab:
    st.markdown("### Alias Manager")
    st.caption("Control plan name synonyms without code. Format: canonical → aliases list.")
    current = st.session_state["aliases"]

    colA, colB = st.columns(2)
    with colA:
        st.markdown("**Current Aliases (JSON)**")
        alias_text = st.text_area("",
            value=json.dumps(current, indent=2),
            height=260, label_visibility="collapsed"
        )
        if st.button("Save Aliases"):
            try:
                user_dict = json.loads(alias_text)
                st.session_state["aliases"] = merge_aliases({}, normalize_alias_dict(user_dict))
                st.success("Aliases saved for this session.")
            except Exception as e:
                st.error(f"Invalid JSON: {e}")

        st.download_button(
            "Download Aliases (.json)",
            data=json.dumps(st.session_state["aliases"], indent=2).encode("utf-8"),
            file_name="ivyrecon_plan_aliases.json",
            mime="application/json",
        )

    with colB:
        st.markdown("**Load Aliases (.json)**")
        uploaded = st.file_uploader("Upload aliases JSON", type=["json"], key="alias_json")
        if uploaded:
            try:
                js = json.load(uploaded)
                st.session_state["aliases"] = merge_aliases({}, normalize_alias_dict(js))
                st.success("Aliases loaded for this session.")
            except Exception as e:
                st.error(f"Invalid file: {e}")

        st.markdown("**Tips**")
        st.write("- Canonical names should be lowercase, e.g., `short term disability`.")
        st.write("- Put aliases like `std`, `short-term disability` under that canonical.")

# ---------- RUN: Column Mapper + Reconcile ----------
with run_tab:
    # Uploads + Options in cards
    up_col, opt_col = st.columns([2, 1])
    with up_col:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Upload Files")
        u1, u2, u3 = st.columns(3)
        with u1:
            payroll_file = st.file_uploader("Payroll (CSV/XLSX)", type=["csv", "xlsx"], key=f"payroll_{st.session_state.upl_ver}")
        with u2:
            carrier_file = st.file_uploader("Carrier (CSV/XLSX)", type=["csv", "xlsx"], key=f"carrier_{st.session_state.upl_ver}")
        with u3:
            benadmin_file = st.file_uploader("BenAdmin (CSV/XLSX)", type=["csv", "xlsx"], key=f"benadmin_{st.session_state.upl_ver}")
        st.caption("Required Columns: SSN, First Name, Last Name, Plan Name, Employee Cost, Employer Cost")
        st.markdown('</div>', unsafe_allow_html=True)

    with opt_col:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Options")
        threshold = st.slider("Plan Name Match Threshold", 0.5, 1.0, 0.90, 0.01, help="Lower = more tolerant fuzzy matches")
        group_name = st.text_input("Group Name (export header)", value="")
        period = st.text_input("Reporting Period", value="")
        st.markdown("**ROI Assumptions**")
        minutes_per_line = st.slider("Manual mins per line", 0.5, 3.0, 1.2, 0.1)
        hourly_rate = st.slider("Hourly cost ($)", 15, 150, 40, 5)
        # right after hourly_rate slider
        treat_blank_as_zero = st.checkbox(
            "Treat blank amounts as $0.00",
            value=True,
            help="If checked, empty Employer/Employee Cost cells are treated as zero."
        )

        amount_tolerance_cents = st.slider(
            "Amount tolerance (cents)",
            0, 25, 1, 1,
            help="Ignore tiny differences due to rounding (e.g., 1 = $0.01)."
        )

        # --- NEW: Duplicate handling selectbox ---
        dup_mode = st.selectbox(
        "Duplicate handling",
        ["Ignore exact duplicates (recommended)", "Aggregate duplicates (sum amounts)", "Keep all (strict)"],
        index=0,
        help="How to treat multiple identical lines per SSN/Plan."
    )
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

    # Load data
    p_df = load_any(payroll_file)
    c_df = load_any(carrier_file)
    b_df = load_any(benadmin_file)

    # Standardize all
    p_df = standardize_df(p_df)
    c_df = standardize_df(c_df)
    b_df = standardize_df(b_df)


    # Column Mapper UI per file (expander)
    def map_columns_ui(df: pd.DataFrame, label: str):
        if df is None or df.empty:
            st.info(f"No {label} file uploaded."); return None, {}
        st.markdown(f"**{label} columns detected:** {', '.join(df.columns.map(str))}")
        with st.expander(f"Map {label} columns (if headers differ)"):
            cols_lower = {c.lower(): c for c in df.columns}
            mapping = {}
            for req in REQUIRED:
                # guess by lowercase exact or fuzzy contains
                guess = cols_lower.get(req.lower())
                if not guess:
                    # simple contains guess
                    for c in df.columns:
                        if req.lower().replace(" ", "") in str(c).lower().replace(" ", ""):
                            guess = c; break
                mapping[req] = st.selectbox(f"{req} →", options=["-- choose --", *list(df.columns)], index=(list(df.columns).index(guess) + 1) if guess in df.columns else 0)
            if st.button(f"Apply Mapping for {label}"):
                # build rename dict
                rename = {}
                for req, chosen in mapping.items():
                    if chosen and chosen != "-- choose --":
                        rename[chosen] = req
                new_df = df.rename(columns=rename)
                # warn about missing
                missing = [req for req in REQUIRED if req not in new_df.columns]
                if missing:
                    st.warning(f"{label}: missing required columns after mapping → {', '.join(missing)}")
                else:
                    st.success(f"{label}: mapping applied.")
                return new_df, mapping
        return df, {}

    # Show previews only before run
    if not st.session_state.ran:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Previews & Column Mapper")
        pcol, ccol, bcol = st.columns(3)
        with pcol:
            st.markdown("#### Payroll")
            p_df, _ = map_columns_ui(p_df, "Payroll")
            st.dataframe((p_df.head(12) if p_df is not None else pd.DataFrame()), use_container_width=True)
            if p_df is not None: quick_stats(p_df, "Payroll")
        with ccol:
            st.markdown("#### Carrier")
            c_df, _ = map_columns_ui(c_df, "Carrier")
            st.dataframe((c_df.head(12) if c_df is not None else pd.DataFrame()), use_container_width=True)
            if c_df is not None: quick_stats(c_df, "Carrier")
        with bcol:
            st.markdown("#### BenAdmin")
            b_df, _ = map_columns_ui(b_df, "BenAdmin")
            st.dataframe((b_df.head(12) if b_df is not None else pd.DataFrame()), use_container_width=True)
            if b_df is not None: quick_stats(b_df, "BenAdmin")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown("### Results")

    if run_clicked:
        st.session_state.ran = True

    if st.session_state.ran:
        try:
            # Apply aliases normalization to plan names before reconcile
            aliases = st.session_state["aliases"]
            for df in (p_df, c_df, b_df):
                if df is not None and not df.empty and "Plan Name" in df.columns:
                    apply_aliases_to_df(df, "Plan Name", aliases, threshold=threshold)

            def _normalize_amounts(df: pd.DataFrame):
                if df is None or df.empty:
                    return df
                for col in ["Employee Cost", "Employer Cost"]:
                    if col in df.columns:
                        if treat_blank_as_zero:  # from the UI checkbox
                            df[col] = df[col].fillna(0.0)
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
                        # snap to cents and apply tolerance
                        df[col] = (df[col] * 100).round() / 100.0
                        if amount_tolerance_cents > 0:
                            step = max(1, amount_tolerance_cents)
                            df[col] = ((df[col] * 100 / step).round() * step / 100.0)
                        df[col] = df[col].round(2)
                return df

            p_df = _normalize_amounts(p_df)
            c_df = _normalize_amounts(c_df)
            b_df = _normalize_amounts(b_df)        

            dup_notes = []

            def _apply_dup_mode(df, label):
                if df is None or df.empty:
                    return df
                if dup_mode == "Ignore exact duplicates (recommended)":
                    new_df, removed = dedupe_exact(df)
                    if removed:
                        dup_notes.append(f"{label}: removed {removed} exact duplicate row(s)")
                    return new_df
                elif dup_mode == "Aggregate duplicates (sum amounts)":
                    new_df, removed = aggregate_duplicates(df)
                    if removed:
                        dup_notes.append(f"{label}: aggregated {removed} row(s) into keys")
                    return new_df
                # Keep all (strict)
                return df

            p_df = _apply_dup_mode(p_df, "Payroll")
            c_df = _apply_dup_mode(c_df, "Carrier")
            b_df = _apply_dup_mode(b_df, "BenAdmin") 

            def normalize_cost_columns(df):
                if df is None or df.empty:
                    return df
                for col in ["Employee Cost", "Employer Cost"]:
                    if col in df.columns:
                        # Replace dashes and blanks with 0, then convert to float
                        df[col] = (
                            df[col]
                            .replace("-", 0)
                            .replace("", 0)
                            .fillna(0)
                            .apply(lambda x: 0 if str(x).strip() in ["-", ""] else x)
                        )
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                return df

            # Apply normalization to all input DataFrames
            p_df = normalize_cost_columns(p_df)
            c_df = normalize_cost_columns(c_df)
            b_df = normalize_cost_columns(b_df)
       

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

            # After st.success(f"Completed: {mode}") and before rendering results:
            errors_df, summary_df, removed_eq = postfilter_amount_mismatches(
                errors_df, summary_df, tolerance_cents=amount_tolerance_cents, blank_is_zero=treat_blank_as_zero
            )
            if removed_eq:
                st.caption(f"Ignored {removed_eq} amount mismatches as equivalent (blank↔$0 within {amount_tolerance_cents}¢).")


            with st.expander("🔎 Debug Investigator: check an employee/plan across files"):
                q_ssn = st.text_input("Enter SSN (9 digits or last 4 ok)")
                q_plan = st.text_input("Optional: Plan contains (e.g., accident)")
            if st.button("Find Records"):
                def _filter(df):
                    if df is None or df.empty: return df
                    cols = {c.lower(): c for c in df.columns}
                    ssn_col = cols.get("ssn")
                    plan_col = cols.get("plan name") or cols.get("plan")
                    out = df
                    if q_ssn:
                        s = str(q_ssn).strip()
                        s = "".join(ch for ch in s if ch.isdigit())
                        # match by last4 if user typed last4, else full
                        if len(s) == 4:
                            out = out[out[ssn_col].astype(str).str[-4:] == s] if ssn_col else out
                        elif len(s) == 9:
                            out = out[out[ssn_col].astype(str) == s] if ssn_col else out
                    if q_plan and (plan_col in out.columns):
                        out = out[out[plan_col].astype(str).str.contains(q_plan.strip().lower(), na=False)]
                    return out

                st.markdown("**Payroll match**")
                st.dataframe(_filter(p_df), use_container_width=True, height=200)
                st.markdown("**Carrier match**")
                st.dataframe(_filter(c_df), use_container_width=True, height=200)
                st.markdown("**BenAdmin match**")
                st.dataframe(_filter(b_df), use_container_width=True, height=200)

            ins = compute_insights(summary_df, errors_df, compared_lines, minutes_per_line, hourly_rate)
            # Insights block
            m1, m2 = st.columns([2, 1])
            with m1:
                # metrics + impact line
                render_quick_insights(ins)
            with m2:
                blurb = (
                    f"{ins['compared_lines']:,} lines • {ins['error_rate']:.1%} errors • "
                    f"saved ~{ins['hours_saved']:.1f} hrs (~${ins['dollars_saved']:,.0f})"
                )
                st.caption("Sales blurb (preview)")
                st.text_area("", blurb, height=60, label_visibility="collapsed", disabled=True)

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
                copy_to_clipboard_button("Copy Insights", build_insights_blurb(ins, mode, group_name, period))
                st.caption("Copied")

        except Exception as e:
            st.error(f"Error: {e}"); st.exception(e)
    else:
        st.info("Upload 2 or 3 files and click **Run Reconciliation**.")
    st.markdown('</div>', unsafe_allow_html=True)

# ---------- Dashboard / Help ----------
with dashboard_tab:
    st.subheader("Summary Dashboard")
    st.caption("Future: persist run snapshots to power trends and client reporting.")
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

        **Plan Name matching**: IvyRecon uses alias normalization + fuzzy matching. Adjust the threshold in Options.

        **Exports**: Multi-tab Excel includes Summary, All Errors, and one sheet per error type — branded to IvyRecon.
        """
    )






