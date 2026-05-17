"""
Period normalizer. Accepts MMYYYY, MM/YYYY, MM-YYYY, 'April 2026', 'Apr-26', etc.
Returns canonical MMYYYY string used by GSTR-1.
"""
import re
from datetime import datetime

MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def normalize_period(value: str) -> str:
    """Return MMYYYY or raise ValueError."""
    if not value:
        raise ValueError("Period is required")
    s = str(value).strip()

    # MMYYYY (6 digits)
    if re.fullmatch(r"\d{6}", s):
        mm, yyyy = int(s[:2]), int(s[2:])
        if 1 <= mm <= 12 and 2000 <= yyyy <= 2099:
            return f"{mm:02d}{yyyy}"

    # MM/YYYY or MM-YYYY
    m = re.fullmatch(r"(\d{1,2})[\/\-](\d{4})", s)
    if m:
        mm, yyyy = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12 and 2000 <= yyyy <= 2099:
            return f"{mm:02d}{yyyy}"

    # MM-YY (e.g. 04-26)
    m = re.fullmatch(r"(\d{1,2})[\/\-](\d{2})", s)
    if m:
        mm, yy = int(m.group(1)), int(m.group(2))
        yyyy = 2000 + yy
        if 1 <= mm <= 12:
            return f"{mm:02d}{yyyy}"

    # "April 2026" or "Apr-2026" or "Apr 26"
    m = re.fullmatch(r"([A-Za-z]+)[\s\-](\d{2,4})", s)
    if m:
        name, year = m.group(1).lower(), int(m.group(2))
        if name in MONTH_NAMES:
            mm = MONTH_NAMES[name]
            yyyy = year if year > 99 else 2000 + year
            return f"{mm:02d}{yyyy}"

    raise ValueError(
        f"Could not parse period '{value}'. Use MMYYYY (e.g. 042026), "
        f"MM/YYYY, or 'April 2026'."
    )


def period_to_label(mmyyyy: str) -> str:
    """042026 -> 'April 2026'."""
    if len(mmyyyy) != 6 or not mmyyyy.isdigit():
        return mmyyyy
    mm, yyyy = int(mmyyyy[:2]), int(mmyyyy[2:])
    try:
        return datetime(yyyy, mm, 1).strftime("%B %Y")
    except ValueError:
        return mmyyyy


def period_bounds(mmyyyy: str) -> tuple:
    """Return (start_date, end_date) for the period."""
    from calendar import monthrange
    mm, yyyy = int(mmyyyy[:2]), int(mmyyyy[2:])
    start = datetime(yyyy, mm, 1).date()
    end = datetime(yyyy, mm, monthrange(yyyy, mm)[1]).date()
    return start, end
