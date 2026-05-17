"""
GSTR-1 JSON Builder — matches GSTN Offline Tool output exactly.

Reverse-engineered from a working JSON produced by the official GST Returns
Offline Tool (V2.2). Key structural points (do NOT change without verifying
against another working JSON from the offline tool):

  - Top-level keys IN ORDER: gstin, fp, version, hash, b2b, b2cl, b2cs, doc_issue, hsn
  - "version": "GST3.2.4"  (literal string, required)
  - "hash":    "hash"      (yes, literal string "hash" — the offline tool uses this placeholder)
  - HSN is nested: { "hsn": { "hsn_b2b": [...], "hsn_b2c": [...] } }
    NOT a top-level "hsn_b2b" key as the V2.2 Excel template might suggest.
  - Inside hsn_b2b/hsn_b2c entries, key ORDER is:
      num, hsn_sc, desc, uqc, qty, rt, txval, iamt, samt, camt, csamt
    (note: samt before camt — this matches the offline tool's output)
  - Item det keys: txval, rt, then ONLY the relevant tax fields:
      * Intra-state (CGST+SGST): omit iamt entirely; include camt, samt
      * Inter-state (IGST):       include iamt; omit camt, samt
      * Always include csamt
  - "rt" values are integers when whole (18, not 18.0); same for amounts that
    happen to be whole numbers. We use `_num()` to produce ints when whole.
  - HSN qty: services have qty=0 (and uqc="NA"). For goods, qty is the actual.
  - Item "num" is 1801 in the offline tool's output for ALL items — this looks
    like an internal placeholder, not a sequence. We mirror that constant.

Known things the offline tool does that we replicate:
  - Outputs minified JSON (no indentation) — but indented JSON is also accepted.
    We keep indented for human inspection; portal handles both.
  - Includes pos always for B2B; format "29".
  - Date format DD-MM-YYYY.
"""
from collections import defaultdict
import pandas as pd


GSTR1_VERSION = "GST3.2.4"
HASH_VALUE = "hash"

# Item num used by offline tool — appears to be a constant placeholder
ITEM_NUM_PLACEHOLDER = 1801


def _num(x):
    """Return int if value is whole (e.g. 18 not 18.0); else float rounded to 2."""
    if x is None:
        return 0
    f = round(float(x), 2)
    if f == int(f):
        return int(f)
    return f


def _format_date(d) -> str:
    """GSTR-1 date format: DD-MM-YYYY."""
    if d is None:
        return ""
    if isinstance(d, str):
        try:
            d = pd.to_datetime(d).date()
        except Exception:
            return d
    return d.strftime("%d-%m-%Y")


def _pos(code) -> str:
    s = str(code or "").strip()
    if s.isdigit():
        return s.zfill(2)
    return s[:2] if len(s) >= 2 else s


# ----- HSN descriptions for common SAC codes (services) ------------------
# These match what the offline tool autofills from its master.
HSN_DESCRIPTIONS = {
    "997314": "Leasing or rental services concerning office machinery and equipment (except computers) without operator",
    "9989": "Other manufacturing services; publishing, printing and reproduction services; materials recovery services",
    "9954": "Construction services",
    "9961": "Services in wholesale trade",
    "9962": "Services in retail trade",
    "9963": "Accommodation, food and beverage services",
    "9964": "Passenger transport services",
    "9965": "Goods transport services",
    "9966": "Rental services of transport vehicles",
    "9967": "Supporting services in transport",
    "9968": "Postal and courier services",
    "9971": "Financial and related services",
    "9972": "Real estate services",
    "9973": "Leasing or rental services with or without operator",
    "9981": "Research and development services",
    "9982": "Legal and accounting services",
    "9983": "Other professional, technical and business services",
    "9984": "Telecommunications, broadcasting and information supply services",
    "9985": "Support services",
    "9986": "Support services to agriculture, hunting, forestry, fishing, mining and utilities",
    "9987": "Maintenance, repair and installation (except construction) services",
    "9988": "Manufacturing services on physical inputs (goods) owned by others",
    "9991": "Public administration and other services provided to the community as a whole; compulsory social security services",
    "9992": "Education services",
    "9993": "Human health and social care services",
    "9994": "Sewage and waste collection, treatment and disposal and other environmental protection services",
    "9995": "Services of membership organizations",
    "9996": "Recreational, cultural and sporting services",
    "9997": "Other services",
    "9998": "Domestic services",
    "9999": "Services provided by extraterritorial organizations and bodies",
}


