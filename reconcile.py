from typing import Tuple
import pandas as pd
from utils import (
    standardize_columns, validate_required_columns, coerce_types,
    plan_similarity, normalize_plan_name
)

def _prepare(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    df = standardize_columns(df)
    missing = validate_required_columns(df)
    if missing:
        raise ValueError(f"{source_name}: Missing required columns: {', '.join(missing)}")
    df = coerce_types(df)
    df["Key"] = df["SSN"].astype(str) + "||" + df["Plan Name"].astype(str)
    df["Source"] = source_name
    return df

def reconcile_two(a: pd.DataFrame, b: pd.DataFrame,
                  a_name: str, b_name: str,
                  plan_match_threshold: float = 0.9) -> Tuple[pd.DataFrame, pd.DataFrame]:
    A = _prepare(a.copy(), a_name)
    B = _prepare(b.copy(), b_name)

    merged = pd.merge(
        A[["Key", "SSN", "First Name", "Last Name", "Plan Name", "Employee Cost", "Employer Cost"]],
        B[["Key", "SSN", "First Name", "Last Name", "Plan Name", "Employee Cost", "Employer Cost"]],
        on="Key", how="outer", suffixes=(f"_{a_name}", f"_{b_name}")
    )

    errors = []
    ssn_plans = {}

    for _, r in merged.iterrows():
        in_a = pd.notna(r.get(f"SSN_{a_name}"))
        in_b = pd.notna(r.get(f"SSN_{b_name}"))

        if in_a and not in_b:
            errors.append(_err_row(r, a_name, b_name, f"Missing in {b_name}")); continue
        if in_b and not in_a:
            errors.append(_err_row(r, a_name, b_name, f"Missing in {a_name}")); continue

        plan_a = str(r.get(f"Plan Name_{a_name}", ""))
        plan_b = str(r.get(f"Plan Name_{b_name}", ""))
        sim = plan_similarity(plan_a, plan_b)
        if sim < plan_match_threshold:
            errors.append(_err_row(r, a_name, b_name, "Plan Name Mismatch", extra={"Similarity": round(sim, 3)}))

        emp_a = r.get(f"Employee Cost_{a_name}", 0.0)
        emp_b = r.get(f"Employee Cost_{b_name}", 0.0)
        if pd.notna(emp_a) and pd.notna(emp_b) and float(emp_a) != float(emp_b):
            errors.append(_err_row(r, a_name, b_name, "Employee Amount Mismatch"))

        er_a = r.get(f"Employer Cost_{a_name}", 0.0)
        er_b = r.get(f"Employer Cost_{b_name}", 0.0)
        if pd.notna(er_a) and pd.notna(er_b) and float(er_a) != float(er_b):
            errors.append(_err_row(r, a_name, b_name, "Employer Amount Mismatch"))

        ssn = str(r.get(f"SSN_{a_name}")) if in_a else str(r.get(f"SSN_{b_name}"))
        if ssn:
            ssn_plans.setdefault(ssn, set()).update({normalize_plan_name(plan_a), normalize_plan_name(plan_b)})

    for ssn, plans in ssn_plans.items():
        if len([p for p in plans if p]) > 1:
            errors.append({
                "Error Type": "Duplicate SSN with Different Plans",
                "SSN": ssn,
                "First Name": None,
                "Last Name": None,
                "Plan Name": ", ".join(sorted(plans)),
                f"Employee Cost ({a_name})": None,
                f"Employee Cost ({b_name})": None,
                f"Employer Cost ({a_name})": None,
                f"Employer Cost ({b_name})": None,
                "Similarity": None
            })

    errors_df = pd.DataFrame(errors) if errors else pd.DataFrame(columns=_err_cols(a_name, b_name))
    summary = _summarize(errors_df)
    return errors_df, summary

def _err_cols(a_name, b_name):
    return [
        "Error Type", "SSN", "First Name", "Last Name", "Plan Name",
        f"Employee Cost ({a_name})", f"Employee Cost ({b_name})",
        f"Employer Cost ({a_name})", f"Employer Cost ({b_name})",
        "Similarity",
    ]

def _err_row(r, a_name, b_name, err_type, extra=None):
    import pandas as pd
    extra = extra or {}
    return {
        "Error Type": err_type,
        "SSN": r.get(f"SSN_{a_name}") if pd.notna(r.get(f"SSN_{a_name}")) else r.get(f"SSN_{b_name}"),
        "First Name": r.get(f"First Name_{a_name}") if pd.notna(r.get(f"First Name_{a_name}")) else r.get(f"First Name_{b_name}"),
        "Last Name": r.get(f"Last Name_{a_name}") if pd.notna(r.get(f"Last Name_{a_name}")) else r.get(f"Last Name_{b_name}"),
        "Plan Name": r.get(f"Plan Name_{a_name}") if pd.notna(r.get(f"Plan Name_{a_name}")) else r.get(f"Plan Name_{b_name}"),
        f"Employee Cost ({a_name})": r.get(f"Employee Cost_{a_name}"),
        f"Employee Cost ({b_name})": r.get(f"Employee Cost_{b_name}"),
        f"Employer Cost ({a_name})": r.get(f"Employer Cost_{a_name}"),
        f"Employer Cost ({b_name})": r.get(f"Employer Cost_{b_name}"),
        "Similarity": extra.get("Similarity", None)
    }

def _summarize(errors_df: pd.DataFrame) -> pd.DataFrame:
    if errors_df.empty:
        return pd.DataFrame({"Error Type": ["Total"], "Count": [0]})
    summary = errors_df["Error Type"].value_counts(dropna=False).reset_index()
    summary.columns = ["Error Type", "Count"]
    total = int(summary["Count"].sum())
    return pd.concat([summary, pd.DataFrame([{"Error Type":"Total","Count":total}])], ignore_index=True)

def reconcile_three(payroll: pd.DataFrame, carrier: pd.DataFrame, benadmin: pd.DataFrame,
                    plan_match_threshold: float = 0.9) -> Tuple[pd.DataFrame, pd.DataFrame]:
    e_pc, _ = reconcile_two(payroll, carrier, "Payroll", "Carrier", plan_match_threshold)
    e_pb, _ = reconcile_two(payroll, benadmin, "Payroll", "BenAdmin", plan_match_threshold)
    e_cb, _ = reconcile_two(carrier, benadmin, "Carrier", "BenAdmin", plan_match_threshold)
    all_errs = pd.concat([e_pc, e_pb, e_cb], ignore_index=True)
    if not all_errs.empty:
        all_errs["__k"] = all_errs["Error Type"].astype(str) + "|" + all_errs["SSN"].astype(str) + "|" + all_errs["Plan Name"].astype(str)
        all_errs = all_errs.drop_duplicates("__k").drop(columns="__k")
    summary = all_errs["Error Type"].value_counts().reset_index() if not all_errs.empty else pd.DataFrame({"Error Type":["Total"],"Count":[0]})
    if not summary.empty:
        summary.columns = ["Error Type", "Count"]
        total = int(summary["Count"].sum())
        summary = pd.concat([summary, pd.DataFrame([{"Error Type":"Total","Count":total}])], ignore_index=True)
    return all_errs, summary
