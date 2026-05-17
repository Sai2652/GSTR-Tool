"""
Firm profile storage. Persists to a JSON file so profiles survive restarts.
"""
import json
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from gstin_validator import validate_gstin


class FirmStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save({})

    def _load(self) -> dict:
        with open(self.path) as f:
            return json.load(f)

    def _save(self, data: dict):
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

    def list_firms(self) -> list:
        data = self._load()
        return [
            {"id": k, **v}
            for k, v in sorted(data.items(), key=lambda x: x[1].get("name", ""))
        ]

    def get(self, firm_id: str) -> dict:
        return self._load().get(firm_id)

    def add(self, name: str, gstin: str, legal_name: str = "") -> dict:
        gstin = (gstin or "").strip().upper().replace(" ", "")
        validation = validate_gstin(gstin)
        if not validation["valid"]:
            raise ValueError(
                f"Invalid GSTIN: {validation['reason']}. "
                f"Firm GSTIN must be a fully valid 15-character GSTIN."
            )
        data = self._load()
        firm_id = gstin  # GSTIN itself is the unique key
        data[firm_id] = {
            "name": name.strip(),
            "gstin": validation["gstin"],
            "legal_name": legal_name.strip(),
            "state_code": validation["gstin"][:2],
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save(data)
        return {"id": firm_id, **data[firm_id]}

    def delete(self, firm_id: str) -> bool:
        data = self._load()
        if firm_id in data:
            del data[firm_id]
            self._save(data)
            return True
        return False

    def update(self, firm_id: str, name: str = None, legal_name: str = None) -> dict:
        data = self._load()
        if firm_id not in data:
            raise KeyError(f"Firm {firm_id} not found")
        if name is not None:
            data[firm_id]["name"] = name.strip()
        if legal_name is not None:
            data[firm_id]["legal_name"] = legal_name.strip()
        self._save(data)
        return {"id": firm_id, **data[firm_id]}