def _hsn_desc(hsn_sc: str) -> str:
    """Return description for known HSN/SAC codes."""
    return HSN_DESCRIPTIONS.get(str(hsn_sc).strip(), "")


def _is_service_hsn(hsn_sc: str) -> bool:
    """Service codes (SAC) start with 99."""
    s = str(hsn_sc).strip()
    return s.startswith("99")


# ======================================================================
# Item detail builder — chooses tax fields based on inter/intra state
# ======================================================================
def _itm_det(item: dict, is_interstate: bool) -> dict:
    """Build itm_det object matching offline-tool format."""
    det = {
        "txval": _num(item["taxable_value"]),
        "rt": _num(item["tax_rate"]),
    }
    if is_interstate:
        det["iamt"] = _num(item["igst_amount"])
    else:
        # Order matters in offline tool output: samt before camt? Actually no,
        # the real JSON shows camt before samt for the inv items. Let's match.
        det["camt"] = _num(item["cgst_amount"])
        det["samt"] = _num(item["sgst_amount"])
    det["csamt"] = _num(item["cess_amount"])
    return det


# ======================================================================
# B2B
# ======================================================================
def _collapse_items_by_rate(items: list) -> list:
    """
    Within an invoice, the offline tool collapses items at the SAME rate
    into a single entry (HSN-level data lives separately in the HSN section).
    """
    by_rate = {}
    for item in items:
        rate = round(float(item["tax_rate"] or 0), 2)
        if rate not in by_rate:
            by_rate[rate] = {
                "tax_rate": rate,
                "taxable_value": 0.0,
                "igst_amount": 0.0,
                "cgst_amount": 0.0,
                "sgst_amount": 0.0,
                "cess_amount": 0.0,
            }
        a = by_rate[rate]
        a["taxable_value"] += float(item["taxable_value"] or 0)
        a["igst_amount"] += float(item["igst_amount"] or 0)
        a["cgst_amount"] += float(item["cgst_amount"] or 0)
        a["sgst_amount"] += float(item["sgst_amount"] or 0)
        a["cess_amount"] += float(item["cess_amount"] or 0)
    return list(by_rate.values())


def build_b2b_section(invoices: list) -> list:
    grouped = defaultdict(list)
    for inv in invoices:
        grouped[inv["gstin"]].append(inv)

    b2b = []
    for ctin, inv_list in grouped.items():
        invs = []
        for inv in inv_list:
            collapsed = _collapse_items_by_rate(inv["items"])
            itms = []
            for item in collapsed:
                itms.append({
                    "num": ITEM_NUM_PLACEHOLDER,
                    "itm_det": _itm_det(item, inv["is_interstate"]),
                })
            invs.append({
                "inum": str(inv["invoice_no"]).strip(),
                "idt": _format_date(inv["invoice_date"]),
                "val": _num(inv["invoice_value"]),
                "pos": _pos(inv["place_of_supply"]),
                "rchrg": "N",
                "inv_typ": "R",
                "itms": itms,
            })
        b2b.append({"ctin": ctin, "inv": invs})
    return b2b


