"""
Firm storage — Supabase Postgres backend.

Drop-in replacement for the JSON-based FirmStore. Public API matches
the original so app.py needs no other changes.

Accepts EITHER a GSTIN (15 chars) or a UUID (36 chars with hyphens) as
the firm_id parameter. Internally the projects table joins on UUID.
"""
from typing import Any, Dict, List, Optional

from supabase_client import get_client


class FirmStore:
    """Firms persisted in the Supabase `firms` table."""

    def __init__(self, _legacy_path: Any = None) -> None:
        # Path arg is ignored; data lives in Postgres now.
        self._client = get_client()

    # ---- Read --------------------------------------------------------

    def list_firms(self) -> List[Dict[str, Any]]:
        """All firms, ordered by name."""
        resp = self._client.table("firms").select("*").order("name").execute()
        return resp.data or []

    def get(self, firm_id: str) -> Optional[Dict[str, Any]]:
        """
        Look up a firm by either GSTIN (15 chars) or UUID id (36 chars).
        Returns the full row or None.
        """
        if not firm_id:
            return None
        key = firm_id.strip()

        # UUID lookup (36 chars, contains 4 hyphens)
        if len(key) == 36 and key.count("-") == 4:
            try:
                resp = (self._client.table("firms").select("*")
                        .eq("id", key).limit(1).execute())
                if resp.data:
                    return resp.data[0]
            except Exception:
                pass

        # GSTIN lookup (15 chars, uppercase)
        if len(key) == 15:
            try:
                resp = (self._client.table("firms").select("*")
                        .eq("gstin", key.upper()).limit(1).execute())
                if resp.data:
                    return resp.data[0]
            except Exception:
                pass

        return None

    # Alias for any older code using get_firm()
    def get_firm(self, firm_id: str) -> Optional[Dict[str, Any]]:
        return self.get(firm_id)

    def get_uuid(self, firm_id: str) -> Optional[str]:
        """Helper used by ProjectStore — accepts GSTIN or UUID, returns UUID."""
        f = self.get(firm_id)
        return f["id"] if f else None

    # ---- Write -------------------------------------------------------

    def add(self, name: str, gstin: str, legal_name: str = "") -> Dict[str, Any]:
        """Create a new firm. Raises ValueError on validation failure."""
        name = (name or "").strip()
        gstin = (gstin or "").strip().upper()
        legal_name = (legal_name or "").strip() or None
        if not name:
            raise ValueError("Firm name is required.")
        if len(gstin) != 15:
            raise ValueError("Valid 15-character GSTIN is required.")
        if self.get(gstin):
            raise ValueError(f"Firm with GSTIN {gstin} already exists.")
        resp = self._client.table("firms").insert({
            "name": name, "gstin": gstin, "legal_name": legal_name,
        }).execute()
        if not resp.data:
            raise ValueError("Insert returned no row.")
        return resp.data[0]

    def update(self, firm_id: str,
               name: Optional[str] = None,
               legal_name: Optional[str] = None) -> Dict[str, Any]:
        """Update name / legal_name. GSTIN is immutable."""
        firm = self.get(firm_id)
        if not firm:
            raise KeyError(f"Firm {firm_id} not found.")
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name.strip()
        if legal_name is not None:
            payload["legal_name"] = legal_name.strip() or None
        if not payload:
            return firm
        resp = (self._client.table("firms").update(payload)
                .eq("id", firm["id"]).execute())
        return resp.data[0] if resp.data else firm

    def delete(self, firm_id: str) -> bool:
        firm = self.get(firm_id)
        if not firm:
            return False
        self._client.table("firms").delete().eq("id", firm["id"]).execute()
        return True
