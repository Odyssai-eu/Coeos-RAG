"""Run the real console and controller against the SIMULATED world.

    python -m tests.dev_server        # then open http://127.0.0.1:8780/

No real LightRAG instance, launchd service or engine is touched. `POST /dev/world`
flips the simulator's failure knobs (busy, fail_bootstrap, starting_polls, ...), which
lets every success and failure path of the UI be exercised by hand.

The browser talks to LightRAG directly, so a fake LightRAG API is mounted under
`/dev/lightrag` on the same server (documents, queue, query, health). The two demo
corpora ("alpha" and "company") share that one simulated instance: a change made
through one of them shows up in the other.
"""
import base64
import json
import os
import struct
import tempfile
import zlib
from pathlib import Path

from datetime import datetime, timezone

import httpx
import uvicorn
from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from coeos_rag.app import create_app
from coeos_rag.launchd import Launchd
from coeos_rag.registry import Era, Registry, RegistryModel
from tests.fakes import FakeWorld, fake_vec, make_corpus, make_settings, write_plist


def main() -> None:
    port = int(os.environ.get("ODYRAG_DEMO_PORT", "8780"))
    tmp = Path(tempfile.mkdtemp(prefix="odyrag-demo-"))
    plist_path = write_plist(tmp / "lightrag.plist")
    workdir = tmp / "workdir"
    workdir.mkdir()

    def stored(vec):
        return base64.b64encode(zlib.compress(struct.pack(f"<{len(vec)}e", *vec))).decode()

    items = [{"__id__": f"c{i}", "content": f"chunk number {i}", "vector": stored(fake_vec(f"chunk number {i}"))}
             for i in range(30)]
    (workdir / "vdb_chunks.json").write_text(json.dumps({"embedding_dim": 8, "data": items}))
    (workdir / "kv_store_doc_status.json").write_text(json.dumps(
        {f"d{i}": {"created_at": f"2026-0{6 + i % 3}-15T10:00:00+00:00"} for i in range(30)}))

    world = FakeWorld(plist_path)
    world.starting_polls = 2
    settings = make_settings(tmp, admin_token=os.environ.get("ODYRAG_DEMO_TOKEN", "dev"))
    corpus = make_corpus(plist_path, workdir, certified=False)
    corpus.instance.public_url = f"http://127.0.0.1:{port}/dev/lightrag"
    corpus.indexed_with.llm_eras = [
        Era(model="older-model", start="2026-06-01", end="2026-07-15", source="shard env backups"),
        Era(model="old-model", start="2026-07-15", end=None, source="Coeos-RAG settings"),
    ]
    Registry(settings.registry_path).save(RegistryModel(corpora={
        "alpha": corpus,
        "company": corpus.model_copy(update={"label": "Company"}),
    }))
    app = create_app(
        settings,
        http=httpx.AsyncClient(transport=httpx.MockTransport(world.handler)),
        launchd=Launchd(501, world.runner),
    )

    @app.post("/dev/world")
    async def knobs(body: dict = Body(...)):
        for k, v in body.items():
            if not hasattr(world, k):
                raise HTTPException(400, f"unknown knob {k}")
            setattr(world, k, v)
        return {"ok": True, "world": {k: getattr(world, k) for k in body}}

    # ── fake LightRAG 1.4.16 (the subset the console uses), mounted under /dev/lightrag ──
    def _iso(day: int, hour: int = 10) -> str:
        return f"2026-10-{day:02d}T{hour:02d}:00:00+00:00"

    docs: list[dict] = []
    for i in range(34):
        status = "failed" if i % 9 == 4 else "processed"
        docs.append({
            "id": f"doc-{i:04d}", "status": status, "file_path": f"notes/dossier-{i:02d}.md",
            "content_summary": f"Compte rendu numero {i} : decisions, personnes et echeances du dossier.",
            "content_length": 1800 + 137 * i, "created_at": _iso(1 + i % 27), "updated_at": _iso(1 + i % 27, 11),
            "track_id": f"insert_demo_{i:04d}", "chunks_count": 0 if status == "failed" else 2 + i % 5,
            "error_msg": "Content already exists. Original doc_id: doc-0000, Status: processed" if status == "failed" else None,
            "metadata": {},
        })
    pipeline = {"busy": False, "job_name": "Default Job", "job_start": None, "docs": 0, "batchs": 0,
                "cur_batch": 0, "request_pending": False, "latest_message": "",
                "history_messages": []}

    def _counts() -> dict:
        out = {k: 0 for k in ("pending", "processing", "preprocessed", "processed", "failed")}
        for d in docs:
            out[d["status"]] += 1
        out["all"] = len(docs)
        return out

    def _insert(text: str, source: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        n = len(docs)
        docs.append({"id": f"doc-{n:04d}", "status": "processed", "file_path": source or "unknown_source",
                     "content_summary": text[:120], "content_length": len(text), "created_at": now,
                     "updated_at": now, "track_id": f"insert_demo_{n:04d}", "chunks_count": 1 + len(text) // 1200,
                     "error_msg": None, "metadata": {}})
        pipeline.update(job_start=now, latest_message=f"Processed {source or 'text'}",
                        history_messages=(pipeline["history_messages"] + [f"Processed {source or 'text'}"])[-20:])
        return {"status": "success", "message": f"Document accepted: {source or 'text'}", "track_id": f"insert_demo_{n:04d}"}

    lr = APIRouter(prefix="/dev/lightrag")

    @lr.get("/health")
    async def lr_health():
        env = world.env
        return {"status": "healthy", "core_version": "1.4.16", "pipeline_busy": pipeline["busy"],
                "configuration": {"llm_model": env.get("LLM_MODEL"), "llm_binding_host": env.get("LLM_BINDING_HOST"),
                                  "embedding_model": env.get("EMBEDDING_MODEL"),
                                  "embedding_binding_host": env.get("EMBEDDING_BINDING_HOST"),
                                  "enable_rerank": False, "rerank_model": None, "max_async": 4,
                                  "max_parallel_insert": 2, "summary_language": "English"}}

    @lr.get("/documents/status_counts")
    async def lr_counts():
        return {"status_counts": _counts()}

    @lr.get("/documents/pipeline_status")
    async def lr_pipeline():
        return pipeline

    @lr.post("/documents/paginated")
    async def lr_paginated(body: dict = Body(default={})):
        status = body.get("status_filter")
        page, size = int(body.get("page") or 1), int(body.get("page_size") or 20)
        rows = sorted((d for d in docs if not status or d["status"] == status),
                      key=lambda d: d["updated_at"], reverse=body.get("sort_direction", "desc") == "desc")
        total = len(rows)
        pages = max(1, -(-total // size))
        return {"documents": rows[(page - 1) * size: page * size],
                "pagination": {"page": page, "page_size": size, "total_count": total, "total_pages": pages,
                               "has_next": page < pages, "has_prev": page > 1},
                "status_counts": _counts()}

    @lr.post("/documents/text")
    async def lr_text(body: dict = Body(...)):
        return _insert(str(body.get("text") or ""), str(body.get("file_source") or ""))

    @lr.post("/documents/upload")
    async def lr_upload(file: UploadFile = File(...)):
        raw = await file.read()
        return _insert(raw.decode("utf-8", "replace"), file.filename or "upload")

    @lr.post("/documents/scan")
    async def lr_scan():
        return {"status": "scanning_started", "message": "Scanning process has been initiated in the background", "track_id": "scan_demo"}

    @lr.post("/documents/reprocess_failed")
    async def lr_reprocess():
        return {"status": "reprocessing_started", "message": "Reprocessing of failed documents has been initiated in background", "track_id": "retry_demo"}

    @lr.post("/documents/cancel_pipeline")
    async def lr_cancel():
        pipeline["busy"] = False
        return {"status": "not_busy", "message": "Pipeline is not currently running. No cancellation needed."}

    @lr.post("/query")
    async def lr_query(body: dict = Body(...)):
        q = str(body.get("query") or "")
        return {"response": f"Reponse de demonstration ({body.get('mode', 'mix')}) a la question : {q}\n\nLe corpus simule contient {len(docs)} documents.",
                "references": [{"reference_id": "1", "file_path": docs[0]["file_path"]},
                               {"reference_id": "2", "file_path": docs[1]["file_path"]}]}

    app.include_router(lr)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
