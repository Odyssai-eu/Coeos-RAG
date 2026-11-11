"""Read-only client for the inference engine's public OpenAI-compatible API.

The engine is only ever a *client target*: Coeos-RAG lists which models are ready so a
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
        self._cache: dict[str, tuple[float, list[EngineModel]]] = {}

    @property
    def default_base(self) -> Optional[str]:
        """OpenAI-compatible base of the engine configured for the service, or None."""
        return f"{self.base_url}/v1" if self.base_url else None

    async def models_at(self, base: Optional[str]) -> Optional[list[EngineModel]]:
        """Models published at `base` (an OpenAI-compatible base URL such as http://host:8000/v1).
        None when `base` is empty or unreachable (callers must then refuse rather than guess)."""
        if not base:
            return None
        base = base.rstrip("/")
        now = self._clock()
        hit = self._cache.get(base)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            r = await self._c.get(f"{base}/models", timeout=self._timeout)
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
        self._cache[base] = (now, models)
        return models

    async def ready_ids_at(self, base: Optional[str]) -> Optional[set[str]]:
        models = await self.models_at(base)
        return None if models is None else {m.id for m in models if m.ready}

    async def models(self) -> Optional[list[EngineModel]]:
        return await self.models_at(self.default_base)

    async def ready_ids(self) -> Optional[set[str]]:
        return await self.ready_ids_at(self.default_base)
