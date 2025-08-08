import re
import pandas as pd
from difflib import SequenceMatcher

REQUIRED_COLS = [
    "SSN", "First Name", "Last Name", "Plan Name", "Employee Cost", "Employer Cost"
]

CANONICAL_MAP = {
    "ssn": "SSN",
    "social security number": "SSN",
    "first name": "First Name",
    "last name": "Last Name",
    "plan": "Plan Name",
    "plan name": "Plan Name",
    "employee amount": "Employee Cost",
    "employee cost": "Employee Cost",
    "employer amount": "Employer Cost",
    "employer cost": "Employer Cost",
}

PLAN_ALIASES = {
    "medical": "health",
    "health": "health",
    "std": "short term disability",
    "short term disability": "short term disability",
    "ltd": "long term disability",
    "long term disability": "long term disability",
    "dental": "dental",
    "vision": "vision",
    "ppo": "ppo",
    "hmo": "hmo",
    "hsa": "hsa",
    "fsa": "fsa",
}

def normalize_header(h: str):
    key = re.sub(r"\s+", " ", h.strip().lower())
    return CANONICAL_MAP.get(key, None)

def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    new_cols = {}
    for c in df.columns:
        m = normalize_header(str(c))
        new_cols[c] = m if m else c
    return df.rename(columns=new_cols)

def validate_required_columns(df: pd.DataFrame):
    cols = set(df.columns)
    return [c for c in REQUIRED_COLS if c not in cols]

def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    for c in ["First Name", "Last Name", "Plan Name"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()
    if "SSN" in df.columns:
        df["SSN"] = df["SSN"].astype(str).str.replace(r"\D", "", regex=True)
    for amt in ["Employee Cost", "Employer Cost"]:
        if amt in df.columns:
            df[amt] = pd.to_numeric(df[amt], errors="coerce").fillna(0.0)
    return df

def normalize_plan_name(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [PLAN_ALIASES.get(tok, tok) for tok in s.split()]
    return " ".join(tokens)

def plan_similarity(a: str, b: str) -> float:
    a_n, b_n = normalize_plan_name(a), normalize_plan_name(b)
    return SequenceMatcher(None, a_n, b_n).ratio()
