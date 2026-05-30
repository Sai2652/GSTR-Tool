"""
Validation pipeline:
  1. Build a "master list" of valid GSTIN -> canonical customer name from clean rows.
  2. For each row, validate the GSTIN. If invalid, attempt auto-correction.
  3. Cross-check customer name against the master list for the GSTIN.
  4. Produce row-level annotations + an exceptions report.
"""
import pandas as pd
from collections import defaultdict
from gstin_validator import (
    validate_gstin,
    auto_correct_gstin,
    name_match_score,
    get_state_code,
    get_state_name,
)


def build_master_list(df: pd.DataFrame) -> dict:
    """Build {GSTIN: most_common_clean_name} from valid rows."""
    master = defaultdict(lambda: defaultdict(int))
    for _, row in df.iterrows():
        v = validate_gstin(row["gstin"])
        if v["valid"]:
            name = str(row["customer_name"]).strip()
            if name and name.lower() != "nan":
                # Pick the longest reasonable name as canonical
                master[v["gstin"]][name] += 1
    # Choose canonical name = most frequent (tie-break: longest)
    canonical = {}
    for gstin, names in master.items():
        canonical[gstin] = max(names.items(), key=lambda x: (x[1], len(x[0])))[0]
    return canonical


HSN_TURNOVER_THRESHOLD = 5_00_00_000  # Rs 5 crore — Notification 78/2020-CT


def _hsn_min_digits(annual_turnover: float | None) -> int:
    """
    Per Notification 78/2020-CT (effective 1-Apr-2021):
      - Turnover > Rs 5 crore: 6-digit HSN mandatory on B2B and B2C
      - Turnover <= Rs 5 crore: 4-digit HSN mandatory on B2B
    If turnover is unknown, fall back to 4 (the universal minimum for B2B).
    """
    if annual_turnover is None:
        return 4
    return 6 if annual_turnover >= HSN_TURNOVER_THRESHOLD else 4


def validate_dataframe(df: pd.DataFrame, firm_state_code: str = "29",
                       external_cache: dict = None,
                       annual_turnover: float | None = None) -> tuple:
    """
    Runs validation per row.

    Args:
      df: input DataFrame.
      firm_state_code: 2-digit state code of the filing firm.
      external_cache: optional {gstin: name} map from a persistent customer
        cache. Used to (a) suggest canonical names when sheet has no name,
        and (b) catch name mismatches against historical data.

    Returns (annotated_df, exceptions_df, master_list).
    """
    master = build_master_list(df)

    # Merge external cache as a lower-priority source of canonical names —
    # in-file master takes precedence, but cache fills in gaps.
    if external_cache:
        for g, name in external_cache.items():
            if g not in master and name:
                master[g] = name

    hsn_min = _hsn_min_digits(annual_turnover)

    annotations = []
    exceptions = []

    for idx, row in df.iterrows():
        original_gstin = str(row["gstin"]).strip()
        customer_name = str(row["customer_name"]).strip()

        ann = {
            "row": idx + 2,  # Excel-row reference (header is row 1)
            "original_gstin": original_gstin,
            "corrected_gstin": "",
            "correction_method": "",
            "correction_confidence": 0,
            "gstin_valid": False,
            "name_match_score": 0,
            "name_canonical": "",
            "is_b2b": False,
            "is_interstate": False,
            "issues": [],
        }

        # ---- GSTIN validation ----
        validation = validate_gstin(original_gstin)
        if validation["valid"]:
            ann["corrected_gstin"] = validation["gstin"]
            ann["correction_method"] = "ALREADY_VALID"
            ann["correction_confidence"] = 100
            ann["gstin_valid"] = True
        else:
            # Try to correct
            correction = auto_correct_gstin(original_gstin, master_list=master)
            if correction["corrected"]:
                ann["corrected_gstin"] = correction["corrected"]
                ann["correction_method"] = correction["method"]
                ann["correction_confidence"] = correction["confidence"]
                ann["gstin_valid"] = True
                ann["issues"].append(
                    f"GSTIN auto-corrected ({correction['method']}): "
                    f"{original_gstin} -> {correction['corrected']}"
                )
            else:
                ann["issues"].append(
                    f"GSTIN invalid and uncorrectable ({validation['reason']})"
                )
                # Still track for B2CS/B2CL classification
                ann["corrected_gstin"] = ""

        # ---- Name match against master list ----
        if ann["gstin_valid"] and ann["corrected_gstin"] in master:
            canonical = master[ann["corrected_gstin"]]
            ann["name_canonical"] = canonical
            score = name_match_score(customer_name, canonical)
            ann["name_match_score"] = score
            if score < 75:
                ann["issues"].append(
                    f"Name mismatch (score={score}): "
                    f"sheet='{customer_name}' vs canonical='{canonical}'"
                )

        # ---- B2B / Interstate flags ----
        if ann["gstin_valid"] and ann["corrected_gstin"]:
            ann["is_b2b"] = True
            cust_state = get_state_code(ann["corrected_gstin"])
            ann["is_interstate"] = cust_state != firm_state_code

        # ---- HSN length check (Notification 78/2020-CT) ----
        hsn_raw = str(row.get("hsn", "") or "").strip()
        # Strip non-digit chars (some sheets carry " 9989 " or "9989.0")
        hsn_digits = "".join(ch for ch in hsn_raw if ch.isdigit())
        if hsn_digits and len(hsn_digits) < hsn_min:
            ann["issues"].append(
                f"HSN '{hsn_raw}' has {len(hsn_digits)} digits; "
                f"{hsn_min}-digit HSN required"
                + (f" (turnover > Rs 5 cr)" if hsn_min == 6 else "")
            )
        elif not hsn_digits and ann["is_b2b"]:
            ann["issues"].append("HSN missing on B2B invoice")

        annotations.append(ann)

        if ann["issues"]:
            exceptions.append({
                "Row": ann["row"],
                "Invoice No": row.get("invoice_no", ""),
                "Original GSTIN": original_gstin,
                "Corrected GSTIN": ann["corrected_gstin"],
                "Method": ann["correction_method"],
                "Confidence": ann["correction_confidence"],
                "Customer Name (sheet)": customer_name,
                "Canonical Name": ann["name_canonical"],
                "Name Match Score": ann["name_match_score"],
                "Issues": " | ".join(ann["issues"]),
            })

    ann_df = pd.DataFrame(annotations)
    exc_df = pd.DataFrame(exceptions)

    # Merge annotations back into the main df. Handle empty case (no rows).
    df = df.copy()
    if len(ann_df) == 0 or "corrected_gstin" not in ann_df.columns:
        df["corrected_gstin"] = ""
        df["gstin_valid"] = False
        df["is_b2b"] = False
        df["is_interstate"] = False
        df["correction_method"] = ""
        df["name_match_score"] = 0
    else:
        df["corrected_gstin"] = ann_df["corrected_gstin"].values
        df["gstin_valid"] = ann_df["gstin_valid"].values
        df["is_b2b"] = ann_df["is_b2b"].values
        df["is_interstate"] = ann_df["is_interstate"].values
        df["correction_method"] = ann_df["correction_method"].values
        df["name_match_score"] = ann_df["name_match_score"].values

    return df, exc_df, master