# ======================================================================
# B2CL
# ======================================================================
def build_b2cl_section(invoices: list) -> list:
    grouped = defaultdict(list)
    for inv in invoices:
        grouped[_pos(inv["place_of_supply"])].append(inv)

    b2cl = []
    for pos, inv_list in grouped.items():
        invs = []
        for inv in inv_list:
            collapsed = _collapse_items_by_rate(inv["items"])
            itms = []
            for item in collapsed:
                itms.append({
                    "num": ITEM_NUM_PLACEHOLDER,
                    "itm_det": {
                        "txval": _num(item["taxable_value"]),
                        "rt": _num(item["tax_rate"]),
                        "iamt": _num(item["igst_amount"]),
                        "csamt": _num(item["cess_amount"]),
                    },
                })
            invs.append({
                "inum": str(inv["invoice_no"]).strip(),
                "idt": _format_date(inv["invoice_date"]),
                "val": _num(inv["invoice_value"]),
                "itms": itms,
            })
        b2cl.append({"pos": pos, "inv": invs})
    return b2cl


# ======================================================================
# B2CS — rate-wise consolidated
# ======================================================================
def build_b2cs_section(invoices: list) -> list:
    consolidated = defaultdict(lambda: {
        "txval": 0.0, "iamt": 0.0, "camt": 0.0, "samt": 0.0, "csamt": 0.0,
    })
    for inv in invoices:
        for item in inv["items"]:
            sply_ty = "INTER" if inv["is_interstate"] else "INTRA"
            key = (_pos(inv["place_of_supply"]), round(float(item["tax_rate"] or 0), 2), sply_ty)
            c = consolidated[key]
            c["txval"] += float(item["taxable_value"] or 0)
            c["iamt"] += float(item["igst_amount"] or 0)
            c["camt"] += float(item["cgst_amount"] or 0)
            c["samt"] += float(item["sgst_amount"] or 0)
            c["csamt"] += float(item["cess_amount"] or 0)

    b2cs = []
    for (pos, rate, sply_ty), v in consolidated.items():
        entry = {
            "sply_ty": sply_ty,
            "rt": _num(rate),
            "typ": "OE",
            "pos": pos,
            "txval": _num(v["txval"]),
        }
        if sply_ty == "INTER":
            entry["iamt"] = _num(v["iamt"])
        else:
            entry["camt"] = _num(v["camt"])
            entry["samt"] = _num(v["samt"])
        entry["csamt"] = _num(v["csamt"])
        b2cs.append(entry)
    return b2cs


# ======================================================================
# CDNR — Credit/Debit Notes to Registered customers
# ======================================================================
def build_cdnr_section(notes: list) -> list:
    """
    CDNR: grouped by counter-party GSTIN (ctin).
    Each entry has 'nt' (notes) array.

    Schema per offline tool:
      [{
        "ctin": "...",
        "nt": [{
          "ntty": "C" | "D",
          "nt_num": "...",
          "nt_dt": "DD-MM-YYYY",
          "rsn": "...",                 (reason - usually omitted)
          "p_gst": "N",                 (pre-GST: N for current regime)
          "inum": "...",                (original invoice no, optional)
          "idt": "DD-MM-YYYY",          (original invoice date, optional)
          "val": ...,
          "pos": "29",
          "rchrg": "N",
          "inv_typ": "R",
          "itms": [...]                 (same item structure as B2B)
        }]
      }]
    """
    grouped = defaultdict(list)
    for note in notes:
        grouped[note["gstin"]].append(note)

    cdnr = []
    for ctin, notes_list in grouped.items():
        nt_arr = []
        for note in notes_list:
            collapsed = _collapse_items_by_rate(note["items"])
            itms = []
            for item in collapsed:
                itms.append({
                    "num": ITEM_NUM_PLACEHOLDER,
                    "itm_det": _itm_det(item, note["is_interstate"]),
                })
            entry = {
                "ntty": note.get("doc_type", "C"),
                "nt_num": str(note["invoice_no"]).strip(),
                "nt_dt": _format_date(note["invoice_date"]),
                "p_gst": "N",
            }
            # Original invoice link (if available)
            if note.get("orig_invoice_no"):
                entry["inum"] = str(note["orig_invoice_no"]).strip()
            if note.get("orig_invoice_date"):
                entry["idt"] = _format_date(note["orig_invoice_date"])
            entry["val"] = _num(note["invoice_value"])
            entry["pos"] = _pos(note["place_of_supply"])
            entry["rchrg"] = "N"
            entry["inv_typ"] = "R"
            entry["itms"] = itms
            nt_arr.append(entry)
        cdnr.append({"ctin": ctin, "nt": nt_arr})
    return cdnr


