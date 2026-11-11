"""A faithful-enough simulation of launchd + a LightRAG instance + the services around it.

Faithful on the points that matter for the apply flow: a LightRAG process reads its
environment ONLY at startup (from the plist, at `bootstrap`); `bootout` stops it; an
instance that is stopped or still starting refuses connections.
"""
from __future__ import annotations

import json
import plistlib
import subprocess
from pathlib import Path

import httpx

from coeos_rag import embedder as emb
from coeos_rag.config import Settings
from coeos_rag.engine import EngineClient
from coeos_rag.launchd import Launchd
from coeos_rag.registry import (
    Corpus, EmbedderConfig, EmbedderIdentity, IndexedWith, Instance, Registry, RegistryModel,
)

INSTANCE = "http://127.0.0.1:8766"
EMBEDDER = "http://127.0.0.1:8765"
ENGINE = "http://127.0.0.1:8000"
ENGINE2 = "http://127.0.0.1:9000"
LABEL = "eu.test.lightrag"


def fake_vec(text: str, dim: int = 8, salt: str = "") -> list[float]:
    import hashlib
    out = []
    for i in range(dim):
        h = hashlib.sha256(f"{salt}{text}{i}".encode()).digest()
        out.append((int.from_bytes(h[:2], "big") - 32768) / 32768)
    return out


class FakeClock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


def write_plist(path: Path, **env) -> Path:
    data = {
        "Label": LABEL, "KeepAlive": True, "RunAtLoad": True, "WorkingDirectory": "/w",
        "ProgramArguments": ["/bin/python", "-m", "lightrag", "--port", "8766"],
        "EnvironmentVariables": {
            "LLM_MODEL": "old-model", "LLM_BINDING_HOST": f"{ENGINE}/v1",
            "EMBEDDING_BINDING_HOST": EMBEDDER, "EMBEDDING_MODEL": "emb", "EMBEDDING_DIM": "8", **env,
        },
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)
    return path


def drifted(vec: list[float], text: str, amount: float) -> list[float]:
    """Mix a vector with a text-dependent orthogonal-ish component (amount 0 = untouched)."""
    if amount <= 0:
        return vec
    noise = fake_vec(text, dim=len(vec), salt="batch-padding")
    return [v + amount * n for v, n in zip(vec, noise)]


