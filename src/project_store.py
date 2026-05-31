"""
Project storage — firm × month "projects" plus their files in Supabase Storage.

A project = (firm, period) tuple, e.g. (HITECH SYSTEMS, April 2026).
A project_file = one of: sales_register, gstr1_json, gstr1_report,
                        gstr2b_input, gstr3b_excel.

The file's bytes live in Supabase Storage under
    {firm_gstin}/{period}/{filename}
and a row in the `project_files` table tracks it.

Public API:
    ProjectStore.get_or_create(firm_uuid, period, period_label)  -> dict
    ProjectStore.add_file(project_id, kind, filename, file_bytes) -> dict
    ProjectStore.add_file_from_path(project_id, kind, file_path)  -> dict
    ProjectStore.list_all()                                       -> list
    ProjectStore.list_for_firm(firm_uuid)                         -> list
    ProjectStore.list_files(project_id)                           -> list
    ProjectStore.get_file_by_id(file_id)                          -> dict
    ProjectStore.download_file(storage_path)                      -> bytes
    ProjectStore.get_signed_url(storage_path, expires_in=3600)    -> str
    ProjectStore.delete_file(file_id)                             -> bool
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from supabase_client import get_bucket, get_client


VALID_KINDS = {
    "sales_register",
    "gstr1_json",
    "gstr1_report",
    "gstr2b_input",
    "gstr3b_excel",
    "gstr3b_pdf",
}

KIND_LABELS = {
    "sales_register": "Sales Register",
    "gstr1_json":     "GSTR-1 JSON",
    "gstr1_report":   "GSTR-1 Report",
    "gstr2b_input":   "GSTR-2B Input",
    "gstr3b_excel":   "GSTR-3B Computation",
    "gstr3b_pdf":     "GSTR-3B PDF",
}


class ProjectStore:

    def __init__(self) -> None:
        self._client = get_client()
        self._bucket = get_bucket()

    # ---- Projects ----------------------------------------------------

    def get_or_create(self, firm_uuid: str, period: str,
                      period_label: Optional[str] = None) -> Dict[str, Any]:
        """Find or create the project for (firm, period)."""
        if not firm_uuid or not period:
            raise ValueError("firm_uuid and period are required")
        resp = (self._client.table("projects").select("*")
                .eq("firm_id", firm_uuid).eq("period", period)
                .limit(1).execute())
        if resp.data:
            return resp.data[0]
        resp = self._client.table("projects").insert({
            "firm_id": firm_uuid,
            "period": period,
            "period_label": period_label,
        }).execute()
        return resp.data[0]

    def list_all(self) -> List[Dict[str, Any]]:
        """All projects, newest first, joined with firm info."""
        resp = (self._client.table("projects")
                .select("*, firm:firms(name, gstin, legal_name)")
                .order("created_at", desc=True).execute())
        return resp.data or []

    def list_for_firm(self, firm_uuid: str) -> List[Dict[str, Any]]:
        resp = (self._client.table("projects").select("*")
                .eq("firm_id", firm_uuid)
                .order("period", desc=True).execute())
        return resp.data or []

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        resp = (self._client.table("projects").select("*")
                .eq("id", project_id).limit(1).execute())
        return resp.data[0] if resp.data else None

    def find_project(self, firm_uuid: str, period: str) -> Optional[Dict[str, Any]]:
        if not firm_uuid or not period:
            return None
        resp = (self._client.table("projects").select("*")
                .eq("firm_id", firm_uuid).eq("period", period)
                .limit(1).execute())
        return resp.data[0] if resp.data else None

    def mark_filed(self, project_id: str, return_type: str,
                   arn: Optional[str] = None) -> Dict[str, Any]:
        """
        Mark a return as filed. return_type is 'gstr1' or 'gstr3b'.
        Stores filed flag, timestamp, and optional ARN inside the project's
        `meta` JSONB. (No schema change required.)
        """
        from datetime import datetime, timezone
        proj = self.get_project(project_id)
        if not proj:
            raise KeyError(f"Project {project_id} not found")
        meta = (proj.get("meta") or {})
        meta.setdefault("filings", {})
        meta["filings"][return_type] = {
            "filed": True,
            "filed_at": datetime.now(timezone.utc).isoformat(),
            "arn": arn or "",
        }
        resp = (self._client.table("projects")
                .update({"meta": meta}).eq("id", project_id).execute())
        return resp.data[0] if resp.data else proj

    def mark_unfiled(self, project_id: str, return_type: str) -> Dict[str, Any]:
        proj = self.get_project(project_id)
        if not proj:
            raise KeyError(f"Project {project_id} not found")
        meta = (proj.get("meta") or {})
        if "filings" in meta and return_type in meta["filings"]:
            meta["filings"][return_type] = {"filed": False}
        resp = (self._client.table("projects")
                .update({"meta": meta}).eq("id", project_id).execute())
        return resp.data[0] if resp.data else proj

    def is_filed(self, project: Dict[str, Any], return_type: str) -> bool:
        meta = (project or {}).get("meta") or {}
        f = (meta.get("filings") or {}).get(return_type) or {}
        return bool(f.get("filed"))

    def delete_project(self, project_id: str) -> bool:
        # Cascade deletes project_files rows; clean up storage manually
        files = self.list_files(project_id)
        for f in files:
            try:
                self._client.storage.from_(self._bucket).remove([f["storage_path"]])
            except Exception:
                pass
        self._client.table("projects").delete().eq("id", project_id).execute()
        return True

    # ---- Files -------------------------------------------------------

    def list_files(self, project_id: str) -> List[Dict[str, Any]]:
        resp = (self._client.table("project_files").select("*")
                .eq("project_id", project_id)
                .order("created_at").execute())
        return resp.data or []

    def get_file_by_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        resp = (self._client.table("project_files").select("*")
                .eq("id", file_id).limit(1).execute())
        return resp.data[0] if resp.data else None

    def add_file(self, project_id: str, kind: str,
                 filename: str, file_bytes: bytes,
                 metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Upload file bytes to Supabase Storage and record in project_files.
        If a file of the same `kind` already exists for this project, it's
        replaced (one file per kind per project).
        """
        if kind not in VALID_KINDS:
            raise ValueError(f"Invalid kind: {kind}. Allowed: {sorted(VALID_KINDS)}")
        if not project_id or not filename or not file_bytes:
            raise ValueError("project_id, filename, file_bytes are required")

        # Look up project to determine the storage folder
        proj_resp = (self._client.table("projects")
                     .select("*, firm:firms(gstin)")
                     .eq("id", project_id).limit(1).execute())
        if not proj_resp.data:
            raise KeyError(f"Project {project_id} not found")
        proj = proj_resp.data[0]
        firm_gstin = proj["firm"]["gstin"]
        period = proj["period"]
        storage_path = f"{firm_gstin}/{period}/{filename}"

        # Upload (upsert overwrites existing object at same path)
        self._client.storage.from_(self._bucket).upload(
            path=storage_path,
            file=file_bytes,
            file_options={"upsert": "true", "content-type": _guess_mime(filename)},
        )

        # Delete existing project_files row of same kind, insert new
        (self._client.table("project_files").delete()
            .eq("project_id", project_id).eq("kind", kind).execute())

        resp = self._client.table("project_files").insert({
            "project_id": project_id,
            "kind": kind,
            "filename": filename,
            "storage_path": storage_path,
            "size_bytes": len(file_bytes),
            "metadata": metadata or {},
        }).execute()
        return resp.data[0]

    def add_file_from_path(self, project_id: str, kind: str,
                           file_path: Path,
                           filename: Optional[str] = None) -> Dict[str, Any]:
        """Read a file from disk and upload to Storage."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(file_path)
        if filename is None:
            filename = file_path.name
        with open(file_path, "rb") as fh:
            data = fh.read()
        return self.add_file(project_id, kind, filename, data)

    def get_signed_url(self, storage_path: str,
                       expires_in: int = 3600) -> str:
        """Generate a time-limited URL to download a private file."""
        resp = self._client.storage.from_(self._bucket).create_signed_url(
            storage_path, expires_in)
        # supabase-py returns dict with 'signedURL' or 'signed_url' depending on version
        return (resp.get("signedURL") if isinstance(resp, dict) else None) \
            or (resp.get("signed_url") if isinstance(resp, dict) else None) \
            or ""

    def download_file(self, storage_path: str) -> bytes:
        return self._client.storage.from_(self._bucket).download(storage_path)

    def delete_file(self, file_id: str) -> bool:
        rec = self.get_file_by_id(file_id)
        if not rec:
            return False
        try:
            self._client.storage.from_(self._bucket).remove([rec["storage_path"]])
        except Exception:
            pass
        self._client.table("project_files").delete().eq("id", file_id).execute()
        return True


def _guess_mime(filename: str) -> str:
    n = filename.lower()
    if n.endswith(".json"): return "application/json"
    if n.endswith(".xlsx"): return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if n.endswith(".xls"):  return "application/vnd.ms-excel"
    if n.endswith(".csv"):  return "text/csv"
    if n.endswith(".pdf"):  return "application/pdf"
    return "application/octet-stream"
