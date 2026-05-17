"""
Customer GSTIN cache — Supabase Postgres backend.

Drop-in replacement for the JSON-based CustomerStore. The public method
signatures match the original.

Each row: gstin (PK), name, variants (JSONB list of alternate spellings),
occurrence_count, first_seen, last_seen.
"""
from typing import Any, Dict, Iterable, List, Optional, Tuple

from supabase_client import get_client


class CustomerStore:
    def __init__(self, _legacy_path: Any = None) -> None:
        self._client = get_client()

    # ---- Read --------------------------------------------------------

    def list_all(self) -> List[Dict[str, Any]]:
        resp = self._client.table("customers").select("*").order("name").execute()
        rows = resp.data or []
        # Ensure variants is a list (PostgREST returns it as parsed JSON)
        for r in rows:
            r.setdefault("variants", [])
        return rows

    def count(self) -> int:
        resp = (self._client.table("customers")
                .select("gstin", count="exact").execute())
        return resp.count or 0

    def get(self, gstin: str) -> Optional[Dict[str, Any]]:
        if not gstin:
            return None
        key = gstin.strip().upper()
        resp = (self._client.table("customers")
                .select("*").eq("gstin", key).limit(1).execute())
        if resp.data:
            row = resp.data[0]
            row.setdefault("variants", [])
            return row
        return None

    def get_name(self, gstin: str) -> str:
        rec = self.get(gstin)
        return (rec or {}).get("name", "")

    # ---- Write -------------------------------------------------------

    def observe(self, gstin: str, name: str) -> bool:
        """
        Upsert a (gstin, name) pair.
        Returns True if a new row was created, False if an existing row
        was incremented.
        """
        if not gstin or not name:
            return False
        gstin = gstin.strip().upper()
        name = name.strip()
        if len(gstin) != 15 or not name:
            return False

        existing = self.get(gstin)
        if existing:
            variants = list(existing.get("variants") or [])
            if existing["name"] != name and name not in variants:
                variants.append(name)
            (self._client.table("customers").update({
                "occurrence_count": (existing.get("occurrence_count") or 0) + 1,
                "variants": variants,
            }).eq("gstin", gstin).execute())
            return False
        else:
            (self._client.table("customers").insert({
                "gstin": gstin,
                "name": name,
                "variants": [],
                "occurrence_count": 1,
            }).execute())
            return True

    def bulk_observe(self, items: Iterable[Tuple[str, str]]) -> int:
        """Process many (gstin, name) pairs. Returns count of new rows."""
        new_count = 0
        for gstin, name in items:
            if self.observe(gstin, name):
                new_count += 1
        return new_count

    def update_name(self, gstin: str, new_name: str) -> bool:
        gstin = gstin.strip().upper()
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        if not self.get(gstin):
            return False
        (self._client.table("customers").update({"name": new_name})
            .eq("gstin", gstin).execute())
        return True

    def delete(self, gstin: str) -> bool:
        if not gstin:
            return False
        self._client.table("customers").delete().eq("gstin", gstin.strip().upper()).execute()
        return True

    def clear_all(self) -> int:
        n = self.count()
        # Postgres needs a where clause for DELETE through PostgREST
        self._client.table("customers").delete().neq("gstin", "").execute()
        return n
