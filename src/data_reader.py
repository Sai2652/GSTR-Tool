"""
Excel/HTML reader for sales data.
Handles both true .xlsx files and Tally-exported .xls (which are TSV/HTML).
"""
import pandas as pd
from pathlib import Path


CANONICAL_COLUMNS = [
    "gstin", "customer_name", "state_code", "invoice_no", "invoice_date",
    "hsn", "description", "uqc", "quantity", "rate",
    "taxable_value", "igst_rate", "igst_amount", "cgst_rate", "cgst_amount",
    "sgst_rate", "sgst_amount", "utgst_rate", "utgst_amount", "cess_amount",
]


COLUMN_MAP = {
    "GSTIN/ UIN": "gstin",
    "GSTIN/UIN": "gstin",
    "Customer Name": "customer_name",
    "State Code": "state_code",
    "Invoice No.": "invoice_no",
    "Invoice No": "invoice_no",
    "Invoice Date.": "invoice_date",
    "Invoice Date": "invoice_date",
    "HSN/SAC": "hsn",
    "Description": "description",
    "UQC": "uqc",
    "Total Quantity": "quantity",
    "Quantity": "quantity",
    "Rate": "rate",
    "Taxable Value": "taxable_value",
    "IGST(%)": "igst_rate",
    "Integrated Tax Amount": "igst_amount",
    "CGST(%)": "cgst_rate",
    "Central Tax Amount": "cgst_amount",
    "SGST(%)": "sgst_rate",
    "State Tax Amount": "sgst_amount",
    "UTGST(%)": "utgst_rate",
    "UT Tax Amount": "utgst_amount",
    "Cess Amount": "cess_amount",
    # Document type indicators (for credit/debit note detection)
    "Document Type": "doc_type",
    "Doc Type": "doc_type",
    "Voucher Type": "doc_type",
    "Note Type": "note_type",
    "Original Invoice No.": "orig_invoice_no",
    "Original Invoice No": "orig_invoice_no",
    "Original Invoice Date": "orig_invoice_date",
    "Original Invoice Date.": "orig_invoice_date",
}


def _detect_format(path: Path) -> str:
    """Tally exports a TSV with .xls extension. Detect actual format."""
    with open(path, "rb") as f:
        head = f.read(8)
    # XLSX = ZIP signature
    if head[:2] == b"PK":
        return "xlsx"
    # OLE2/legacy XLS
    if head == b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1":
        return "xls_legacy"
    # HTML
    text_head = head.decode("utf-8", errors="ignore").lower()
    if "<html" in text_head or "<!doc" in text_head:
        return "html"
    # Plain TSV
    return "tsv"


def read_sales(path: str) -> pd.DataFrame:
    """Read a sales sheet regardless of format and return a normalized DataFrame."""
    p = Path(path)
    fmt = _detect_format(p)

    if fmt == "xlsx":
        df = pd.read_excel(p, dtype={"GSTIN/ UIN": str, "GSTIN/UIN": str})
    elif fmt == "tsv":
        df = pd.read_csv(p, sep="\t", dtype={"GSTIN/ UIN": str, "GSTIN/UIN": str})
    elif fmt == "html":
        df = pd.read_html(str(p))[0]
    else:
        df = pd.read_excel(p)

    # Strip whitespace from column names
    df.columns = [str(c).strip() for c in df.columns]

    # Drop fully empty trailing columns ('Unnamed: 20' etc.)
    df = df.loc[:, ~df.columns.str.contains(r"^Unnamed", na=False)]

    # Apply canonical mapping
    rename = {col: COLUMN_MAP[col] for col in df.columns if col in COLUMN_MAP}
    df = df.rename(columns=rename)

    # Drop "Grand Total" rows and rows with no invoice number
    df = df[~df["gstin"].astype(str).str.contains("Grand Total", case=False, na=False)]
    df = df[df["invoice_no"].notna()]
    df = df[df["invoice_no"].astype(str).str.strip() != ""]

    # Clean string columns
    for c in ["gstin", "customer_name", "state_code", "invoice_no", "hsn",
              "description", "uqc"]:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip()
            df.loc[df[c].str.lower() == "nan", c] = ""

    # Numeric columns
    numeric_cols = ["quantity", "rate", "taxable_value", "igst_rate", "igst_amount",
                    "cgst_rate", "cgst_amount", "sgst_rate", "sgst_amount",
                    "utgst_rate", "utgst_amount", "cess_amount"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # Date column
    if "invoice_date" in df.columns:
        df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce", dayfirst=True)

    # Normalize HSN: strip trailing .0 from float-converted HSN codes
    if "hsn" in df.columns:
        df["hsn"] = df["hsn"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()

    # Date column for original invoice (credit notes)
    if "orig_invoice_date" in df.columns:
        df["orig_invoice_date"] = pd.to_datetime(df["orig_invoice_date"], errors="coerce", dayfirst=True)

    # Classify document type: 'INV' (regular invoice), 'C' (credit note), 'D' (debit note)
    df["doc_type_canonical"] = df.apply(_classify_doc_type, axis=1)

    df = df.reset_index(drop=True)
    return df


# ----- Document type classification (needed for CDNR / credit notes) ----
def _classify_doc_type(row) -> str:
    """
    Classify a row as 'INV' (invoice), 'C' (credit note) or 'D' (debit note).
    Detection priority:
      1. Explicit doc_type / note_type column value
      2. Invoice number prefix ("CN-", "CR-", "Credit/", "DN-", "DR-")
      3. Negative taxable value (treated as credit note)
    """
    for col in ("doc_type", "note_type"):
        if col in row.index:
            v = str(row.get(col, "") or "").strip().upper()
            if any(w in v for w in ("CREDIT NOTE", "CREDIT", "CRN")):
                if "DEBIT" not in v:
                    return "C"
            if any(w in v for w in ("DEBIT NOTE", "DEBIT", "DBN")):
                return "D"
    inum = str(row.get("invoice_no", "") or "").upper()
    if inum.startswith(("CN-", "CN/", "CR-", "CR/", "CREDIT", "C/N")):
        return "C"
    if inum.startswith(("DN-", "DN/", "DR-", "DR/", "DEBIT", "D/N")):
        return "D"
    try:
        tv = float(row.get("taxable_value", 0) or 0)
        if tv < 0:
            return "C"
    except (ValueError, TypeError):
        pass
    return "INV"
