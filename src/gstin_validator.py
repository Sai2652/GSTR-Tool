"""
GSTIN Validation, Correction & Verification Module
====================================================
GSTIN structure (15 chars):
  Positions 1-2:  State code (numeric)
  Positions 3-12: PAN (5 letters + 4 digits + 1 letter)
  Position 13:    Entity number (alphanumeric)
  Position 14:    'Z' (default)
  Position 15:    Checksum digit (alphanumeric, mod-36)
"""
import re
from itertools import product
from rapidfuzz import fuzz, process

# Mod-36 charset used by GSTN checksum algorithm
CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CHAR_TO_INT = {c: i for i, c in enumerate(CHARSET)}
INT_TO_CHAR = {i: c for i, c in enumerate(CHARSET)}

# State code -> State name (for IGST/CGST split)
STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu", "26": "Dadra and Nagar Haveli",
    "27": "Maharashtra", "28": "Andhra Pradesh (Old)", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
}

# Common OCR/typing confusions
SUBSTITUTIONS = {
    "O": "0", "0": "O", "I": "1", "1": "I",
    "L": "1", "S": "5", "B": "8", "Z": "2",
}

# GSTIN regex (structural)
GSTIN_REGEX = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9A-Z]{1}Z[0-9A-Z]{1}$"
)


def calculate_checksum(gstin_first_14: str) -> str:
    """Calculate the 15th GSTIN checksum digit using GSTN's mod-36 algorithm."""
    if len(gstin_first_14) != 14:
        return ""
    factor = 2
    total = 0
    for ch in reversed(gstin_first_14.upper()):
        if ch not in CHAR_TO_INT:
            return ""
        digit = CHAR_TO_INT[ch] * factor
        digit = (digit // 36) + (digit % 36)
        total += digit
        factor = 1 if factor == 2 else 2
    remainder = total % 36
    check_code = (36 - remainder) % 36
    return INT_TO_CHAR[check_code]


def is_structurally_valid(gstin: str) -> bool:
    """Check if GSTIN matches the structural regex."""
    return bool(GSTIN_REGEX.match(gstin or ""))


def is_checksum_valid(gstin: str) -> bool:
    """Check if the 15th digit is a valid checksum."""
    if not is_structurally_valid(gstin):
        return False
    return calculate_checksum(gstin[:14]) == gstin[14]


def validate_gstin(gstin: str) -> dict:
    """Full validation. Returns dict with status."""
    if not gstin or str(gstin).lower() == "nan":
        return {"valid": False, "reason": "EMPTY", "gstin": ""}
    g = str(gstin).strip().upper().replace(" ", "")
    if len(g) != 15:
        return {"valid": False, "reason": f"LENGTH_{len(g)}", "gstin": g}
    if not is_structurally_valid(g):
        return {"valid": False, "reason": "STRUCTURE_INVALID", "gstin": g}
    if not is_checksum_valid(g):
        return {"valid": False, "reason": "CHECKSUM_INVALID", "gstin": g}
    return {"valid": True, "reason": "OK", "gstin": g}


def _candidate_corrections(gstin: str):
    """Generate candidate corrections by substituting commonly-confused chars
    at structurally suspicious positions."""
    g = list(gstin)
    # Each position has an expected type; flag positions where char doesn't fit
    expected = ["N", "N", "L", "L", "L", "L", "L", "N", "N", "N", "N", "L", "AN", "Z", "AN"]
    suspicious = []
    for i, (ch, exp) in enumerate(zip(g, expected)):
        if exp == "N" and not ch.isdigit():
            suspicious.append(i)
        elif exp == "L" and not ch.isalpha():
            suspicious.append(i)
        elif exp == "Z" and ch != "Z":
            suspicious.append(i)
    # Limit combinatorial blow-up
    if len(suspicious) > 4:
        suspicious = suspicious[:4]
    if not suspicious:
        # Try last digit + known substitution positions
        suspicious = [12, 14]
    options = []
    for pos in suspicious:
        opts = [g[pos]]
        if g[pos] in SUBSTITUTIONS:
            opts.append(SUBSTITUTIONS[g[pos]])
        options.append((pos, opts))
    for combo in product(*[opts for _, opts in options]):
        candidate = g.copy()
        for (pos, _), val in zip(options, combo):
            candidate[pos] = val
        yield "".join(candidate)


def auto_correct_gstin(gstin: str, master_list: dict = None) -> dict:
    """
    Try to correct an invalid GSTIN.
    Strategy:
      1. Strip/upper/remove spaces
      2. If still invalid, try character substitutions (O<->0, I<->1, L<->1)
      3. If master_list given, fuzzy-match against known correct GSTINs
    Returns dict with: original, corrected, method, confidence
    """
    result = {
        "original": gstin,
        "corrected": None,
        "method": None,
        "confidence": 0,
    }
    if not gstin or str(gstin).lower() == "nan":
        return result

    g = str(gstin).strip().upper().replace(" ", "")
    result["cleaned"] = g

    # Step 1: Already valid after cleanup?
    if is_checksum_valid(g):
        result["corrected"] = g
        result["method"] = "CLEANUP"
        result["confidence"] = 100
        return result

    # Step 2: Try substitution-based corrections
    if len(g) == 15:
        for cand in _candidate_corrections(g):
            if is_checksum_valid(cand):
                result["corrected"] = cand
                result["method"] = "SUBSTITUTION"
                result["confidence"] = 95
                return result

    # Step 3: Master-list fuzzy match
    if master_list:
        best = process.extractOne(g, list(master_list.keys()), scorer=fuzz.ratio)
        if best and best[1] >= 87:
            result["corrected"] = best[0]
            result["method"] = "MASTER_LIST_FUZZY"
            result["confidence"] = best[1]
            return result

    return result


def name_match_score(name1: str, name2: str) -> int:
    """Fuzzy-match two customer names. Returns 0-100."""
    if not name1 or not name2:
        return 0
    n1 = re.sub(r"[^\w\s]", "", str(name1).upper()).strip()
    n2 = re.sub(r"[^\w\s]", "", str(name2).upper()).strip()
    # Token sort handles word-order differences and abbreviations
    return int(fuzz.token_sort_ratio(n1, n2))


def get_state_code(gstin: str) -> str:
    """Extract state code from GSTIN."""
    if gstin and len(gstin) >= 2:
        return gstin[:2]
    return ""


def get_state_name(state_code: str) -> str:
    """Get state name from 2-digit code."""
    return STATE_CODES.get(str(state_code).zfill(2), "Unknown")


# ======================================================================
# GST Portal API hook (placeholder)
# ======================================================================
def verify_via_gst_api(gstin: str, api_key: str = None) -> dict:
    """
    Hook for live GSTN verification via GSP/Public API.
    Production usage requires GSP credentials (paid) or the public-search
    API which has rate limits. Left as a stub for the user to wire up.
    """
    if not api_key:
        return {"verified": False, "reason": "NO_API_KEY", "data": None}
    # When implemented:
    # response = requests.post(
    #     "https://services.gst.gov.in/services/api/search/taxpayerDetails",
    #     headers={"Authorization": f"Bearer {api_key}"},
    #     json={"gstin": gstin},
    # )
    return {"verified": False, "reason": "NOT_IMPLEMENTED", "data": None}
