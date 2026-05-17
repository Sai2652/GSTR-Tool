"""
Pre-flight validations.

Catches data issues BEFORE generating JSON, so the user can fix them upfront
instead of getting a portal rejection. Each check returns a list of issues
with severity, row reference, and a suggested action.

Severities:
  - 'error'   : will likely cause portal rejection or wrong tax liability
  - 'warning' : possibly wrong but tolerable; user should review
  - 'info'    : informational only
"""
import pandas as pd
from collections import defaultdict


# Tolerance for floating-point tax math (rupees)
TAX_MATH_TOLERANCE = 1.0


def _make_issue(severity, row_idx, invoice_no, customer, message, suggested_action=""):
    return {
        "severity": severity,
        "row": int(row_idx) if row_idx is not None else None,
        "invoice_no": str(invoice_no or ""),
        "customer": str(customer or ""),
        "message": message,
        "suggested_action": suggested_action,
    }


def check_tax_math(df: pd.DataFrame) -> list:
    """
    Check if IGST/CGST/SGST amounts match (taxable_value × rate / 100).
    Tolerates rupee-level rounding.
    """
    issues = []
    if df is None or df.empty:
        return issues

    for idx, row in df.iterrows():
        try:
            txval = float(row.get("taxable_value", 0) or 0)
            igst_rate = float(row.get("igst_rate", 0) or 0)
            igst_amt = float(row.get("igst_amount", 0) or 0)
            cgst_rate = float(row.get("cgst_rate", 0) or 0)
            cgst_amt = float(row.get("cgst_amount", 0) or 0)
            sgst_rate = float(row.get("sgst_rate", 0) or 0)
            sgst_amt = float(row.get("sgst_amount", 0) or 0)
        except (ValueError, TypeError):
            continue

        if abs(txval) < 0.01:
            continue  # Zero-value row, skip

        # Expected vs actual
        expected_igst = abs(txval) * igst_rate / 100.0
        expected_cgst = abs(txval) * cgst_rate / 100.0
        expected_sgst = abs(txval) * sgst_rate / 100.0

        if igst_rate > 0 and abs(abs(igst_amt) - expected_igst) > TAX_MATH_TOLERANCE:
            issues.append(_make_issue(
                "error", idx,
                row.get("invoice_no"), row.get("customer_name"),
                f"IGST amount mismatch: {abs(igst_amt):.2f} stated vs {expected_igst:.2f} expected ({igst_rate}% on {abs(txval):.2f})",
                "Recalculate IGST in source data"
            ))

        if cgst_rate > 0 and abs(abs(cgst_amt) - expected_cgst) > TAX_MATH_TOLERANCE:
            issues.append(_make_issue(
                "error", idx,
                row.get("invoice_no"), row.get("customer_name"),
                f"CGST amount mismatch: {abs(cgst_amt):.2f} stated vs {expected_cgst:.2f} expected ({cgst_rate}% on {abs(txval):.2f})",
                "Recalculate CGST in source data"
            ))

        if sgst_rate > 0 and abs(abs(sgst_amt) - expected_sgst) > TAX_MATH_TOLERANCE:
            issues.append(_make_issue(
                "error", idx,
                row.get("invoice_no"), row.get("customer_name"),
                f"SGST amount mismatch: {abs(sgst_amt):.2f} stated vs {expected_sgst:.2f} expected ({sgst_rate}% on {abs(txval):.2f})",
                "Recalculate SGST in source data"
            ))

        # Sanity: intra-state should have CGST+SGST and no IGST; inter has only IGST
        if igst_rate > 0 and (cgst_rate > 0 or sgst_rate > 0):
            issues.append(_make_issue(
                "warning", idx,
                row.get("invoice_no"), row.get("customer_name"),
                f"Mixed tax types: IGST {igst_rate}% + CGST {cgst_rate}% + SGST {sgst_rate}% on same row",
                "Verify whether transaction is inter-state or intra-state"
            ))

    return issues


