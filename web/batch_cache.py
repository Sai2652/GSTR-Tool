"""
Batch state cache.

When the user uploads sheets, we parse them once and cache the result so
that the review screen can show parsed invoices, the user can pick what
to exclude, and the JSON generation step can use the cached + filtered data
instead of re-parsing.

State is held in memory (per-process) keyed by a UUID. Auto-expires after
60 minutes to avoid unbounded growth.
"""
import time
import uuid
from threading import Lock


class BatchStateCache:
    def __init__(self, ttl_seconds: int = 3600):
        self._states = {}   # batch_id -> { "data": ..., "ts": ... }
        self._lock = Lock()
        self.ttl = ttl_seconds

    def _evict_expired(self):
        now = time.time()
        expired = [k for k, v in self._states.items() if now - v["ts"] > self.ttl]
        for k in expired:
            self._states.pop(k, None)

    def put(self, data: dict) -> str:
        """Store a payload, return a batch ID."""
        with self._lock:
            self._evict_expired()
            batch_id = uuid.uuid4().hex
            self._states[batch_id] = {"data": data, "ts": time.time()}
            return batch_id

    def get(self, batch_id: str) -> dict:
        """Retrieve by ID, or None if missing/expired."""
        with self._lock:
            self._evict_expired()
            entry = self._states.get(batch_id)
            return entry["data"] if entry else None

    def update(self, batch_id: str, data: dict) -> bool:
        with self._lock:
            self._evict_expired()
            if batch_id in self._states:
                self._states[batch_id]["data"] = data
                self._states[batch_id]["ts"] = time.time()
                return True
            return False

    def delete(self, batch_id: str) -> bool:
        with self._lock:
            return self._states.pop(batch_id, None) is not None
