"""
Supabase client singleton.

Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from environment variables.
The service_role key bypasses RLS — only use this from the server.
"""
import os
from typing import Optional

from supabase import create_client, Client


_client: Optional[Client] = None


def get_client() -> Client:
    """Return a memoised Supabase client. Raises if env vars are missing."""
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables "
                "must be set. See DEPLOY_RENDER.md."
            )
        _client = create_client(url, key)
    return _client


def get_bucket() -> str:
    """The Supabase Storage bucket name (defaults to 'gstr-data')."""
    return os.environ.get("SUPABASE_BUCKET", "gstr-data")
