"""Run the real dashboard and controller against the SIMULATED world.

    python -m tests.dev_server        # then open http://127.0.0.1:8780/

No real LightRAG instance, launchd service or engine is touched. `POST /dev/world`
flips the simulator's failure knobs (busy, fail_bootstrap, starting_polls, ...), which
lets every success and failure path of the UI be exercised by hand.
"""
import base64
import json
import os
import struct
import tempfile
import zlib
from pathlib import Path

import httpx
import uvicorn
from fastapi import Body, HTTPException

from odyrag.app import create_app
from odyrag.launchd import Launchd
from odyrag.registry import Era, Registry, RegistryModel
from tests.fakes import FakeWorld, fake_vec, make_corpus, make_settings, write_plist


def main() -> None:
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
    corpus.indexed_with.llm_eras = [
        Era(model="telerag1", start="2026-06-01", end="2026-07-15", source="shard env backups"),
        Era(model="old-model", start="2026-07-15", end=None, source="OdyRAG settings"),
    ]
    Registry(settings.registry_path).save(RegistryModel(corpora={"alpha": corpus}))
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

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("ODYRAG_DEMO_PORT", "8780")), log_level="warning")


if __name__ == "__main__":
    main()
