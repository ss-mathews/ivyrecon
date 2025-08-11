# app.py — IvyRecon (Smart Reconciliation: sales-friendly)
import os, json
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth
import streamlit.components.v1 as components

from reconcile import reconcile_two, reconcile_three  # existing row-level engine
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
      .ivy-header { position: sticky; top: 0; z-index: 50; background:#fff; border-bottom:1px solid var(--line); }
      .ivy-header .wrap { display:flex; align-items:center; justify-content:space-between; padding: 12px 4px; }
      .ivy-brand { font-weight:700; font-size:18px; letter-spacing:.3px; }
      .ivy-badge { font-size:12px; padding:.2rem .5rem; border-radius:999px; background:var(--bg2); border:1px solid var(--line); }
      .stButton>button { background: var(--teal); color:#0F2A37; border:0; padding:.6rem 1rem; border-radius:12px; font-weight:600; }
      .stButton>button:hover { filter:brightness(0.97); }
      .card { border:1px solid var(--line); border-radius:16px; background:#fff; padding:16px; margin: 8px 0 16px; }
      .chip { display:inline-flex; align-items:center; gap:.5rem; padding:.35rem .6rem; border-radius:999px; background:var(--bg2); color:var(--navy); border:1px solid var(--line); font-size:0.9rem; }
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
authenticator = stauth.Authenticate(credentials=credentials, cookie_name="ivyrecon_cookies", key="ivyrecon_key", cookie_expiry_days=1)
authenticator.login(location="main")
auth_status = st.session_state.get("authentication_status")
name = st.session_state.get("name"); username = st.session_state.get("username")

if auth_status is False:
    st.error("Invalid credentials"); st.stop()
elif auth_status is None:
    st.info("Enter your email and password to continue."); st.stop()

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

# ---------------- State & defaults ----------------
if "upl_ver" not in st.session_state: st.session_state["upl_ver"] = 0
if "ran" not in st.session_state: st.session_state["ran"] = False
if "aliases" not in st.session_state:
    from_secrets = load_aliases_from_secrets(st)
    st.session_state["aliases"] = merge_aliases(DEFAULT_ALIASES, normalize_alias_dict(from_secrets))

REQUIRED = ["SSN","First Name","Last Name","Plan Name","Employee Cost","Employer Cost"]

# ---------------- Helpers: I/O & styling ----------------
def load_any(uploaded) -> pd.DataFrame | None:
    if uploaded is None: return None
    try:
        if uploaded.name.lower().endswith((".xlsx", ".xls")): return pd.read_excel(uploaded)
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
    if df is None or df.empty: st.metric(f"{label} Rows", 0); return
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
        chips.append(f'<div class="chip {color}"><b>{cnt}</b> {et}</div>')
    if total: chips.append(f'<div class="chip"><b>Total:</b> {total}</div>')
    st.markdown(" ".join(chips), unsafe_allow_html=True)

# ---------------- Helpers: normalization & matching ----------------
def _clean_money(x):
    if pd.isna(x): return pd.NA
    s = str(x).strip()
    if s in ("", "-", "--"): return 0.0
    s = s.replace("$","").replace(",","")
    try: return float(s)
    except: return pd.NA

def standardize_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    df = df.copy()
    for c in df.columns:
        if df[c].dtype == object: df[c] = df[c].astype(str).str.strip()
    cols = {c.lower(): c for c in df.columns}
    ssn_col  = cols.get("ssn")
    plan_col = cols.get("plan name") or cols.get("plan")
    fn_col   = cols.get("first name")
    ln_col   = cols.get("last name")
    ee_col   = cols.get("employee cost") or cols.get("employee amount") or cols.get("ee amount")
    er_col   = cols.get("employer cost") or cols.get("employer amount") or cols.get("er amount")
    if ssn_col: df[ssn_col] = df[ssn_col].astype(str).str.replace(r"\D","",regex=True).str.zfill(9)
    if fn_col:  df[fn_col]  = df[fn_col].astype(str).str.title()
    if ln_col:  df[ln_col]  = df[ln_col].astype(str).str.title()
    if plan_col: df[plan_col] = df[plan_col].astype(str).str.lower()
    if ee_col:  df[ee_col]  = df[ee_col].apply(_clean_money)
    if er_col:  df[er_col]  = df[er_col].apply(_clean_money)
    return df

CARRIER_TOKENS = {"sun","life","metlife","voya","unum","guardian","lincoln","principal","anthem"}
def strip_carrier_prefixes(df):
    if df is None or df.empty or "Plan Name" not in df.columns: return df
    out = df.copy()
    def _strip(p):
        s = str(p).strip().lower()
        return " ".join([t for t in s.split() if t not in CARRIER_TOKENS])
    out["Plan Name"] = out["Plan Name"].astype(str).apply(_strip)
    return out

def normalize_amounts(df: pd.DataFrame, tolerance_cents: int, blank_is_zero: bool=True):
    if df is None or df.empty: return df
    out = df.copy()
    for col in ["Employee Cost","Employer Cost"]:
        if col in out.columns:
            if blank_is_zero:
                out[col] = out[col].replace(["", "-", "--"], 0).fillna(0)
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0 if blank_is_zero else 0)
            out[col] = (out[col] * 100).round() / 100.0
            step = max(1, int(tolerance_cents))
            if step > 0:
                out[col] = ((out[col] * 100 / step).round() * step / 100.0)
            out[col] = out[col].round(2)
    return out

def dedupe_exact(df: pd.DataFrame) -> tuple[pd.DataFrame,int]:
    if df is None or df.empty: return df, 0
    key_cols = [c for c in ["SSN","Plan Name","Employee Cost","Employer Cost"] if c in df.columns]
    if not key_cols: return df, 0
    before = len(df)
    out = df.drop_duplicates(subset=key_cols, keep="first").reset_index(drop=True)
    return out, before - len(out)

def aggregate_by_key_distinct(df: pd.DataFrame) -> pd.DataFrame:
    """Group by SSN+Plan; sum DISTINCT amounts; keep first/last name."""
    if df is None or df.empty: return df
    cols = df.columns
    req = [c for c in ["SSN","Plan Name"] if c in cols]
    if not req: return df
    out = df.copy()
    dupe_cols = [c for c in ["SSN","Plan Name","Employee Cost","Employer Cost"] if c in cols]
    if dupe_cols: out = out.drop_duplicates(subset=dupe_cols, keep="first")
    def _sum_distinct(s):
        ss = pd.to_numeric(s, errors="coerce").dropna()
        return ss.drop_duplicates().sum()
    agg = {}
    if "Employee Cost" in cols: agg["Employee Cost"] = _sum_distinct
    if "Employer Cost" in cols: agg["Employer Cost"] = _sum_distinct
    for k in ["First Name","Last Name"]:
        if k in cols: agg[k] = "first"
    return out.groupby(req, dropna=False, as_index=False).agg(agg).reset_index(drop=True)

# Totals engines (noise-tolerant)
def _tol_ok(a,b,cents:int)->bool:
    try: aa = 0.0 if pd.isna(a) else float(a); bb = 0.0 if pd.isna(b) else float(b)
    except: return False
    return abs(aa-bb) <= max(0,int(cents))/100.0

def totals_by_key(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["SSN","Plan Name","First Name","Last Name","Employee Cost","Employer Cost"])
    sums = {c:"sum" for c in ["Employee Cost","Employer Cost"] if c in df.columns}
    keep = {c:"first" for c in ["First Name","Last Name"] if c in df.columns}
    agg = {**sums, **keep} if sums else keep
    return df.groupby(["SSN","Plan Name"], dropna=False, as_index=False).agg(agg).reset_index(drop=True)

def reconcile_totals_two(a: pd.DataFrame, b: pd.DataFrame, a_name: str, b_name: str, cents: int):
    A, B = totals_by_key(a), totals_by_key(b)
    merged = pd.merge(A, B, on=["SSN","Plan Name"], how="outer", suffixes=(f" ({a_name})", f" ({b_name})"))
    errors = []
    for _, r in merged.iterrows():
        in_a = pd.notna(r.get(f"Employee Cost ({a_name})")) or pd.notna(r.get(f"Employer Cost ({a_name})"))
        in_b = pd.notna(r.get(f"Employee Cost ({b_name})")) or pd.notna(r.get(f"Employer Cost ({b_name})"))
        if in_a and not in_b:
            errors.append({"Error Type": f"Missing in {b_name}","SSN": r["SSN"],"First Name": r.get(f"First Name ({a_name})") or r.get(f"First Name ({b_name})"),
                           "Last Name": r.get(f"Last Name ({a_name})") or r.get(f"Last Name ({b_name})"),"Plan Name": r["Plan Name"]}); continue
        if in_b and not in_a:
            errors.append({"Error Type": f"Missing in {a_name}","SSN": r["SSN"],"First Name": r.get(f"First Name ({a_name})") or r.get(f"First Name ({b_name})"),
                           "Last Name": r.get(f"Last Name ({a_name})") or r.get(f"Last Name ({b_name})"),"Plan Name": r["Plan Name"]}); continue
        ee_a, ee_b = r.get(f"Employee Cost ({a_name})",0.0), r.get(f"Employee Cost ({b_name})",0.0)
        er_a, er_b = r.get(f"Employer Cost ({a_name})",0.0), r.get(f"Employer Cost ({b_name})",0.0)
        if not _tol_ok(ee_a, ee_b, cents):
            errors.append({"Error Type":"Employee Amount Mismatch","SSN": r["SSN"],"First Name": r.get(f"First Name ({a_name})") or r.get(f"First Name ({b_name})"),
                           "Last Name": r.get(f"Last Name ({a_name})") or r.get(f"Last Name ({b_name})"),"Plan Name": r["Plan Name"],
                           f"Employee Cost ({a_name})": ee_a, f"Employee Cost ({b_name})": ee_b})
        if not _tol_ok(er_a, er_b, cents):
            errors.append({"Error Type":"Employer Amount Mismatch","SSN": r["SSN"],"First Name": r.get(f"First Name ({a_name})") or r.get(f"First Name ({b_name})"),
                           "Last Name": r.get(f"Last Name ({a_name})") or r.get(f"Last Name ({b_name})"),"Plan Name": r["Plan Name"],
                           f"Employer Cost ({a_name})": er_a, f"Employer Cost ({b_name})": er_b})
    errors_df = pd.DataFrame(errors)
    if errors_df.empty:
        summary_df = pd.DataFrame({"Error Type":["Total"],"Count":[0]})
    else:
        summary_df = errors_df.groupby("Error Type", dropna=False).size().reset_index(name="Count")
        summary_df = pd.concat([summary_df, pd.DataFrame({"Error Type":["Total"],"Count":[int(summary_df["Count"].sum())]})], ignore_index=True)
    compared = len(merged)
    return errors_df, summary_df, compared

def reconcile_totals_three(p: pd.DataFrame, c: pd.DataFrame, b: pd.DataFrame, cents: int):
    parts = []
    for (x, xn), (y, yn) in [((p,"Payroll"),(c,"Carrier")), ((p,"Payroll"),(b,"BenAdmin")), ((c,"Carrier"),(b,"BenAdmin"))]:
        e, s, comp = reconcile_totals_two(x, y, xn, yn, cents)
        if not e.empty: parts.append(e)
    errors_df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["Error Type"])
    if errors_df.empty:
        summary_df = pd.DataFrame({"Error Type":["Total"],"Count":[0]})
    else:
        summary_df = errors_df.groupby("Error Type", dropna=False).size().reset_index(name="Count")
        summary_df = pd.concat([summary_df, pd.DataFrame({"Error Type":["Total"],"Count":[int(summary_df["Count"].sum())]})], ignore_index=True)
    compared = 0
    return errors_df, summary_df, compared

# Drilldown: run row-level only for mismatched SSN+Plan keys
def drilldown_row_level_for_keys(p_df, c_df, b_df, keys, threshold):
    if not keys: return pd.DataFrame()
    def _filter(df):
        if df is None or df.empty: return df
        return df[df.apply(lambda r: (str(r.get("SSN")), str(r.get("Plan Name"))) in keys, axis=1)]
    p2, c2, b2 = _filter(p_df), _filter(c_df), _filter(b_df)
    # choose correct two/three reconcile
    if p2 is not None and c2 is not None and b2 is not None and not p2.empty and not c2.empty and not b2.empty:
        e, _ = reconcile_three(p2, c2, b2, plan_match_threshold=threshold)
        return e
    # else try pairs prioritizing payroll
    parts = []
    if p2 is not None and c2 is not None and not p2.empty and not c2.empty:
        e, _ = reconcile_two(p2, c2, "Payroll", "Carrier", plan_match_threshold=threshold)
        parts.append(e)
    if p2 is not None and b2 is not None and not p2.empty and not b2.empty:
        e, _ = reconcile_two(p2, b2, "Payroll", "BenAdmin", plan_match_threshold=threshold)
        parts.append(e)
    if c2 is not None and b2 is not None and not c2.empty and not b2.empty:
        e, _ = reconcile_two(c2, b2, "Carrier", "BenAdmin", plan_match_threshold=threshold)
        parts.append(e)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

# Insights
def compute_insights(summary_df, errors_df, compared_lines, minutes_per_line, hourly_rate):
    total = 0; most = "—"; mismatch_pct = 0.0
    if summary_df is not None and not summary_df.empty:
        tmp = summary_df[summary_df["Error Type"].str.lower()!="total"] if "Error Type" in summary_df.columns else summary_df
        if not tmp.empty:
            top = tmp.sort_values("Count", ascending=False).iloc[0]
            most = f"{top['Error Type']} ({int(top['Count'])})"
        total = int(summary_df[summary_df["Error Type"].str.lower()=="total"]["Count"].sum() or 0)
    if errors_df is not None and not errors_df.empty and compared_lines:
        mismatch_pct = (errors_df["Error Type"].str.contains("Plan Name Mismatch", na=False)).sum() / max(1,compared_lines)
    error_rate = total / max(1, compared_lines)
    minutes_saved = compared_lines * minutes_per_line
    hours_saved = minutes_saved / 60.0
    dollars_saved = hours_saved * hourly_rate
    return {"total_errors":total, "most_common":most, "mismatch_pct":mismatch_pct,
            "error_rate":error_rate, "compared_lines":compared_lines,
            "minutes_saved":minutes_saved,"hours_saved":hours_saved,"dollars_saved":dollars_saved}

def render_quick_insights(ins):
    a,b,c,d,e = st.columns(5)
    with a: st.metric("Lines Reconciled", f"{ins['compared_lines']:,}")
    with b: st.metric("Error Rate", f"{ins['error_rate']:.1%}")
    with c: st.metric("Plan Mismatch %", f"{ins['mismatch_pct']:.1%}")
    with d: st.metric("Most Common Error", ins["most_common"])
    with e: st.metric("Time Saved (hrs)", f"{ins['hours_saved']:,.1f}")
    st.markdown(
        f'<div class="impact">{ins["compared_lines"]:,} records • {ins["error_rate"]:.1%} error rate • '
        f'saved {ins["hours_saved"]:,.1f} hrs (~${ins["dollars_saved"]:,.0f})</div>',
        unsafe_allow_html=True,
    )

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
    st.download_button("Download Insights (.txt)", data=data,
        file_name=f"ivyrecon_insights_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", mime="text/plain")

def copy_to_clipboard_button(label: str, text: str):
    text_js = json.dumps(text)
    components.html(
        f"""<button onclick='navigator.clipboard.writeText({text_js});'
                style="background:#18CCAA;color:#0F2A37;border:0;padding:8px 12px;
                       border-radius:12px;font-weight:600;cursor:pointer;">{label}</button>""",
        height=46,
    )

# ---------------- Tabs ----------------
st.title("IvyRecon")
st.caption("Modern, tech-forward reconciliation for Payroll • Carrier • BenAdmin")
run_tab, dashboard_tab, settings_tab, help_tab = st.tabs(["Run Reconciliation","Summary Dashboard","Settings","Help & Formatting"])

# ---------- SETTINGS: Alias Manager ----------
with settings_tab:
    st.markdown("### Alias Manager")
    st.caption("Control plan name synonyms without code. Format: canonical → aliases list.")
    current = st.session_state["aliases"]
    colA,colB = st.columns(2)
    with colA:
        st.markdown("**Current Aliases (JSON)**")
        alias_text = st.text_area("", value=json.dumps(current, indent=2), height=260, label_visibility="collapsed")
        if st.button("Save Aliases"):
            try:
                user_dict = json.loads(alias_text)
                st.session_state["aliases"] = merge_aliases({}, normalize_alias_dict(user_dict))
                st.success("Aliases saved for this session.")
            except Exception as e:
                st.error(f"Invalid JSON: {e}")
        st.download_button("Download Aliases (.json)", data=json.dumps(st.session_state["aliases"], indent=2).encode("utf-8"),
                           file_name="ivyrecon_plan_aliases.json", mime="application/json")
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

# ---------- RUN: Smart Reconciliation ----------
with run_tab:
    up_col, opt_col = st.columns([2,1])
    with up_col:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Upload Files")
        u1,u2,u3 = st.columns(3)
        with u1: payroll_file  = st.file_uploader("Payroll (CSV/XLSX)",  type=["csv","xlsx"], key=f"pay_{st.session_state.upl_ver}")
        with u2: carrier_file  = st.file_uploader("Carrier (CSV/XLSX)",  type=["csv","xlsx"], key=f"car_{st.session_state.upl_ver}")
        with u3: benadmin_file = st.file_uploader("BenAdmin (CSV/XLSX)", type=["csv","xlsx"], key=f"ben_{st.session_state.upl_ver}")
        st.caption("Required Columns: SSN, First Name, Last Name, Plan Name, Employee Cost, Employer Cost")
        st.markdown('</div>', unsafe_allow_html=True)

    with opt_col:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Details")
        group_name = st.text_input("Group Name", value="")
        period     = st.text_input("Reporting Period", value="")
        run_clicked = st.button("Run Reconciliation", type="primary", use_container_width=True)
        with st.expander("Advanced (optional)"):
            st.caption("Defaults are battle-tested. Tweak only if needed.")
            threshold = st.slider("Plan Name Match Threshold", 0.5, 1.0, 0.90, 0.01)
            treat_blank_as_zero = st.checkbox("Treat blank amounts as $0.00", value=True)
            amount_tolerance_cents = st.slider("Amount tolerance (cents)", 0, 25, 2, 1)
            minutes_per_line = st.slider("Manual mins per line (for ROI)", 0.5, 3.0, 1.2, 0.1)
            hourly_rate = st.slider("Hourly cost ($)", 15, 150, 40, 5)
        st.markdown('</div>', unsafe_allow_html=True)

    # Load & preview before run
    p_df = standardize_df(load_any(payroll_file))
    c_df = standardize_df(load_any(carrier_file))
    b_df = standardize_df(load_any(benadmin_file))

    if not run_clicked:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Previews")
        pcol,ccol,bcol = st.columns(3)
        with pcol:
            st.markdown("#### Payroll");   st.dataframe((p_df.head(12) if p_df is not None else pd.DataFrame()), use_container_width=True); quick_stats(p_df, "Payroll")
        with ccol:
            st.markdown("#### Carrier");   st.dataframe((c_df.head(12) if c_df is not None else pd.DataFrame()), use_container_width=True); quick_stats(c_df, "Carrier")
        with bcol:
            st.markdown("#### BenAdmin");  st.dataframe((b_df.head(12) if b_df is not None else pd.DataFrame()), use_container_width=True); quick_stats(b_df, "BenAdmin")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True); st.markdown("### Results")

    if run_clicked:
        try:
            # Smart pipeline (no knobs)
            # 1) strip vendor prefixes
            p_df = strip_carrier_prefixes(p_df); c_df = strip_carrier_prefixes(c_df); b_df = strip_carrier_prefixes(b_df)
            # 2) apply aliases (assign!)
            aliases = st.session_state["aliases"]
            p_df = apply_aliases_to_df(p_df, "Plan Name", aliases, threshold=threshold) if p_df is not None else None
            c_df = apply_aliases_to_df(c_df, "Plan Name", aliases, threshold=threshold) if c_df is not None else None
            b_df = apply_aliases_to_df(b_df, "Plan Name", aliases, threshold=threshold) if b_df is not None else None
            # 3) amounts normalization
            p_df = normalize_amounts(p_df, tolerance_cents=amount_tolerance_cents, blank_is_zero=treat_blank_as_zero)
            c_df = normalize_amounts(c_df, tolerance_cents=amount_tolerance_cents, blank_is_zero=treat_blank_as_zero)
            b_df = normalize_amounts(b_df, tolerance_cents=amount_tolerance_cents, blank_is_zero=treat_blank_as_zero)
            # 4) ignore exact dups
            dup_notes = []
            def _dedupe(df,label):
                nd, removed = dedupe_exact(df)
                if removed: dup_notes.append(f"{label}: removed {removed} exact duplicate row(s)")
                return nd
            p_df = _dedupe(p_df,"Payroll"); c_df = _dedupe(c_df,"Carrier"); b_df = _dedupe(b_df,"BenAdmin")
            # 5) aggregate by SSN+Plan (distinct amounts) to neutralize split lines
            p_tot = aggregate_by_key_distinct(p_df); c_tot = aggregate_by_key_distinct(c_df); b_tot = aggregate_by_key_distinct(b_df)

            # 6) totals engine
            if p_tot is not None and c_tot is not None and b_tot is not None and not p_tot.empty and not c_tot.empty and not b_tot.empty:
                errors_df, summary_df, _ = reconcile_totals_three(p_tot, c_tot, b_tot, amount_tolerance_cents)
                mode = "Smart totals (Payroll vs Carrier vs BenAdmin)"
                compared_lines = len(pd.concat([p_tot, c_tot, b_tot], ignore_index=True))
            elif p_tot is not None and c_tot is not None and not p_tot.empty and not c_tot.empty:
                errors_df, summary_df, compared_lines = reconcile_totals_two(p_tot, c_tot, "Payroll", "Carrier", amount_tolerance_cents)
                mode = "Smart totals (Payroll vs Carrier)"
            elif p_tot is not None and b_tot is not None and not p_tot.empty and not b_tot.empty:
                errors_df, summary_df, compared_lines = reconcile_totals_two(p_tot, b_tot, "Payroll", "BenAdmin", amount_tolerance_cents)
                mode = "Smart totals (Payroll vs BenAdmin)"
            elif c_tot is not None and b_tot is not None and not c_tot.empty and not b_tot.empty:
                errors_df, summary_df, compared_lines = reconcile_totals_two(c_tot, b_tot, "Carrier", "BenAdmin", amount_tolerance_cents)
                mode = "Smart totals (Carrier vs BenAdmin)"
            else:
                st.warning("Please upload at least two files to reconcile."); st.markdown('</div>', unsafe_allow_html=True); st.stop()

            # 7) drilldown only where totals mismatched (replace those with row-level detail)
            if not errors_df.empty:
                mismatch_mask = errors_df["Error Type"].str.contains("Amount Mismatch", case=False, na=False)
                keys = set(zip(errors_df.loc[mismatch_mask, "SSN"], errors_df.loc[mismatch_mask, "Plan Name"]))
                if keys:
                    row_detail = drilldown_row_level_for_keys(p_df, c_df, b_df, keys, threshold)
                    if row_detail is not None and not row_detail.empty:
                        # drop the coarse totals mismatches for those keys, keep missings & others
                        keep_idx = []
                        for i, r in errors_df.iterrows():
                            if mismatch_mask.iloc[i] and (str(r["SSN"]), str(r["Plan Name"])) in keys:
                                continue
                            keep_idx.append(i)
                        errors_df = pd.concat([errors_df.iloc[keep_idx].reset_index(drop=True), row_detail], ignore_index=True)
                        # rebuild summary
                        if not errors_df.empty:
                            summary_df = errors_df.groupby("Error Type", dropna=False).size().reset_index(name="Count")
                            summary_df = pd.concat([summary_df, pd.DataFrame({"Error Type":["Total"],"Count":[int(summary_df["Count"].sum())]})], ignore_index=True)

            st.success(f"Completed: {mode}")
            if dup_notes: st.caption(" • ".join(dup_notes))

            # Insights
            ins = compute_insights(summary_df, errors_df, compared_lines, minutes_per_line, hourly_rate)
            a,b = st.columns([2,1])
            with a: render_quick_insights(ins)
            with b:
                blurb = f"{ins['compared_lines']:,} lines • {ins['error_rate']:.1%} errors • saved ~{ins['hours_saved']:.1f} hrs (~${ins['dollars_saved']:,.0f})"
                st.caption("Sales blurb"); st.text_area("", blurb, height=60, label_visibility="collapsed", disabled=True)

            render_error_chips(summary_df)

            # Results tables
            L,R = st.columns([1,2])
            with L:
                st.markdown("**Summary**")
                st.dataframe(summary_df, use_container_width=True)
            with R:
                st.markdown("**Errors**")
                if errors_df is not None and not errors_df.empty:
                    st.dataframe(style_errors(errors_df), use_container_width=True, height=520)
                else:
                    st.info("No errors found.")

            # Debug Investigator
            with st.expander("🔎 Debug Investigator: check an employee/plan across files"):
                q_ssn  = st.text_input("Enter SSN (9 digits or last 4 ok)")
                q_plan = st.text_input("Optional: Plan contains (e.g., accident)")
                if st.button("Find Records"):
                    def _filter(df):
                        if df is None or df.empty: return df
                        cols = {c.lower(): c for c in df.columns}
                        ssn_col = cols.get("ssn"); plan_col = cols.get("plan name") or cols.get("plan")
                        out = df
                        if q_ssn and ssn_col:
                            s = "".join(ch for ch in str(q_ssn).strip() if ch.isdigit())
                            if len(s)==4: out = out[out[ssn_col].astype(str).str[-4:]==s]
                            elif len(s)==9: out = out[out[ssn_col].astype(str)==s]
                        if q_plan and plan_col:
                            out = out[out[plan_col].astype(str).str.contains(q_plan.strip().lower(), na=False)]
                        return out
                    st.markdown("**Payroll**");  st.dataframe(_filter(p_df), use_container_width=True, height=200)
                    st.markdown("**Carrier**");  st.dataframe(_filter(c_df), use_container_width=True, height=200)
                    st.markdown("**BenAdmin**"); st.dataframe(_filter(b_df), use_container_width=True, height=200)

            # Exports
            st.markdown("#### Export")
            xlsx = export_errors_multitab(errors_df, summary_df, group_name=group_name, period=period)
            c1,c2,c3 = st.columns(3)
            with c1:
                st.download_button("Download Error Report (Excel)", data=xlsx,
                    file_name=f"ivyrecon_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with c2:
                download_insights_button(ins, mode, group_name, period)
            with c3:
                copy_to_clipboard_button("Copy Insights",
                    f"{ins['compared_lines']:,} lines • {ins['error_rate']:.1%} errors • saved ~{ins['hours_saved']:.1f} hrs (~${ins['dollars_saved']:,.0f})")
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

        **Plan Name matching**: IvyRecon auto-applies aliases + fuzzy normalization. Threshold is in Advanced.

        **Exports**: Multi-tab Excel includes Summary, All Errors, and one sheet per error type — branded to IvyRecon.
        """
    )