class FakeWorld:
    """launchd + one LightRAG instance + an embedder + an engine, behind one HTTP transport."""

    def __init__(self, plist_path: Path) -> None:
        self.plist_path = plist_path
        self.loaded = True
        self.env = self._read_env()  # environment of the running process (frozen at start)
        self.starting_polls = 0  # health polls that still fail after a bootstrap
        self.busy = False
        self.fail_bootstrap = 0  # number of upcoming bootstrap calls that fail
        self.stuck_stopping = False  # bootout returns but the process never dies
        self.force_live_env: dict[str, str] = {}  # what the process reports regardless of the plist
        self.pending_start_polls = 0
        self.calls: list[str] = []
        # embedder
        self.embedder_alive = True
        self.embedder_salt = ""
        self.embedder_weights = "fake/weights"
        self.embedder_dim = 8
        self.embed_batches: list[int] = []  # size of every /embeddings request received
        self.batch_drift = 0.0  # >0: texts embedded together drift from their solo vector (padding)
        # engine
        self.engine_models: list[dict] | None = [
            {"id": "old-model", "x_odyssai": {"ready": True, "kind": "mlx-distributed"}},
            {"id": "new-model", "x_odyssai": {"ready": True, "kind": "mlx-distributed"}},
            {"id": "unloaded-model", "x_odyssai": {"ready": False, "kind": "replica"}},
        ]
        self.engine2_models: list[dict] | None = [
            {"id": "old-model", "x_odyssai": {"ready": True}},
            {"id": "moved-model", "x_odyssai": {"ready": True}},
        ]

    def _read_env(self) -> dict:
        with open(self.plist_path, "rb") as f:
            return dict(plistlib.load(f).get("EnvironmentVariables", {}))

    # launchctl
    def runner(self, argv):
        cmd = argv[1]
        self.calls.append(cmd)
        if cmd == "bootout":
            if not self.loaded:
                return subprocess.CompletedProcess(argv, 113, "", "Could not find service")
            if self.stuck_stopping:
                return subprocess.CompletedProcess(argv, 0, "", "")
            self.loaded = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "bootstrap":
            if self.loaded:
                return subprocess.CompletedProcess(argv, 5, "", "Bootstrap failed: already loaded")
            if self.fail_bootstrap > 0:
                self.fail_bootstrap -= 1
                return subprocess.CompletedProcess(argv, 5, "", "Bootstrap failed: 5: Input/output error")
            self.loaded = True
            self.env = self._read_env()
            self.pending_start_polls = self.starting_polls
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "print":
            return subprocess.CompletedProcess(argv, 0 if self.loaded else 113, "", "")
        raise AssertionError(f"unexpected launchctl command {argv}")

    # http
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        origin = f"{url.scheme}://{url.host}:{url.port}"
        if origin == INSTANCE:
            return self._instance(url.path)
        if origin == EMBEDDER:
            return self._embedder(request)
        if origin == ENGINE:
            return self._engine(url.path)
        if origin == ENGINE2:
            if self.engine2_models is None:
                raise httpx.ConnectError("engine2 down")
            if url.path == "/v1/models":
                return httpx.Response(200, json={"data": self.engine2_models})
            return httpx.Response(404)
        raise httpx.ConnectError(f"no route to {origin}")

    def _instance(self, path: str) -> httpx.Response:
        if not self.loaded:
            raise httpx.ConnectError("refused")
        if self.pending_start_polls > 0:
            self.pending_start_polls -= 1
            raise httpx.ConnectError("still starting")
        env = {**self.env, **self.force_live_env}
        if path == "/health":
            return httpx.Response(200, json={"status": "healthy", "core_version": "1.4.16", "configuration": {
                "llm_model": env.get("LLM_MODEL"),
                "llm_binding_host": env.get("LLM_BINDING_HOST"),
                "embedding_binding_host": env.get("EMBEDDING_BINDING_HOST"),
                "embedding_model": env.get("EMBEDDING_MODEL"),
            }})
        if path == "/documents/pipeline_status":
            return httpx.Response(200, json={"busy": self.busy})
        if path == "/documents/status_counts":
            return httpx.Response(200, json={"status_counts": {"processed": 3, "failed": 0, "all": 3}})
        return httpx.Response(404)

    def _embedder(self, request: httpx.Request) -> httpx.Response:
        if not self.embedder_alive:
            raise httpx.ConnectError("refused")
        if request.url.path.endswith("/embeddings"):
            texts = json.loads(request.content)["input"]
            self.embed_batches.append(len(texts))
            drift = self.batch_drift if len(texts) > 1 else 0.0
            return httpx.Response(200, json={"data": [
                {"index": i, "embedding": drifted(fake_vec(t, dim=self.embedder_dim, salt=self.embedder_salt), t, drift)}
                for i, t in enumerate(texts)]})
        if request.url.path == "/health":
            return httpx.Response(200, json={"embed_model": self.embedder_weights})
        return httpx.Response(404)

    def _engine(self, path: str) -> httpx.Response:
        if self.engine_models is None:
            raise httpx.ConnectError("engine down")
        if path == "/v1/models":
            return httpx.Response(200, json={"data": self.engine_models})
        return httpx.Response(404)


def make_settings(tmp_path: Path, **kw) -> Settings:
    base = dict(state_dir=tmp_path / "state", engine_url=ENGINE, admin_token="secret", launchd_uid=501,
                wait_down_s=20.0, wait_up_s=30.0, http_timeout_s=2.0, embed_cos_threshold=0.98)
    base.update(kw)
    return Settings(**base)


def make_corpus(plist_path: Path, workdir: Path, *, certified: bool = True) -> Corpus:
    ident = EmbedderIdentity(dim=8)
    if certified:
        ident = EmbedderIdentity(weights="fake/weights", dim=8, golden_probe=emb.PROBE_TEXT,
                                 golden_vec=emb.encode_vec(fake_vec(emb.PROBE_TEXT)))
    return Corpus(
        label="Alpha",
        instance=Instance(url=INSTANCE, launchd_label=LABEL, plist=str(plist_path), workdir=str(workdir)),
        llm="old-model",
        embedder=EmbedderConfig(url=EMBEDDER, model="emb", dim=8),
        indexed_with=IndexedWith(embedder=ident),
    )


def build(tmp_path: Path, *, certified: bool = True):
    """Returns (controller, world, clock, registry, http_client)."""
    from coeos_rag.controller import Controller

    plist_path = write_plist(tmp_path / "lightrag.plist")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    world = FakeWorld(plist_path)
    clock = FakeClock()
    settings = make_settings(tmp_path)
    reg = Registry(settings.registry_path)
    reg.save(RegistryModel(corpora={"alpha": make_corpus(plist_path, workdir, certified=certified)}))
    http = httpx.AsyncClient(transport=httpx.MockTransport(world.handler))
    engine = EngineClient(http, settings.engine_url, ttl_s=0)
    ctl = Controller(settings, reg, Launchd(501, world.runner), http, engine,
                     clock=clock, sleep=clock.sleep, now_iso=lambda: "2026-10-02T12:00:00")
    return ctl, world, clock, reg, http
