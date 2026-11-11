import json

from coeos_rag.jobs import JobManager


def test_job_lifecycle_and_audit_log(tmp_path):
    t = [100.0]
    jm = JobManager(tmp_path / "state" / "actions.jsonl", clock=lambda: t[0])
    job = jm.create("alpha", "llm")
    jm.step(job, "precheck", True, "idle")
    t[0] = 130.0
    jm.finish(job, {"changed": True})
    assert job.status == "done" and jm.get(job.id).public()["steps"][0]["step"] == "precheck"
    job2 = jm.create("alpha", "embedder")
    jm.fail(job2, "different vector space")
    lines = (tmp_path / "state" / "actions.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    assert first["status"] == "done" and first["took_s"] == 30.0 and first["result"] == {"changed": True}
    tail = jm.audit_tail()
    assert [a["status"] for a in tail] == ["failed", "done"]  # newest first
    assert tail[0]["error"] == "different vector space"


def test_recent_is_bounded_and_newest_first(tmp_path):
    jm = JobManager(None, keep=3)
    ids = [jm.create("a", "k").id for _ in range(5)]
    assert jm.get(ids[0]) is None and jm.get(ids[-1]) is not None
    assert [j["id"] for j in jm.recent()] == ids[-1:-4:-1]
    assert jm.audit_tail() == []


def test_a_corrupt_audit_line_is_skipped(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text('{"ok": 1}\nnot json\n{"ok": 2}\n')
    assert [x["ok"] for x in JobManager(p).audit_tail()] == [2, 1]
