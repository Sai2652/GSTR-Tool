"""
GSTR-1 Generator — Web UI
Flask app with auth, firm profiles, batch processing, file management.
"""
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from flask import (
    Flask, render_template, request, jsonify, redirect,
    url_for, send_from_directory, session,
)
from werkzeug.utils import secure_filename

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent / "src"))

from data_reader import read_sales
from validator import validate_dataframe
from consolidator import consolidate_invoices, classify_invoices
from json_builder import build_gstr1_json
from report_builder import build_report
from gstin_validator import validate_gstin
from preflight import run_all_preflight_checks
from gstr2b_reader import parse_gstr2b
from gstr3b_compute import compute_gstr3b
from gstr3b_excel_writer import write_gstr3b_excel
from gstr3b_pdf_writer import write_gstr3b_pdf
from project_store import ProjectStore, KIND_LABELS
import io

from firm_store import FirmStore
from period_utils import normalize_period, period_to_label, period_bounds
from auth import register_auth_routes, login_required
from customer_store import CustomerStore
from batch_cache import BatchStateCache
import file_manager


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

# Secret key — MUST be set in production
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    if os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError(
            "FLASK_SECRET_KEY environment variable must be set in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    app.secret_key = "dev-only-secret-do-not-use-in-production"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV") == "production",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

# Storage location — DATA_ROOT can be overridden via env (used on Render)
DATA_ROOT = Path(os.environ.get("DATA_ROOT", str(ROOT)))
UPLOAD_DIR = DATA_ROOT / "uploads"
OUTPUT_DIR = DATA_ROOT / "output"
DATA_DIR = DATA_ROOT / "data"
for d in (UPLOAD_DIR, OUTPUT_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)

firms = FirmStore(DATA_DIR / "firms.json")
customers = CustomerStore(DATA_DIR / "customers.json")
projects = ProjectStore()
batch_cache = BatchStateCache(ttl_seconds=3600)
ALLOWED_EXT = {".xlsx", ".xls", ".csv", ".tsv"}

register_auth_routes(app)


# ---------- Pages ----------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html",
                           user=session.get("user"),
                           recent_outputs=_recent_outputs(limit=8),
                           current_year=datetime.now().year)

@app.route("/gstr1")
@login_required
def gstr1_page():
    return render_template("index.html",
                           firms=firms.list_firms(),
                           default_period=_default_period(),
                           user=session.get("user"))


@app.route("/firms")
@login_required
def firms_page():
    return render_template("firms.html",
                           firms=firms.list_firms(),
                           user=session.get("user"))


@app.route("/files")
@login_required
def files_page():
    summary = file_manager.storage_summary(UPLOAD_DIR, OUTPUT_DIR)
    try:
        all_projects = projects.list_all()
        for p in all_projects:
            p["files"] = projects.list_files(p["id"])
    except Exception as e:
        app.logger.warning(f"Could not load Supabase projects: {e}")
        all_projects = []
    return render_template("files.html",
                           summary=summary,
                           projects=all_projects,
                           kind_labels=KIND_LABELS,
                           user=session.get("user"))


@app.route("/customers")
@login_required
def customers_page():
    return render_template("customers.html",
                           customers=customers.list_all(),
                           user=session.get("user"))

@app.route("/gstr3b")
@login_required
def gstr3b_page():
    return render_template("gstr3b.html",
                           firms=firms.list_firms(),
                           default_period=_default_period(),
                           user=session.get("user"))