def check_duplicate_invoices(df: pd.DataFrame) -> list:
    """
    Detect invoice numbers that appear with more than one (date, customer) pair.
    True duplicates of the same (gstin, invoice_no, date) are NOT a problem —
    those are multi-line invoices. We flag when the SAME invoice number is
    used for DIFFERENT customers or DIFFERENT dates.
    """
    issues = []
    if df is None or df.empty or "invoice_no" not in df.columns:
        return issues

    grouped = defaultdict(set)
    for idx, row in df.iterrows():
        inv_no = str(row.get("invoice_no", "") or "").strip()
        if not inv_no:
            continue
        gstin = str(row.get("corrected_gstin") or row.get("gstin") or "").strip()
        date = pd.to_datetime(row.get("invoice_date"), errors="coerce")
        date_str = date.strftime("%Y-%m-%d") if pd.notna(date) else "UNKNOWN"
        # Track all unique (gstin, date) combos per invoice number
        grouped[inv_no].add((gstin, date_str))

    for inv_no, combos in grouped.items():
        if len(combos) > 1:
            # Find first occurrence row for the issue
            for idx, row in df.iterrows():
                if str(row.get("invoice_no", "") or "").strip() == inv_no:
                    issues.append(_make_issue(
                        "warning", idx, inv_no, row.get("customer_name"),
                        f"Invoice number '{inv_no}' is used across {len(combos)} different (customer, date) combinations",
                        "Verify invoice numbering — same number should not be reused"
                    ))
                    break

    return issues


def check_missing_critical_fields(df: pd.DataFrame) -> list:
    """Flag rows missing critical fields (invoice_no, taxable_value, hsn)."""
    issues = []
    if df is None or df.empty:
        return issues

    for idx, row in df.iterrows():
        inv_no = str(row.get("invoice_no", "") or "").strip()
        if not inv_no:
            issues.append(_make_issue(
                "error", idx, "", row.get("customer_name"),
                "Missing invoice number",
                "Set an invoice number in source data"
            ))
            continue

        try:
            txval = float(row.get("taxable_value", 0) or 0)
        except (ValueError, TypeError):
            txval = 0
        if abs(txval) < 0.01:
            issues.append(_make_issue(
                "warning", idx, inv_no, row.get("customer_name"),
                "Zero taxable value",
                "Verify whether this row should be included"
            ))

        hsn = str(row.get("hsn", "") or "").strip()
        if not hsn:
            issues.append(_make_issue(
                "warning", idx, inv_no, row.get("customer_name"),
                "Missing HSN/SAC code",
                "Add HSN/SAC in source data"
            ))

    return issues


def check_state_code_consistency(df: pd.DataFrame, firm_state_code: str = "29") -> list:
    """
    For B2B rows: GSTIN's first 2 digits should match a state code; flag
    where the customer's state_code column contradicts the GSTIN's state.
    """
    issues = []
    if df is None or df.empty:
        return issues

    for idx, row in df.iterrows():
        gstin = str(row.get("corrected_gstin") or row.get("gstin") or "").strip().upper()
        if len(gstin) != 15:
            continue
        gstin_state = gstin[:2]
        sheet_state = str(row.get("state_code", "") or "").split("-")[0].strip()
        if sheet_state and sheet_state.isdigit() and sheet_state.zfill(2) != gstin_state:
            issues.append(_make_issue(
                "warning", idx, row.get("invoice_no"), row.get("customer_name"),
                f"Sheet state code '{sheet_state}' but GSTIN state code is '{gstin_state}'",
                "Verify customer's actual state — GSTIN takes precedence"
            ))

    return issues


def run_all_preflight_checks(df: pd.DataFrame, firm_state_code: str = "29") -> dict:
    """
    Run all pre-flight checks and return a summary.
    Returns: {
      'errors':   [...],
      'warnings': [...],
      'info':     [...],
      'totals':   {'errors': N, 'warnings': N, 'info': N}
    }
    """
    all_issues = []
    all_issues.extend(check_missing_critical_fields(df))
    all_issues.extend(check_tax_math(df))
    all_issues.extend(check_duplicate_invoices(df))
    all_issues.extend(check_state_code_consistency(df, firm_state_code))

    errors = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]
    info = [i for i in all_issues if i["severity"] == "info"]

    return {
        "errors": errors,
        "warnings": warnings,
        "info": info,
        "totals": {
            "errors": len(errors),
            "warnings": len(warnings),
            "info": len(info),
        },
    }