# ======================================================================
# CDNUR — Credit/Debit Notes to Unregistered customers
# ======================================================================
def build_cdnur_section(notes: list) -> list:
    """
    CDNUR: flat list, no grouping by ctin.

    Schema:
      [{
        "ntty": "C" | "D",
        "nt_num": "...",
        "nt_dt": "DD-MM-YYYY",
        "p_gst": "N",
        "typ": "B2CL" | "EXPWP" | "EXPWOP",   (B2CL for our use case)
        "inum": "...",                          (optional)
        "idt": "DD-MM-YYYY",                    (optional)
        "val": ...,
        "pos": "29",
        "itms": [...]
      }]
    """
    cdnur = []
    for note in notes:
        # CDNUR primarily covers B2CL-type adjustments (interstate > 2.5L)
        # plus exports. We mark as B2CL by default (most common case).
        ur_type = "B2CL"
        collapsed = _collapse_items_by_rate(note["items"])
        itms = []
        for item in collapsed:
            itms.append({
                "num": ITEM_NUM_PLACEHOLDER,
                "itm_det": {
                    "txval": _num(item["taxable_value"]),
                    "rt": _num(item["tax_rate"]),
                    "iamt": _num(item["igst_amount"]),
                    "csamt": _num(item["cess_amount"]),
                },
            })
        entry = {
            "ntty": note.get("doc_type", "C"),
            "nt_num": str(note["invoice_no"]).strip(),
            "nt_dt": _format_date(note["invoice_date"]),
            "p_gst": "N",
            "typ": ur_type,
        }
        if note.get("orig_invoice_no"):
            entry["inum"] = str(note["orig_invoice_no"]).strip()
        if note.get("orig_invoice_date"):
            entry["idt"] = _format_date(note["orig_invoice_date"])
        entry["val"] = _num(note["invoice_value"])
        entry["pos"] = _pos(note["place_of_supply"])
        entry["itms"] = itms
        cdnur.append(entry)
    return cdnur


# ======================================================================
# HSN — nested under "hsn" with hsn_b2b and/or hsn_b2c
# ======================================================================
def _build_hsn_entries(invoices: list, is_b2b_set: bool) -> list:
    """
    Build HSN entries. Key order matches offline tool output:
      num, hsn_sc, desc, uqc, qty, rt, txval, iamt, samt, camt, csamt
    """
    grouped = defaultdict(lambda: {
        "qty": 0.0, "txval": 0.0,
        "iamt": 0.0, "camt": 0.0, "samt": 0.0, "csamt": 0.0,
    })
    for inv in invoices:
        for item in inv["items"]:
            hsn = str(item["hsn"]).strip()
            rate = round(float(item["tax_rate"] or 0), 2)
            key = (hsn, rate)
            g = grouped[key]
            g["qty"] += float(item.get("quantity") or 0)
            g["txval"] += float(item.get("taxable_value") or 0)
            g["iamt"] += float(item.get("igst_amount") or 0)
            g["camt"] += float(item.get("cgst_amount") or 0)
            g["samt"] += float(item.get("sgst_amount") or 0)
            g["csamt"] += float(item.get("cess_amount") or 0)

    entries = []
    # Sort by HSN code for deterministic output
    sorted_keys = sorted(grouped.keys())
    for i, key in enumerate(sorted_keys, start=1):
        hsn, rate = key
        g = grouped[key]
        is_service = _is_service_hsn(hsn)
        # Services → uqc=NA, qty=0; Goods → keep actual qty
        if is_service:
            uqc = "NA"
            qty = 0
        else:
            uqc = "NOS"  # Default for goods if not otherwise specified
            qty = _num(g["qty"])

        entry = {
            "num": i,
            "hsn_sc": hsn,
            "desc": _hsn_desc(hsn),
            "uqc": uqc,
            "qty": qty,
            "rt": _num(rate),
            "txval": _num(g["txval"]),
            "iamt": _num(g["iamt"]),
            "samt": _num(g["samt"]),
            "camt": _num(g["camt"]),
            "csamt": _num(g["csamt"]),
        }
        entries.append(entry)
    return entries


