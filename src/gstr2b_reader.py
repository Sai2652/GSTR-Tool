"""
GSTR-2B reader — parses the Excel file the GST portal exports.

The summary sheets (ITC Available / Not Available / Reversal / Rejected) all
share the same column layout:
    A: S.no.                B: Heading              C: GSTR-3B table
    D: Integrated Tax       E: Central Tax          F: State/UT Tax
    G: Cess                 H: Advisory

Heading rows with a Roman numeral (I, II, III, IV) in column A are the
*category roll-ups* — these are the numbers we use for GSTR-3B Table 4 entries.

Public API:
    parse_gstr2b(path)  ->  dict (see _empty_result)
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import openpyxl


# --- Helpers ---------------------------------------------------------------

def _safe_float(v: Any) -> float:
    """Cell value -> float, never raises. Blank cells become 0."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _empty_tax() -> Dict[str, float]:
    return {"igst": 0.0, "cgst": 0.0, "sgst": 0.0, "cess": 0.0}


def _row_to_tax(ws, row: int) -> Dict[str, float]:
    """Extract IGST/CGST/SGST/Cess values from a single row (cols D-G)."""
    return {
        "igst": _safe_float(ws.cell(row, 4).value),
        "cgst": _safe_float(ws.cell(row, 5).value),
        "sgst": _safe_float(ws.cell(row, 6).value),
        "cess": _safe_float(ws.cell(row, 7).value),
    }


def _add_tax(a: Dict[str, float], b: Dict[str, float]) -> Dict[str, float]:
    return {k: round(a[k] + b[k], 2) for k in a}


def _sub_tax(a: Dict[str, float], b: Dict[str, float]) -> Dict[str, float]:
    return {k: round(a[k] - b[k], 2) for k in a}


# --- Category detection ----------------------------------------------------

# Map S.No (column A) values to internal category keys. Roman numerals are
# what the portal uses. We only care about Part A rows for "ITC to claim";
# Part B rows are credit notes that net off.
CATEGORY_LABELS = {
    "I":   "all_other_itc",          # Table 4(A)(5) - regular B2B
    "II":  "isd",                    # Table 4(A)(4) - ISD
    "III": "reverse_charge",         # Table 3.1(d) + 4(A)(3)
    "IV":  "imports",                # Table 4(A)(1) - IMPG + IMPGSEZ
}


def _parse_summary_sheet(ws) -> Dict[str, Dict[str, float]]:
    """
    Read a summary-style sheet and return a dict keyed by category.

    Returns: {
        "all_other_itc":  {"igst": .., "cgst": .., "sgst": .., "cess": ..},
        "isd":            {...},
        "reverse_charge": {...},
        "imports":        {...},
        "credit_notes":   {...},   # Part B total (negative effect on ITC)
        "total":          {...},   # whole-sheet total
    }
    """
    result: Dict[str, Dict[str, float]] = {
        k: _empty_tax() for k in CATEGORY_LABELS.values()
    }
    result["credit_notes"] = _empty_tax()
    result["total"] = _empty_tax()

    in_part_b = False

    for row in range(7, ws.max_row + 1):
        a = ws.cell(row, 1).value
        if a is None:
            continue
        a_str = str(a).strip()

        # Detect Part B (credit notes section)
        if a_str.startswith("Part B"):
            in_part_b = True
            continue
        if a_str.startswith("Part A"):
            in_part_b = False
            continue

        if a_str in CATEGORY_LABELS:
            tax = _row_to_tax(ws, row)
            if in_part_b:
                # All Part B roll-ups go into "credit_notes"
                result["credit_notes"] = _add_tax(result["credit_notes"], tax)
            else:
                key = CATEGORY_LABELS[a_str]
                result[key] = tax

    # Compute net total (Part A categories minus credit notes)
    parta_total = _empty_tax()
    for k in CATEGORY_LABELS.values():
        parta_total = _add_tax(parta_total, result[k])
    result["total"] = _sub_tax(parta_total, result["credit_notes"])

    return result


# --- Public ---------------------------------------------------------------

def _empty_result() -> Dict[str, Any]:
    return {
        "filename": "",
        "gstin": "",
        "period": "",
        "generated_on": "",
        "itc_available": {
            "all_other_itc": _empty_tax(),
            "isd": _empty_tax(),
            "reverse_charge": _empty_tax(),
            "imports": _empty_tax(),
            "credit_notes": _empty_tax(),
            "total": _empty_tax(),
        },
        "itc_not_available": {
            "all_other_itc": _empty_tax(),
            "isd": _empty_tax(),
            "reverse_charge": _empty_tax(),
            "imports": _empty_tax(),
            "credit_notes": _empty_tax(),
            "total": _empty_tax(),
        },
        "itc_reversal": {
            "all_other_itc": _empty_tax(),
            "isd": _empty_tax(),
            "reverse_charge": _empty_tax(),
            "imports": _empty_tax(),
            "credit_notes": _empty_tax(),
            "total": _empty_tax(),
        },
        "itc_rejected": {
            "all_other_itc": _empty_tax(),
            "isd": _empty_tax(),
            "reverse_charge": _empty_tax(),
            "imports": _empty_tax(),
            "credit_notes": _empty_tax(),
            "total": _empty_tax(),
        },
        "warnings": [],
    }


