"""Read-only client for one LightRAG instance (the standard LightRAG REST API)."""
from __future__ import annotations

from typing import Optional

import httpx


class LightRagClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, timeout: float = 8.0) -> None:
        self._c = client
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def _get(self, path: str) -> Optional[dict]:
        try:
            r = await self._c.get(f"{self.base_url}{path}", timeout=self._timeout)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            body = r.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    async def health(self) -> Optional[dict]:
        """The /health payload, or None when the instance is unreachable or unhealthy."""
        body = await self._get("/health")
        if body is None or body.get("status") != "healthy":
            return None
        return body

    async def busy(self) -> Optional[bool]:
        """True while the pipeline extracts documents. None when unknown."""
        body = await self._get("/documents/pipeline_status")
        if body is None or "busy" not in body:
            return None
        return bool(body["busy"])

    async def counts(self) -> Optional[dict]:
        body = await self._get("/documents/status_counts")
        return None if body is None else body.get("status_counts")
