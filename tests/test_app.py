import asyncio

import httpx
import pytest

from odyrag import __version__
from odyrag.app import create_app
from odyrag.launchd import Launchd
from odyrag.registry import Registry, RegistryModel
from tests.fakes import ENGINE2, FakeWorld, make_corpus, make_settings, write_plist

AUTH = {"Authorization": "Bearer secret"}


@pytest.fixture
async def env(tmp_path):
    plist_path = write_plist(tmp_path / "lightrag.plist")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    world = FakeWorld(plist_path)
    settings = make_settings(tmp_path)
    reg = Registry(settings.registry_path)
    reg.save(RegistryModel(corpora={"alpha": make_corpus(plist_path, workdir)}))
    http = httpx.AsyncClient(transport=httpx.MockTransport(world.handler))
    app = create_app(settings, http=http, launchd=Launchd(501, world.runner))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://odyrag") as c:
            yield c, world, settings, reg, tmp_path


async def wait_job(c, job_id, tries=300):
    for _ in range(tries):
        job = (await c.get(f"/api/jobs/{job_id}")).json()
        if job["status"] != "running":
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


async def test_health_and_dashboard_are_served(env):
    c, *_ = env
    h = (await c.get("/health")).json()
    assert h["status"] == "ok" and h["mutations_enabled"] is True
    page = await c.get("/")
    assert page.status_code == 200 and "Coeos-Rag" in page.text and f"/static/app.js?v={__version__}" in page.text
    assert (await c.get("/static/app.js")).status_code == 200


async def test_overview_endpoint(env):
    c, *_ = env
    v = (await c.get("/api/overview")).json()
    assert v["corpora"][0]["id"] == "alpha" and v["corpora"][0]["llm"]["live"] == "old-model"


async def test_mutations_need_the_token(env):
    c, world, *_ = env
    r = await c.post("/api/corpora/alpha/llm", json={"model": "new-model"})
    assert r.status_code == 401
    r = await c.post("/api/corpora/alpha/llm", json={"model": "new-model"}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert "bootout" not in world.calls


async def test_mutations_are_disabled_without_a_configured_token(tmp_path):
    plist_path = write_plist(tmp_path / "lightrag.plist")
    world = FakeWorld(plist_path)
    settings = make_settings(tmp_path, admin_token=None)
    Registry(settings.registry_path).save(RegistryModel(corpora={"alpha": make_corpus(plist_path, tmp_path)}))
    app = create_app(settings, http=httpx.AsyncClient(transport=httpx.MockTransport(world.handler)),
                     launchd=Launchd(501, world.runner))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://odyrag") as c:
            r = await c.post("/api/corpora/alpha/llm", json={"model": "new-model"}, headers=AUTH)
            assert r.status_code == 403 and "disabled" in r.json()["detail"]
            assert (await c.get("/api/overview")).json()["mutations_enabled"] is False


async def test_llm_change_runs_as_a_job_and_lands_in_the_audit_log(env):
    c, world, settings, reg, _ = env
    r = await c.post("/api/corpora/alpha/llm", json={"model": "new-model"}, headers=AUTH)
    assert r.status_code == 202
    job = await wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["live"]["llm_model"] == "new-model"
    assert [s["step"] for s in job["steps"]][-2:] == ["verify", "registry"]
    assert reg.corpus("alpha").llm == "new-model"
    assert (await c.get("/api/overview")).json()["corpora"][0]["llm"]["live"] == "new-model"
    audit = (await c.get("/api/actions")).json()
    assert audit[0]["kind"] == "llm" and audit[0]["status"] == "done" and audit[0]["corpus"] == "alpha"


async def test_a_refused_change_is_a_failed_job_with_a_readable_reason(env):
    c, world, *_ = env
    world.busy = True
    r = await c.post("/api/corpora/alpha/llm", json={"model": "new-model"}, headers=AUTH)
    job = await wait_job(c, r.json()["job_id"])
    assert job["status"] == "failed" and "extracting" in job["error"]
    assert job["steps"][-1]["step"] == "refused"
    assert "bootout" not in world.calls


async def test_failed_change_is_reported_as_failed_with_rollback(env):
    c, world, _, reg, tmp = env
    world.fail_bootstrap = 1
    r = await c.post("/api/corpora/alpha/llm", json={"model": "new-model"}, headers=AUTH)
    job = await wait_job(c, r.json()["job_id"])
    assert job["status"] == "failed" and "rolled back" in job["error"]
    assert reg.corpus("alpha").llm == "old-model"


async def test_unknown_corpus_and_unknown_job(env):
    c, *_ = env
    assert (await c.post("/api/corpora/nope/llm", json={"model": "m"}, headers=AUTH)).status_code == 404
    assert (await c.get("/api/jobs/doesnotexist")).status_code == 404


async def test_invalid_body_is_rejected(env):
    c, *_ = env
    assert (await c.post("/api/corpora/alpha/llm", json={}, headers=AUTH)).status_code == 422


async def test_embedder_and_start_endpoints_exist(env):
    c, world, *_ = env
    r = await c.post("/api/corpora/alpha/embedder", json={"url": "http://8.8.8.8:1", "model": "x"}, headers=AUTH)
    job = await wait_job(c, r.json()["job_id"])
    assert job["status"] == "failed" and "public" in job["error"]
    r = await c.post("/api/corpora/alpha/start", headers=AUTH)
    assert (await wait_job(c, r.json()["job_id"]))["result"] == {"started": False, "note": "already loaded"}


async def test_llm_change_can_move_the_endpoint(env):
    c, world, settings, reg, _ = env
    r = await c.post("/api/corpora/alpha/llm", json={"model": "moved-model", "endpoint": f"{ENGINE2}/v1"}, headers=AUTH)
    assert r.status_code == 202
    job = await wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["live"]["llm_binding_host"] == f"{ENGINE2}/v1"


async def test_endpoint_probe_needs_the_token_and_lists_models(env):
    c, *_ = env
    assert (await c.post("/api/endpoint/models", json={"url": f"{ENGINE2}/v1"})).status_code == 401
    r = await c.post("/api/endpoint/models", json={"url": f"{ENGINE2}/v1"}, headers=AUTH)
    assert r.status_code == 200 and "moved-model" in {m["id"] for m in r.json()["models"]}
    r = await c.post("/api/endpoint/models", json={"url": "http://8.8.8.8:1/v1"}, headers=AUTH)
    assert r.status_code == 422
