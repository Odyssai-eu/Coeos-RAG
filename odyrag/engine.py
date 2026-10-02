"""Read-only client for the inference engine's public OpenAI-compatible API.

The engine is only ever a *client target*: OdyRAG lists which models are ready so a
corpus is never pointed at a model that is not loaded. Nothing is written to it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass(frozen=True)
class EngineModel:
    id: str
    kind: Optional[str]
    ready: bool


class EngineClient:
    def __init__(self, client: httpx.AsyncClient, base_url: Optional[str], timeout: float = 8.0,
                 ttl_s: float = 5.0, clock=time.monotonic) -> None:
        self._c = client
        self.base_url = base_url
        self._timeout = timeout
        self._ttl = ttl_s
        self._clock = clock
        self._cache: Optional[tuple[float, list[EngineModel]]] = None

    async def models(self) -> Optional[list[EngineModel]]:
        """Models the engine can serve right now. None when the engine is not configured
        or unreachable (callers must then refuse rather than guess)."""
        if not self.base_url:
            return None
        now = self._clock()
        if self._cache and now - self._cache[0] < self._ttl:
            return self._cache[1]
        try:
            r = await self._c.get(f"{self.base_url}/v1/models", timeout=self._timeout)
            r.raise_for_status()
            data = r.json()["data"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None
        models: list[EngineModel] = []
        for m in data:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                continue
            x = m.get("x_odyssai") if isinstance(m.get("x_odyssai"), dict) else {}
            models.append(EngineModel(id=m["id"], kind=x.get("kind"), ready=x.get("ready", True) is not False))
        self._cache = (now, models)
        return models

    async def ready_ids(self) -> Optional[set[str]]:
        models = await self.models()
        return None if models is None else {m.id for m in models if m.ready}
