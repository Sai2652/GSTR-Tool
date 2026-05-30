"""
GSTR-3B PDF generator — mimics the GST portal's filed-return PDF layout.

Sections rendered (per CBIC Form GSTR-3B as displayed on www.gst.gov.in):
  Header        : GSTIN, Legal Name, Trade Name, FY, Period
  Table 3.1     : Outward & inward (RCM) supplies — rows (a) to (e)
  Table 3.1.1   : Supplies through e-commerce operator — rows (i) (ii)
  Table 3.2     : Inter-State supplies to URD / composition / UIN
  Table 4       : Eligible ITC — sub-rows per Notification 14/2022-CT
  Table 5       : Exempt / Nil-rated / Non-GST inward supplies
  Table 5.1     : Interest & Late fee
  Table 6.1     : Payment of tax
  Verification

Public API:
    write_gstr3b_pdf(out_path, firm, period_label, gstr2b, computation,
                     supplies_3_1=None, ecom_3_1_1=None, inter_state_3_2=None,
                     exempt_inward_5=None, interest_late_fee=None)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


# ---------- styling ----------
PORTAL_BLUE = colors.HexColor("#0F4C81")   # GST portal header blue
HEADER_BG = colors.HexColor("#E8EEF7")     # Table header bg
ROW_ALT = colors.HexColor("#F7F9FC")
BORDER = colors.HexColor("#9CA3AF")
TEXT_DARK = colors.HexColor("#111827")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=13, textColor=PORTAL_BLUE, leading=16, alignment=1),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, textColor=TEXT_DARK, leading=12, alignment=1),
        "section": ParagraphStyle(
            "section", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10.5, textColor=PORTAL_BLUE, leading=14, spaceBefore=6,
            spaceAfter=3),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.5, textColor=TEXT_DARK, leading=11),
        "cellb": ParagraphStyle(
            "cellb", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=8.5, textColor=TEXT_DARK, leading=11),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontName="Helvetica",
            fontSize=8, textColor=TEXT_DARK, leading=10),
    }


def _money(x: Any) -> str:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return "0.00"
    return f"{v:,.2f}"


def _zero_tax() -> Dict[str, float]:
    return {"igst": 0.0, "cgst": 0.0, "sgst": 0.0, "cess": 0.0}


# ---------- header ----------

def _build_header(firm: Dict, period_label: str, st) -> List:
    gstin = firm.get("gstin", "")
    legal = firm.get("legal_name") or firm.get("name") or ""
    trade = firm.get("name") or ""
    # FY guess from period (e.g. "May 2026" → FY 2026-27 if month>=Apr)
    fy = _fy_label(period_label)

    rows = [
        ["1. GSTIN", gstin],
        ["2. Legal name of the registered person", legal],
        ["3(a). Trade name, if any", trade],
        ["4. Year", fy],
        ["5. Period", period_label],
    ]
    t = Table(rows, colWidths=[75 * mm, 105 * mm])
    t.setStyle(TableStyle([
        ("FONT",       (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT",       (0, 0), (0, -1),  "Helvetica-Bold", 9),
        ("BACKGROUND", (0, 0), (0, -1),  HEADER_BG),
        ("TEXTCOLOR",  (0, 0), (0, -1),  PORTAL_BLUE),
        ("BOX",        (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID",  (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
    ]))
    return [
        Paragraph("Form GSTR-3B", st["title"]),
        Paragraph("[See rule 61(5)]", st["subtitle"]),
        Spacer(1, 6),
        t,
        Spacer(1, 8),
    ]


def _fy_label(period_label: str) -> str:
    """Best-effort: from 'May 2026' or '052026' produce '2026-27'."""
    s = (period_label or "").strip()
    yr = None
    mo = None
    # MMYYYY
    if len(s) == 6 and s.isdigit():
        mo, yr = int(s[:2]), int(s[2:])
    else:
        from datetime import datetime
        for fmt in ("%B %Y", "%b %Y", "%m-%Y", "%m/%Y"):
            try:
                dt = datetime.strptime(s, fmt)
                mo, yr = dt.month, dt.year
                break
            except ValueError:
                continue
    if yr is None:
        return s
    fy_start = yr if (mo or 1) >= 4 else yr - 1
    return f"{fy_start}-{str(fy_start + 1)[2:]}"


# ---------- section helper ----------

def _tax_table(headers: List[str], rows: List[List[Any]],
               col_widths: List[float], money_cols: set[int]) -> Table:
    """Build a portal-style 5/6-column table with bordered cells."""
    body = [headers] + rows
    t = Table(body, colWidths=col_widths, repeatRows=1)
    n_rows = len(body)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR",  (0, 0), (-1, 0), PORTAL_BLUE),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING",   (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2.5),
    ]
    # Right-align money columns; alternate row shading
    for c in money_cols:
        style.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    for r in range(1, n_rows):
        if r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


# ---------- Table 3.1 ----------

def _build_table_3_1(supplies: Optional[Dict[str, Dict[str, float]]],
                     output_total: Dict[str, float], st) -> List:
    """
    supplies (optional): { '3.1.a': {tx,igst,cgst,sgst,cess}, ... '3.1.e': {...} }
    If absent, fall back to showing the total on 3.1(a) line.
    """
    rows_def = [
        ("(a) Outward taxable supplies (other than zero rated, nil rated and exempted)",
         "3.1.a"),
        ("(b) Outward taxable supplies (zero rated)", "3.1.b"),
        ("(c) Other outward supplies (Nil rated, exempted)", "3.1.c"),
        ("(d) Inward supplies (liable to reverse charge)", "3.1.d"),
        ("(e) Non-GST outward supplies", "3.1.e"),
    ]
    supplies = supplies or {}
    rows = []
    for label, key in rows_def:
        s = supplies.get(key) or {}
        if key == "3.1.a" and not supplies:
            # Fall-back: show output_total on the (a) line
            s = {"tx": 0.0, **output_total}
        rows.append([
            Paragraph(label, st["cell"]),
            _money(s.get("tx", 0)),
            _money(s.get("igst", 0)),
            _money(s.get("cgst", 0)),
            _money(s.get("sgst", 0)),
            _money(s.get("cess", 0)),
        ])
    headers = ["Nature of Supplies", "Total taxable\nvalue",
               "Integrated\nTax", "Central\nTax", "State/UT\nTax", "Cess"]
    widths = [70 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 18 * mm]
    return [
        Paragraph("3.1 Details of Outward Supplies and inward supplies liable to reverse charge",
                  st["section"]),
        _tax_table(headers, rows, widths, money_cols={1, 2, 3, 4, 5}),
        Spacer(1, 6),
    ]


# ---------- Table 3.1.1 ----------

def _build_table_3_1_1(ecom: Optional[Dict[str, Dict[str, float]]], st) -> List:
    ecom = ecom or {}
    rows_def = [
        ("(i) Taxable supplies on which e-commerce operator pays tax u/s 9(5)\n     [to be furnished by ECO]", "i"),
        ("(ii) Taxable supplies made by registered person through e-commerce operator,\n     on which e-commerce operator is required to pay tax u/s 9(5)\n     [to be furnished by registered person making supplies through ECO]", "ii"),
    ]
    rows = []
    for label, key in rows_def:
        s = ecom.get(key) or {}
        rows.append([
            Paragraph(label, st["cell"]),
            _money(s.get("tx", 0)),
            _money(s.get("igst", 0)),
            _money(s.get("cgst", 0)),
            _money(s.get("sgst", 0)),
            _money(s.get("cess", 0)),
        ])
    headers = ["Nature of Supplies", "Total taxable\nvalue",
               "Integrated\nTax", "Central\nTax", "State/UT\nTax", "Cess"]
    widths = [70 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 18 * mm]
    return [
        Paragraph("3.1.1 Details of Supplies notified u/s 9(5) of the CGST Act, 2017",
                  st["section"]),
        _tax_table(headers, rows, widths, money_cols={1, 2, 3, 4, 5}),
        Spacer(1, 6),
    ]


# ---------- Table 3.2 ----------

def _build_table_3_2(inter: Optional[List[Dict[str, Any]]], st) -> List:
    inter = inter or []
    headers = ["Place of Supply (State/UT)", "Total Taxable Value",
               "Amount of Integrated Tax"]
    blocks = []
    for header_label, key in [
        ("Supplies made to Unregistered Persons", "urd"),
        ("Supplies made to Composition Taxable Persons", "comp"),
        ("Supplies made to UIN holders", "uin"),
    ]:
        sub = [r for r in inter if r.get("kind") == key]
        rows = [[r.get("pos", ""), _money(r.get("tx", 0)), _money(r.get("igst", 0))]
                for r in sub] or [["—", "0.00", "0.00"]]
        blocks.append(Paragraph(header_label, st["cellb"]))
        blocks.append(_tax_table(headers, rows,
                                  [80 * mm, 50 * mm, 50 * mm],
                                  money_cols={1, 2}))
        blocks.append(Spacer(1, 4))
    return [
        Paragraph("3.2 Of the supplies shown in 3.1(a) and 3.1.1(ii), details of inter-State supplies",
                  st["section"]),
        *blocks,
        Spacer(1, 4),
    ]


# ---------- Table 4 ----------

def _build_table_4(table4: Dict, st) -> List:
    """Render Table 4 from gstr2b['table4'] (built by gstr2b_reader)."""
    t4 = table4 or {}
    z = _zero_tax()

    def row(label: str, key: Optional[str], bold: bool = False):
        s = t4.get(key, z) if key else z
        style = st["cellb"] if bold else st["cell"]
        return [
            Paragraph(label, style),
            _money(s.get("igst", 0)),
            _money(s.get("cgst", 0)),
            _money(s.get("sgst", 0)),
            _money(s.get("cess", 0)),
        ]

    rows = [
        row("(A) ITC Available (whether in full or part)", None, bold=True),
        row("    (1) Import of goods", "4A1_import_goods"),
        row("    (2) Import of services", "4A2_import_services"),
        row("    (3) Inward supplies liable to reverse charge (other than 1 & 2 above)",
            "4A3_reverse_charge"),
        row("    (4) Inward supplies from ISD", "4A4_isd"),
        row("    (5) All other ITC", "4A5_all_other_itc"),
        row("(B) ITC Reversed", None, bold=True),
        row("    (1) As per rules 38, 42 & 43 of CGST Rules and section 17(5)",
            "4B1_rules_38_42_43_17_5"),
        row("    (2) Others", "4B2_others"),
        row("(C) Net ITC Available (A - B)", "4C_net_itc", bold=True),
        row("(D) Other Details", None, bold=True),
        row("    (1) ITC reclaimed which was reversed under Table 4(B)(2) in earlier tax period",
            "4D1_reclaimed"),
        row("    (2) Ineligible ITC under section 16(4) & ITC restricted due to PoS rules",
            "4D2_ineligible_16_4_pos"),
    ]
    headers = ["Details", "Integrated\nTax", "Central\nTax", "State/UT\nTax", "Cess"]
    widths = [88 * mm, 23 * mm, 23 * mm, 23 * mm, 19 * mm]
    return [
        Paragraph("4. Eligible ITC", st["section"]),
        _tax_table(headers, rows, widths, money_cols={1, 2, 3, 4}),
        Spacer(1, 6),
    ]


# ---------- Table 5 & 5.1 ----------

def _build_table_5(exempt: Optional[Dict[str, Dict[str, float]]], st) -> List:
    exempt = exempt or {}
    rows = []
    for label, key in [
        ("From a supplier under composition scheme, Exempt and Nil rated supply", "exempt"),
        ("Non-GST supply", "non_gst"),
    ]:
        s = exempt.get(key) or {}
        rows.append([
            Paragraph(label, st["cell"]),
            _money(s.get("inter", 0)),
            _money(s.get("intra", 0)),
        ])
    headers = ["Nature of Supplies", "Inter-State Supplies", "Intra-State Supplies"]
    widths = [100 * mm, 40 * mm, 40 * mm]
    return [
        Paragraph("5. Values of exempt, nil rated and non-GST inward supplies",
                  st["section"]),
        _tax_table(headers, rows, widths, money_cols={1, 2}),
        Spacer(1, 6),
    ]


def _build_table_5_1(int_late: Optional[Dict[str, Dict[str, float]]], st) -> List:
    int_late = int_late or {}
    z = _zero_tax()
    rows = []
    for label, key in [("Interest", "interest"), ("Late fee", "late_fee")]:
        s = int_late.get(key) or z
        rows.append([
            Paragraph(label, st["cell"]),
            _money(s.get("igst", 0)),
            _money(s.get("cgst", 0)),
            _money(s.get("sgst", 0)),
            _money(s.get("cess", 0)),
        ])
    headers = ["Details", "Integrated\nTax", "Central\nTax", "State/UT\nTax", "Cess"]
    widths = [88 * mm, 23 * mm, 23 * mm, 23 * mm, 19 * mm]
    return [
        Paragraph("5.1 Interest and Late fee for previous tax period", st["section"]),
        _tax_table(headers, rows, widths, money_cols={1, 2, 3, 4}),
        Spacer(1, 6),
    ]


# ---------- Table 6.1 ----------

def _build_table_6_1(comp: Dict, st) -> List:
    """
    Build Table 6.1 — Payment of tax.
    Columns mimic the portal: Description | Tax payable | Paid through ITC (split) |
                              Tax paid TDS/TCS | Tax/Cess paid in cash | Interest | Late fee
    """
    output = comp.get("output_tax", _zero_tax())
    credit_used = comp.get("credit_used", _zero_tax())
    cash_payable = comp.get("cash_payable", _zero_tax())
    setoff_steps = comp.get("setoff_steps", [])

    def itc_from_to(from_h: str, to_h: str) -> float:
        return sum(s["amount"] for s in setoff_steps
                   if s["from"].lower() == from_h and s["to"].lower() == to_h)

    rows = []
    for head_key, label in [("igst", "Integrated Tax"),
                            ("cgst", "Central Tax"),
                            ("sgst", "State/UT Tax"),
                            ("cess", "Cess")]:
        payable = output.get(head_key, 0.0)
        # ITC paid via IGST/CGST/SGST/Cess into this head
        paid_igst = itc_from_to("igst", head_key)
        paid_cgst = itc_from_to("cgst", head_key)
        paid_sgst = itc_from_to("sgst", head_key)
        paid_cess = itc_from_to("cess", head_key)
        cash = cash_payable.get(head_key, 0.0)
        rows.append([
            Paragraph(label, st["cellb"]),
            _money(payable),
            _money(paid_igst), _money(paid_cgst),
            _money(paid_sgst), _money(paid_cess),
            "0.00",            # TDS/TCS
            _money(cash),      # cash
            "0.00",            # interest
            "0.00",            # late fee
        ])
    headers = [
        "Description", "Total\ntax\npayable",
        "Paid through ITC — IGST", "CGST", "SGST", "Cess",
        "TDS/TCS", "Tax paid\nin cash", "Interest", "Late fee"
    ]
    widths = [22 * mm, 15 * mm, 18 * mm, 14 * mm, 14 * mm, 12 * mm,
              14 * mm, 16 * mm, 14 * mm, 14 * mm]
    return [
        Paragraph("6.1 Payment of tax", st["section"]),
        _tax_table(headers, rows, widths, money_cols={1, 2, 3, 4, 5, 6, 7, 8, 9}),
        Spacer(1, 6),
    ]


# ---------- Verification ----------

def _build_verification(firm: Dict, st) -> List:
    legal = firm.get("legal_name") or firm.get("name") or ""
    txt = (
        "<b>Verification</b><br/>"
        "I hereby solemnly affirm and declare that the information given herein above "
        "is true and correct to the best of my knowledge and belief and nothing has been "
        "concealed therefrom.<br/><br/>"
        f"Name of Authorized Signatory: <b>{legal}</b><br/>"
        "Designation / Status: <b>Proprietor / Authorized Signatory</b><br/>"
        "Date: ____________________   Place: ____________________"
    )
    return [
        Spacer(1, 8),
        Paragraph(txt, st["small"]),
    ]


# ---------- Public ----------

def write_gstr3b_pdf(
    out_path: str | Path,
    firm: Dict,
    period_label: str,
    gstr2b: Dict,
    computation: Dict,
    supplies_3_1: Optional[Dict[str, Dict[str, float]]] = None,
    ecom_3_1_1: Optional[Dict[str, Dict[str, float]]] = None,
    inter_state_3_2: Optional[List[Dict[str, Any]]] = None,
    exempt_inward_5: Optional[Dict[str, Dict[str, float]]] = None,
    interest_late_fee: Optional[Dict[str, Dict[str, float]]] = None,
) -> str:
    """
    Render the GSTR-3B return as a PDF in the GST portal's filed-return layout.

    Required:
      firm           : dict with gstin / name / legal_name
      period_label   : e.g. 'May 2026' or '052026'
      gstr2b         : output of parse_gstr2b() (must include 'table4')
      computation    : output of compute_gstr3b()

    Optional row-level breakdowns (zeros shown if omitted):
      supplies_3_1     : { '3.1.a'..'3.1.e': {'tx','igst','cgst','sgst','cess'} }
      ecom_3_1_1       : { 'i'/'ii': {...} }
      inter_state_3_2  : [{ 'kind': 'urd'|'comp'|'uin', 'pos': '..', 'tx':.., 'igst':.. }, ...]
      exempt_inward_5  : { 'exempt'/'non_gst': {'inter':.., 'intra':..} }
      interest_late_fee: { 'interest'/'late_fee': {'igst':.., 'cgst':.., 'sgst':.., 'cess':..} }
    """
    out_path = str(out_path)
    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"GSTR-3B {firm.get('gstin','')} {period_label}",
    )
    st = _styles()
    story: List = []
    story += _build_header(firm, period_label, st)
    story += _build_table_3_1(supplies_3_1, computation.get("output_tax", _zero_tax()), st)
    story += _build_table_3_1_1(ecom_3_1_1, st)
    story += _build_table_3_2(inter_state_3_2, st)
    story += _build_table_4(gstr2b.get("table4") or {}, st)
    story += _build_table_5(exempt_inward_5, st)
    story += _build_table_5_1(interest_late_fee, st)
    story += _build_table_6_1(computation, st)
    story += _build_verification(firm, st)
    doc.build(story)
    return out_path
