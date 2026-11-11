"""HTTP API and dashboard of the OdyRAG control plane.

Reads are open on the LAN. Every mutation needs the admin bearer token, and stays
disabled altogether until a token is configured. Mutations run as background jobs
(a restart takes a minute or more); the dashboard polls the job.
"""
from __future__ import annotations

import asyncio
import hmac
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import __version__
from .config import Settings
from .controller import ChangeError, Controller, Log
from .engine import EngineClient
from .jobs import JobManager
from .launchd import Launchd
from .registry import Registry

STATIC = Path(__file__).parent / "static"
INDEX = STATIC / "index.html"


class LlmChange(BaseModel):
    model: str
    force: bool = False
    endpoint: Optional[str] = None


class EndpointProbe(BaseModel):
    url: str


class EmbedderChange(BaseModel):
    url: str
    model: str


def create_app(
    settings: Optional[Settings] = None,
    *,
    http: Optional[httpx.AsyncClient] = None,
    launchd: Optional[Launchd] = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    registry = Registry(settings.registry_path)
    jobs = JobManager(settings.actions_log_path)
    state: dict = {}
    background: set[asyncio.Task] = set()  # strong refs: asyncio keeps only weak ones

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = http or httpx.AsyncClient()
        state["controller"] = Controller(
            settings, registry, launchd or Launchd(settings.launchd_uid), client,
            EngineClient(client, settings.engine_url, settings.http_timeout_s),
        )
        yield
        if http is None:
            await client.aclose()

    app = FastAPI(title="Coeos-Rag control plane", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def controller() -> Controller:
        return state["controller"]

    def require_admin(authorization: Optional[str] = Header(default=None)) -> None:
        if not settings.mutations_enabled:
            raise HTTPException(403, "mutations are disabled: no admin token is configured")
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not supplied or not hmac.compare_digest(supplied.encode(), (settings.admin_token or "").encode()):
            raise HTTPException(401, "missing or wrong admin token")

    def launch(corpus_id: str, kind: str, work: Callable[[Log], Awaitable[dict]]) -> dict:
        try:
            registry.corpus(corpus_id)
        except KeyError:
            raise HTTPException(404, f"unknown corpus {corpus_id!r}") from None
        job = jobs.create(corpus_id, kind)

        def log(step: str, ok: bool = True, detail: str = "") -> None:
            jobs.step(job, step, ok, detail)

        async def runner() -> None:
            try:
                jobs.finish(job, await work(log))
            except ChangeError as e:
                jobs.step(job, "refused" if e.rolled_back is None else "failed", False, e.message)
                jobs.fail(job, e.message)
            except Exception as e:  # noqa: BLE001 - a job must always end in a terminal state
                traceback.print_exc(file=sys.stderr)
                jobs.fail(job, f"internal error: {type(e).__name__}: {e}")

        task = asyncio.create_task(runner())
        background.add(task)
        task.add_done_callback(background.discard)
        return {"job_id": job.id, "status_url": f"/api/jobs/{job.id}"}

    # ── reads ──────────────────────────────────────────────────────────────
    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__, "mutations_enabled": settings.mutations_enabled}

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        # Read fresh on each request: a UI-only change needs no restart. The version stamps
        # the asset URLs so a browser never runs a stale script after a deploy.
        return HTMLResponse(INDEX.read_text(encoding="utf-8").replace("__VERSION__", __version__))

    @app.get("/api/overview")
    async def overview() -> dict:
        return await controller().overview()

    @app.get("/api/jobs")
    async def list_jobs() -> list[dict]:
        return jobs.recent()

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        return job.public()

    @app.get("/api/actions")
    async def actions() -> list[dict]:
        return jobs.audit_tail()

    # ── mutations (admin token required) ───────────────────────────────────
    @app.post("/api/corpora/{corpus_id}/llm", status_code=202, dependencies=[Depends(require_admin)])
    async def change_llm(corpus_id: str, body: LlmChange) -> dict:
        return launch(corpus_id, "llm", lambda log: controller().apply_llm(
            corpus_id, body.model, log, force=body.force, endpoint=body.endpoint))

    @app.post("/api/corpora/{corpus_id}/embedder", status_code=202, dependencies=[Depends(require_admin)])
    async def change_embedder(corpus_id: str, body: EmbedderChange) -> dict:
        return launch(corpus_id, "embedder", lambda log: controller().apply_embedder(corpus_id, body.url, body.model, log))

    @app.post("/api/corpora/{corpus_id}/certify", status_code=202, dependencies=[Depends(require_admin)])
    async def certify(corpus_id: str) -> dict:
        return launch(corpus_id, "certify", lambda log: controller().certify(corpus_id, log))

    @app.post("/api/corpora/{corpus_id}/start", status_code=202, dependencies=[Depends(require_admin)])
    async def start(corpus_id: str) -> dict:
        return launch(corpus_id, "start", lambda log: controller().start(corpus_id, log))

    @app.post("/api/endpoint/models", dependencies=[Depends(require_admin)])
    async def endpoint_models(body: EndpointProbe) -> dict:
        try:
            return await controller().probe_endpoint(body.url)
        except ChangeError as e:
            raise HTTPException(e.status, e.message) from None

    return app
