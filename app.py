# app.py — IvyRecon (Smart Reconciliation + Frequency-Aware Totals + Stronger Aliasing - CLEAN)

# ========= Imports =========
import os, json, time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

import pandas as pd
import streamlit as st
# Silence legacy API some libs still call
if hasattr(st, "experimental_get_query_params"):
    st.experimental_get_query_params = lambda: st.query_params

import streamlit_authenticator as stauth
import streamlit.components.v1 as components

# PDF / report
from io import BytesIO
from reportlab.lib.pagesizes import LETTER
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch

# Invites / hashing
import jwt
import bcrypt

# Your modules
from reconcile import reconcile_two, reconcile_three
from excel_export import export_errors_multitab
from aliases import (
    DEFAULT_ALIASES, load_aliases_from_secrets, normalize_alias_dict,
    merge_aliases, apply_aliases_to_df,
)


# ========= Secrets / config helpers =========
def _secret(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

# Admin account (bcrypt hash must be provided in secrets)
ADMIN_EMAIL = _secret("ADMIN_EMAIL", os.environ.get("ADMIN_EMAIL", "admin@example.com")).lower()
ADMIN_NAME  = _secret("ADMIN_NAME",  os.environ.get("ADMIN_NAME", "Admin"))
ADMIN_PASSWORD_HASH = _secret("ADMIN_PASSWORD_HASH",
                              os.environ.get("ADMIN_PASSWORD_HASH", "$2b$12$PLACEHOLDER"))

# Cookie/session settings
_auth_cfg = _secret("auth", {}) or {}
if not isinstance(_auth_cfg, dict): _auth_cfg = {}
COOKIE_NAME = _auth_cfg.get("cookie_name", "ivyrecon_cookies")
COOKIE_KEY  = _auth_cfg.get("key", "ivyrecon_key")
try:
    COOKIE_EXPIRY_DAYS = int(_auth_cfg.get("cookie_expiry_days", 1))
except Exception:
    COOKIE_EXPIRY_DAYS = 1

# Invite config
INVITE_SIGNING_KEY = _secret("INVITE_SIGNING_KEY", os.environ.get("INVITE_SIGNING_KEY"))
APP_BASE_URL       = _secret("APP_BASE_URL",       os.environ.get("APP_BASE_URL"))

# Users DB path -> Path object, safe default for Streamlit Cloud
def _resolve_users_db_path() -> Path:
    raw = _secret("USERS_DB_PATH", os.environ.get("USERS_DB_PATH", "/tmp/users.json"))
    p = Path(raw)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p

USERS_DB_PATH = _resolve_users_db_path()


# ========= Users DB (single source of truth) =========
def _ensure_users_file():
    USERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not USERS_DB_PATH.exists():
        USERS_DB_PATH.write_text(json.dumps({"usernames": {}}, indent=2), encoding="utf-8")

def _load_users() -> dict:
    _ensure_users_file()
    try:
        data = json.loads(USERS_DB_PATH.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):  # heal old formats
            data = {"usernames": {}}
        data.setdefault("usernames", {})
        return data
    except Exception:
        return {"usernames": {}}

def _save_users(data: dict) -> None:
    data = data or {}
    if not isinstance(data, dict):
        data = {"usernames": {}}
    data.setdefault("usernames", {})
    _ensure_users_file()
    USERS_DB_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

def _ensure_admin():
    data = _load_users()
    u = data["usernames"]
    if ADMIN_EMAIL not in u:
        u[ADMIN_EMAIL] = {
            "email": ADMIN_EMAIL,
            "name": ADMIN_NAME,
            "password": ADMIN_PASSWORD_HASH,  # must be bcrypt hash
            "role": "admin",
            "created_at": datetime.utcnow().isoformat(),
        }
        _save_users(data)
    return data

def _add_user(email: str, name: str, password_hash: str, role: str = "analyst"):
    email = (email or "").strip().lower()
    data = _load_users()
    u = data["usernames"]
    u[email] = {
        "email": email,
        "name": name or email,
        "password": password_hash,  # bcrypt hash
        "role": role,
        "created_at": datetime.utcnow().isoformat(),
    }
    _save_users(data)

def _get_credentials() -> dict:
    data = _ensure_admin()
    # streamlit_authenticator format
    return {"usernames": data["usernames"]}


# ========= Invites (JWT) =========
def create_invite_url(email: str, role: str = "analyst", ttl_seconds: int = 48*3600) -> str:
    """Generate a signed invite link your admin can share."""
    if not (INVITE_SIGNING_KEY and APP_BASE_URL):
        return ""
    payload = {"email": email.lower().strip(), "role": role, "exp": int(time.time()) + ttl_seconds}
    token = jwt.encode(payload, INVITE_SIGNING_KEY, algorithm="HS256")
    return f"{APP_BASE_URL}?register=1&token={token}"

def verify_invite_token(token: str) -> Dict[str, Any] | None:
    try:
        data = jwt.decode(token, INVITE_SIGNING_KEY, algorithms=["HS256"])
        return {"email": data["email"], "role": data.get("role", "analyst")}
    except jwt.ExpiredSignatureError:
        st.error("Invite link expired.")
    except jwt.InvalidTokenError:
        st.error("Invalid invite link.")
    return None


# ========= Page + Global CSS =========
st.set_page_config(page_title="IvyRecon", page_icon="🪄", layout="wide")

def _inject_css_once():
    if st.session_state.get("_css_done"):
        return
    st.session_state["_css_done"] = True
    st.markdown(
        """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Raleway:wght@500;600;700&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root { --teal:#18CCAA; --navy:#2F455C; --bg:#FFFFFF; --bg2:#F6F8FA; --line:#E5E7EB; }
  html, body, [class*="css"] { font-family:"Roboto",system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; color:var(--navy); }
  h1, h2, h3, h4, h5, h6 { font-family:"Raleway",sans-serif; letter-spacing:.2px; color:var(--navy); }
  .block-container { padding-top: 0; }
  .ivy-header { position: sticky; top: 0; z-index: 50; background:#fff; border-bottom:1px solid var(--line); backdrop-filter: saturate(1.2) blur(6px); }
  .ivy-header .wrap { display:flex; align-items:center; justify-content:space-between; padding: 12px 4px; }
  .ivy-brand { font-weight:700; font-size:18px; letter-spacing:.3px; }
  .ivy-badge { font-size:12px; padding:.2rem .5rem; border-radius:999px; background:var(--bg2); border:1px solid var(--line); }

  .stButton>button { background: var(--teal); color:#0F2A37; border:0; padding:.65rem 1rem; border-radius:12px; font-weight:600; box-shadow: 0 1px 0 rgba(0,0,0,.04); }
  .stButton>button:hover { filter:brightness(0.97); transform: translateY(-1px); transition: all .15s ease; }

  .card { border:1px solid var(--line); border-radius:16px; background:var(--bg); padding:16px; margin: 8px 0 16px; box-shadow: 0 6px 20px rgba(47,69,92,0.06); }
  .card h3, .card h4 { margin: 0 0 8px 0; }

  .chip { display:inline-flex; align-items:center; gap:.5rem; padding:.35rem .6rem; border-radius:999px; background:var(--bg2); color:var(--navy); border:1px solid var(--line); font-size:0.9rem; }
  .chip.red { background:#FFF5F5; border-color:#FEE2E2; }
  .chip.yellow { background:#FFFBEB; border-color:#FEF3C7; }
  .chip.blue { background:#EFF6FF; border-color:#DBEAFE; }
  .chip.green { background:#ECFDF5; border-color:#D1FAE5; }

  .section-title { font-family:"Raleway",sans-serif; font-weight:600; margin: 0 0 6px; }
  .section-sub { color:#64748B; margin: -2px 0 8px; font-size: 12px; }

  .stDataFrame { border-radius: 12px; overflow: hidden; }
</style>
        """,
        unsafe_allow_html=True,
    )
_inject_css_once()

# Mobile/compact CSS
st.markdown("""
<style>
@media (max-width: 900px){
  .block-container { padding-left: 8px; padding-right: 8px; }
  .ivy-header .wrap { padding: 10px 0; }
  .ivy-brand { font-size: 16px; }
  .card { padding: 12px; border-radius: 14px; }
  .stButton>button { width: 100%; padding: .75rem 1rem; border-radius: 12px; }
  .stDownloadButton>button { width: 100%; }
  .chip { font-size: 0.85rem; padding:.3rem .55rem; }
  .stDataFrame { border-radius: 10px; }
  .stDataFrame [data-testid="stHorizontalBlock"] { overflow-x: auto; }
}
[data-testid="stDataFrame"] thead tr th { white-space: nowrap; }
</style>
""", unsafe_allow_html=True)


# ========= Registration via invite (BEFORE login) =========
q = st.query_params
if q.get("register", ["0"])[0] == "1":
    st.title("Create your IvyRecon account")
    token = q.get("token", [None])[0]
    info = verify_invite_token(token) if token else None

    if info:
        st.write(f"Invited email: **{info['email']}** · Role: **{info['role']}**")
        name = st.text_input("Your Name")
        pwd  = st.text_input("Create Password", type="password")
        pwd2 = st.text_input("Confirm Password", type="password")
        agree = st.checkbox("I agree to the Terms of Service")

        if st.button("Create Account", type="primary", use_container_width=True):
            if not name or not pwd:
                st.error("Please enter your name and password.")
            elif pwd != pwd2:
                st.error("Passwords do not match.")
            elif not agree:
                st.error("Please accept the Terms.")
            else:
                hashed = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                _add_user(info["email"], name, hashed, info["role"])
                st.success("Account created. You can now log in.")
                st.query_params.clear()
                st.rerun()
    else:
        st.error("Invalid or expired invite link.")
    st.stop()


# ========= Build credentials & login (ONE authenticator) =========
credentials = _get_credentials()
authenticator = stauth.Authenticate(
    credentials=credentials,
    cookie_name=COOKIE_NAME,
    key=COOKIE_KEY,
    cookie_expiry_days=COOKIE_EXPIRY_DAYS,
)
authenticator.login(location="main")

auth_status = st.session_state.get("authentication_status")
name = st.session_state.get("name")
username = (st.session_state.get("username") or "").lower()

if auth_status is False:
    st.error("Invalid credentials"); st.stop()
elif auth_status is None:
    st.info("Enter your email and password to continue."); st.stop()

# Role from DB; fallback to admin by email
users_db = _load_users()
USER_ROLE = users_db["usernames"].get(username, {}).get("role", "admin" if username == ADMIN_EMAIL else "analyst")
st.session_state["role"] = USER_ROLE

# Header + sidebar basics
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

    DEMO_MODE = str(_secret("DEMO_MODE", os.environ.get("DEMO_MODE", "false"))).lower() == "true"
    if "reset_ver" not in st.session_state:
        st.session_state["reset_ver"] = 0

    _q = st.query_params
    DEFAULT_COMPACT = "1" in _q.get("compact", []) or "true" in _q.get("compact", [])
    COMPACT = st.toggle("📱 Compact (mobile)", value=DEFAULT_COMPACT,
                        help="Simplifies layout for small screens. Tip: add ?compact=1 to the URL")

st.sidebar.caption(f"Role: **{USER_ROLE.title()}**")

# Admin: Invite UI (link-based)
INVITES_ENABLED = bool(INVITE_SIGNING_KEY and APP_BASE_URL)
if USER_ROLE == "admin":
    st.sidebar.subheader("Admin Controls")
    with st.sidebar.expander("Invite a new user"):
        with st.form("invite_user_form"):
            inv_email = st.text_input("User email")
            inv_name  = st.text_input("User name")
            inv_role  = st.selectbox("Role", ["analyst", "viewer", "admin"], index=0)
            submitted = st.form_submit_button("Create Invite Link")

        if submitted:
            inv_email = (inv_email or "").strip().lower()
            inv_name  = (inv_name  or "").strip() or (inv_email.split("@")[0] if inv_email else "")
            if not inv_email or "@" not in inv_email:
                st.error("Enter a valid email.")
            elif not INVITES_ENABLED:
                st.error("Invites are disabled. Set INVITE_SIGNING_KEY and APP_BASE_URL in Secrets.")
            else:
                # Create (or update) a placeholder record without password until registration
                data = _load_users()
                data["usernames"].setdefault(inv_email, {
                    "email": inv_email, "name": inv_name, "password": "", "role": inv_role
                })
                # persist role/name (safe to update)
                data["usernames"][inv_email]["name"] = inv_name
                data["usernames"][inv_email]["role"] = inv_role
                _save_users(data)

                link = create_invite_url(inv_email, inv_role)
                st.success(f"Invite link created for {inv_email}. Send this link:")
                st.code(link, language="text")

# ---------------- State & defaults ----------------
if "aliases" not in st.session_state:
    from_secrets = load_aliases_from_secrets(st)
    STRONG_DEFAULTS = {
        "short term disability": ["std","voluntary short term disability","short-term disability","short term dis","std voluntary","voluntary std"],
        "long term disability":  ["ltd","voluntary long term disability","long-term disability","long term dis","ltd voluntary","voluntary ltd"],
        "ad&d":                  ["add","accidental death and dismemberment","voluntary ad&d","voluntary add","vol add","voluntary"],
        "accident":              ["accident plan","accident insurance","acc","voluntary accident"],
        "hospital indemnity":    ["hospital indemnity plan","hospital","hi","voluntary hospital indemnity","hospital plan"],
        "critical illness":      ["critical illness plan","critical","ci","voluntary critical illness"],
        "medical":               ["health","med","medical plan","health plan"],
        "dental":                ["dent","dntl","dental plan"],
        "vision":                ["vis","vision plan","vba"],
        "life":                  ["basic life","group life","life insurance","voluntary life","vol life","employee life","emp life"],
    }
    st.session_state["aliases"] = merge_aliases(
        merge_aliases(DEFAULT_ALIASES, STRONG_DEFAULTS),
        normalize_alias_dict(from_secrets)
    )

REQUIRED = ["SSN","First Name","Last Name","Plan Name","Employee Cost","Employer Cost"]

# ========= UI Tabs =========
st.title("IvyRecon")
st.caption("Modern, tech-forward reconciliation for Payroll • Carrier • BenAdmin")
if USER_ROLE == "admin":
    run_tab, dashboard_tab, settings_tab, admin_tab, help_tab = st.tabs(
        ["Run Reconciliation","Summary Dashboard","Settings","Admin","Help & Formatting"]
    )
else:
    run_tab, dashboard_tab, settings_tab, help_tab = st.tabs(
        ["Run Reconciliation","Summary Dashboard","Settings","Help & Formatting"]
    )

# ========= Helpers: I/O & styling (unchanged) =========
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
        color = "yellow" if et.startswith("Missing in") else ("red" if "Mismatch" in et else "blue")
        chips.append(f'<div class="chip {color}"><b>{cnt}</b> {et}</div>')
    if total: chips.append(f'<div class="chip"><b>Total:</b> {total}</div>')
    st.markdown(" ".join(chips), unsafe_allow_html=True)

# ---------- Postfilter A: collapse drilldown rows when per-key sums match ----------
def postfilter_row_detail_totals(errors_df: pd.DataFrame, cents: int, extra_cents: int = 20):
    if errors_df is None or errors_df.empty:
        return errors_df, 0
    need = {"Error Type", "Plan Name", "SSN"}
    if not need.issubset(errors_df.columns):
        return errors_df, 0

    mask = errors_df["Error Type"].str.contains("Employee Amount Mismatch", case=False, na=False)
    if not mask.any():
        return errors_df, 0

    sub = errors_df[mask].copy()
    ee_cols = [c for c in sub.columns if "employee cost (" in c.lower()]
    if len(ee_cols) < 2:
        return errors_df, 0
    a_col, b_col = ee_cols[0], ee_cols[1]

    def _cents(v):
        try:
            return 0 if pd.isna(v) else int(round(float(v) * 100))
        except Exception:
            return 0

    FREQ_FACTORS = [2, 4, 12, 24, 26, 52]
    slack = max(0, int(cents)) + max(0, int(extra_cents))

    def _totals_match(a_sum_cents: int, b_sum_cents: int) -> bool:
        if abs(a_sum_cents - b_sum_cents) <= slack:
            return True
        for f in FREQ_FACTORS:
            if abs(a_sum_cents - b_sum_cents * f) <= slack:
                return True
            if abs(b_sum_cents - a_sum_cents * f) <= slack:
                return True
        return False

    drop_keys = set()
    for (ssn, plan), g in sub.groupby(["SSN", "Plan Name"], dropna=False):
        a_sum_c = _cents(pd.to_numeric(g[a_col], errors="coerce").fillna(0).sum())
        b_sum_c = _cents(pd.to_numeric(g[b_col], errors="coerce").fillna(0).sum())
        if _totals_match(a_sum_c, b_sum_c):
            drop_keys.add((str(ssn), str(plan)))

    if not drop_keys:
        return errors_df, 0

    keep_idx = []
    dropped = 0
    for i, row in errors_df.iterrows():
        if not mask.iloc[i]:
            keep_idx.append(i); continue
        key = (str(row.get("SSN")), str(row.get("Plan Name")))
        if key in drop_keys:
            dropped += 1
        else:
            keep_idx.append(i)

    filtered = errors_df.loc[keep_idx].reset_index(drop=True)
    return filtered, dropped

# ---------- Postfilter B: normalized-plan, frequency+slack totals check ----------
def _norm_plan(s) -> str:
    if s is None: return ""
    import re
    t = re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()
    return re.sub(r"\s+", " ", t)

def _cents_safe(v):
    try:
        return 0 if pd.isna(v) else int(round(float(v) * 100))
    except Exception:
        return 0

def _totals_match_with_freq(a_total, b_total, cents: int, extra_cents: int = 30) -> bool:
    aa = _cents_safe(a_total); bb = _cents_safe(b_total)
    slack = max(0, int(cents)) + max(0, int(extra_cents))
    if abs(aa - bb) <= slack: return True
    for f in [2, 4, 12, 24, 26, 52]:
        if abs(aa - bb * f) <= slack: return True
        if abs(bb - aa * f) <= slack: return True
    return False

def postfilter_keys_matching_by_frequency(errors_df: pd.DataFrame,
                                          p_df: pd.DataFrame,
                                          b_df: pd.DataFrame,
                                          cents: int,
                                          extra_cents: int = 30):
    if errors_df is None or errors_df.empty: return errors_df, 0
    need = {"Error Type","SSN","Plan Name"}
    if not need.issubset(errors_df.columns): return errors_df, 0
    mask = errors_df["Error Type"].str.contains("Amount Mismatch", case=False, na=False)
    if not mask.any(): return errors_df, 0

    def _totals(df):
        if df is None or df.empty:
            return pd.DataFrame(columns=["SSN","NormPlan","EE","ER","Plan Name"])
        tmp = df.copy()
        if "Plan Name" not in tmp.columns or "SSN" not in tmp.columns:
            return pd.DataFrame(columns=["SSN","NormPlan","EE","ER","Plan Name"])
        tmp["NormPlan"] = tmp["Plan Name"].apply(_norm_plan)
        g = (tmp.groupby(["SSN","NormPlan"], dropna=False, as_index=False)
                 .agg({"Employee Cost":"sum","Employer Cost":"sum","Plan Name":"first"})
                 .rename(columns={"Employee Cost":"EE","Employer Cost":"ER"}))
        return g

    p_tot = _totals(p_df)
    b_tot = _totals(b_df)

    errs = errors_df.loc[mask, ["SSN","Plan Name"]].copy()
    errs["NormPlan"] = errs["Plan Name"].apply(_norm_plan)
    mism = errs.drop_duplicates(subset=["SSN","NormPlan"])[["SSN","NormPlan"]]

    merged = mism.merge(p_tot, on=["SSN","NormPlan"], how="left", suffixes=("", ""))
    merged = merged.merge(b_tot, on=["SSN","NormPlan"], how="left", suffixes=("_P","_B"))

    resolvable_keys = set()
    for _, r in merged.iterrows():
        ok_ee = _totals_match_with_freq(r.get("EE_P"), r.get("EE_B"), cents, extra_cents)
        ok_er = _totals_match_with_freq(r.get("ER_P"), r.get("ER_B"), cents, extra_cents)
        if ok_ee or ok_er:
            resolvable_keys.add((str(r["SSN"]), str(r["NormPlan"])))

    if not resolvable_keys: return errors_df, 0

    keep_idx = []; dropped = 0
    for i, row in errors_df.iterrows():
        if "Amount Mismatch" not in str(row.get("Error Type","")):
            keep_idx.append(i); continue
        key = (str(row.get("SSN")), _norm_plan(row.get("Plan Name")))
        if key in resolvable_keys:
            dropped += 1
        else:
            keep_idx.append(i)

    filtered = errors_df.loc[keep_idx].reset_index(drop=True)
    return filtered, dropped

# ---------- Insights ----------
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


# ========= Normalization & reconciliation (unchanged) =========
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

def totals_by_key_all(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    cols = df.columns
    req = [c for c in ["SSN", "Plan Name"] if c in cols]
    if not req: return df
    sums = {c: "sum" for c in ["Employee Cost", "Employer Cost"] if c in cols}
    keep = {c: "first" for c in ["First Name", "Last Name"] if c in cols}
    agg = {**sums, **keep} if sums else keep
    return df.groupby(req, dropna=False, as_index=False).agg(agg).reset_index(drop=True)

FREQUENCY_FACTORS = [2, 4, 12, 24, 26, 52]

def _tol_ok(a, b, cents: int) -> bool:
    try:
        aa = 0 if pd.isna(a) else int(round(float(a) * 100))
        bb = 0 if pd.isna(b) else int(round(float(b) * 100))
    except Exception:
        return False
    return abs(aa - bb) <= max(0, int(cents))

def _freq_ok(a, b, cents: int, extra_cents: int = 10):
    if _tol_ok(a, b, cents):
        return True, 1
    try:
        aa = 0.0 if pd.isna(a) else float(a)
        bb = 0.0 if pd.isna(b) else float(b)
    except Exception:
        return False, None
    tol = max(0, int(cents)) / 100.0
    extra = max(0, int(extra_cents)) / 100.0
    slack = tol + extra
    for f in FREQUENCY_FACTORS:
        if abs(aa - bb * f) <= slack: return True, f
        if abs(bb - aa * f) <= slack: return True, f
    return False, None

def reconcile_totals_two(a: pd.DataFrame, b: pd.DataFrame, a_name: str, b_name: str, cents: int):
    A, B = totals_by_key_all(a), totals_by_key_all(b)
    merged = pd.merge(A, B, on=["SSN","Plan Name"], how="outer", suffixes=(f" ({a_name})", f" ({b_name})"))
    errors = []; freq_resolved = 0
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
        ok_ee, f_ee = _freq_ok(ee_a, ee_b, cents)
        ok_er, f_er = _freq_ok(er_a, er_b, cents)
        if ok_ee and ok_er:
            if (f_ee and f_ee != 1) or (f_er and f_er != 1): freq_resolved += 1
            continue
        if not ok_ee:
            errors.append({
                "Error Type":"Employee Amount Mismatch","SSN": r["SSN"],"First Name": r.get(f"First Name ({a_name})") or r.get(f"First Name ({b_name})"),
                "Last Name": r.get(f"Last Name ({a_name})") or r.get(f"Last Name ({b_name})"),"Plan Name": r["Plan Name"],
                f"Employee Cost ({a_name})": ee_a, f"Employee Cost ({b_name})": ee_b
            })
        if not ok_er:
            errors.append({
                "Error Type":"Employer Amount Mismatch","SSN": r["SSN"],"First Name": r.get(f"First Name ({a_name})") or r.get(f"First Name ({b_name})"),
                "Last Name": r.get(f"Last Name ({a_name})") or r.get(f"Last Name ({b_name})"),"Plan Name": r["Plan Name"],
                f"Employer Cost ({a_name})": er_a, f"Employer Cost ({b_name})": er_b
            })
    errors_df = pd.DataFrame(errors)
    if errors_df.empty:
        summary_df = pd.DataFrame({"Error Type":["Total"],"Count":[0]})
    else:
        summary_df = errors_df.groupby("Error Type", dropna=False).size().reset_index(name="Count")
        summary_df = pd.concat([summary_df, pd.DataFrame({"Error Type":["Total"],"Count":[int(summary_df["Count"].sum())]})], ignore_index=True)
    compared = len(merged)
    return errors_df, summary_df, compared, freq_resolved

def reconcile_totals_three(p: pd.DataFrame, c: pd.DataFrame, b: pd.DataFrame, cents: int):
    parts = []; resolved = 0
    for (x, xn), (y, yn) in [((p,"Payroll"),(c,"Carrier")), ((p,"Payroll"),(b,"BenAdmin")), ((c,"Carrier"),(b,"BenAdmin"))]:
        e, _, _, r = reconcile_totals_two(x, y, xn, yn, cents)
        if not e.empty: parts.append(e)
        resolved += r
    errors_df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["Error Type"])
    if errors_df.empty:
        summary_df = pd.DataFrame({"Error Type":["Total"],"Count":[0]})
    else:
        summary_df = errors_df.groupby("Error Type", dropna=False).size().reset_index(name="Count")
        summary_df = pd.concat([summary_df, pd.DataFrame({"Error Type":["Total"],"Count":[int(summary_df["Count"].sum())]})], ignore_index=True)
    compared = 0
    return errors_df, summary_df, compared, resolved

def drilldown_row_level_for_keys(p_df, c_df, b_df, keys, threshold):
    if not keys: return pd.DataFrame()
    def _filter(df):
        if df is None or df.empty: return df
        return df[df.apply(lambda r: (str(r.get("SSN")), str(r.get("Plan Name"))) in keys, axis=1)]
    p2, c2, b2 = _filter(p_df), _filter(c_df), _filter(b_df)
    parts = []
    if p2 is not None and c2 is not None and not p2.empty and not c2.empty:
        e, _ = reconcile_two(p2, c2, "Payroll", "Carrier", plan_match_threshold=threshold); parts.append(e)
    if p2 is not None and b2 is not None and not p2.empty and not b2.empty:
        e, _ = reconcile_two(p2, b2, "Payroll", "BenAdmin", plan_match_threshold=threshold); parts.append(e)
    if c2 is not None and b2 is not None and not c2.empty and not b2.empty:
        e, _ = reconcile_two(c2, b2, "Carrier", "BenAdmin", plan_match_threshold=threshold); parts.append(e)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

# Postfilters + insights (unchanged; omitted here for brevity, keep your existing ones)
# --- keep all your postfilter_* and insights functions exactly as in your file ---

# ========= Tabs / Main UI (unchanged except COMPACT heights) =========
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

with run_tab:
    up_col, opt_col = st.columns([2,1])
    with up_col:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">Upload Files</div>', unsafe_allow_html=True)
        st.markdown('<div class="section-sub">CSV or Excel. Columns: SSN, First/Last, Plan Name, Employee/Employer Cost</div>', unsafe_allow_html=True)

    if st.session_state.get("COMPACT_OVERRIDE", False):
        COMPACT = True  # optional override
    # File inputs
    if st.session_state.get("compact_ui", False):  # safety if you later store it
        COMPACT = True

    if 'COMPACT' not in locals():
        COMPACT = False  # failsafe

    if COMPACT:
        payroll_file  = st.file_uploader("Payroll (CSV/XLSX)",  type=["csv","xlsx"], key=f"payroll_{st.session_state.reset_ver}")
        carrier_file  = st.file_uploader("Carrier (CSV/XLSX)",  type=["csv","xlsx"], key=f"carrier_{st.session_state.reset_ver}")
        benadmin_file = st.file_uploader("BenAdmin (CSV/XLSX)", type=["csv","xlsx"], key=f"benadmin_{st.session_state.reset_ver}")
    else:
        u1, u2, u3 = st.columns(3)
        with u1:
            payroll_file = st.file_uploader("Payroll (CSV/XLSX)",  type=["csv","xlsx"], key=f"payroll_{st.session_state.reset_ver}")
        with u2:
            carrier_file = st.file_uploader("Carrier (CSV/XLSX)",  type=["csv","xlsx"], key=f"carrier_{st.session_state.reset_ver}")
        with u3:
            benadmin_file = st.file_uploader("BenAdmin (CSV/XLSX)", type=["csv","xlsx"], key=f"benadmin_{st.session_state.reset_ver}")

        st.caption("Required Columns: SSN, First Name, Last Name, Plan Name, Employee Cost, Employer Cost")
        st.markdown('</div>', unsafe_allow_html=True)

    with opt_col:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">Details</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        group_name = st.text_input("Group Name", value="")
        period     = st.text_input("Reporting Period", value="")
        with st.expander("Advanced (optional)"):
            st.caption("Defaults are battle-tested. Tweak only if needed.")
            threshold = st.slider("Plan Name Match Threshold", 0.5, 1.0, 0.90, 0.01)
            treat_blank_as_zero = st.checkbox("Treat blank amounts as $0.00", value=True)
            amount_tolerance_cents = st.slider("Amount tolerance (cents)", 0, 25, 2, 1)
            minutes_per_line = st.slider("Manual mins per line (for ROI)", 0.5, 3.0, 1.2, 0.1)
            hourly_rate = st.slider("Hourly cost ($)", 15, 150, 40, 5)
            smart_cleanup = st.checkbox("Smart cleanup (recommended)", value=True,
                                        help="Auto-resolve split-line, pay-frequency, rounding, and blank↔$0 cases.")
            if DEMO_MODE and not smart_cleanup:
                smart_cleanup = True
                st.caption("Demo mode: Smart cleanup forced ON.")

        clear_col1, clear_col2 = st.columns(2)
        with clear_col1:
            run_clicked = st.button("Run Reconciliation", type="primary", use_container_width=True)
        with clear_col2:
            if st.button("Clear All", use_container_width=True):
                st.session_state.reset_ver += 1
                st.cache_data.clear()
                st.cache_resource.clear()
                st.rerun()
            if COMPACT:
                st.markdown("<div style='margin-top:-6px'></div>", unsafe_allow_html=True)
                st.button("▶️ Run Reconciliation", type="primary", key="run_mobile",
                          on_click=lambda: st.session_state.__setitem__('run_click_proxy', True))
                run_clicked = st.session_state.get('run_click_proxy', False) or run_clicked
        st.markdown('</div>', unsafe_allow_html=True)

    # Load & preview before run
    p_df = standardize_df(load_any(payroll_file))
    c_df = standardize_df(load_any(carrier_file))
    b_df = standardize_df(load_any(benadmin_file))

    if not run_clicked:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("### Previews")
        if COMPACT:
            st.markdown("#### Payroll");  st.dataframe((p_df.head(8) if p_df is not None else pd.DataFrame()), use_container_width=True, height=220); quick_stats(p_df, "Payroll")
            st.markdown("#### Carrier");  st.dataframe((c_df.head(8) if c_df is not None else pd.DataFrame()), use_container_width=True, height=220); quick_stats(c_df, "Carrier")
            st.markdown("#### BenAdmin"); st.dataframe((b_df.head(8) if b_df is not None else pd.DataFrame()), use_container_width=True, height=220); quick_stats(b_df, "BenAdmin")
        else:
            pcol, ccol, bcol = st.columns(3)
            with pcol:
                st.markdown("#### Payroll");   st.dataframe((p_df.head(12) if p_df is not None else pd.DataFrame()), use_container_width=True); quick_stats(p_df, "Payroll")
            with ccol:
                st.markdown("#### Carrier");   st.dataframe((c_df.head(12) if c_df is not None else pd.DataFrame()), use_container_width=True); quick_stats(c_df, "Carrier")
            with bcol:
                st.markdown("#### BenAdmin");  st.dataframe((b_df.head(12) if b_df is not None else pd.DataFrame()), use_container_width=True); quick_stats(b_df, "BenAdmin")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True); st.markdown("### Results")
    if smart_cleanup:
        st.markdown('<span class="chip" style="background:#ECFDF5;border-color:#D1FAE5;">⚡ AI-powered Smart Cleanup</span>', unsafe_allow_html=True)

    if run_clicked:
        try:
            # 1) vendor prefixes
            p_df = strip_carrier_prefixes(p_df); c_df = strip_carrier_prefixes(c_df); b_df = strip_carrier_prefixes(b_df)
            # 2) aliases
            aliases = st.session_state["aliases"]
            p_df = apply_aliases_to_df(p_df, "Plan Name", aliases, threshold=0.90) if p_df is not None else None
            c_df = apply_aliases_to_df(c_df, "Plan Name", aliases, threshold=0.90) if c_df is not None else None
            b_df = apply_aliases_to_df(b_df, "Plan Name", aliases, threshold=0.90) if b_df is not None else None
            # 3) amounts normalization
            amount_tolerance_cents = 2 if 'amount_tolerance_cents' not in locals() else amount_tolerance_cents
            treat_blank_as_zero = True if 'treat_blank_as_zero' not in locals() else treat_blank_as_zero
            p_df = normalize_amounts(p_df, tolerance_cents=amount_tolerance_cents, blank_is_zero=treat_blank_as_zero)
            c_df = normalize_amounts(c_df, tolerance_cents=amount_tolerance_cents, blank_is_zero=treat_blank_as_zero)
            b_df = normalize_amounts(b_df, tolerance_cents=amount_tolerance_cents, blank_is_zero=treat_blank_as_zero)
            # 4) drop exact dupes
            _drop = lambda df: df.drop_duplicates().reset_index(drop=True) if (df is not None and not df.empty) else df
            p_df, c_df, b_df = _drop(p_df), _drop(c_df), _drop(b_df)

            p_tot, c_tot, b_tot = p_df, c_df, b_df

            # 6) totals engine
            if all([x is not None and not x.empty for x in [p_tot, c_tot, b_tot]]):
                errors_df, summary_df, _comp, freq_resolved = reconcile_totals_three(p_tot, c_tot, b_tot, amount_tolerance_cents)
                mode = "Smart totals (frequency-aware): Payroll vs Carrier vs BenAdmin"
                compared_lines = len(pd.concat([p_tot, c_tot, b_tot], ignore_index=True))
            elif p_tot is not None and c_tot is not None and not p_tot.empty and not c_tot.empty:
                errors_df, summary_df, compared_lines, freq_resolved = reconcile_totals_two(p_tot, c_tot, "Payroll", "Carrier", amount_tolerance_cents)
                mode = "Smart totals (frequency-aware): Payroll vs Carrier"
            elif p_tot is not None and b_tot is not None and not p_tot.empty and not b_tot.empty:
                errors_df, summary_df, compared_lines, freq_resolved = reconcile_totals_two(p_tot, b_tot, "Payroll", "BenAdmin", amount_tolerance_cents)
                mode = "Smart totals (frequency-aware): Payroll vs BenAdmin"
            elif c_tot is not None and b_tot is not None and not c_tot.empty and not b_tot.empty:
                errors_df, summary_df, compared_lines, freq_resolved = reconcile_totals_two(c_tot, b_tot, "Carrier", "BenAdmin", amount_tolerance_cents)
                mode = "Smart totals (frequency-aware): Carrier vs BenAdmin"
            else:
                st.warning("Please upload at least two files to reconcile."); st.markdown('</div>', unsafe_allow_html=True); st.stop()

            # 7) drilldown
            if not errors_df.empty:
                mismatch_mask = errors_df["Error Type"].str.contains("Amount Mismatch", case=False, na=False)
                keys = set(zip(errors_df.loc[mismatch_mask, "SSN"], errors_df.loc[mismatch_mask, "Plan Name"]))
                if keys:
                    row_detail = drilldown_row_level_for_keys(p_df, c_df, b_df, keys, 0.90)
                    if row_detail is not None and not row_detail.empty:
                        keep_idx = []
                        for i, r in errors_df.iterrows():
                            if mismatch_mask.iloc[i] and (str(r["SSN"]), str(r["Plan Name"])) in keys:
                                continue
                            keep_idx.append(i)
                        errors_df = pd.concat([errors_df.iloc[keep_idx].reset_index(drop=True), row_detail], ignore_index=True)
                        if not errors_df.empty:
                            summary_df = errors_df.groupby("Error Type", dropna=False).size().reset_index(name="Count")
                            summary_df = pd.concat([summary_df, pd.DataFrame({"Error Type":["Total"],"Count":[int(summary_df["Count"].sum())]})], ignore_index=True)

            # Snapshot before postfilters
            errors_df_raw = errors_df.copy() if errors_df is not None else pd.DataFrame()
            summary_df_raw = (summary_df.copy()
                              if summary_df is not None else pd.DataFrame({"Error Type": ["Total"], "Count": [0]}))
            raw_total_errors = int(summary_df_raw[summary_df_raw["Error Type"].str.lower()=="total"]["Count"].sum() or 0)

            st.success(f"Completed: {mode}")
            if freq_resolved:
                st.caption(f"Resolved {freq_resolved} premium differences by recognizing monthly/per-pay frequency scaling.")

            # --- Your postfilters & insights from original file (keep as-is) ---
            # (postfilter_row_detail_totals, postfilter_keys_matching_by_frequency, compute_insights, render_quick_insights, etc.)
            # ... keep the rest of your original code from here down unchanged ...
            # (I kept your full blocks in your original paste—no functional changes)

            # ====== Keep your original postfilters + insights + exports block here (unchanged) ======
            # [PASTE: your existing postfilter_* functions are already defined above; now reuse them here]
            dropped_rd = 0; dropped_freq = 0
            if smart_cleanup:
                errors_df, dropped_rd = postfilter_row_detail_totals(errors_df, amount_tolerance_cents)
                if dropped_rd and not errors_df.empty:
                    summary_df = (errors_df.groupby("Error Type", dropna=False).size().reset_index(name="Count"))
                    summary_df = pd.concat([summary_df, pd.DataFrame({"Error Type":["Total"],"Count":[int(summary_df["Count"].sum())]})], ignore_index=True)
                    st.caption(f"Collapsed {dropped_rd} split-line mismatches whose totals matched within {amount_tolerance_cents}¢.")
                try:
                    errors_df, dropped_freq = postfilter_keys_matching_by_frequency(errors_df, p_df, b_df, cents=amount_tolerance_cents, extra_cents=30)
                    if dropped_freq:
                        if not errors_df.empty:
                            summary_df = (errors_df.groupby("Error Type", dropna=False).size().reset_index(name="Count"))
                            summary_df = pd.concat([summary_df, pd.DataFrame({"Error Type":["Total"],"Count":[int(summary_df["Count"].sum())]})], ignore_index=True)
                        st.caption(f"Resolved {dropped_freq} split/frequency cases (normalized plan key + extra slack).")
                except Exception:
                    pass
            else:
                st.info("Smart cleanup is OFF — showing raw reconciliation results.")
                clean_total_errors = 0
                if summary_df is not None and not summary_df.empty:
                    clean_total_errors = int(summary_df[summary_df["Error Type"].str.lower()=="total"]["Count"].sum() or 0)
                m1, m2, m3 = st.columns(3)
                with m1: st.metric("Errors (raw)", f"{raw_total_errors:,}")
                with m2: st.metric("Errors (after cleanup)", f"{clean_total_errors:,}")
                with m3:
                    delta = raw_total_errors - clean_total_errors
                    pct = (delta / raw_total_errors) if raw_total_errors else 0
                    st.metric("Auto-resolved", f"{delta:,}", f"{pct:.0%} cleaned")

            minutes_per_line = 1.2 if 'minutes_per_line' not in locals() else minutes_per_line
            hourly_rate = 40 if 'hourly_rate' not in locals() else hourly_rate
            ins = compute_insights(summary_df, errors_df, compared_lines, minutes_per_line, hourly_rate)
            a,b = st.columns([2,1])
            with a: render_quick_insights(ins)
            with b:
                blurb = f"{ins['compared_lines']:,} lines • {ins['error_rate']:.1%} errors • saved ~{ins['hours_saved']:.1f} hrs (~${ins['dollars_saved']:,.0f})"
                st.caption("Sales blurb"); st.text_area("", blurb, height=60, label_visibility="collapsed", disabled=True)

            render_error_chips(summary_df)

            L,R = st.columns([1,2])
            with L:
                st.markdown("**Summary**")
                st.dataframe(summary_df, use_container_width=True)
            with R:
                st.markdown("**Errors**")
                if errors_df is not None and not errors_df.empty:
                    st.dataframe(style_errors(errors_df), use_container_width=True, height=(360 if COMPACT else 520))
                else:
                    st.info("No errors found.")

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
                    f"{ins['compared_lines']:,} lines • {ins['error_rate']:.1%} errors • saved ~{ins['hours_saved']:,.1f} hrs (~${ins['dollars_saved']:,.0f})")
                st.caption("Copied")

        except Exception as e:
            st.error(f"Error: {e}"); st.exception(e)
    else:
        st.info("Upload 2 or 3 files and click **Run Reconciliation**.")
    st.markdown('</div>', unsafe_allow_html=True)


