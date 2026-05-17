"""
GSTR-1 Generator — Main Entry Point
====================================
Usage:
    python main.py --input <sales_file> --gstin <firm_gstin> --period MMYYYY \
                   --firm-name "Firm Name" --output-dir ./output

Reads a sales sheet (xlsx, xls, or Tally TSV-as-xls), validates and corrects
GSTINs, consolidates invoices, generates an Excel report, and emits a
GSTR-1 JSON ready for the GST portal offline tool.
"""
import argparse
import json
import sys
from pathlib import Path

from data_reader import read_sales
from validator import validate_dataframe
from consolidator import consolidate_invoices, classify_invoices
from json_builder import build_gstr1_json
from report_builder import build_report


def process_firm(
    input_path: str,
    firm_gstin: str,
    return_period: str,
    firm_name: str,
    output_dir: str,
    b2cl_threshold: float = 250000.0,
):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c if c.isalnum() else "_" for c in firm_name)
    json_path = output_dir / f"GSTR1_{safe_name}_{return_period}.json"
    report_path = output_dir / f"GSTR1_Report_{safe_name}_{return_period}.xlsx"

    print(f"\n{'='*70}")
    print(f"Processing: {firm_name}")
    print(f"GSTIN: {firm_gstin}  |  Period: {return_period}")
    print(f"Source: {input_path}")
    print(f"{'='*70}")

    # Step 1: Read
    df = read_sales(input_path)
    print(f"  Loaded {len(df)} line-item rows")

    # Step 2: Validate
    firm_state = firm_gstin[:2] if len(firm_gstin) >= 2 else "29"
    df, exceptions, master = validate_dataframe(df, firm_state_code=firm_state)
    print(f"  Master list: {len(master)} unique customer GSTINs")
    print(f"  Exceptions:  {len(exceptions)} rows flagged")

    # Step 3: Consolidate
    invoices = consolidate_invoices(df)
    print(f"  Consolidated into {len(invoices)} invoices")

    # Step 4: Classify
    buckets = classify_invoices(invoices, b2cl_threshold=b2cl_threshold)
    print(f"  B2B:  {len(buckets['b2b'])} invoices")
    print(f"  B2CL: {len(buckets['b2cl'])} invoices")
    print(f"  B2CS: {len(buckets['b2cs'])} invoices")

    # Step 5: Build JSON
    gstr1 = build_gstr1_json(firm_gstin, return_period, buckets, invoices)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(gstr1, f, indent=2, ensure_ascii=False)
    print(f"  -> JSON:   {json_path}")

    # Step 6: Build Excel report
    build_report(firm_name, firm_gstin, return_period, invoices, buckets,
                 exceptions, str(report_path))
    print(f"  -> Report: {report_path}")

    return {
        "json": str(json_path),
        "report": str(report_path),
        "stats": {
            "rows": len(df),
            "invoices": len(invoices),
            "b2b": len(buckets["b2b"]),
            "b2cl": len(buckets["b2cl"]),
            "b2cs": len(buckets["b2cs"]),
            "exceptions": len(exceptions),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="GSTR-1 JSON Generator")
    parser.add_argument("--input", required=True, help="Path to sales Excel/TSV file")
    parser.add_argument("--gstin", required=True, help="Firm GSTIN")
    parser.add_argument("--period", required=True, help="Return period MMYYYY (e.g. 042026)")
    parser.add_argument("--firm-name", required=True, help="Firm display name")
    parser.add_argument("--output-dir", default="./output", help="Output directory")
    parser.add_argument("--b2cl-threshold", type=float, default=250000.0,
                        help="B2CL invoice value threshold (default 2.5L)")
    args = parser.parse_args()

    result = process_firm(
        input_path=args.input,
        firm_gstin=args.gstin,
        return_period=args.period,
        firm_name=args.firm_name,
        output_dir=args.output_dir,
        b2cl_threshold=args.b2cl_threshold,
    )

    print(f"\n{'='*70}")
    print("DONE")
    print(json.dumps(result["stats"], indent=2))
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
