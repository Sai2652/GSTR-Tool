"""
GSTR-3B liability computation engine.

Implements the ITC set-off rules per Section 49 of the CGST Act plus
Rule 88A (introduced 2019, amended 2022):

  1. IGST credit must be FULLY exhausted before any CGST/SGST credit
     can be used. Within IGST:
       (a) First applied to IGST output liability (mandatory)
       (b) Any remaining IGST credit then to CGST output, then SGST output
           — taxpayer can choose the cross-order; we use CGST-first by default.
  2. CGST credit can offset CGST output, then IGST output. Never SGST.
  3. SGST/UTGST credit can offset SGST output, then IGST output. Never CGST.
  4. Cess credit can ONLY offset Cess output.

Public API:
    compute_gstr3b(inputs)  ->  result dict (see compute_gstr3b docstring)
"""
from __future__ import annotations

from typing import Any, Dict


# ---------- helpers --------------------------------------------------------

TAX_HEADS = ("igst", "cgst", "sgst", "cess")


def _zero_tax() -> Dict[str, float]:
    return {h: 0.0 for h in TAX_HEADS}


def _round_tax(t: Dict[str, float]) -> Dict[str, float]:
    return {k: round(float(t.get(k, 0.0)), 2) for k in TAX_HEADS}


def _sum_tax(*items: Dict[str, float]) -> Dict[str, float]:
    out = _zero_tax()
    for item in items:
        if not item:
            continue
        for k in TAX_HEADS:
            out[k] += float(item.get(k, 0.0))
    return _round_tax(out)


def _subtract(a: Dict[str, float], b: Dict[str, float]) -> Dict[str, float]:
    return _round_tax({k: float(a.get(k, 0.0)) - float(b.get(k, 0.0)) for k in TAX_HEADS})


# ---------- core set-off step --------------------------------------------

def _apply(credit_pool: Dict[str, float],
           pool_head: str,
           liability: Dict[str, float],
           liability_head: str) -> float:
    """
    Apply available credit from `credit_pool[pool_head]` to reduce
    `liability[liability_head]`. Mutates both dicts. Returns the amount applied.
    """
    avail = max(0.0, float(credit_pool.get(pool_head, 0.0)))
    need = max(0.0, float(liability.get(liability_head, 0.0)))
    used = min(avail, need)
    if used <= 0:
        return 0.0
    credit_pool[pool_head] = round(avail - used, 2)
    liability[liability_head] = round(need - used, 2)
    return round(used, 2)


# ---------- main entry point ---------------------------------------------