@app.route("/api/gstr3b/parse", methods=["POST"])
@login_required
def api_gstr3b_parse():
    """Accept GSTR-2B Excel upload, parse it, return summary JSON."""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "Empty filename"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in {".xlsx", ".xls"}:
        return jsonify({"ok": False, "error": f"Unsupported extension {ext}"}), 400

    # Save under uploads/ for traceability (Render free tier wipes on restart;
    # that's fine for this transient file)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = secure_filename(f.filename)
    save_path = UPLOAD_DIR / f"gstr2b_{stamp}_{safe}"
    f.save(save_path)

    try:
        data = parse_gstr2b(save_path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Parse failed: {e}"}), 500

    return jsonify({"ok": True, "data": data})


@app.route("/api/gstr3b/output-from-gstr1")
@login_required
def api_gstr3b_output_from_gstr1():
    """
    Look up the most recent GSTR-1 JSON for the given firm + period in the
    Supabase project archive (falls back to local disk if Supabase miss).
    """
    firm_id = (request.args.get("firm") or "").strip()
    period = (request.args.get("period") or "").strip()

    if not firm_id or not period:
        return jsonify({"ok": False, "error": "firm and period required"}), 400

    firm = firms.get(firm_id)
    if not firm:
        return jsonify({"ok": False, "error": "Firm not found"}), 404

    gstr1 = None
    source_file = ""

    # ----- Try Supabase first (the permanent archive) -----
    try:
        firm_uuid = firms.get_uuid(firm["gstin"])
        if firm_uuid:
            proj_resp = (projects._client.table("projects").select("*")
                         .eq("firm_id", firm_uuid).eq("period", period)
                         .limit(1).execute())
            if proj_resp.data:
                project_id = proj_resp.data[0]["id"]
                files = projects.list_files(project_id)
                json_file = next((f for f in files if f["kind"] == "gstr1_json"), None)
                if json_file:
                    raw = projects.download_file(json_file["storage_path"])
                    gstr1 = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                    source_file = json_file["filename"]
    except Exception as e:
        app.logger.warning(f"Supabase GSTR-1 lookup failed: {e}")

    # ----- Fall back to local disk (for current-session files) -----
    if gstr1 is None:
        safe_name = "".join(c if c.isalnum() else "_" for c in firm["name"])
        # Search recursively because generated files live in batch_<ts>/ subdirs,
        # and use the actual filename pattern GSTR1_<safe>_<period>.json
        candidates = sorted(
            OUTPUT_DIR.rglob(f"GSTR1_{safe_name}_{period}.json"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            try:
                with open(candidates[0], "r", encoding="utf-8") as fh:
                    gstr1 = json.load(fh)
                source_file = candidates[0].name
            except Exception as e:
                return jsonify({"ok": False, "error": f"Could not read JSON: {e}"}), 500

    if gstr1 is None:
        return jsonify({"ok": True, "found": False})

    totals = _sum_gstr1_output(gstr1)
    breakdown = _gstr1_to_3b_breakdown(gstr1)
    return jsonify({
        "ok": True,
        "found": True,
        "source_file": source_file,
        "totals": totals,
        "supplies_3_1": breakdown["supplies_3_1"],
        "inter_state_3_2": breakdown["inter_state_3_2"],
    })

    firm = firms.get(firm_id)
    if not firm:
        return jsonify({"ok": False, "error": "Firm not found"}), 404

    safe_name = "".join(c if c.isalnum() else "_" for c in firm["name"])

    # Find the latest GSTR-1 JSON file matching the firm + period.
    # Pattern used by the existing GSTR-1 export: <safe>_<period>_<HHMMSS>.json
    candidates = sorted(
        OUTPUT_DIR.glob(f"{safe_name}_{period}_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return jsonify({"ok": True, "found": False})

    latest = candidates[0]
    try:
        with open(latest, "r", encoding="utf-8") as fh:
            gstr1 = json.load(fh)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not read JSON: {e}"}), 500

    totals = _sum_gstr1_output(gstr1)
    return jsonify({
        "ok": True,
        "found": True,
        "source_file": latest.name,
        "totals": totals,
    })


def _sum_gstr1_output(g) -> dict:
    """
    Sum IGST / CGST / SGST / Cess from a GSTR-1 JSON object.
    Covers b2b, b2cl, b2cs, cdnr, cdnur (credit notes are subtracted).
    """
    totals = {"igst": 0.0, "cgst": 0.0, "sgst": 0.0, "cess": 0.0}

    def add_itm(itm_det, sign=1):
        if not itm_det:
            return
        totals["igst"] += sign * float(itm_det.get("iamt", 0) or 0)
        totals["cgst"] += sign * float(itm_det.get("camt", 0) or 0)
        totals["sgst"] += sign * float(itm_det.get("samt", 0) or 0)
        totals["cess"] += sign * float(itm_det.get("csamt", 0) or 0)

    # b2b: list of {ctin, inv:[{itms:[{itm_det}]}]}
    for c in (g.get("b2b") or []):
        for inv in c.get("inv", []):
            for itm in inv.get("itms", []):
                add_itm(itm.get("itm_det"))

    # b2cl: list of {pos, inv:[{itms:[{itm_det}]}]}
    for state in (g.get("b2cl") or []):
        for inv in state.get("inv", []):
            for itm in inv.get("itms", []):
                add_itm(itm.get("itm_det"))

    # b2cs: list of summary rows with iamt/camt/samt/csamt directly
    for r in (g.get("b2cs") or []):
        totals["igst"] += float(r.get("iamt", 0) or 0)
        totals["cgst"] += float(r.get("camt", 0) or 0)
        totals["sgst"] += float(r.get("samt", 0) or 0)
        totals["cess"] += float(r.get("csamt", 0) or 0)

    # cdnr: credit notes - subtract from output
    for c in (g.get("cdnr") or []):
        for nt in c.get("nt", []):
            note_type = (nt.get("ntty") or "").upper()
            sign = -1 if note_type == "C" else 1   # Credit note reduces output
            for itm in nt.get("itms", []):
                add_itm(itm.get("itm_det"), sign=sign)

    # cdnur: unregistered credit/debit notes
    for nt in (g.get("cdnur") or []):
        note_type = (nt.get("ntty") or "").upper()
        sign = -1 if note_type == "C" else 1
        for itm in nt.get("itms", []):
            add_itm(itm.get("itm_det"), sign=sign)

    return {k: round(max(0.0, v), 2) for k, v in totals.items()}


def _gstr1_to_3b_breakdown(g: dict) -> dict:
    """
    Derive GSTR-3B Tables 3.1 and 3.2 breakdown from a GSTR-1 JSON.

    Returns:
      {
        "supplies_3_1": {
          "3.1.a": {tx, igst, cgst, sgst, cess},   # regular taxable outward
          "3.1.b": {...},                          # zero-rated (exp + SEZ)
          "3.1.c": {...},                          # nil-rated + exempt outward
          "3.1.d": {...},                          # left as zeros — populated
                                                   # from GSTR-2B RCM elsewhere
          "3.1.e": {...},                          # non-GST outward
        },
        "inter_state_3_2": [
          {"kind": "urd", "pos": "29", "tx": .., "igst": ..},
          ...
        ],
      }
    """
    z = {"tx": 0.0, "igst": 0.0, "cgst": 0.0, "sgst": 0.0, "cess": 0.0}
    a = dict(z); b = dict(z); c = dict(z); d = dict(z); e = dict(z)

    def add(row, itm_det, sign=1):
        row["igst"] += sign * float(itm_det.get("iamt", 0) or 0)
        row["cgst"] += sign * float(itm_det.get("camt", 0) or 0)
        row["sgst"] += sign * float(itm_det.get("samt", 0) or 0)
        row["cess"] += sign * float(itm_det.get("csamt", 0) or 0)
        row["tx"]   += sign * float(itm_det.get("txval", 0) or 0)

    # 3.1(a) — regular B2B (rchrg=N), B2CL, B2CS.
    # SEZ supplies (inv_typ='SEWP'/'SEWOP') and Deemed Exports ('DE') in B2B
    # are zero-rated → route to 3.1(b) instead of 3.1(a).
    ZERO_RATED_INV_TYPES = {"SEWP", "SEWOP", "SEWP/B", "SEWOP/B", "DE"}
    for ctin in (g.get("b2b") or []):
        for inv in ctin.get("inv", []):
            if (inv.get("rchrg") or "N").upper() == "Y":
                continue  # RCM B2B counts as 3.1(d) for the recipient — skip outward
            target = b if (inv.get("inv_typ") or "R").upper() in ZERO_RATED_INV_TYPES else a
            for itm in inv.get("itms", []):
                add(target, itm.get("itm_det", {}))
    for state in (g.get("b2cl") or []):
        for inv in state.get("inv", []):
            for itm in inv.get("itms", []):
                add(a, itm.get("itm_det", {}))
    for r in (g.get("b2cs") or []):
        # B2CS rows carry totals directly, not nested itm_det
        a["igst"] += float(r.get("iamt", 0) or 0)
        a["cgst"] += float(r.get("camt", 0) or 0)
        a["sgst"] += float(r.get("samt", 0) or 0)
        a["cess"] += float(r.get("csamt", 0) or 0)
        a["tx"]   += float(r.get("txval", 0) or 0)

    # Credit/debit notes for registered customers (subtract for C, add for D)
    for ctin in (g.get("cdnr") or []):
        for nt in ctin.get("nt", []):
            sign = -1 if (nt.get("ntty") or "").upper() == "C" else 1
            if (nt.get("rchrg") or "N").upper() == "Y":
                continue
            note_target = b if (nt.get("inv_typ") or "R").upper() in ZERO_RATED_INV_TYPES else a
            for itm in nt.get("itms", []):
                add(note_target, itm.get("itm_det", {}), sign=sign)
    for nt in (g.get("cdnur") or []):
        sign = -1 if (nt.get("ntty") or "").upper() == "C" else 1
        ur_typ = (nt.get("typ") or "").upper()
        target = b if ur_typ in ("EXPWP", "EXPWOP") else a
        for itm in nt.get("itms", []):
            add(target, itm.get("itm_det", {}), sign=sign)

    # 3.1(b) — zero-rated: exports + SEZ
    for blk in (g.get("exp") or []):
        for inv in blk.get("inv", []):
            for itm in inv.get("itms", []):
                add(b, itm.get("itm_det", itm))  # exp items have flat keys

    # 3.1(c) — nil-rated + exempt; 3.1(e) — non-GST
    for entry in ((g.get("nil") or {}).get("inv") or []):
        c["tx"] += float(entry.get("nil_amt", 0) or 0)
        c["tx"] += float(entry.get("expt_amt", 0) or 0)
        e["tx"] += float(entry.get("ngsup_amt", 0) or 0)

    # Round + clamp
    def fix(r):
        return {k: round(max(0.0, v), 2) for k, v in r.items()}
    supplies_3_1 = {
        "3.1.a": fix(a), "3.1.b": fix(b), "3.1.c": fix(c),
        "3.1.d": fix(d), "3.1.e": fix(e),
    }

    # 3.2 — Inter-state to URD by POS: derived from B2CL + interstate B2CS rows
    pos_urd = {}
    for state in (g.get("b2cl") or []):
        pos = str(state.get("pos") or "").zfill(2)
        agg = pos_urd.setdefault(pos, {"tx": 0.0, "igst": 0.0})
        for inv in state.get("inv", []):
            for itm in inv.get("itms", []):
                det = itm.get("itm_det", {})
                agg["tx"]   += float(det.get("txval", 0) or 0)
                agg["igst"] += float(det.get("iamt", 0) or 0)
    for r in (g.get("b2cs") or []):
        if (r.get("sply_ty") or "").upper() != "INTER":
            continue
        pos = str(r.get("pos") or "").zfill(2)
        agg = pos_urd.setdefault(pos, {"tx": 0.0, "igst": 0.0})
        agg["tx"]   += float(r.get("txval", 0) or 0)
        agg["igst"] += float(r.get("iamt", 0) or 0)

    inter_state_3_2 = [
        {"kind": "urd", "pos": pos,
         "tx": round(v["tx"], 2), "igst": round(v["igst"], 2)}
        for pos, v in sorted(pos_urd.items())
        if round(v["tx"], 2) or round(v["igst"], 2)
    ]
    return {"supplies_3_1": supplies_3_1, "inter_state_3_2": inter_state_3_2}


@app.route("/api/gstr3b/compute", methods=["POST"])
@login_required
def api_gstr3b_compute():
    """Run the set-off computation and return the result JSON."""
    payload = request.get_json(silent=True) or {}
    try:
        result = compute_gstr3b(payload)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Compute failed: {e}"}), 500
    return jsonify({"ok": True, "result": result})


@app.route("/api/gstr3b/download", methods=["POST"])
@login_required
def api_gstr3b_download():
    """Generate the formatted Excel and return it as a download."""
    payload = request.get_json(silent=True) or {}
    firm = payload.get("firm") or {}
    period = payload.get("period") or ""
    inputs = payload.get("inputs") or {}
    gstr2b = payload.get("gstr2b") or {}

    # Re-run the compute on the server (don't trust client-side numbers)
    try:
        computation = compute_gstr3b(inputs)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Compute failed: {e}"}), 500

    period_label = period_to_label(period) if period else period
    safe = "".join(c if c.isalnum() else "_" for c in (firm.get("name") or "firm"))
    out_path = OUTPUT_DIR / f"GSTR3B_{safe}_{period}_{datetime.now().strftime('%H%M%S')}.xlsx"

    try:
        write_gstr3b_excel(out_path, firm, period_label, gstr2b, computation)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Excel generation failed: {e}"}), 500
# ----- Save GSTR-3B outputs to Supabase project archive -----
    try:
        firm_gstin_str = (firm.get("gstin") or "").upper()
        firm_uuid = firms.get_uuid(firm_gstin_str)
        if firm_uuid and period:
            project = projects.get_or_create(
                firm_uuid=firm_uuid,
                period=period,
                period_label=period_label,
            )
            projects.add_file_from_path(
                project["id"], "gstr3b_excel",
                out_path, filename=out_path.name)
            gstr2b_filename = (gstr2b or {}).get("filename")
            if gstr2b_filename:
                matches = sorted(UPLOAD_DIR.glob(f"gstr2b_*_{gstr2b_filename}"),
                                 key=lambda p: p.stat().st_mtime, reverse=True)
                if matches:
                    projects.add_file_from_path(
                        project["id"], "gstr2b_input",
                        matches[0], filename=gstr2b_filename)
    except Exception as _e:
        app.logger.warning(f"Supabase GSTR-3B upload failed: {_e}")
    return send_from_directory(
        out_path.parent,
        out_path.name,
        as_attachment=True,
        download_name=out_path.name,
    )


@app.route("/api/projects/status")
@login_required
def api_project_status():
    """Get the project (filing status + files) for a firm + period."""
    firm_id = (request.args.get("firm") or "").strip()
    period = (request.args.get("period") or "").strip()
    if not firm_id or not period:
        return jsonify({"ok": False, "error": "firm and period required"}), 400
    try:
        firm = firms.get(firm_id)
        firm_uuid = firm["id"] if firm else None
        if not firm_uuid:
            return jsonify({"ok": True, "found": False})
        proj = projects.find_project(firm_uuid, period)
        if not proj:
            return jsonify({"ok": True, "found": False})
        files = projects.list_files(proj["id"])
        meta = proj.get("meta") or {}
        filings = meta.get("filings") or {}
        return jsonify({
            "ok": True,
            "found": True,
            "project_id": proj["id"],
            "period": proj["period"],
            "period_label": proj.get("period_label"),
            "filings": filings,
            "files": [{
                "id": f["id"], "kind": f["kind"], "filename": f["filename"],
                "size_bytes": f.get("size_bytes"),
            } for f in files],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/projects/<project_id>/mark-filed", methods=["POST"])
@login_required
def api_project_mark_filed(project_id):
    payload = request.get_json(silent=True) or {}
    return_type = (payload.get("return_type") or "").strip().lower()
    arn = (payload.get("arn") or "").strip()
    if return_type not in ("gstr1", "gstr3b"):
        return jsonify({"ok": False, "error": "return_type must be gstr1 or gstr3b"}), 400
    try:
        proj = projects.mark_filed(project_id, return_type, arn=arn or None)
        return jsonify({"ok": True, "project": proj})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/projects/<project_id>/unlock", methods=["POST"])
@login_required
def api_project_unlock(project_id):
    payload = request.get_json(silent=True) or {}
    return_type = (payload.get("return_type") or "").strip().lower()
    if return_type not in ("gstr1", "gstr3b"):
        return jsonify({"ok": False, "error": "return_type must be gstr1 or gstr3b"}), 400
    try:
        proj = projects.mark_unfiled(project_id, return_type)
        return jsonify({"ok": True, "project": proj})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/projects/file/<file_id>/download")
@login_required
def api_project_file_download(file_id):
    """Return a signed URL or stream a project file by id."""
    try:
        rec = projects.get_file_by_id(file_id)
        if not rec:
            return jsonify({"ok": False, "error": "File not found"}), 404
        data = projects.download_file(rec["storage_path"])
        from flask import Response
        return Response(
            data, mimetype=_guess_mime_simple(rec["filename"]),
            headers={"Content-Disposition": f'attachment; filename="{rec["filename"]}"'},
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _guess_mime_simple(filename: str) -> str:
    n = filename.lower()
    if n.endswith(".json"): return "application/json"
    if n.endswith(".xlsx"): return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if n.endswith(".pdf"):  return "application/pdf"
    return "application/octet-stream"


@app.route("/api/gstr3b/download-pdf", methods=["POST"])
@login_required
def api_gstr3b_download_pdf():
    """Render a portal-style GSTR-3B PDF and return it as a download."""
    payload = request.get_json(silent=True) or {}
    firm = payload.get("firm") or {}
    # Enrich firm dict with the canonical Supabase record (legal_name, arn,
    # designation, etc.) — the client only sends id/name/gstin.
    firm_id = firm.get("id") or firm.get("gstin")
    if firm_id:
        full = firms.get(firm_id) or {}
        # Merge: server fields overwrite client fields
        for k, v in full.items():
            if v not in (None, ""):
                firm[k] = v
    period = payload.get("period") or ""
    inputs = payload.get("inputs") or {}
    gstr2b = payload.get("gstr2b") or {}
    # Optional row-level breakdowns the client can pass to populate Tables 3.1, 3.1.1,
    # 3.2, 5, 5.1. If absent, those rows render as zero.
    supplies_3_1     = payload.get("supplies_3_1")
    ecom_3_1_1       = payload.get("ecom_3_1_1")
    inter_state_3_2  = payload.get("inter_state_3_2")
    exempt_inward_5  = payload.get("exempt_inward_5")
    interest_late_fee = payload.get("interest_late_fee")
    reversal_buckets = payload.get("reversal_buckets") or {}

    # If client supplied invoice-level reversal buckets, overlay them onto
    # the Table 4 lines so 4(B)(1)/(B)(2) reflect the user's classification.
    if reversal_buckets and isinstance(gstr2b.get("table4"), dict):
        t4 = gstr2b["table4"]
        for src_key, target_key in [("4B1", "4B1_rules_38_42_43_17_5"),
                                    ("4B2", "4B2_others")]:
            src = reversal_buckets.get(src_key) or {}
            if any(float(src.get(h, 0) or 0) for h in ("igst", "cgst", "sgst", "cess")):
                t4[target_key] = {h: round(float(src.get(h, 0) or 0), 2)
                                  for h in ("igst", "cgst", "sgst", "cess")}
        # Recompute B totals and C net
        b_total = {h: round(t4["4B1_rules_38_42_43_17_5"].get(h, 0) +
                            t4["4B2_others"].get(h, 0), 2)
                   for h in ("igst", "cgst", "sgst", "cess")}
        a_total = t4.get("4A_total") or {h: 0 for h in ("igst","cgst","sgst","cess")}
        t4["4B_total"] = b_total
        t4["4C_net_itc"] = {h: round(float(a_total.get(h, 0)) - b_total[h], 2)
                            for h in ("igst", "cgst", "sgst", "cess")}

    # Derive 3.1(d) inward RCM from GSTR-2B BEFORE compute, so the RCM portion
    # can be passed as a separate cash-only liability into the set-off engine.
    if supplies_3_1 is None:
        supplies_3_1 = {}
    _existing_d = supplies_3_1.get("3.1.d") or {}
    _all_zero = all(float(_existing_d.get(k, 0) or 0) == 0
                    for k in ("tx", "igst", "cgst", "sgst", "cess"))
    if _all_zero:
        rcm_in = ((gstr2b or {}).get("itc_available") or {}).get("reverse_charge") or {}
        rcm_tx_val = 0.0
        for inv in (gstr2b or {}).get("invoices") or []:
            if (inv.get("category") or "") == "reverse_charge":
                rcm_tx_val += float(inv.get("taxable_value") or 0)
        supplies_3_1["3.1.d"] = {
            "tx": round(rcm_tx_val, 2),
            "igst": float(rcm_in.get("igst", 0) or 0),
            "cgst": float(rcm_in.get("cgst", 0) or 0),
            "sgst": float(rcm_in.get("sgst", 0) or 0),
            "cess": float(rcm_in.get("cess", 0) or 0),
        }

    # Feed RCM tax payable into the compute as a separate cash-only bucket.
    rcm_d = supplies_3_1.get("3.1.d") or {}
    inputs.setdefault("rcm_tax_payable", {
        h: float(rcm_d.get(h, 0) or 0) for h in ("igst", "cgst", "sgst", "cess")
    })
    # Total output_tax should INCLUDE RCM (3.1(a) + 3.1(d)) per portal — the
    # set-off engine internally subtracts RCM before applying ITC.
    out = inputs.get("output_tax") or {}
    for h in ("igst", "cgst", "sgst", "cess"):
        out[h] = float(out.get(h, 0) or 0) + float(rcm_d.get(h, 0) or 0)
    inputs["output_tax"] = out

    try:
        computation = compute_gstr3b(inputs)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Compute failed: {e}"}), 500

    period_label = period_to_label(period) if period else period
    safe = "".join(c if c.isalnum() else "_" for c in (firm.get("name") or "firm"))
    out_path = OUTPUT_DIR / f"GSTR3B_{safe}_{period}_{datetime.now().strftime('%H%M%S')}.pdf"

    try:
        write_gstr3b_pdf(
            out_path, firm, period_label, gstr2b, computation,
            supplies_3_1=supplies_3_1, ecom_3_1_1=ecom_3_1_1,
            inter_state_3_2=inter_state_3_2,
            exempt_inward_5=exempt_inward_5,
            interest_late_fee=interest_late_fee,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"PDF generation failed: {e}"}), 500

    # Save to Supabase project archive (best-effort)
    try:
        firm_gstin_str = (firm.get("gstin") or "").upper()
        firm_uuid = firms.get_uuid(firm_gstin_str)
        if firm_uuid and period:
            project = projects.get_or_create(
                firm_uuid=firm_uuid, period=period, period_label=period_label)
            projects.add_file_from_path(
                project["id"], "gstr3b_pdf", out_path, filename=out_path.name)
    except Exception as _e:
        app.logger.warning(f"Supabase GSTR-3B PDF upload failed: {_e}")

    return send_from_directory(
        out_path.parent, out_path.name,
        as_attachment=True, download_name=out_path.name,
    )


# ---------- Firm API -------------------------------------------------------
@app.route("/api/firms", methods=["GET"])
def api_list_firms():
    return jsonify(firms.list_firms())


@app.route("/api/firms", methods=["POST"])
def api_add_firm():
    data = request.json or {}
    try:
        firm = firms.add(name=data.get("name", ""),
                         gstin=data.get("gstin", ""),
                         legal_name=data.get("legal_name", ""))
        return jsonify({"ok": True, "firm": firm})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/firms/<firm_id>", methods=["DELETE"])
def api_delete_firm(firm_id):
    return jsonify({"ok": firms.delete(firm_id)})


@app.route("/api/firms/<firm_id>", methods=["PATCH"])
def api_update_firm(firm_id):
    data = request.json or {}
    try:
        firm = firms.update(firm_id, name=data.get("name"),
                            legal_name=data.get("legal_name"))
        return jsonify({"ok": True, "firm": firm})
    except KeyError as e:
        return jsonify({"ok": False, "error": str(e)}), 404


# ---------- GSTIN check ----------------------------------------------------
@app.route("/api/check_gstin", methods=["POST"])
def api_check_gstin():
    data = request.json or {}
    g = (data.get("gstin") or "").strip().upper().replace(" ", "")
    result = validate_gstin(g)
    # If valid, also return any cached name
    if result.get("valid"):
        cached_name = customers.get_name(result["gstin"])
        result["cached_name"] = cached_name
    else:
        result["cached_name"] = ""
    return jsonify(result)


# ---------- Customer cache API --------------------------------------------
@app.route("/api/customers", methods=["GET"])
def api_list_customers():
    return jsonify({
        "count": customers.count(),
        "customers": customers.list_all(),
    })


@app.route("/api/customers/<gstin>", methods=["GET"])
def api_get_customer(gstin):
    rec = customers.get(gstin)
    if rec:
        return jsonify({"ok": True, "gstin": gstin.upper(), **rec})
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.route("/api/customers/<gstin>", methods=["PATCH"])
def api_update_customer(gstin):
    data = request.json or {}
    new_name = (data.get("name") or "").strip()
    if not new_name:
        return jsonify({"ok": False, "error": "Name required"}), 400
    ok = customers.update_name(gstin, new_name)
    return jsonify({"ok": ok})


@app.route("/api/customers/<gstin>", methods=["DELETE"])
def api_delete_customer(gstin):
    return jsonify({"ok": customers.delete(gstin)})


@app.route("/api/customers/clear", methods=["POST"])
def api_clear_customers():
    n = customers.clear_all()
    return jsonify({"ok": True, "deleted": n})


# ---------- File management API --------------------------------------------
@app.route("/api/files/clear", methods=["POST"])
def api_clear_files():
    data = request.json or {}
    target = data.get("target", "")
    counts = {"uploads": 0, "outputs": 0}
    if target in ("uploads", "all"):
        counts["uploads"] = file_manager.clear_directory(UPLOAD_DIR)
    if target in ("outputs", "all"):
        counts["outputs"] = file_manager.clear_directory(OUTPUT_DIR)
    return jsonify({"ok": True, "deleted": counts})


@app.route("/api/files/delete", methods=["POST"])
def api_delete_file():
    data = request.json or {}
    target = data.get("target")
    rel = data.get("path", "")
    base = UPLOAD_DIR if target == "uploads" else OUTPUT_DIR if target == "outputs" else None
    if not base:
        return jsonify({"ok": False, "error": "Invalid target"}), 400
    return jsonify({"ok": file_manager.delete_one(base, rel)})


# ---------- Batch processing ----------------------------------------------
@app.route("/api/process", methods=["POST"])
def api_process():
    raw_period = request.form.get("period", "")
    try:
        period = normalize_period(raw_period)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    try:
        jobs = json.loads(request.form.get("jobs", "[]"))
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "Invalid jobs payload"}), 400

    if not jobs:
        return jsonify({"ok": False, "error": "No jobs provided"}), 400

    results = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir = OUTPUT_DIR / f"batch_{timestamp}"
    batch_dir.mkdir(exist_ok=True)
    period_start, period_end = period_bounds(period)

    for job in jobs:
        firm_id = job.get("firm_id")
        file_field = job.get("file_field")
        firm = firms.get(firm_id)
        if not firm:
            results.append({"firm_id": firm_id, "ok": False, "error": "Firm not found"})
            continue
        if file_field not in request.files:
            results.append({"firm_id": firm_id, "firm_name": firm["name"],
                            "ok": False, "error": "File missing"})
            continue
        file = request.files[file_field]
        if not file.filename:
            results.append({"firm_id": firm_id, "firm_name": firm["name"],
                            "ok": False, "error": "No filename"})
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            results.append({"firm_id": firm_id, "firm_name": firm["name"],
                            "ok": False, "error": f"Unsupported file type: {ext}"})
            continue

        try:
            r = _process_one(file, firm, period, period_start, period_end, batch_dir)
            results.append(r)
        except Exception as e:
            import traceback
            results.append({
                "firm_id": firm_id, "firm_name": firm["name"], "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(),
            })

    zip_path = batch_dir / f"GSTR1_Batch_{period}_{timestamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for r in results:
            if r.get("ok"):
                for key in ("json_path", "report_path"):
                    p = r.get(key)
                    if p and Path(p).exists():
                        z.write(p, Path(p).name)

    return jsonify({
        "ok": True,
        "period": period,
        "period_label": period_to_label(period),
        "batch_dir": str(batch_dir.relative_to(OUTPUT_DIR)).replace("\\", "/"),
        "zip_url": url_for("download_file", subpath=str(zip_path.relative_to(OUTPUT_DIR)).replace("\\", "/")),
        "results": [
            {
                **{k: v for k, v in r.items() if k not in ("trace",)},
                "json_url": url_for("download_file", subpath=str(Path(r["json_path"]).relative_to(OUTPUT_DIR)).replace("\\", "/")) if r.get("json_path") else None,
                "report_url": url_for("download_file", subpath=str(Path(r["report_path"]).relative_to(OUTPUT_DIR)).replace("\\", "/")) if r.get("report_path") else None,
            }
            for r in results
        ],
    })


@app.route("/download/<path:subpath>")
@login_required
def download_file(subpath):
    return send_from_directory(OUTPUT_DIR, subpath, as_attachment=True)

@app.route("/project_file/<file_id>/download")
@login_required
def download_project_file(file_id):
    """Download a project file from Supabase Storage via signed URL redirect."""
    rec = projects.get_file_by_id(file_id)
    if not rec:
        return "File not found", 404
    try:
        url = projects.get_signed_url(rec["storage_path"], expires_in=600)
        if url:
            return redirect(url)
        from flask import Response
        data = projects.download_file(rec["storage_path"])
        return Response(
            data,
            headers={
                "Content-Disposition": f'attachment; filename="{rec["filename"]}"',
                "Content-Type": "application/octet-stream",
            },
        )
    except Exception as e:
        return f"Download failed: {e}", 500


# ======================================================================
# Two-phase processing: /api/preview then /api/generate
# Allows the user to review parsed invoices and exclude rows before JSON.
# ======================================================================
@app.route("/api/preview", methods=["POST"])
def api_preview():
    """
    Parse uploaded sheets, run validations, return a previewable summary
    plus a batch_id that can be used in /api/generate.
    """
    raw_period = request.form.get("period", "")
    try:
        period = normalize_period(raw_period)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    try:
        jobs = json.loads(request.form.get("jobs", "[]"))
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "Invalid jobs payload"}), 400

    if not jobs:
        return jsonify({"ok": False, "error": "No jobs provided"}), 400

    period_start, period_end = period_bounds(period)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    previews = []
    for job in jobs:
        firm_id = job.get("firm_id")
        file_field = job.get("file_field")
        firm = firms.get(firm_id)
        if not firm:
            previews.append({"firm_id": firm_id, "ok": False, "error": "Firm not found"})
            continue
        if file_field not in request.files:
            previews.append({"firm_id": firm_id, "firm_name": firm["name"],
                             "ok": False, "error": "File missing"})
            continue
        file = request.files[file_field]
        if not file.filename:
            previews.append({"firm_id": firm_id, "firm_name": firm["name"],
                             "ok": False, "error": "No filename"})
            continue
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXT:
            previews.append({"firm_id": firm_id, "firm_name": firm["name"],
                             "ok": False, "error": f"Unsupported file type: {ext}"})
            continue

        try:
            preview = _preview_one(file, firm, period, period_start, period_end, timestamp)
            previews.append(preview)
        except Exception as e:
            import traceback
            previews.append({
                "firm_id": firm_id, "firm_name": firm["name"], "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(),
            })

    # Cache the entire preview state so generate can pick it up
    batch_id = batch_cache.put({
        "period": period,
        "timestamp": timestamp,
        "previews": previews,
    })

    # Build JSON-safe response (drop the heavy 'invoices' / 'df_records' from cache copy)
    response_previews = []
    for p in previews:
        if not p.get("ok"):
            response_previews.append({k: v for k, v in p.items() if k != "trace"})
            continue
        response_previews.append({
            "ok": True,
            "firm_id": p["firm_id"],
            "firm_name": p["firm_name"],
            "firm_gstin": p["firm_gstin"],
            "stats": p["stats"],
            "warnings": p["warnings"],
            "preflight": p["preflight_summary"],
            "invoices": p["invoices_preview"],   # serialized for UI
        })

    return jsonify({
        "ok": True,
        "batch_id": batch_id,
        "period": period,
        "period_label": period_to_label(period),
        "previews": response_previews,
    })


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """
    Generate JSON files from a previously-previewed batch, optionally
    excluding specific document keys.

    Request:
      {
        "batch_id": "...",
        "exclusions": {                       (optional)
          "<firm_id>": ["doc_key_1", "doc_key_2", ...]
        }
      }
    """
    data = request.get_json(force=True, silent=True) or {}
    batch_id = data.get("batch_id", "")
    exclusions = data.get("exclusions", {}) or {}
    # Per-firm supply-type and reverse-charge overrides keyed by doc_key.
    # Format: { firm_id: { doc_key: {"supply_type": "SEZ_WOPAY", "reverse_charge": "Y"} } }
    overrides = data.get("overrides", {}) or {}

    state = batch_cache.get(batch_id)
    if not state:
        return jsonify({"ok": False, "error": "Batch expired or not found. Please re-upload."}), 400

    period = state["period"]
    timestamp = state["timestamp"]
    batch_dir = OUTPUT_DIR / f"batch_{timestamp}"
    batch_dir.mkdir(exist_ok=True)

    results = []
    for preview in state["previews"]:
        if not preview.get("ok"):
            results.append({k: v for k, v in preview.items() if k not in ("trace",)})
            continue
        try:
            firm_id = preview["firm_id"]
            excluded_keys = set(exclusions.get(firm_id, []))
            # Apply supply_type / reverse_charge overrides to the cached invoices
            firm_overrides = overrides.get(firm_id) or {}
            if firm_overrides:
                for inv in preview.get("_invoices") or []:
                    o = firm_overrides.get(_doc_key_str(inv))
                    if not o:
                        continue
                    if "supply_type" in o:
                        inv["supply_type"] = (o["supply_type"] or "REGULAR").upper()
                    if "reverse_charge" in o:
                        inv["reverse_charge"] = (o["reverse_charge"] or "N").upper()
            r = _generate_one(preview, period, batch_dir, excluded_keys)
            results.append(r)
        except Exception as e:
            import traceback
            results.append({
                "firm_id": preview.get("firm_id"),
                "firm_name": preview.get("firm_name"),
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(),
            })

    # Persist excluded invoices into projects.meta for next-month carry-forward.
    for preview in state["previews"]:
        if not preview.get("ok"):
            continue
        firm_id = preview["firm_id"]
        excluded_keys = set(exclusions.get(firm_id, []))
        if not excluded_keys:
            continue
        try:
            firm = firms.get(firm_id)
            if not firm:
                continue
            firm_uuid = firm["id"]
            proj = projects.get_or_create(
                firm_uuid=firm_uuid, period=period,
                period_label=period_to_label(period))
            ex_list = []
            for d in preview.get("_invoices") or []:
                if _doc_key_str(d) not in excluded_keys:
                    continue
                ex_list.append({
                    "key": _doc_key_str(d),
                    "doc_type": d.get("doc_type", "INV"),
                    "invoice_no": d.get("invoice_no", ""),
                    "invoice_date": d["invoice_date"].strftime("%d-%m-%Y") if d.get("invoice_date") else "",
                    "gstin": d.get("gstin", ""),
                    "customer_name": d.get("customer_name", ""),
                    "taxable_value": float(d.get("invoice_total_taxable", 0) or 0),
                    "total_tax": float(d.get("invoice_total_tax", 0) or 0),
                    "invoice_value": float(d.get("invoice_value", 0) or 0),
                    "supply_type": d.get("supply_type", "REGULAR"),
                })
            full = projects.get_project(proj["id"]) or {}
            meta = full.get("meta") or {}
            meta["excluded_invoices"] = ex_list
            projects._client.table("projects").update({"meta": meta}).eq("id", proj["id"]).execute()
        except Exception as _e:
            app.logger.warning(f"Could not persist exclusions for firm {firm_id}: {_e}")

    zip_path = batch_dir / f"GSTR1_Batch_{period}_{timestamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for r in results:
            if r.get("ok"):
                for key in ("json_path", "report_path"):
                    p = r.get(key)
                    if p and Path(p).exists():
                        z.write(p, Path(p).name)

    return jsonify({
        "ok": True,
        "period": period,
        "period_label": period_to_label(period),
        "batch_dir": str(batch_dir.relative_to(OUTPUT_DIR)).replace("\\", "/"),
        "zip_url": url_for("download_file", subpath=str(zip_path.relative_to(OUTPUT_DIR)).replace("\\", "/")),
        "results": [
            {
                **{k: v for k, v in r.items() if k not in ("trace",)},
                "json_url": url_for("download_file", subpath=str(Path(r["json_path"]).relative_to(OUTPUT_DIR)).replace("\\", "/")) if r.get("json_path") else None,
                "report_url": url_for("download_file", subpath=str(Path(r["report_path"]).relative_to(OUTPUT_DIR)).replace("\\", "/")) if r.get("report_path") else None,
            }
            for r in results
        ],
    })


@app.route("/api/edit_invoice", methods=["POST"])
def api_edit_invoice():
    """
    Edit specific fields (invoice_no, invoice_date, gstin) on an invoice
    in a previewed batch. Only these three identity fields are editable;
    tax math and amounts must be fixed in the source sheet.

    Request:
      {
        "batch_id": "...",
        "firm_id": "...",
        "doc_key": "...",            # original key
        "field": "invoice_no" | "invoice_date" | "gstin",
        "value": "..."                # new value
      }

    Returns the updated invoice (with new key, since key changed) plus
    any re-validation outcomes (B2B/B2C flip, inter-state flip, etc.).
    """
    data = request.get_json(force=True, silent=True) or {}
    batch_id = data.get("batch_id", "")
    firm_id = data.get("firm_id", "")
    doc_key = data.get("doc_key", "")
    field = data.get("field", "")
    new_value = (data.get("value") or "").strip()

    if field not in ("invoice_no", "invoice_date", "gstin"):
        return jsonify({"ok": False, "error": f"Field '{field}' is not editable"}), 400

    state = batch_cache.get(batch_id)
    if not state:
        return jsonify({"ok": False, "error": "Batch expired or not found. Please re-upload."}), 400

    # Find the firm's preview
    target_preview = None
    for p in state["previews"]:
        if p.get("ok") and p.get("firm_id") == firm_id:
            target_preview = p
            break
    if not target_preview:
        return jsonify({"ok": False, "error": "Firm not found in batch"}), 404

    # Find the invoice by current key
    target_inv = None
    for inv in target_preview["_invoices"]:
        if _doc_key_str(inv) == doc_key:
            target_inv = inv
            break
    if not target_inv:
        return jsonify({"ok": False, "error": "Invoice not found in batch"}), 404

    # Apply edit + re-validate
    field_warnings = []
    firm_state_code = target_preview["firm_gstin"][:2]

    if field == "invoice_no":
        if not new_value:
            return jsonify({"ok": False, "error": "Invoice number cannot be empty"}), 400
        target_inv["invoice_no"] = new_value
        # Re-detect doc_type if user typed a CN- / DN- prefix
        upper = new_value.upper()
        if upper.startswith(("CN-", "CN/", "CR-", "CR/", "CREDIT", "C/N")):
            target_inv["doc_type"] = "C"
        elif upper.startswith(("DN-", "DN/", "DR-", "DR/", "DEBIT", "D/N")):
            target_inv["doc_type"] = "D"

    elif field == "invoice_date":
        # Accept DD-MM-YYYY or YYYY-MM-DD
        try:
            new_date = pd.to_datetime(new_value, dayfirst=True).date()
        except Exception:
            return jsonify({"ok": False, "error": f"Invalid date format. Use DD-MM-YYYY (e.g. 01-04-2026)."}), 400
        target_inv["invoice_date"] = new_date

    elif field == "gstin":
        new_gstin = new_value.upper().replace(" ", "")
        # Empty GSTIN is allowed → flips to B2C
        if new_gstin:
            v = validate_gstin(new_gstin)
            if not v.get("valid"):
                return jsonify({
                    "ok": False,
                    "error": f"Invalid GSTIN: {v.get('reason', 'checksum failed')}",
                }), 400
            target_inv["gstin"] = v["gstin"]
            target_inv["is_b2b"] = True
            target_inv["place_of_supply"] = v["gstin"][:2]
            cust_state = v["gstin"][:2]
            target_inv["is_interstate"] = (cust_state != firm_state_code)
            # Update place_of_supply to match new GSTIN's state
        else:
            target_inv["gstin"] = ""
            target_inv["is_b2b"] = False
            # When dropping the GSTIN, leave POS as-is (from sheet's state code)
            # but flip interstate to whatever the original state code suggested
            target_inv["is_interstate"] = False  # default to intra-state

    # Save back to cache
    batch_cache.update(batch_id, state)

    # Build the updated UI row + new key
    updated = _serialize_invoice_for_ui(target_inv)
    return jsonify({
        "ok": True,
        "old_key": doc_key,
        "invoice": updated,
        "warnings": field_warnings,
    })


@app.route("/healthz")
def healthz():
    return {"ok": True, "service": "gstr1-generator"}, 200


# ---------- Helpers --------------------------------------------------------
def _default_period() -> str:
    now = datetime.now()
    if now.month == 1:
        mm, yy = 12, now.year - 1
    else:
        mm, yy = now.month - 1, now.year
    return f"{mm:02d}{yy}"

def _recent_outputs(limit=8):
    """Recent JSON/Excel outputs from OUTPUT_DIR for the dashboard."""
    items = []
    try:
        for p in OUTPUT_DIR.rglob("*"):
            if not p.is_file():
                continue
            name = p.name
            if name.startswith("GSTR1_") and name.endswith(".json"):
                kind = "GSTR-1"
            elif name.startswith("GSTR1_Report_") and name.endswith(".xlsx"):
                kind = "GSTR-1"
            elif name.startswith("GSTR3B_") and name.endswith(".xlsx"):
                kind = "GSTR-3B"
            else:
                continue
            items.append({"kind": kind, "name": name, "mtime": p.stat().st_mtime})
    except Exception:
        return []

    items.sort(key=lambda r: r["mtime"], reverse=True)
    items = items[:limit]

    now = datetime.now()
    out = []
    for it in items:
        ts = datetime.fromtimestamp(it["mtime"])
        delta = now - ts
        if delta.total_seconds() < 3600:
            when = f"{int(delta.total_seconds() / 60)} min ago"
        elif delta.total_seconds() < 86400:
            when = f"{int(delta.total_seconds() / 3600)} hr ago"
        elif delta.days < 30:
            when = f"{delta.days} d ago"
        else:
            when = ts.strftime("%d %b %Y")
        out.append({"kind": it["kind"], "name": it["name"], "when": when})
    return out


def _process_one(file, firm, period, period_start, period_end, batch_dir):
    safe_name = "".join(c if c.isalnum() else "_" for c in firm["name"])
    stamp = datetime.now().strftime("%H%M%S")
    upload_path = UPLOAD_DIR / f"{safe_name}_{period}_{stamp}_{secure_filename(file.filename)}"
    file.save(upload_path)

    df = read_sales(str(upload_path))

    out_of_period = 0
    if "invoice_date" in df.columns:
        in_period = df["invoice_date"].between(
            pd.Timestamp(period_start), pd.Timestamp(period_end))
        out_of_period = int((~in_period).sum())

    firm_gstin = firm["gstin"]
    if "gstin" not in df.columns:
        raise ValueError(
            f"Could not find a 'GSTIN/UIN' column in the sheet. "
            f"Found columns: {list(df.columns)[:10]}{'...' if len(df.columns) > 10 else ''}. "
            f"Please ensure the sheet has a column named 'GSTIN/UIN' or 'GSTIN/ UIN'."
        )
    self_invoice_rows = int((df["gstin"].str.upper().str.strip() == firm_gstin).sum())

    # Pull existing cache as an external reference (for name-mismatch checks
    # on customers seen in past months but not in this sheet's master list)
    cache_dict = {c["gstin"]: c["name"] for c in customers.list_all()}
    firm_turnover = firm.get("annual_turnover") if isinstance(firm, dict) else None
    df, exceptions, master = validate_dataframe(df, firm_state_code=firm_gstin[:2],
                                                external_cache=cache_dict,
                                                annual_turnover=firm_turnover)

    # Persist all valid GSTIN/name pairs to the long-term customer cache.
    cache_updates = customers.bulk_observe(list(master.items()))

    invoices = consolidate_invoices(df)
    buckets = classify_invoices(invoices)

    payload = build_gstr1_json(firm_gstin, period, buckets, invoices)
    json_path = batch_dir / f"GSTR1_{safe_name}_{period}.json"
    report_path = batch_dir / f"GSTR1_Report_{safe_name}_{period}.xlsx"

    # Write JSON with explicit LF line endings (Windows defaults to CRLF,
    # which the GST offline tool rejects as "invalid JSON")
    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    with open(json_path, "wb") as f:
        f.write(json_text.encode("utf-8"))

    build_report(firm["name"], firm_gstin, period, invoices, buckets,
                 exceptions, str(report_path))
# ----- Save outputs to Supabase project archive -----
    try:
        firm_uuid = firms.get_uuid(firm_gstin)
        if firm_uuid:
            project = projects.get_or_create(
                firm_uuid=firm_uuid,
                period=period,
                period_label=period_to_label(period),
            )
            projects.add_file_from_path(
                project["id"], "gstr1_json",
                json_path, filename=json_path.name)
            projects.add_file_from_path(
                project["id"], "gstr1_report",
                report_path, filename=report_path.name)
            if upload_path.exists():
                projects.add_file_from_path(
                    project["id"], "sales_register",
                    upload_path, filename=upload_path.name)
    except Exception as _e:
        app.logger.warning(f"Supabase upload failed: {_e}")
    return {
        "ok": True,
        "firm_id": firm["gstin"],
        "firm_name": firm["name"],
        "firm_gstin": firm_gstin,
        "stats": {
            "rows": len(df), "invoices": len(invoices),
            "b2b": len(buckets["b2b"]), "b2cl": len(buckets["b2cl"]),
            "b2cs": len(buckets["b2cs"]), "exceptions": len(exceptions),
            "out_of_period_rows": out_of_period,
            "self_invoice_rows": self_invoice_rows,
            "customers_cached": cache_updates,
        },
        "warnings": _collect_warnings(out_of_period, self_invoice_rows, len(df)),
        "json_path": str(json_path),
        "report_path": str(report_path),
    }


def _doc_key_str(doc) -> str:
    """Stable string key for a consolidated document, used for exclusion."""
    date_part = doc["invoice_date"].isoformat() if doc.get("invoice_date") else "NULL"
    return f"{doc.get('gstin','')}|{doc.get('invoice_no','')}|{date_part}|{doc.get('doc_type','INV')}"


def _auto_supply_type(doc) -> str:
    """
    Infer supply_type from the numbers if user/Excel didn't set it.

    Heuristic:
      - taxable_value > 0 AND all of igst/cgst/sgst = 0  →  zero-rated
        - if invoice is B2B (registered buyer)            →  SEZ-WOPAY
        - else (unregistered / overseas buyer)            →  EXPORT-WOPAY
      - taxable_value == 0 AND tax == 0  →  treat as NIL (likely nil-rated)
      - otherwise                          →  REGULAR
    User can override on the review screen.
    """
    explicit = (doc.get("supply_type") or "REGULAR").upper()
    if explicit and explicit != "REGULAR":
        return explicit
    taxable = float(doc.get("invoice_total_taxable", 0) or 0)
    tax = float(doc.get("invoice_total_tax", 0) or 0)
    if taxable > 0 and tax <= 0.01:
        if doc.get("is_b2b") and doc.get("gstin"):
            return "SEZ_WOPAY"
        return "EXPORT_WOPAY"
    if taxable <= 0.01 and tax <= 0.01:
        return "NIL"
    return "REGULAR"


def _serialize_invoice_for_ui(doc) -> dict:
    """Convert a consolidated doc to a UI-friendly dict (no defaultdicts, no Timestamps)."""
    auto_type = _auto_supply_type(doc)
    # Stash back so downstream generate() also sees the auto-detected value
    if not doc.get("supply_type") or doc["supply_type"] == "REGULAR":
        doc["supply_type"] = auto_type
    return {
        "key": _doc_key_str(doc),
        "doc_type": doc.get("doc_type", "INV"),
        "gstin": doc.get("gstin", ""),
        "customer_name": doc.get("customer_name", ""),
        "invoice_no": doc.get("invoice_no", ""),
        "invoice_date": doc["invoice_date"].strftime("%d-%m-%Y") if doc.get("invoice_date") else "",
        "is_b2b": bool(doc.get("is_b2b")),
        "is_interstate": bool(doc.get("is_interstate")),
        "place_of_supply": doc.get("place_of_supply", ""),
        "items_count": len(doc.get("items", [])),
        "taxable_value": float(doc.get("invoice_total_taxable", 0)),
        "total_tax": float(doc.get("invoice_total_tax", 0)),
        "invoice_value": float(doc.get("invoice_value", 0)),
        "orig_invoice_no": doc.get("orig_invoice_no", "") or "",
        "supply_type": doc.get("supply_type", "REGULAR"),
        "supply_type_auto": auto_type,  # what server inferred (for UI hint)
        "reverse_charge": doc.get("reverse_charge", "N"),
    }


def _preview_one(file, firm, period, period_start, period_end, timestamp):
    """Parse a single sheet, run validations, return a previewable structure."""
    safe_name = "".join(c if c.isalnum() else "_" for c in firm["name"])
    stamp = datetime.now().strftime("%H%M%S")
    upload_path = UPLOAD_DIR / f"{safe_name}_{period}_{stamp}_{secure_filename(file.filename)}"
    file.save(upload_path)

    df = read_sales(str(upload_path))

    out_of_period = 0
    if "invoice_date" in df.columns:
        in_period = df["invoice_date"].between(
            pd.Timestamp(period_start), pd.Timestamp(period_end))
        out_of_period = int((~in_period).sum())

    firm_gstin = firm["gstin"]
    if "gstin" not in df.columns:
        raise ValueError(
            f"Could not find a 'GSTIN/UIN' column in the sheet. "
            f"Found columns: {list(df.columns)[:10]}{'...' if len(df.columns) > 10 else ''}. "
            f"Please ensure the sheet has a column named 'GSTIN/UIN' or 'GSTIN/ UIN'."
        )
    self_invoice_rows = int((df["gstin"].str.upper().str.strip() == firm_gstin).sum())

    cache_dict = {c["gstin"]: c["name"] for c in customers.list_all()}
    firm_turnover = firm.get("annual_turnover") if isinstance(firm, dict) else None
    df_validated, exceptions, master = validate_dataframe(
        df, firm_state_code=firm_gstin[:2], external_cache=cache_dict,
        annual_turnover=firm_turnover)

    cache_updates = customers.bulk_observe(list(master.items()))

    # Pre-flight checks (run on the validated DataFrame)
    preflight = run_all_preflight_checks(df_validated, firm_state_code=firm_gstin[:2])

    invoices = consolidate_invoices(df_validated)
    buckets = classify_invoices(invoices)

    invoices_preview = [_serialize_invoice_for_ui(d) for d in invoices]

    # Build a compact summary of preflight (top 50 issues to avoid huge UI payloads)
    preflight_summary = {
        "totals": preflight["totals"],
        "errors": preflight["errors"][:50],
        "warnings": preflight["warnings"][:50],
    }

    return {
        "ok": True,
        "firm_id": firm["gstin"],
        "firm_name": firm["name"],
        "firm_gstin": firm_gstin,
        "safe_name": safe_name,
        "stats": {
            "rows": len(df_validated),
            "invoices": len([d for d in invoices if d.get("doc_type", "INV") == "INV"]),
            "credit_notes": len([d for d in invoices if d.get("doc_type") == "C"]),
            "debit_notes": len([d for d in invoices if d.get("doc_type") == "D"]),
            "b2b": len(buckets["b2b"]),
            "b2cl": len(buckets["b2cl"]),
            "b2cs": len(buckets["b2cs"]),
            "cdnr": len(buckets.get("cdnr", [])),
            "cdnur": len(buckets.get("cdnur", [])),
            "exceptions": len(exceptions),
            "out_of_period_rows": out_of_period,
            "self_invoice_rows": self_invoice_rows,
            "customers_cached": cache_updates,
        },
        "warnings": _collect_warnings(out_of_period, self_invoice_rows, len(df)),
        "preflight_summary": preflight_summary,
        "invoices_preview": invoices_preview,
        # Stored in cache (not returned to UI):
        "_invoices": invoices,
        "_exceptions": exceptions,
        "upload_path": str(upload_path),
    }


def _generate_one(preview, period, batch_dir, excluded_keys: set):
    """Generate JSON + Excel for one firm using cached preview, applying exclusions."""
    firm_name = preview["firm_name"]
    firm_gstin = preview["firm_gstin"]
    safe_name = preview["safe_name"]

    invoices_all = preview["_invoices"]
    excluded_count = 0
    excluded_list = []
    if excluded_keys:
        kept = []
        for d in invoices_all:
            if _doc_key_str(d) in excluded_keys:
                excluded_count += 1
                excluded_list.append(d)
            else:
                kept.append(d)
        invoices = kept
    else:
        invoices = invoices_all

    buckets = classify_invoices(invoices)
    payload = build_gstr1_json(firm_gstin, period, buckets, invoices)
    json_path = batch_dir / f"GSTR1_{safe_name}_{period}.json"
    report_path = batch_dir / f"GSTR1_Report_{safe_name}_{period}.xlsx"

    json_text = json.dumps(payload, indent=2, ensure_ascii=False)
    with open(json_path, "wb") as f:
        f.write(json_text.encode("utf-8"))

    build_report(firm_name, firm_gstin, period, invoices, buckets,
                 preview["_exceptions"], str(report_path),
                 excluded_invoices=excluded_list)
# ----- Save outputs to Supabase project archive -----
    try:
        firm_uuid = firms.get_uuid(firm_gstin)
        if firm_uuid:
            project = projects.get_or_create(
                firm_uuid=firm_uuid,
                period=period,
                period_label=period_to_label(period),
            )
            projects.add_file_from_path(
                project["id"], "gstr1_json",
                json_path, filename=json_path.name)
            projects.add_file_from_path(
                project["id"], "gstr1_report",
                report_path, filename=report_path.name)
            sales_src = preview.get("upload_path")
            if sales_src and Path(sales_src).exists():
                projects.add_file_from_path(
                    project["id"], "sales_register",
                    Path(sales_src), filename=Path(sales_src).name)
    except Exception as _e:
        app.logger.warning(f"Supabase upload failed for {firm_name}/{period}: {_e}")
    return {
        "ok": True,
        "firm_id": preview["firm_id"],
        "firm_name": firm_name,
        "firm_gstin": firm_gstin,
        "stats": {
            "invoices": len([d for d in invoices if d.get("doc_type", "INV") == "INV"]),
            "credit_notes": len([d for d in invoices if d.get("doc_type") == "C"]),
            "debit_notes": len([d for d in invoices if d.get("doc_type") == "D"]),
            "b2b": len(buckets["b2b"]),
            "b2cl": len(buckets["b2cl"]),
            "b2cs": len(buckets["b2cs"]),
            "cdnr": len(buckets.get("cdnr", [])),
            "cdnur": len(buckets.get("cdnur", [])),
            "excluded": excluded_count,
        },
        "json_path": str(json_path),
        "report_path": str(report_path),
    }


def _collect_warnings(out_of_period, self_invoice_rows, total_rows=0) -> list:
    w = []
    if out_of_period:
        # If MOST rows are out-of-period, this is almost certainly a wrong-period mistake
        if total_rows > 0 and out_of_period >= total_rows * 0.5:
            w.append(
                f"⚠️ CRITICAL: {out_of_period} of {total_rows} invoices ({out_of_period * 100 // total_rows}%) "
                f"are dated OUTSIDE the return period you selected. "
                f"You probably picked the wrong month/year. The GST portal will REJECT all out-of-period invoices. "
                f"Click 'Back to upload' and choose the correct return period."
            )
        else:
            w.append(f"{out_of_period} rows have invoice dates outside the declared period")
    if self_invoice_rows:
        w.append(f"{self_invoice_rows} rows have customer GSTIN equal to firm GSTIN (self-invoice — likely data entry error)")
    return w


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
