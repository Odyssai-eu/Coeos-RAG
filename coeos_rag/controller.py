"""Apply a model change to a LightRAG instance, safely.

LightRAG reads LLM_MODEL / EMBEDDING_* from its process environment at startup only, so
a change means: edit the instance plist, restart the instance. This module does that with
the safeguards the architecture asks for (backup before mutation, refuse while the
pipeline is busy, verify the effect, roll back on failure, audit every action).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

import httpx

from . import certify as certify_mod
from . import embedder as emb
from . import plist
from .config import Settings
from .engine import EngineClient
from .launchd import Launchd
from .lightrag import LightRagClient
from .registry import (
    Corpus, EmbedderConfig, EmbedderIdentity, Registry, open_era,
)

Log = Callable[[str, bool, str], None]

POLL_INTERVAL_S = 2.0
# Concurrent LLM calls a LightRAG instance may send (its MAX_ASYNC). The right value depends
# on the pool behind the endpoint: one node cannot use more than one or two, a replica pool
# takes about two per node before requests outlast LightRAG's own timeout.
MAX_ASYNC_RANGE = (1, 64)
# launchctl exit codes meaning "this service is not loaded": not an error for bootout.
_NOT_LOADED_RC = (3, 113)


class ChangeError(Exception):
    """A change was refused or failed. `message` is meant for the operator."""

    def __init__(self, message: str, *, status: int = 409, rolled_back: Optional[bool] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.rolled_back = rolled_back


class Busy(ChangeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class Controller:
    def __init__(
        self,
        settings: Settings,
        registry: Registry,
        launchd: Launchd,
        http: httpx.AsyncClient,
        engine: EngineClient,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now_iso: Callable[[], str] = _now_iso,
    ) -> None:
        self.s = settings
        self.registry = registry
        self.launchd = launchd
        self.http = http
        self.engine = engine
        self._clock = clock
        self._sleep = sleep
        self._now_iso = now_iso
        self._locks: dict[str, asyncio.Lock] = {}
        self._era_cache: dict[str, tuple[tuple[float, int], list[int]]] = {}

    # ── helpers ────────────────────────────────────────────────────────────
    def _corpus(self, corpus_id: str) -> Corpus:
        try:
            return self.registry.corpus(corpus_id)
        except KeyError:
            raise ChangeError(f"unknown corpus {corpus_id!r}", status=404) from None

    def _lr(self, corpus: Corpus) -> LightRagClient:
        return LightRagClient(self.http, corpus.instance.url, self.s.http_timeout_s)

    def _llm_endpoint(self, corpus: Corpus, cfg: dict) -> Optional[str]:
        """The endpoint the instance calls for its LLM: what it reports live, else what the
        registry last applied, else the engine configured for the service."""
        ep = cfg.get("llm_binding_host") or corpus.llm_endpoint or self.engine.default_base
        return ep.rstrip("/") if ep else None

    def lock_for(self, corpus_id: str) -> asyncio.Lock:
        return self._locks.setdefault(corpus_id, asyncio.Lock())

    async def _wait(self, predicate: Callable[[], Awaitable[bool]], timeout: float) -> bool:
        t0 = self._clock()
        while True:
            if await predicate():
                return True
            if self._clock() - t0 >= timeout:
                return False
            await self._sleep(POLL_INTERVAL_S)

    async def _is_down(self, label: str, inst: LightRagClient) -> bool:
        loaded = await asyncio.to_thread(self.launchd.is_loaded, label)
        return (not loaded) and (await inst.health()) is None

    async def _wait_healthy(self, inst: LightRagClient) -> dict:
        holder: dict = {}

        async def up() -> bool:
            h = await inst.health()
            if h is not None:
                holder["h"] = h
                return True
            return False

        if not await self._wait(up, self.s.wait_up_s):
            raise ChangeError(f"the instance did not come back healthy within {int(self.s.wait_up_s)} s")
        return holder["h"]

    # ── generic apply ──────────────────────────────────────────────────────
    async def _apply(
        self,
        corpus_id: str,
        updates: dict[str, str],
        *,
        check: Callable[[dict], bool],
        describe_live: Callable[[dict], str],
        commit: Callable[[Corpus, str], None],
        log: Log,
    ) -> dict:
        lock = self.lock_for(corpus_id)
        if lock.locked():
            raise Busy("another change is already running on this corpus")
        async with lock:
            t0 = self._clock()
            corpus = self._corpus(corpus_id)
            inst = self._lr(corpus)
            plist_path = Path(corpus.instance.plist).expanduser()

            busy = await inst.busy()
            if busy:
                raise Busy(
                    "the instance is extracting documents (pipeline busy): restarting now could "
                    "corrupt its doc_status. Wait for it to be idle, or cancel the pipeline first."
                )
            log("precheck", True, "idle" if busy is False else "instance unreachable: repair mode")

            try:
                bak = plist.apply_env(plist_path, updates)
            except (plist.PlistError, OSError) as e:
                raise ChangeError(f"cannot edit the plist: {e}", status=500) from e
            log("backup", True, f"plist backed up as {bak.name}")

            try:
                await self._restart(corpus, inst, log)
                health = await self._wait_healthy(inst)
                cfg = health.get("configuration") or {}
                if not check(cfg):
                    raise ChangeError(f"the instance is up but reports {describe_live(cfg)}, not the requested values")
                log("verify", True, describe_live(cfg))
            except asyncio.CancelledError:
                plist.restore(bak, plist_path)  # best effort, synchronous: never leave a half-applied plist
                raise
            except Exception as e:  # noqa: BLE001 - any failure must trigger the rollback
                rolled = await self._rollback(corpus, inst, bak, plist_path, log)
                msg = e.message if isinstance(e, ChangeError) else f"{type(e).__name__}: {e}"
                raise ChangeError(
                    f"{msg} - {'rolled back to the previous configuration' if rolled else 'ROLLBACK FAILED, check the instance'}",
                    status=500, rolled_back=rolled,
                ) from e

            with self.registry.txn() as reg:
                commit(reg.corpora[corpus_id], self._now_iso())
            log("registry", True, "saved")
            return {
                "changed": True, "corpus": corpus_id, "applied": updates, "backup": str(bak),
                "took_s": round(self._clock() - t0, 1),
                "live": {k: cfg.get(k) for k in ("llm_model", "llm_binding_host", "max_async", "embedding_binding_host", "embedding_model")},
            }

    async def _restart(self, corpus: Corpus, inst: LightRagClient, log: Log) -> None:
        label, plist_path = corpus.instance.launchd_label, corpus.instance.plist
        rc, msg = await asyncio.to_thread(self.launchd.bootout, label)
        log("bootout", rc == 0 or rc in _NOT_LOADED_RC, msg or f"rc={rc}")
        if not await self._wait(lambda: self._is_down(label, inst), self.s.wait_down_s):
            raise ChangeError(f"the instance did not stop within {int(self.s.wait_down_s)} s")
        log("stopped", True, "")
        rc, msg = await asyncio.to_thread(self.launchd.bootstrap, str(Path(plist_path).expanduser()))
        if rc != 0:
            raise ChangeError(f"launchctl bootstrap failed (rc={rc}): {msg}")
        log("bootstrap", True, "service loaded, waiting for health")

    async def _rollback(self, corpus: Corpus, inst: LightRagClient, bak: Path, plist_path: Path, log: Log) -> bool:
        label = corpus.instance.launchd_label
        try:
            plist.restore(bak, plist_path)
            log("rollback", True, "previous plist restored")
            if await asyncio.to_thread(self.launchd.is_loaded, label):
                await asyncio.to_thread(self.launchd.bootout, label)
                await self._wait(lambda: self._is_down(label, inst), self.s.wait_down_s)
            rc, msg = await asyncio.to_thread(self.launchd.bootstrap, str(plist_path))
            if rc != 0:
                log("rollback", False, f"bootstrap failed (rc={rc}): {msg}")
                return False
            await self._wait_healthy(inst)
            log("rollback", True, "previous configuration is serving again")
            return True
        except Exception as e:  # noqa: BLE001
            log("rollback", False, f"{type(e).__name__}: {e}")
            return False

    # ── public operations ──────────────────────────────────────────────────
    async def apply_llm(self, corpus_id: str, model: str, log: Log, *, force: bool = False,
                        endpoint: Optional[str] = None, max_async: Optional[int] = None) -> dict:
        corpus = self._corpus(corpus_id)
        model = (model or "").strip()
        if not model:
            raise ChangeError("empty model", status=422)
        endpoint = (endpoint or "").strip().rstrip("/") or None
        if endpoint is not None:
            try:
                emb.validate_embedder_url(endpoint, what="LLM endpoint")
            except ValueError as e:
                raise ChangeError(str(e), status=422) from e
        if max_async is not None and not (MAX_ASYNC_RANGE[0] <= max_async <= MAX_ASYNC_RANGE[1]):
            raise ChangeError(f"max_async must be between {MAX_ASYNC_RANGE[0]} and {MAX_ASYNC_RANGE[1]}", status=422)
        live = await self._lr(corpus).health()
        cfg = (live or {}).get("configuration") or {}
        live_llm = cfg.get("llm_model")
        live_ep = (cfg.get("llm_binding_host") or "").rstrip("/") or None
        target_ep = endpoint or self._llm_endpoint(corpus, cfg)
        ready = await self.engine.ready_ids_at(target_ep)
        if not force:
            if ready is None:
                raise ChangeError("cannot verify that the model is loaded (engine unreachable or not configured)")
            if model not in ready:
                raise ChangeError(f"model {model!r} is not loaded on the engine")
        endpoint_changes = endpoint is not None and endpoint != live_ep
        async_changes = max_async is not None and cfg.get("max_async") != max_async
        if live_llm == model and corpus.llm == model and not endpoint_changes and not async_changes:
            return {"changed": False, "corpus": corpus_id, "note": "already applied"}

        updates = {"LLM_MODEL": model}
        if endpoint_changes:
            updates["LLM_BINDING_HOST"] = endpoint
        if async_changes:
            updates["MAX_ASYNC"] = str(max_async)

        def commit(c: Corpus, now: str) -> None:
            c.llm = model
            if target_ep:
                c.llm_endpoint = target_ep
            open_era(c, model, now, "Coeos-RAG settings")

        def check(c: dict) -> bool:
            if c.get("llm_model") != model:
                return False
            if async_changes and c.get("max_async") != max_async:
                return False
            return (not endpoint_changes) or (c.get("llm_binding_host") or "").rstrip("/") == endpoint

        def describe(c: dict) -> str:
            text = f"llm_model={c.get('llm_model')!r}"
            if endpoint_changes:
                text += f", llm_binding_host={c.get('llm_binding_host')!r}"
            if async_changes:
                text += f", max_async={c.get('max_async')!r}"
            return text

        return await self._apply(corpus_id, updates, check=check, describe_live=describe, commit=commit, log=log)

    async def apply_embedder(self, corpus_id: str, url: str, model: str, log: Log) -> dict:
        corpus = self._corpus(corpus_id)
        url, model = (url or "").strip().rstrip("/"), (model or "").strip()
        if not url or not model:
            raise ChangeError("url and model are required", status=422)
        try:
            emb.validate_embedder_url(url)
        except ValueError as e:
            raise ChangeError(str(e), status=422) from e
        ident = corpus.indexed_with.embedder
        if not ident.golden_vec:
            raise ChangeError(
                "this index is not certified yet: run 'Certify' first (the identity of its vector "
                "space must be known before its embedder can be changed)"
            )
        probe = await emb.probe(self.http, url, model, timeout=self.s.http_timeout_s * 4)
        if not probe.alive:
            raise ChangeError(f"the embedder is not alive: {probe.error}")
        if probe.dim != ident.dim:
            raise ChangeError(f"dimension {probe.dim} != {ident.dim} of this index: a different space, re-index needed")
        cos = emb.cosine(emb.decode_vec(ident.golden_vec), probe.vec or [])
        if cos < self.s.embed_cos_threshold:
            raise ChangeError(
                f"this is a different vector space (cosine {cos:.3f} < {self.s.embed_cos_threshold}): "
                "using it would make vector search return noise. Changing the space means re-indexing."
            )
        live = await self._lr(corpus).health()
        cfg = (live or {}).get("configuration") or {}
        if cfg.get("embedding_binding_host") == url and cfg.get("embedding_model") == model:
            return {"changed": False, "corpus": corpus_id, "note": "already applied"}

        def commit(c: Corpus, now: str) -> None:
            c.embedder = EmbedderConfig(url=url, model=model, dim=probe.dim or c.embedder.dim)

        return await self._apply(
            corpus_id, {"EMBEDDING_BINDING_HOST": url, "EMBEDDING_MODEL": model},
            check=lambda c: c.get("embedding_binding_host") == url and c.get("embedding_model") == model,
            describe_live=lambda c: f"embedding={c.get('embedding_binding_host')!r}/{c.get('embedding_model')!r}",
            commit=commit, log=log,
        )

    async def certify(self, corpus_id: str, log: Log, *, samples: int = 24) -> dict:
        """Re-embed stored chunks with the instance's embedder and compare with the stored
        vectors. On success the identity (weights, dimension, golden vector) is recorded."""
        corpus = self._corpus(corpus_id)
        live = await self._lr(corpus).health()
        cfg = (live or {}).get("configuration") or {}
        url = cfg.get("embedding_binding_host") or corpus.embedder.url
        model = cfg.get("embedding_model") or corpus.embedder.model
        vdb = Path(corpus.instance.workdir).expanduser() / "vdb_chunks.json"
        try:
            sample_list = await asyncio.to_thread(certify_mod.read_chunk_samples, vdb, samples)
        except (OSError, ValueError) as e:
            raise ChangeError(f"cannot read stored chunks: {e}", status=500) from e
        log("samples", True, f"{len(sample_list)} stored chunks read from {vdb.name}")

        async def embed_fn(texts):
            return await emb.embed(self.http, url, model, texts, timeout=self.s.http_timeout_s * 8)

        try:
            cert = await certify_mod.certify(embed_fn, sample_list, now=self._now_iso())
        except emb.EmbedderError as e:
            raise ChangeError(f"certification failed: {e}") from e
        log("recompute", True, f"min cosine {cert.min_cos}, median {cert.median_cos}")
        if cert.median_cos < self.s.cert_median_min or cert.min_cos < self.s.cert_min_floor:
            raise ChangeError(
                f"the embedder {model!r} at {url} does NOT produce this index's vector space "
                f"(median cosine {cert.median_cos}, min {cert.min_cos}; the same space gives a median "
                f">= {self.s.cert_median_min} and a floor >= {self.s.cert_min_floor}, a different one ~0). "
                "Nothing was recorded."
            )
        probe = await emb.probe(self.http, url, model, timeout=self.s.http_timeout_s * 4)
        if not probe.alive or probe.vec is None:
            raise ChangeError(f"embedder probe failed: {probe.error}")
        with self.registry.txn() as reg:
            c = reg.corpora[corpus_id]
            c.indexed_with.embedder = EmbedderIdentity(
                weights=probe.weights, dim=probe.dim or c.embedder.dim, golden_probe=emb.PROBE_TEXT,
                golden_vec=emb.encode_vec(probe.vec), certified=cert,
            )
            c.embedder = EmbedderConfig(url=url, model=model, dim=probe.dim or c.embedder.dim)
        log("registry", True, "identity recorded")
        return {"certified": True, "corpus": corpus_id, "weights": probe.weights, "dim": probe.dim,
                "min_cos": cert.min_cos, "median_cos": cert.median_cos, "samples": cert.samples}

    async def start(self, corpus_id: str, log: Log) -> dict:
        """Load the instance in launchd if it is not (recovery after an interrupted change)."""
        corpus = self._corpus(corpus_id)
        inst = self._lr(corpus)
        if await asyncio.to_thread(self.launchd.is_loaded, corpus.instance.launchd_label):
            return {"started": False, "note": "already loaded"}
        rc, msg = await asyncio.to_thread(
            self.launchd.bootstrap, str(Path(corpus.instance.plist).expanduser()))
        if rc != 0:
            raise ChangeError(f"launchctl bootstrap failed (rc={rc}): {msg}", status=500)
        log("bootstrap", True, "service loaded, waiting for health")
        await self._wait_healthy(inst)
        log("healthy", True, "")
        return {"started": True}

    async def probe_endpoint(self, url: str) -> dict:
        """List the models an OpenAI-compatible endpoint publishes (read-only)."""
        url = (url or "").strip().rstrip("/")
        try:
            emb.validate_embedder_url(url, what="LLM endpoint")
        except ValueError as e:
            raise ChangeError(str(e), status=422) from e
        models = await self.engine.models_at(url)
        return {"url": url, "reachable": models is not None,
                "models": [{"id": m.id, "ready": m.ready} for m in (models or [])]}

    # ── read model for the dashboard ───────────────────────────────────────
    async def _era_counts(self, corpus: Corpus) -> Optional[list[int]]:
        path = Path(corpus.instance.workdir).expanduser() / "kv_store_doc_status.json"
        try:
            st = path.stat()
        except OSError:
            return None
        key = (st.st_mtime, st.st_size)
        cached = self._era_cache.get(corpus.instance.workdir)
        eras = corpus.indexed_with.llm_eras
        if cached and cached[0] == key and len(cached[1]) == len(eras):
            return cached[1]
        try:
            counts = await asyncio.to_thread(certify_mod.era_counts, path, eras)
        except (OSError, ValueError):
            return None
        self._era_cache[corpus.instance.workdir] = (key, counts)
        return counts

    async def _corpus_view(self, corpus_id: str, corpus: Corpus) -> dict:
        inst = self._lr(corpus)
        health, busy, counts, loaded, probe, era_counts = await asyncio.gather(
            inst.health(), inst.busy(), inst.counts(),
            asyncio.to_thread(self.launchd.is_loaded, corpus.instance.launchd_label),
            emb.probe(self.http, corpus.embedder.url, corpus.embedder.model, timeout=self.s.http_timeout_s),
            self._era_counts(corpus),
        )
        cfg = (health or {}).get("configuration") or {}
        ep = self._llm_endpoint(corpus, cfg)
        ready = await self.engine.ready_ids_at(ep)
        ident = corpus.indexed_with.embedder
        cos: Optional[float] = None
        if probe.alive and probe.vec and ident.golden_vec:
            try:
                cos = round(emb.cosine(emb.decode_vec(ident.golden_vec), probe.vec), 5)
            except emb.EmbedderError:
                cos = None
        live_llm = cfg.get("llm_model")
        eras = [e.model_dump() for e in corpus.indexed_with.llm_eras]
        for i, e in enumerate(eras):
            e["docs"] = era_counts[i] if era_counts is not None else None
        return {
            "id": corpus_id, "label": corpus.label,
            "instance": {
                "url": corpus.instance.url, "public_url": corpus.instance.public_url,
                "launchd_label": corpus.instance.launchd_label,
                "up": health is not None, "loaded": loaded, "busy": busy, "counts": counts,
                "version": (health or {}).get("core_version"),
            },
            "llm": {
                "configured": corpus.llm, "live": live_llm,
                "in_sync": live_llm == corpus.llm if live_llm else None,
                "ready": None if ready is None else (corpus.llm in ready),
            },
            "engine": {
                "endpoint": {"configured": corpus.llm_endpoint,
                             "live": (cfg.get("llm_binding_host") or None), "effective": ep},
                "reachable": ready is not None,
                "ready_models": sorted(ready) if ready is not None else None,
                "max_async": cfg.get("max_async"),
            },
            "embedder": {
                "configured": corpus.embedder.model_dump(),
                "live": {"url": cfg.get("embedding_binding_host"), "model": cfg.get("embedding_model")},
                "alive": probe.alive, "latency_ms": probe.latency_ms, "dim": probe.dim,
                "weights": probe.weights, "error": probe.error,
                "cos_vs_index": cos,
                "same_space": None if cos is None else cos >= self.s.embed_cos_threshold,
            },
            "indexed_with": {
                "embedder": {
                    "weights": ident.weights, "dim": ident.dim,
                    "certified": ident.certified.model_dump() if ident.certified else None,
                },
                "llm_eras": eras,
            },
        }

    async def overview(self) -> dict:
        reg = self.registry.load()
        models = await self.engine.models()
        ready = None if models is None else {m.id for m in models if m.ready}
        views = await asyncio.gather(*[self._corpus_view(cid, c) for cid, c in reg.corpora.items()])
        return {
            "engine": {
                "configured": bool(self.engine.base_url), "reachable": models is not None,
                "ready_models": sorted(ready) if ready is not None else None,
            },
            "mutations_enabled": self.s.mutations_enabled,
            "corpora": list(views),
        }