def build_hsn_section(buckets: dict) -> dict:
    """
    Build the nested HSN section: { "hsn_b2b": [...], "hsn_b2c": [...] }
    Only includes a sub-key if there are entries for it.
    """
    result = {}

    b2b_invs = buckets.get("b2b", [])
    if b2b_invs:
        entries = _build_hsn_entries(b2b_invs, is_b2b_set=True)
        if entries:
            result["hsn_b2b"] = entries

    b2c_invs = buckets.get("b2cl", []) + buckets.get("b2cs", [])
    if b2c_invs:
        entries = _build_hsn_entries(b2c_invs, is_b2b_set=False)
        if entries:
            result["hsn_b2c"] = entries

    return result


# ======================================================================
# Document Issued
# ======================================================================
def build_doc_issue_section(invoices: list) -> dict:
    if not invoices:
        return {"doc_det": []}

    inv_nums = sorted([str(inv["invoice_no"]).strip() for inv in invoices if inv["invoice_no"]])
    if not inv_nums:
        return {"doc_det": []}

    return {
        "doc_det": [{
            "doc_num": 1,
            "doc_typ": "Invoices for outward supply",
            "docs": [{
                "num": 1,
                "from": inv_nums[0],
                "to": inv_nums[-1],
                "totnum": len(inv_nums),
                "cancel": 0,
                "net_issue": len(inv_nums),
            }],
        }],
    }


# ======================================================================
# Top-level builder — keys in EXACT order the offline tool uses
# ======================================================================
def build_gstr1_json(
    firm_gstin: str,
    return_period: str,
    buckets: dict,
    all_invoices: list,
) -> dict:
    """
    Build top-level GSTR-1 JSON exactly as the GST Offline Tool produces it.
    """
    payload = {}
    # Order matters — Python 3.7+ dicts preserve insertion order
    payload["gstin"] = firm_gstin
    payload["fp"] = return_period
    payload["version"] = GSTR1_VERSION
    payload["hash"] = HASH_VALUE

    if buckets.get("b2b"):
        payload["b2b"] = build_b2b_section(buckets["b2b"])
    if buckets.get("b2cl"):
        payload["b2cl"] = build_b2cl_section(buckets["b2cl"])
    if buckets.get("b2cs"):
        payload["b2cs"] = build_b2cs_section(buckets["b2cs"])
    if buckets.get("cdnr"):
        payload["cdnr"] = build_cdnr_section(buckets["cdnr"])
    if buckets.get("cdnur"):
        payload["cdnur"] = build_cdnur_section(buckets["cdnur"])

    # doc_issue counts only regular outward invoices, not credit/debit notes
    regular_invoices = [d for d in all_invoices if d.get("doc_type", "INV") == "INV"]
    doc_issue = build_doc_issue_section(regular_invoices)
    if doc_issue["doc_det"]:
        payload["doc_issue"] = doc_issue

    hsn = build_hsn_section(buckets)
    if hsn:
        payload["hsn"] = hsn

    return payload
