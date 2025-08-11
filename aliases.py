# aliases.py — IvyRecon plan name normalization & alias helpers
from __future__ import annotations
from typing import Dict, List
from rapidfuzz import fuzz, process

# Canonical → list of aliases (case-insensitive)
DEFAULT_ALIASES: Dict[str, List[str]] = {
    "medical": ["health", "med", "medical plan", "health plan"],
    "dental": ["dent", "dntl"],
    "vision": ["vis", "vba"],
    "short term disability": ["std", "short-term disability", "short term dis", "short term"],
    "long term disability": ["ltd", "long-term disability", "long term dis", "long term"],
    "life": ["basic life", "group life", "life insurance"],
    "hsa": ["health savings account", "hsa plan"],
    "fsa": ["flexible spending account", "medical fsa", "fsa medical"],
}

def load_aliases_from_secrets(st) -> Dict[str, List[str]]:
    """Load aliases dict from Streamlit secrets if present, else empty."""
    try:
        a = st.secrets.get("PLAN_ALIASES")
        return dict(a) if isinstance(a, dict) else {}
    except Exception:
        return {}

def normalize_alias_dict(raw: Dict[str, List[str]]) -> Dict[str, List[str]]:
    norm: Dict[str, List[str]] = {}
    for canon, al in (raw or {}).items():
        key = (canon or "").strip().lower()
        if not key:
            continue
        vals = []
        for x in (al or []):
            s = (str(x) or "").strip().lower()
            if s and s != key and s not in vals:
                vals.append(s)
        norm[key] = vals
    return norm

def merge_aliases(a: Dict[str, List[str]], b: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Merge two alias dicts, deduping lowercase strings."""
    out = dict(a or {})
    for canon, lst in (b or {}).items():
        canon_l = canon.strip().lower()
        base = set(out.get(canon_l, []))
        for x in lst or []:
            s = (str(x) or "").strip().lower()
            if s and s != canon_l:
                base.add(s)
        out[canon_l] = sorted(base)
    return out

def normalize_with_aliases(name: str, aliases: Dict[str, List[str]], threshold: float = 0.9) -> str:
    """
    Return a canonical plan name using aliases+fuzzy matching.
    - Exact/alias match wins.
    - Else fuzzy to nearest canonical if ≥ threshold (0..1).
    """
    if not name:
        return name
    s = str(name).strip().lower()
    if not s:
        return name

    # exact canonical?
    if s in aliases:
        return s

    # exact alias → canonical
    for canon, al in aliases.items():
        if s == canon or s in al:
            return canon

    # fuzzy against canonicals+aliases
    all_keys = list(aliases.keys())
    # Build expanded choices list (canon + each alias mapping back to canon)
    expanded = []
    backmap = {}
    for canon, al in aliases.items():
        expanded.append(canon)
        backmap[canon] = canon
        for x in al:
            expanded.append(x)
            backmap[x] = canon

    if not expanded:
        return name

    best = process.extractOne(
        s, expanded, scorer=fuzz.token_sort_ratio
    )
    if best and best[1] >= int(threshold * 100):
        match = best[0]
        return backmap.get(match, match)

    return name

def apply_aliases_to_df(df, plan_col: str, aliases: Dict[str, List[str]], threshold: float = 0.9):
    if df is None or df.empty or plan_col not in df.columns:
        return df
    df = df.copy()
    df[plan_col] = df[plan_col].astype(str).apply(lambda x: normalize_with_aliases(x, aliases, threshold))
    return df
