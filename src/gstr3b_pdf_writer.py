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
    # legal_name = the proprietor / registered person's legal name (e.g. PETER DSOUZA)
    # name      = trade name / business name (e.g. HITECH SYSTEMS)
    legal = firm.get("legal_name") or firm.get("name") or ""
    trade = firm.get("name") or ""
    arn = firm.get("arn") or ""
    arn_date = firm.get("arn_date") or ""
    fy = _fy_label(period_label)
    month_only = _month_only(period_label)

    # Top right small box for Year/Period (mirrors portal layout)
    yp_table = Table(
        [["Year", fy], ["Period", month_only or period_label]],
        colWidths=[20 * mm, 36 * mm],
    )
    yp_table.setStyle(TableStyle([
        ("FONT",       (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT",       (0, 0), (0, -1),  "Helvetica-Bold", 9),
        ("BACKGROUND", (0, 0), (0, -1),  HEADER_BG),
        ("TEXTCOLOR",  (0, 0), (0, -1),  PORTAL_BLUE),
        ("BOX",        (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID",  (0, 0), (-1, -1), 0.4, BORDER),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
    ]))

    # Right-align the year/period chip within the page
    yp_wrapper = Table([[yp_table]], colWidths=[180 * mm])
    yp_wrapper.setStyle(TableStyle([
        ("ALIGN",         (0, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    # Main firm-info block — portal uses 2(a)/(b)/(c)/(d) numbering
    rows = [
        ["GSTIN of the supplier", gstin],
        ["2(a). Legal name of the registered person", legal],
        ["2(b). Trade name, if any", trade],
        ["2(c). ARN", arn],
        ["2(d). Date of ARN", arn_date],
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
        yp_wrapper,
        Spacer(1, 4),
        t,
        Paragraph("(Amount in ₹ for all tables)",
                  ParagraphStyle("a", parent=st["small"], alignment=2)),
        Spacer(1, 6),
    ]


def _fy_label(period_label: str) -> str:
    """Best-effort: from 'May 2026' or '052026' produce '2026-27'."""
    s = (period_label or "").strip()
    yr = None
    mo = None
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


def _month_only(period_label: str) -> str:
    """'May 2026' / '052026' → 'May'."""
    s = (period_label or "").strip()
    from datetime import datetime
    if len(s) == 6 and s.isdigit():
        try:
            return datetime(int(s[2:]), int(s[:2]), 1).strftime("%B")
        except ValueError:
            return s
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%B")
        except ValueError:
            continue
    return s.split(" ")[0] if " " in s else s


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

    def row(label: str, key: Optional[str], bold: bool = False,
            section_header: bool = False):
        style = st["cellb"] if bold else st["cell"]
        if section_header:
            # Section header rows have blank cells for the tax columns
            return [Paragraph(label, style), "", "", "", ""]
        s = t4.get(key, z) if key else z
        return [
            Paragraph(label, style),
            _money(s.get("igst", 0)),
            _money(s.get("cgst", 0)),
            _money(s.get("sgst", 0)),
            _money(s.get("cess", 0)),
        ]

    rows = [
        row("A. ITC Available (whether in full or part)", None, bold=True, section_header=True),
        row("    (1) Import of goods", "4A1_import_goods"),
        row("    (2) Import of services", "4A2_import_services"),
        row("    (3) Inward supplies liable to reverse charge (other than 1 & 2 above)",
            "4A3_reverse_charge"),
        row("    (4) Inward supplies from ISD", "4A4_isd"),
        row("    (5) All other ITC", "4A5_all_other_itc"),
        row("B. ITC Reversed", None, bold=True, section_header=True),
        row("    (1) As per rules 38, 42 & 43 of CGST Rules and section 17(5)",
            "4B1_rules_38_42_43_17_5"),
        row("    (2) Others", "4B2_others"),
        row("C. Net ITC available (A-B)", "4C_net_itc", bold=True),
        row("(D) Other Details", None, bold=True, section_header=True),
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
    """
    Portal layout:
      Details              | IGST | CGST | SGST | Cess
      System computed Interest  (read-only, dashes if unknown)
      Interest Paid             (entered values, default 0)
      Late fee                  (CGST + SGST only on portal — IGST/Cess dashed)
    """
    int_late = int_late or {}
    z = _zero_tax()
    sys_int = int_late.get("system_computed_interest") or {}
    paid_int = int_late.get("interest") or z
    late = int_late.get("late_fee") or z

    def cell(v, dashed=False):
        if dashed or v is None:
            return "-"
        return _money(v)

    rows = [
        [Paragraph("System computed Interest", st["cell"]),
         cell(sys_int.get("igst"), dashed=not sys_int),
         cell(sys_int.get("cgst"), dashed=not sys_int),
         cell(sys_int.get("sgst"), dashed=not sys_int),
         cell(sys_int.get("cess"), dashed=not sys_int)],
        [Paragraph("Interest Paid", st["cell"]),
         _money(paid_int.get("igst", 0)),
         _money(paid_int.get("cgst", 0)),
         _money(paid_int.get("sgst", 0)),
         _money(paid_int.get("cess", 0))],
        [Paragraph("Late fee", st["cell"]),
         "-",  # Late fee never applies to IGST on portal
         _money(late.get("cgst", 0)),
         _money(late.get("sgst", 0)),
         "-"],
    ]
    headers = ["Details", "Integrated\nTax", "Central\nTax", "State/UT\nTax", "Cess"]
    widths = [88 * mm, 23 * mm, 23 * mm, 23 * mm, 19 * mm]
    return [
        Paragraph("5.1 Interest and Late fee for previous tax period", st["section"]),
        _tax_table(headers, rows, widths, money_cols={1, 2, 3, 4}),
        Spacer(1, 6),
    ]


# ---------- Table 6.1 ----------

def _build_table_6_1(comp: Dict, st,
                     supplies_3_1: Optional[Dict] = None) -> List:
    """
    Portal layout for Table 6.1 — Payment of tax.

    Two sub-sections, each with its own 4 tax-head rows:
      (A) Other than reverse charge
      (B) Reverse charge and supplies made u/s 9(5)

    Columns:
      Description | Tax payable | Adjustment of negative liability of previous
      tax period | Net Tax Payable | Tax paid through ITC (IGST/CGST/SGST/Cess)
      | Tax paid in cash | Interest paid in cash | Late fee paid in cash
    """
    output_total = comp.get("output_tax", _zero_tax())
    cash_payable = comp.get("cash_payable", _zero_tax())
    setoff_steps = comp.get("setoff_steps", [])

    # Split output into (A) regular and (B) RCM portion.
    # RCM tax payable comes from 3.1(d) — it's a self-liability on inward RCM
    # supplies, paid only in CASH (no ITC offset allowed on the same period).
    rcm_d = (supplies_3_1 or {}).get("3.1.d") or {}
    rcm_tax = {h: float(rcm_d.get(h, 0) or 0) for h in ("igst", "cgst", "sgst", "cess")}
    # Section (A) tax payable = total output - RCM-self portion
    other_tax = {h: round(output_total.get(h, 0) - rcm_tax[h], 2)
                 for h in ("igst", "cgst", "sgst", "cess")}
    # Section (A) cash = total cash - RCM cash (RCM is fully cash, so subtract)
    other_cash = {h: round(max(0.0, cash_payable.get(h, 0) - rcm_tax[h]), 2)
                  for h in ("igst", "cgst", "sgst", "cess")}

    def itc_from_to(from_h: str, to_h: str) -> float:
        return sum(s["amount"] for s in setoff_steps
                   if s["from"].lower() == from_h and s["to"].lower() == to_h)

    def head_row(label: str, payable: float, cash: float,
                 itc_igst: float, itc_cgst: float, itc_sgst: float, itc_cess: float):
        return [
            Paragraph(label, st["cellb"]),
            _money(payable),
            _money(0.0),         # Adjustment of negative liability prev period
            _money(payable),     # Net Tax Payable
            _money(itc_igst), _money(itc_cgst), _money(itc_sgst), _money(itc_cess),
            _money(cash),
            _money(0.0),         # Interest paid in cash
            _money(0.0),         # Late fee paid in cash
        ]

    sec_a_rows = []
    for h, label in [("igst", "Integrated tax"), ("cgst", "Central tax"),
                     ("sgst", "State/UT tax"), ("cess", "Cess")]:
        sec_a_rows.append(head_row(
            label,
            other_tax[h], other_cash[h],
            itc_from_to("igst", h), itc_from_to("cgst", h),
            itc_from_to("sgst", h), itc_from_to("cess", h),
        ))

    # Section (B) — RCM. Tax paid in cash only; ITC columns are dashes.
    def rcm_row(label: str, val: float):
        return [
            Paragraph(label, st["cellb"]),
            _money(val),
            _money(0.0),
            _money(val),
            "-", "-", "-", "-",
            _money(val),
            "-", "-",
        ]

    sec_b_rows = [
        rcm_row("Integrated tax", rcm_tax["igst"]),
        rcm_row("Central tax",    rcm_tax["cgst"]),
        rcm_row("State/UT tax",   rcm_tax["sgst"]),
        rcm_row("Cess",           rcm_tax["cess"]),
    ]

    headers = [
        "Description", "Tax\npayable", "Adjustment of\nneg. liability\nprev. period",
        "Net Tax\nPayable",
        "Tax paid through ITC — IGST", "CGST", "SGST", "Cess",
        "Tax paid\nin cash", "Interest\npaid in cash", "Late fee\npaid in cash"
    ]
    widths = [20 * mm, 14 * mm, 17 * mm, 14 * mm,
              17 * mm, 13 * mm, 13 * mm, 11 * mm,
              14 * mm, 14 * mm, 14 * mm]

    money_cols = set(range(1, len(headers)))

    # Build single table with section-header rows interleaved
    section_a_label = ["(A) Other than reverse charge"] + [""] * (len(headers) - 1)
    section_b_label = ["(B) Reverse charge and supplies made u/s 9(5)"] + [""] * (len(headers) - 1)
    body = [headers, section_a_label] + sec_a_rows + [section_b_label] + sec_b_rows
    t = Table(body, colWidths=widths, repeatRows=1)
    n_rows = len(body)
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR",  (0, 0), (-1, 0), PORTAL_BLUE),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",   (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2.5),
    ]
    for c in money_cols:
        style.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    # Section header rows styling
    for r_idx in (1, 2 + len(sec_a_rows)):
        style.append(("SPAN", (0, r_idx), (-1, r_idx)))
        style.append(("BACKGROUND", (0, r_idx), (-1, r_idx), HEADER_BG))
        style.append(("FONT", (0, r_idx), (-1, r_idx), "Helvetica-Bold", 8.5))
        style.append(("TEXTCOLOR", (0, r_idx), (-1, r_idx), PORTAL_BLUE))
        style.append(("ALIGN", (0, r_idx), (-1, r_idx), "LEFT"))
    t.setStyle(TableStyle(style))

    return [
        Paragraph("6.1 Payment of tax", st["section"]),
        t,
        Spacer(1, 6),
    ]


def _build_breakup(comp: Dict, period_label: str, st) -> List:
    """Breakup of tax liability declared (for interest computation)."""
    output = comp.get("output_tax", _zero_tax())
    rows = [[
        period_label,
        _money(output.get("igst", 0)),
        _money(output.get("cgst", 0)),
        _money(output.get("sgst", 0)),
        _money(output.get("cess", 0)),
    ]]
    headers = ["Period", "Integrated tax", "Central tax", "State/UT tax", "Cess"]
    widths = [50 * mm, 32 * mm, 32 * mm, 32 * mm, 30 * mm]
    return [
        Paragraph("Breakup of tax liability declared (for interest computation)",
                  st["section"]),
        _tax_table(headers, rows, widths, money_cols={1, 2, 3, 4}),
        Spacer(1, 6),
    ]


# ---------- Verification ----------

def _build_verification(firm: Dict, st) -> List:
    legal = firm.get("legal_name") or firm.get("name") or ""
    designation = firm.get("designation") or "PROPRIETOR"
    body = (
        "<b>Verification:</b><br/>"
        "I hereby solemnly affirm and declare that the information given herein above "
        "is true and correct to the best of my knowledge and belief and nothing has "
        "been concealed there from."
    )
    sig_block = (
        "<b>Name of Authorized Signatory</b><br/>"
        f"{legal}<br/><br/>"
        "<b>Designation /Status</b><br/>"
        f"{designation}"
    )
    # Two-column layout: Date on left, Signatory block on right
    sig_tbl = Table(
        [[Paragraph("Date: ____________", st["small"]),
          Paragraph(sig_block, st["small"])]],
        colWidths=[90 * mm, 90 * mm],
    )
    sig_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN",  (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [
        Spacer(1, 8),
        Paragraph(body, st["small"]),
        Spacer(1, 6),
        sig_tbl,
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
    story += _build_table_6_1(computation, st, supplies_3_1=supplies_3_1)
    story += _build_breakup(computation, period_label, st)
    story += _build_verification(firm, st)
    doc.build(story)
    return out_path