def build_summary_pdf(ins: dict, summary_df: pd.DataFrame, group_name: str, period: str) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    W, H = LETTER
    TEAL = colors.HexColor("#18CCAA")
    NAVY = colors.HexColor("#2F455C")
    c.setFillColor(TEAL); c.rect(0, H-0.9*inch, W, 0.9*inch, stroke=0, fill=1)
    c.setFillColor(NAVY); c.setFont("Helvetica-Bold", 20)
    c.drawString(0.75*inch, H-0.55*inch, "IvyRecon — Executive Summary")
    c.setFillColor(NAVY); c.setFont("Helvetica", 10)
    c.drawString(0.75*inch, H-1.1*inch, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    c.drawString(0.75*inch, H-1.3*inch, f"Group: {group_name or '-'}    Period: {period or '-'}")
    y = H-1.8*inch
    c.setFont("Helvetica-Bold", 13); c.drawString(0.75*inch, y, "Key Metrics")
    c.setFont("Helvetica", 12); y -= 0.25*inch
    lines = [
        f"Lines reconciled: {ins['compared_lines']:,}",
        f"Error rate: {ins['error_rate']:.1%}",
        f"Most common error: {ins['most_common']}",
        f"Plan name mismatches: {ins['mismatch_pct']:.1%}",
        f"Time saved (hrs): {ins['hours_saved']:.1f}",
        f"Estimated $ saved: ${ins['dollars_saved']:,.0f}",
    ]
    for t in lines: c.drawString(0.9*inch, y, t); y -= 0.22*inch
    y -= 0.2*inch; c.setFont("Helvetica-Bold", 13); c.drawString(0.75*inch, y, "Error Summary")
    y -= 0.25*inch; c.setFont("Helvetica-Bold", 11)
    c.drawString(0.9*inch, y, "Type"); c.drawRightString(7.25*inch, y, "Count")
    c.setLineWidth(0.5); c.line(0.9*inch, y-3, 7.25*inch, y-3)
    c.setFont("Helvetica", 11); y -= 0.2*inch
    if summary_df is not None and not summary_df.empty:
        top = (summary_df[summary_df["Error Type"].str.lower()!="total"].sort_values("Count", ascending=False).head(6))
        for _, r in top.iterrows():
            c.drawString(0.9*inch, y, str(r["Error Type"])[:60])
            c.drawRightString(7.25*inch, y, f"{int(r['Count']):,}")
            y -= 0.2*inch
    c.setFillColor(colors.grey); c.setFont("Helvetica", 9)
    c.drawString(0.75*inch, 0.6*inch, "Uploads are not stored. Generated by IvyRecon.")
    c.showPage(); c.save(); buf.seek(0); return buf.getvalue()

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

        **Plan Name matching**: IvyRecon auto-applies aliases + fuzzy normalization (and strips vendor words, e.g., "Sun Life").

        **Smart Totals**: We automatically recognize monthly vs per-pay premium scales (×2, ×4, ×24/26, ×52) to avoid false mismatches.

        **Exports**: Multi-tab Excel includes Summary, All Errors, and one sheet per error type — branded to IvyRecon.
        """
    )










