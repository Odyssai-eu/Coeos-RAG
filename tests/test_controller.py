import asyncio
import json

import pytest

from coeos_rag import embedder as emb
from coeos_rag import plist
from coeos_rag.controller import Busy, ChangeError
from tests.fakes import build, fake_vec, EMBEDDER, ENGINE, ENGINE2

import base64, struct, zlib


def collector():
    logs = []
    return logs, (lambda step, ok, detail="": logs.append((step, ok, detail)))


def plist_env(tmp_path):
    return plist.read_env(tmp_path / "lightrag.plist")


# ── apply_llm: the happy path and its guards ──────────────────────────────

async def test_apply_llm_success_full_cycle(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    logs, log = collector()
    res = await ctl.apply_llm("alpha", "new-model", log)
    assert res["changed"] and res["live"]["llm_model"] == "new-model"
    assert plist_env(tmp_path)["LLM_MODEL"] == "new-model"
    assert [s for s, _, _ in logs] == ["precheck", "backup", "bootout", "stopped", "bootstrap", "verify", "registry"]
    assert all(ok for _, ok, _ in logs)
    c = reg.corpus("alpha")
    assert c.llm == "new-model"
    assert [(e.model, e.end) for e in c.indexed_with.llm_eras] == [("new-model", None)]
    baks = list(tmp_path.glob("lightrag.plist.bak-odyrag-*"))
    assert len(baks) == 1 and plist.read_env(baks[0])["LLM_MODEL"] == "old-model"
    assert world.calls.index("bootout") < world.calls.index("bootstrap")  # stop before start


async def test_apply_llm_waits_for_a_slow_start(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    world.starting_polls = 5
    _, log = collector()
    t0 = clock()
    res = await ctl.apply_llm("alpha", "new-model", log)
    assert res["changed"] and clock() - t0 >= 5 * 2.0  # polled with the injected clock


async def test_apply_llm_noop_when_already_applied(tmp_path):
    ctl, world, *_ = build(tmp_path)
    _, log = collector()
    res = await ctl.apply_llm("alpha", "old-model", log)
    assert res == {"changed": False, "corpus": "alpha", "note": "already applied"}
    assert "bootout" not in world.calls


async def test_apply_llm_refuses_model_not_ready_unless_forced(tmp_path):
    ctl, world, *_ = build(tmp_path)
    _, log = collector()
    with pytest.raises(ChangeError, match="not loaded"):
        await ctl.apply_llm("alpha", "unloaded-model", log)
    with pytest.raises(ChangeError, match="not loaded"):
        await ctl.apply_llm("alpha", "ghost-model", log)
    assert "bootout" not in world.calls and plist_env(tmp_path)["LLM_MODEL"] == "old-model"
    res = await ctl.apply_llm("alpha", "unloaded-model", log, force=True)
    assert res["changed"]


async def test_apply_llm_refuses_when_engine_unreachable_unless_forced(tmp_path):
    ctl, world, *_ = build(tmp_path)
    world.engine_models = None
    _, log = collector()
    with pytest.raises(ChangeError, match="cannot verify"):
        await ctl.apply_llm("alpha", "new-model", log)
    assert (await ctl.apply_llm("alpha", "new-model", log, force=True))["changed"]


async def test_apply_llm_rejects_empty_and_unknown_corpus(tmp_path):
    ctl, *_ = build(tmp_path)
    _, log = collector()
    with pytest.raises(ChangeError) as e:
        await ctl.apply_llm("alpha", "  ", log)
    assert e.value.status == 422
    with pytest.raises(ChangeError) as e:
        await ctl.apply_llm("nope", "new-model", log)
    assert e.value.status == 404


async def test_never_restarts_a_busy_instance(tmp_path):
    ctl, world, *_ = build(tmp_path)
    world.busy = True
    _, log = collector()
    with pytest.raises(Busy, match="doc_status"):
        await ctl.apply_llm("alpha", "new-model", log)
    assert "bootout" not in world.calls
    assert plist_env(tmp_path)["LLM_MODEL"] == "old-model"
    assert not list(tmp_path.glob("lightrag.plist.bak-odyrag-*"))  # refused before any mutation


async def test_concurrent_changes_on_one_corpus_are_serialized(tmp_path):
    ctl, world, *_ = build(tmp_path)
    _, log = collector()
    results = await asyncio.gather(
        ctl.apply_llm("alpha", "new-model", log), ctl.apply_llm("alpha", "new-model", log),
        return_exceptions=True)
    kinds = sorted(type(r).__name__ for r in results)
    assert kinds in (["Busy", "dict"], ["dict", "dict"])  # second is refused, or sees "already applied"
    assert world.calls.count("bootout") == 1


# ── failures: every one must roll back ───────────────────────────────────

async def test_bootstrap_failure_rolls_back_and_serves_the_old_config(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    world.fail_bootstrap = 1  # the new config fails to load, the rollback load succeeds
    logs, log = collector()
    with pytest.raises(ChangeError) as e:
        await ctl.apply_llm("alpha", "new-model", log)
    assert e.value.rolled_back is True and "rolled back" in e.value.message
    assert plist_env(tmp_path)["LLM_MODEL"] == "old-model"
    assert world.env["LLM_MODEL"] == "old-model" and world.loaded
    assert reg.corpus("alpha").llm == "old-model" and reg.corpus("alpha").indexed_with.llm_eras == []
    assert ("rollback", True, "previous configuration is serving again") in logs


async def test_instance_up_with_wrong_config_is_detected_and_rolled_back(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    world.force_live_env = {"LLM_MODEL": "stale"}  # the process ignores what the plist says
    _, log = collector()
    with pytest.raises(ChangeError, match="reports .*stale") as e:
        await ctl.apply_llm("alpha", "new-model", log)
    assert e.value.rolled_back is True
    assert plist_env(tmp_path)["LLM_MODEL"] == "old-model" and reg.corpus("alpha").llm == "old-model"


async def test_instance_that_never_comes_back_reports_a_failed_rollback(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    world.starting_polls = 10_000
    _, log = collector()
    with pytest.raises(ChangeError) as e:
        await ctl.apply_llm("alpha", "new-model", log)
    assert e.value.rolled_back is False and "ROLLBACK FAILED" in e.value.message
    assert plist_env(tmp_path)["LLM_MODEL"] == "old-model"  # the plist is at least restored
    assert reg.corpus("alpha").llm == "old-model"


async def test_instance_that_will_not_stop_is_rolled_back(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    world.stuck_stopping = True
    _, log = collector()
    with pytest.raises(ChangeError, match="did not stop") as e:
        await ctl.apply_llm("alpha", "new-model", log)
    assert plist_env(tmp_path)["LLM_MODEL"] == "old-model" and reg.corpus("alpha").llm == "old-model"


async def test_repair_mode_when_the_instance_is_down(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    world.loaded = False  # e.g. crashed on a dead model id
    logs, log = collector()
    res = await ctl.apply_llm("alpha", "new-model", log)
    assert res["changed"] and world.loaded and world.env["LLM_MODEL"] == "new-model"
    assert logs[0][2].startswith("instance unreachable")


# ── embedder: identity before anything ───────────────────────────────────

async def test_embedder_change_requires_a_certified_index(tmp_path):
    ctl, world, *_ = build(tmp_path, certified=False)
    _, log = collector()
    with pytest.raises(ChangeError, match="not certified"):
        await ctl.apply_embedder("alpha", EMBEDDER, "emb2", log)
    assert "bootout" not in world.calls


async def test_embedder_same_space_is_applied(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    _, log = collector()
    res = await ctl.apply_embedder("alpha", EMBEDDER, "emb2", log)
    assert res["changed"] and plist_env(tmp_path)["EMBEDDING_MODEL"] == "emb2"
    assert reg.corpus("alpha").embedder.model == "emb2"


async def test_embedder_noop_when_already_applied(tmp_path):
    ctl, world, *_ = build(tmp_path)
    _, log = collector()
    assert (await ctl.apply_embedder("alpha", EMBEDDER, "emb", log))["changed"] is False
    assert "bootout" not in world.calls


async def test_embedder_in_a_different_space_is_refused_even_with_same_dimension(tmp_path):
    ctl, world, *_ = build(tmp_path)
    world.embedder_salt = "other"  # same 8 dims, different space: two different embedders of one dimension
    _, log = collector()
    with pytest.raises(ChangeError, match="different vector space"):
        await ctl.apply_embedder("alpha", EMBEDDER, "emb2", log)
    assert "bootout" not in world.calls and plist_env(tmp_path)["EMBEDDING_MODEL"] == "emb"


async def test_embedder_with_other_dimension_dead_or_public_is_refused(tmp_path):
    ctl, world, *_ = build(tmp_path)
    _, log = collector()
    world.embedder_dim = 4
    with pytest.raises(ChangeError, match="dimension"):
        await ctl.apply_embedder("alpha", EMBEDDER, "emb2", log)
    world.embedder_dim = 8
    world.embedder_alive = False
    with pytest.raises(ChangeError, match="not alive"):
        await ctl.apply_embedder("alpha", EMBEDDER, "emb2", log)
    with pytest.raises(ChangeError) as e:
        await ctl.apply_embedder("alpha", "http://8.8.8.8:8765", "emb2", log)
    assert e.value.status == 422 and "public" in e.value.message


# ── certification ────────────────────────────────────────────────────────

def write_vdb(workdir, n=10, salt=""):
    def stored(vec):
        return base64.b64encode(zlib.compress(struct.pack(f"<{len(vec)}e", *vec))).decode()
    items = [{"__id__": f"c{i}", "content": f"chunk number {i}", "vector": stored(fake_vec(f"chunk number {i}", salt=salt))}
             for i in range(n)]
    (workdir / "vdb_chunks.json").write_text(json.dumps({"embedding_dim": 8, "data": items}))


async def test_certify_records_the_identity_of_the_index(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path, certified=False)
    write_vdb(tmp_path / "workdir")
    logs, log = collector()
    res = await ctl.certify("alpha", log, samples=6)
    assert res["certified"] and res["min_cos"] > 0.99 and res["weights"] == "fake/weights"
    ident = reg.corpus("alpha").indexed_with.embedder
    assert ident.certified.samples == 6 and ident.golden_vec and ident.golden_probe == emb.PROBE_TEXT
    assert reg.corpus("alpha").embedder.dim == 8


async def test_certify_refuses_an_embedder_that_is_not_the_index_space(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path, certified=False)
    write_vdb(tmp_path / "workdir", salt="the-index-was-built-elsewhere")
    _, log = collector()
    with pytest.raises(ChangeError, match="does NOT produce"):
        await ctl.certify("alpha", log, samples=6)
    assert reg.corpus("alpha").indexed_with.embedder.certified is None  # nothing recorded


async def test_certify_sends_each_chunk_alone_to_the_embedder(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path, certified=False)
    write_vdb(tmp_path / "workdir")
    world.batch_drift = 0.8  # batching would corrupt vectors: only per-text calls reproduce the index
    _, log = collector()
    world.embed_batches.clear()
    res = await ctl.certify("alpha", log, samples=6)
    assert res["certified"] and res["min_cos"] > 0.999
    assert world.embed_batches[:6] == [1] * 6


async def test_certify_tolerates_an_index_built_in_noisy_batches_but_not_another_space(tmp_path):
    """The stored vectors of a real index come from batches (cos 0.76..1.0 vs a solo recompute);
    a different embedder lands around 0. The criteria must separate the two."""
    ctl, world, clock, reg, _ = build(tmp_path, certified=False)
    workdir = tmp_path / "workdir"
    def stored(vec):
        return base64.b64encode(zlib.compress(struct.pack(f"<{len(vec)}e", *vec))).decode()
    from tests.fakes import drifted
    items = [{"__id__": f"c{i}", "content": f"chunk number {i}",
              "vector": stored(drifted(fake_vec(f"chunk number {i}"), f"chunk number {i}", 0.45))} for i in range(10)]
    (workdir / "vdb_chunks.json").write_text(json.dumps({"embedding_dim": 8, "data": items}))
    _, log = collector()
    res = await ctl.certify("alpha", log, samples=8)
    assert res["certified"] and 0.5 <= res["min_cos"] < 1.0 and res["median_cos"] >= 0.8

    (tmp_path / "other").mkdir()
    ctl2, world2, _, reg2, _ = build(tmp_path / "other", certified=False)
    write_vdb(tmp_path / "other" / "workdir", salt="a-different-embedder")
    with pytest.raises(ChangeError, match="does NOT produce"):
        await ctl2.certify("alpha", log, samples=8)


async def test_certify_without_a_vdb_is_a_clean_error(tmp_path):
    ctl, *_ = build(tmp_path, certified=False)
    _, log = collector()
    with pytest.raises(ChangeError, match="cannot read stored chunks"):
        await ctl.certify("alpha", log)


# ── start (recovery) and the dashboard read model ────────────────────────

async def test_start_loads_a_stopped_instance_only(tmp_path):
    ctl, world, *_ = build(tmp_path)
    _, log = collector()
    assert (await ctl.start("alpha", log))["started"] is False
    world.loaded = False
    assert (await ctl.start("alpha", log))["started"] is True and world.loaded


async def test_overview_reports_live_state_drift_and_embedder_identity(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    (tmp_path / "workdir" / "kv_store_doc_status.json").write_text(json.dumps({
        "d1": {"created_at": "2026-06-21T10:00:00+00:00"}, "d2": {"created_at": "2026-09-25T10:00:00+00:00"}}))
    with reg.txn() as m:
        from coeos_rag.registry import Era
        m.corpora["alpha"].indexed_with.llm_eras = [
            Era(model="a", start="2026-06-01", end="2026-09-01", source="env backups"),
            Era(model="old-model", start="2026-09-01", end=None, source="settings")]
    v = await ctl.overview()
    assert v["engine"]["reachable"] and "new-model" in v["engine"]["ready_models"]
    assert "unloaded-model" not in v["engine"]["ready_models"] and v["mutations_enabled"] is True
    c = v["corpora"][0]
    assert c["instance"]["up"] and c["instance"]["busy"] is False and c["instance"]["counts"]["processed"] == 3
    assert c["llm"] == {"configured": "old-model", "live": "old-model", "in_sync": True, "ready": True}
    assert c["embedder"]["alive"] and c["embedder"]["same_space"] is True and c["embedder"]["cos_vs_index"] > 0.999
    assert [e["docs"] for e in c["indexed_with"]["llm_eras"]] == [1, 1]


async def test_overview_flags_a_model_that_left_the_engine(tmp_path):
    ctl, world, *_ = build(tmp_path)
    world.engine_models = [{"id": "other", "x_odyssai": {"ready": True}}]  # drift: old-model is gone
    c = (await ctl.overview())["corpora"][0]
    assert c["llm"]["ready"] is False and c["llm"]["in_sync"] is True


async def test_overview_survives_engine_instance_and_embedder_being_down(tmp_path):
    ctl, world, *_ = build(tmp_path)
    world.engine_models = None
    world.loaded = False
    world.embedder_alive = False
    v = await ctl.overview()
    assert v["engine"]["reachable"] is False and v["engine"]["ready_models"] is None
    c = v["corpora"][0]
    assert c["instance"]["up"] is False and c["instance"]["loaded"] is False
    assert c["llm"]["ready"] is None and c["llm"]["in_sync"] is None
    assert c["embedder"]["alive"] is False and c["embedder"]["same_space"] is None


# ── LLM endpoint: a change of LLM_BINDING_HOST goes through the same apply cycle ──

async def test_apply_llm_moves_to_another_endpoint(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    logs, log = collector()
    res = await ctl.apply_llm("alpha", "moved-model", log, endpoint=f"{ENGINE2}/v1")
    assert res["changed"]
    assert plist_env(tmp_path)["LLM_BINDING_HOST"] == f"{ENGINE2}/v1"
    assert plist_env(tmp_path)["LLM_MODEL"] == "moved-model"
    assert reg.corpus("alpha").llm_endpoint == f"{ENGINE2}/v1"
    assert res["live"]["llm_binding_host"] == f"{ENGINE2}/v1"


async def test_apply_llm_endpoint_only_change_keeps_the_model(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    _, log = collector()
    res = await ctl.apply_llm("alpha", "old-model", log, endpoint=f"{ENGINE2}/v1")
    assert res["changed"]
    assert plist_env(tmp_path)["LLM_MODEL"] == "old-model"
    assert plist_env(tmp_path)["LLM_BINDING_HOST"] == f"{ENGINE2}/v1"


async def test_apply_llm_checks_readiness_on_the_new_endpoint(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    _, log = collector()
    # new-model is only published by ENGINE, not by the new endpoint
    with pytest.raises(ChangeError, match="not loaded"):
        await ctl.apply_llm("alpha", "new-model", log, endpoint=f"{ENGINE2}/v1")
    assert "bootout" not in world.calls


async def test_apply_llm_refuses_a_public_endpoint(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    _, log = collector()
    with pytest.raises(ChangeError) as e:
        await ctl.apply_llm("alpha", "old-model", log, endpoint="http://8.8.8.8:1/v1")
    assert e.value.status == 422
    assert "bootout" not in world.calls


async def test_endpoint_change_is_rolled_back_with_the_model(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    _, log = collector()
    world.fail_bootstrap = 1  # the new config fails to load, the rollback load succeeds
    with pytest.raises(ChangeError) as e:
        await ctl.apply_llm("alpha", "moved-model", log, endpoint=f"{ENGINE2}/v1")
    assert e.value.rolled_back is True
    assert plist_env(tmp_path)["LLM_BINDING_HOST"] == f"{ENGINE}/v1"
    assert plist_env(tmp_path)["LLM_MODEL"] == "old-model"


async def test_overview_reports_the_engine_of_each_corpus(tmp_path):
    ctl, world, *_ = build(tmp_path)
    c = (await ctl.overview())["corpora"][0]
    assert c["engine"] == {"endpoint": {"configured": None, "live": f"{ENGINE}/v1", "effective": f"{ENGINE}/v1"},
                           "reachable": True, "ready_models": ["new-model", "old-model"], "max_async": 4}
    assert c["instance"]["public_url"] is None
    assert c["instance"]["version"] == "1.4.16"


async def test_probe_endpoint_lists_models_and_refuses_public_hosts(tmp_path):
    ctl, world, *_ = build(tmp_path)
    res = await ctl.probe_endpoint(f"{ENGINE2}/v1/")
    assert res["reachable"] is True
    assert {m["id"] for m in res["models"]} == {"old-model", "moved-model"}
    assert res["url"] == f"{ENGINE2}/v1"
    world.engine2_models = None  # ttl_s=0 in build(): no cache to hide the outage
    res = await ctl.probe_endpoint(f"{ENGINE2}/v1")
    assert res["reachable"] is False and res["models"] == []
    with pytest.raises(ChangeError) as e:
        await ctl.probe_endpoint("http://8.8.8.8:1/v1")
    assert e.value.status == 422


# ── parallelism: how many LLM calls the instance may send at once ─────────

async def test_apply_llm_sets_the_parallelism_with_the_model(tmp_path):
    ctl, world, clock, reg, _ = build(tmp_path)
    _, log = collector()
    res = await ctl.apply_llm("alpha", "new-model", log, max_async=8)
    assert res["changed"] and res["live"]["max_async"] == 8 and res["live"]["llm_model"] == "new-model"
    assert plist_env(tmp_path)["MAX_ASYNC"] == "8"
    assert (await ctl.overview())["corpora"][0]["engine"]["max_async"] == 8


async def test_parallelism_only_change_restarts_and_keeps_the_model(tmp_path):
    ctl, world, *_ = build(tmp_path)
    _, log = collector()
    res = await ctl.apply_llm("alpha", "old-model", log, max_async=2)
    assert res["changed"] and plist_env(tmp_path)["LLM_MODEL"] == "old-model" and plist_env(tmp_path)["MAX_ASYNC"] == "2"
    assert "bootout" in world.calls
    world.calls.clear()
    again = await ctl.apply_llm("alpha", "old-model", log, max_async=2)
    assert again == {"changed": False, "corpus": "alpha", "note": "already applied"} and "bootout" not in world.calls


async def test_parallelism_out_of_range_is_refused_before_anything_moves(tmp_path):
    ctl, world, *_ = build(tmp_path)
    _, log = collector()
    for bad in (0, -1, 65, 1000):
        with pytest.raises(ChangeError) as e:
            await ctl.apply_llm("alpha", "new-model", log, max_async=bad)
        assert e.value.status == 422
    assert "bootout" not in world.calls and "MAX_ASYNC" not in plist_env(tmp_path)


async def test_parallelism_is_rolled_back_with_the_model(tmp_path):
    ctl, world, *_ = build(tmp_path)
    world.fail_bootstrap = 1
    _, log = collector()
    with pytest.raises(ChangeError) as e:
        await ctl.apply_llm("alpha", "new-model", log, max_async=8)
    assert e.value.rolled_back is True
    env = plist_env(tmp_path)
    assert env["LLM_MODEL"] == "old-model" and "MAX_ASYNC" not in env