def compute_gstr3b(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the full GSTR-3B computation.

    Expected `inputs` shape:
      {
        "output_tax":      {"igst": .., "cgst": .., "sgst": .., "cess": ..},
        "itc_available":   {"igst": .., "cgst": .., "sgst": .., "cess": ..},
        "itc_reversal":    {"igst": .., "cgst": .., "sgst": .., "cess": ..},
        "opening_balance": {"igst": .., "cgst": .., "sgst": .., "cess": ..},
        "cross_order":     "cgst_first"  |  "sgst_first"   (optional, default cgst_first)
      }

    Returns:
      {
        "output_tax":         {...},
        "itc_available":      {...},
        "itc_reversal":       {...},
        "net_itc":            {...},   # available - reversal
        "opening_balance":    {...},
        "credit_pool":        {...},   # net_itc + opening (before set-off)
        "setoff_steps":       [...],   # list of {from, to, amount} for the trail
        "credit_used":        {"igst": .., "cgst": .., "sgst": .., "cess": ..},
        "cash_payable":       {...},   # remaining after set-off
        "closing_balance":    {...},   # credit_pool - credit_used
        "total_output":       float,
        "total_itc":          float,
        "total_cash_payable": float,
        "total_credit_used":  float,
      }
    """
    output = _round_tax(inputs.get("output_tax") or {})
    # RCM tax payable (3.1(d) on portal) — MUST be paid in cash per Section 49.
    # We exclude it from set-off and treat it as a separate cash liability.
    rcm_tax = _round_tax(inputs.get("rcm_tax_payable") or {})
    itc_avail = _round_tax(inputs.get("itc_available") or {})
    itc_rev = _round_tax(inputs.get("itc_reversal") or {})
    opening = _round_tax(inputs.get("opening_balance") or {})
    cross_order = inputs.get("cross_order", "cgst_first")

    # "Other than RCM" output liability = total output - rcm portion.
    # The set-off engine works only on this.
    output_other = _subtract(output, rcm_tax)
    for k in TAX_HEADS:
        if output_other[k] < 0:
            output_other[k] = 0.0

    # Net ITC = available - reversal (per Table 4)
    net_itc = _subtract(itc_avail, itc_rev)
    # Clamp negative net ITC to 0 — extra reversal carries forward separately
    # but for simplicity we don't model that here
    for k in TAX_HEADS:
        if net_itc[k] < 0:
            net_itc[k] = 0.0

    # Total credit pool available for set-off this period
    credit_pool = _sum_tax(net_itc, opening)
    credit_initial = dict(credit_pool)  # snapshot for "used" calculation later

    # Working copy of liability (RCM excluded — applied separately as cash)
    liability = dict(output_other)
    setoff_steps = []

    def step(from_head: str, to_head: str, label: str):
        used = _apply(credit_pool, from_head, liability, to_head)
        if used > 0:
            setoff_steps.append({
                "from": from_head.upper(),
                "to": to_head.upper(),
                "amount": used,
                "rule": label,
            })

    # ----- IGST credit must be exhausted FIRST (Rule 88A) -----
    # Step 1a: IGST credit -> IGST output (mandatory priority)
    step("igst", "igst", "IGST credit to IGST liability (Rule 88A — priority)")

    # Step 1b + 1c: Remaining IGST credit -> CGST then SGST (or reversed)
    if cross_order == "sgst_first":
        step("igst", "sgst", "Remaining IGST credit to SGST liability")
        step("igst", "cgst", "Remaining IGST credit to CGST liability")
    else:
        step("igst", "cgst", "Remaining IGST credit to CGST liability")
        step("igst", "sgst", "Remaining IGST credit to SGST liability")

    # ----- Now CGST credit -----
    # CGST -> CGST first, then IGST. Never SGST.
    step("cgst", "cgst", "CGST credit to CGST liability")
    step("cgst", "igst", "Remaining CGST credit to IGST liability")

    # ----- Now SGST credit -----
    # SGST -> SGST first, then IGST. Never CGST.
    step("sgst", "sgst", "SGST credit to SGST liability")
    step("sgst", "igst", "Remaining SGST credit to IGST liability")

    # ----- Cess credit only for Cess -----
    step("cess", "cess", "Cess credit to Cess liability")

    # Credit used = initial pool - remaining credit pool
    credit_used = _subtract(credit_initial, credit_pool)

    # Anything left in liability is cash payable
    cash_payable = _round_tax(liability)

    total_output = round(sum(output.values()), 2)
    total_itc = round(sum(net_itc.values()) + sum(opening.values()), 2)
    total_cash = round(sum(cash_payable.values()), 2)
    total_used = round(sum(credit_used.values()), 2)

    return {
        "output_tax": output,
        "itc_available": itc_avail,
        "itc_reversal": itc_rev,
        "net_itc": net_itc,
        "opening_balance": opening,
        "credit_pool": credit_initial,
        "setoff_steps": setoff_steps,
        "credit_used": credit_used,
        "cash_payable": cash_payable,
        "closing_balance": _round_tax(credit_pool),  # what's left after set-off
        "total_output": total_output,
        "total_itc": total_itc,
        "total_cash_payable": total_cash,
        "total_credit_used": total_used,
        "cross_order": cross_order,
    }