def _extract_metadata(ws_readme) -> Dict[str, str]:
    """Pull GSTIN / period / generated date from the Read me sheet."""
    meta = {"gstin": "", "period": "", "generated_on": ""}
    for r in range(1, min(50, ws_readme.max_row + 1)):
        for c in range(1, min(8, ws_readme.max_column + 1)):
            v = ws_readme.cell(r, c).value
            if not v:
                continue
            s = str(v).strip()
            if "GSTIN" in s and ":" in s:
                meta["gstin"] = s.split(":", 1)[1].strip()
            elif "Return period" in s.lower() or "tax period" in s.lower():
                # Next cell or part after colon
                if ":" in s:
                    meta["period"] = s.split(":", 1)[1].strip()
            elif "generated" in s.lower() and ":" in s.lower():
                if ":" in s:
                    meta["generated_on"] = s.split(":", 1)[1].strip()
    return meta


def parse_gstr2b(path: str | Path) -> Dict[str, Any]:
    """
    Parse a GSTR-2B Excel file from the GST portal.

    Returns a dict with parsed totals — see _empty_result() for shape.
    """
    path = Path(path)
    result = _empty_result()
    result["filename"] = path.name

    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    except Exception as e:
        result["warnings"].append(f"Could not open Excel file: {e}")
        return result

    # Optional metadata
    if "Read me" in wb.sheetnames:
        try:
            meta = _extract_metadata(wb["Read me"])
            result.update(meta)
        except Exception:
            pass

    # Parse the four summary sheets
    sheet_map = {
        "ITC Available":     "itc_available",
        "ITC not available": "itc_not_available",
        "ITC Reversal":      "itc_reversal",
        "ITC Rejected":      "itc_rejected",
    }

    for sheet_name, result_key in sheet_map.items():
        if sheet_name not in wb.sheetnames:
            result["warnings"].append(f"Sheet '{sheet_name}' missing from file")
            continue
        try:
            result[result_key] = _parse_summary_sheet(wb[sheet_name])
        except Exception as e:
            result["warnings"].append(f"Could not parse '{sheet_name}': {e}")

    # Section 16(4): ITC on any prior-FY invoice cannot be claimed after
    # 30 November of the following FY (or the date of filing of the annual
    # return, whichever is earlier). We scan detail sheets for invoice dates
    # belonging to a prior FY relative to the 2B period and flag them.
    try:
        prior_fy_count, examples = _scan_prior_fy_invoices(wb, result.get("period", ""))
        if prior_fy_count:
            result["warnings"].append(
                f"Section 16(4): {prior_fy_count} invoice(s) found from a prior "
                f"financial year. ITC cannot be claimed after 30-Nov of the FY "
                f"following the invoice's FY. Examples: {', '.join(examples[:3])}"
            )
    except Exception as e:
        result["warnings"].append(f"Could not run Section 16(4) check: {e}")

    return result


# --- Section 16(4) helpers ------------------------------------------------

def _fy_of(d: date) -> int:
    """Indian financial year start year for a date (Apr-Mar)."""
    return d.year if d.month >= 4 else d.year - 1


def _period_fy(period: str) -> Optional[int]:
    """GSTR-2B period is 'MMYYYY' (e.g. '052026'). Return FY start year."""
    s = (period or "").strip()
    if len(s) == 6 and s.isdigit():
        mm, yyyy = int(s[:2]), int(s[2:])
        return yyyy if mm >= 4 else yyyy - 1
    return None


def _scan_prior_fy_invoices(wb, period: str) -> tuple[int, list[str]]:
    """
    Scan B2B / B2BA / CDNR / IMPG detail sheets for invoice dates that fall in
    an FY earlier than the 2B period's FY. Returns (count, sample_strings).
    """
    period_fy = _period_fy(period)
    if period_fy is None:
        return 0, []

    DETAIL_SHEETS = ("B2B", "B2BA", "CDNR", "CDNRA", "IMPG", "IMPGSEZ")
    count = 0
    samples: list[str] = []
    for name in wb.sheetnames:
        if name.strip().upper() not in DETAIL_SHEETS:
            continue
        ws = wb[name]
        # Find the date column by header text (typically "Invoice date" or "Note date")
        header_row = _find_header_row(ws)
        if header_row is None:
            continue
        date_col = _find_col(ws, header_row, ("invoice date", "note date", "doc date"))
        inum_col = _find_col(ws, header_row, ("invoice number", "note number", "doc number"))
        if date_col is None:
            continue
        for r in range(header_row + 1, ws.max_row + 1):
            v = ws.cell(r, date_col).value
            d = _to_date(v)
            if d is None:
                continue
            if _fy_of(d) < period_fy:
                count += 1
                if len(samples) < 5 and inum_col:
                    inum = ws.cell(r, inum_col).value
                    samples.append(f"{inum} dt {d.strftime('%d-%m-%Y')}")
    return count, samples


def _find_header_row(ws) -> Optional[int]:
    """Detail sheets have a multi-row header; find the row containing 'GSTIN'."""
    for r in range(1, min(15, ws.max_row + 1)):
        for c in range(1, min(20, ws.max_column + 1)):
            v = ws.cell(r, c).value
            if v and "GSTIN" in str(v).upper():
                return r
    return None


def _find_col(ws, header_row: int, keywords: tuple[str, ...]) -> Optional[int]:
    for c in range(1, ws.max_column + 1):
        v = ws.cell(header_row, c).value
        if not v:
            continue
        s = str(v).lower()
        if any(k in s for k in keywords):
            return c
    return None


def _to_date(v) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
