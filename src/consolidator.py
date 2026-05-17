"""
Invoice consolidator (with CDNR/CDNUR support).

Input: validated row-level DataFrame.
Output: a list of consolidated documents, each tagged with doc_type:
  - 'INV' (regular invoice)
  - 'C'   (credit note)
  - 'D'   (debit note)

Critical insight from real data: the SAME invoice number appears across
multiple rows with DIFFERENT HSN/description (e.g. invoice 001/26-27 has
both HSN 9989 and HSN 997314). For GSTR-1 we group by:
  - Document key (gstin, doc_no, doc_date, doc_type)
  - Within a document, line items are consolidated by (HSN, tax_rate)
"""
import pandas as pd
from collections import defaultdict


def _doc_key(row):
    """Document identity = (GSTIN, document_no, document_date, doc_type)."""
    return (
        str(row.get("corrected_gstin") or row.get("gstin") or "").strip(),
        str(row.get("invoice_no", "")).strip(),
        pd.to_datetime(row.get("invoice_date")).date() if pd.notna(row.get("invoice_date")) else None,
        str(row.get("doc_type_canonical", "INV") or "INV"),
    )


def consolidate_invoices(df: pd.DataFrame) -> list:
    """
    Returns a list of document dicts. Each doc carries a 'doc_type' field
    of 'INV' | 'C' | 'D'. Credit notes carry their values as POSITIVE
    amounts (sign is implicit from doc_type), since the GST portal expects
    absolute amounts in the CDNR section.
    """
    docs = defaultdict(lambda: {
        "doc_type": "INV",
        "gstin": "",
        "invoice_no": "",
        "invoice_date": None,
        "customer_name": "",
        "is_b2b": False,
        "is_interstate": False,
        "place_of_supply": "",
        "orig_invoice_no": "",
        "orig_invoice_date": None,
        "items": defaultdict(lambda: {
            "hsn": "",
            "description": "",
            "uqc": "",
            "quantity": 0.0,
            "taxable_value": 0.0,
            "igst_rate": 0.0,
            "igst_amount": 0.0,
            "cgst_rate": 0.0,
            "cgst_amount": 0.0,
            "sgst_rate": 0.0,
            "sgst_amount": 0.0,
            "cess_amount": 0.0,
            "tax_rate": 0.0,
            "raw_rows": 0,
        }),
    })

    for _, row in df.iterrows():
        key = _doc_key(row)
        doc = docs[key]

        doc["doc_type"] = key[3]
        doc["gstin"] = key[0]
        doc["invoice_no"] = key[1]
        doc["invoice_date"] = key[2]
        doc["customer_name"] = str(row.get("customer_name", "")).strip()
        doc["is_b2b"] = bool(row.get("is_b2b", False))
        doc["is_interstate"] = bool(row.get("is_interstate", False))

        # Original invoice info (for credit/debit notes)
        if row.get("orig_invoice_no"):
            doc["orig_invoice_no"] = str(row.get("orig_invoice_no", "")).strip()
        if row.get("orig_invoice_date") is not None and pd.notna(row.get("orig_invoice_date")):
            doc["orig_invoice_date"] = pd.to_datetime(row.get("orig_invoice_date")).date()

        if doc["gstin"]:
            doc["place_of_supply"] = doc["gstin"][:2]
        else:
            sc = str(row.get("state_code", "")).split("-")[0].strip()
            doc["place_of_supply"] = sc.zfill(2) if sc.isdigit() else "29"

        igst_rate = float(row.get("igst_rate", 0) or 0)
        cgst_rate = float(row.get("cgst_rate", 0) or 0)
        sgst_rate = float(row.get("sgst_rate", 0) or 0)
        utgst_rate = float(row.get("utgst_rate", 0) or 0)
        total_rate = igst_rate if igst_rate > 0 else (cgst_rate + sgst_rate + utgst_rate)

        hsn = str(row.get("hsn", "")).strip()
        line_key = (hsn, round(total_rate, 2))
        item = doc["items"][line_key]
        item["hsn"] = hsn
        item["description"] = str(row.get("description", "")).strip()
        item["uqc"] = str(row.get("uqc", "")).strip() or "OTH"

        # Use absolute values: CDNR section needs positive amounts; sign carried by doc_type
        item["quantity"] += abs(float(row.get("quantity", 0) or 0))
        item["taxable_value"] += abs(float(row.get("taxable_value", 0) or 0))
        item["igst_rate"] = igst_rate
        item["igst_amount"] += abs(float(row.get("igst_amount", 0) or 0))
        item["cgst_rate"] = cgst_rate
        item["cgst_amount"] += abs(float(row.get("cgst_amount", 0) or 0))
        item["sgst_rate"] = sgst_rate
        item["sgst_amount"] += abs(float(row.get("sgst_amount", 0) or 0))
        item["cess_amount"] += abs(float(row.get("cess_amount", 0) or 0))
        item["tax_rate"] = total_rate
        item["raw_rows"] += 1

    result = []
    for doc in docs.values():
        items_list = []
        for it in doc["items"].values():
            for k in ["quantity", "taxable_value", "igst_amount",
                      "cgst_amount", "sgst_amount", "cess_amount"]:
                it[k] = round(it[k], 2)
            items_list.append(it)
        doc["items"] = items_list
        doc["invoice_total_taxable"] = round(sum(i["taxable_value"] for i in items_list), 2)
        doc["invoice_total_tax"] = round(
            sum(i["igst_amount"] + i["cgst_amount"] + i["sgst_amount"] + i["cess_amount"]
                for i in items_list), 2
        )
        doc["invoice_value"] = round(doc["invoice_total_taxable"] + doc["invoice_total_tax"], 2)
        result.append(doc)

    result.sort(key=lambda x: (x["invoice_date"] or pd.Timestamp.min, x["invoice_no"]))
    return result


def classify_invoices(invoices: list, b2cl_threshold: float = 250000.0) -> dict:
    """
    Classify documents into buckets by section:
      - b2b   : regular invoices to registered customers
      - b2cl  : unregistered + interstate + > 2.5L
      - b2cs  : unregistered, small or intra-state
      - cdnr  : credit/debit notes to registered customers
      - cdnur : credit/debit notes to unregistered customers
    """
    buckets = {"b2b": [], "b2cl": [], "b2cs": [], "cdnr": [], "cdnur": []}
    for inv in invoices:
        is_note = inv.get("doc_type") in ("C", "D")

        if is_note:
            if inv["is_b2b"] and inv["gstin"]:
                buckets["cdnr"].append(inv)
            else:
                buckets["cdnur"].append(inv)
        else:
            if inv["is_b2b"] and inv["gstin"]:
                buckets["b2b"].append(inv)
            else:
                if inv["is_interstate"] and inv["invoice_value"] > b2cl_threshold:
                    buckets["b2cl"].append(inv)
                else:
                    buckets["b2cs"].append(inv)
    return buckets
