"""
Customer cache.

A persistent GSTIN → canonical-name dictionary that grows every time a
batch is processed. Used to:
  1. Auto-fill the customer name when a known GSTIN is entered.
  2. Validate customer names in uploaded sheets against historical data.
  3. Cross-firm: a GSTIN learned from Firm A's sheet is also known when
     processing Firm B's sheet (since it's the same legal entity).

Stored as a single JSON file: web/data/customers.json.

File schema:
{
  "29AABCA2398D1ZQ": {
    "name": "ARVIND MILLS LIMITED",
    "name_variants": ["Arvind Limited", "Arvind Mills Ltd"],
    "first_seen": "2026-04-15T10:30:00",
    "last_seen": "2026-05-06T14:22:00",
    "occurrence_count": 17
  },
  ...
}
"""
import json
import re
from datetime import datetime
from pathlib import Path
from threading import Lock


_CLEAN_RE = re.compile(r"[^\w\s]")


def _norm(name: str) -> str:
    """Normalize for comparison — uppercase, collapse whitespace, strip punct."""
    if not name:
        return ""
    s = _CLEAN_RE.sub(" ", str(name).upper())
    return re.sub(r"\s+", " ", s).strip()


class CustomerStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        if not self.path.exists():
            self._save({})

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    # ----------------------------------------------------------- read API
    def get(self, gstin: str) -> dict:
        """Return {name, name_variants, ...} or None."""
        if not gstin:
            return None
        data = self._load()
        return data.get(gstin.upper().strip())

    def get_name(self, gstin: str) -> str:
        rec = self.get(gstin)
        return rec.get("name", "") if rec else ""

    def list_all(self) -> list:
        data = self._load()
        items = []
        for gstin, rec in data.items():
            items.append({
                "gstin": gstin,
                "name": rec.get("name", ""),
                "variants": rec.get("name_variants", []),
                "occurrence_count": rec.get("occurrence_count", 0),
                "last_seen": rec.get("last_seen", ""),
                "first_seen": rec.get("first_seen", ""),
            })
        items.sort(key=lambda x: x["last_seen"], reverse=True)
        return items

    def count(self) -> int:
        return len(self._load())

    # ----------------------------------------------------------- write API
    def add_or_update(self, gstin: str, name: str) -> bool:
        """Record a GSTIN/name observation. Returns True if updated."""
        if not gstin or not name:
            return False
        gstin = gstin.upper().strip()
        name = str(name).strip()
        if len(gstin) != 15 or not name:
            return False
        norm_name = _norm(name)
        if not norm_name:
            return False

        with self._lock:
            data = self._load()
            now = datetime.now().isoformat(timespec="seconds")
            rec = data.get(gstin)
            if rec:
                # Update existing
                existing_norm = _norm(rec.get("name", ""))
                if norm_name != existing_norm:
                    variants = rec.setdefault("name_variants", [])
                    # Add new variant if it's substantively different
                    seen_norms = {existing_norm} | {_norm(v) for v in variants}
                    if norm_name not in seen_norms:
                        variants.append(name)
                    # Pick canonical = the longest, most-occurring form
                    # If new name is longer than current and not just punctuation diff,
                    # prefer the longer form.
                    if len(name) > len(rec["name"]) * 1.2:
                        # Bump current canonical to variants
                        if rec["name"] not in variants:
                            variants.insert(0, rec["name"])
                        rec["name"] = name
                rec["last_seen"] = now
                rec["occurrence_count"] = rec.get("occurrence_count", 0) + 1
            else:
                # New entry
                data[gstin] = {
                    "name": name,
                    "name_variants": [],
                    "first_seen": now,
                    "last_seen": now,
                    "occurrence_count": 1,
                }
            self._save(data)
            return True

    def bulk_observe(self, pairs: list) -> int:
        """Record multiple GSTIN/name pairs in one go. Returns count added/updated."""
        if not pairs:
            return 0
        with self._lock:
            data = self._load()
            now = datetime.now().isoformat(timespec="seconds")
            updated = 0
            for gstin, name in pairs:
                if not gstin or not name:
                    continue
                gstin = str(gstin).upper().strip()
                name = str(name).strip()
                if len(gstin) != 15 or not name:
                    continue
                norm_name = _norm(name)
                if not norm_name:
                    continue
                rec = data.get(gstin)
                if rec:
                    existing_norm = _norm(rec.get("name", ""))
                    if norm_name != existing_norm:
                        variants = rec.setdefault("name_variants", [])
                        seen_norms = {existing_norm} | {_norm(v) for v in variants}
                        if norm_name not in seen_norms:
                            variants.append(name)
                        if len(name) > len(rec["name"]) * 1.2:
                            if rec["name"] not in variants:
                                variants.insert(0, rec["name"])
                            rec["name"] = name
                    rec["last_seen"] = now
                    rec["occurrence_count"] = rec.get("occurrence_count", 0) + 1
                else:
                    data[gstin] = {
                        "name": name,
                        "name_variants": [],
                        "first_seen": now,
                        "last_seen": now,
                        "occurrence_count": 1,
                    }
                updated += 1
            self._save(data)
            return updated

    def update_name(self, gstin: str, new_name: str) -> bool:
        """Manually override the canonical name for a GSTIN."""
        with self._lock:
            data = self._load()
            gstin = gstin.upper().strip()
            if gstin not in data:
                return False
            old_name = data[gstin]["name"]
            new_name = str(new_name).strip()
            if not new_name or new_name == old_name:
                return False
            variants = data[gstin].setdefault("name_variants", [])
            if old_name not in variants:
                variants.insert(0, old_name)
            # Remove new_name from variants if it was there
            data[gstin]["name_variants"] = [v for v in variants if _norm(v) != _norm(new_name)]
            data[gstin]["name"] = new_name
            data[gstin]["last_seen"] = datetime.now().isoformat(timespec="seconds")
            self._save(data)
            return True

    def delete(self, gstin: str) -> bool:
        with self._lock:
            data = self._load()
            gstin = gstin.upper().strip()
            if gstin in data:
                del data[gstin]
                self._save(data)
                return True
            return False

    def clear_all(self) -> int:
        with self._lock:
            data = self._load()
            n = len(data)
            self._save({})
            return n
