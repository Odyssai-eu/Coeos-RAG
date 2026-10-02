"""Manual end-to-end test on the REAL host.

A throwaway LightRAG instance (its own port, working dir and launchd label) is driven by
the real controller, the real `launchctl`, the real inference engine and the real
embedder. The production instances are never touched: the port must be free, the label
and the directory are dedicated, everything is removed at the end.

Run ON the host that runs the instances:

    E2E_ENGINE_URL=...  E2E_EMBED_URL=...  E2E_EMBED_MODEL=...  E2E_EMBED_DIM=...  \
    E2E_LLM_A=...  E2E_LLM_B=...  E2E_PORT=...  E2E_LIGHTRAG_PYTHON=... \
    python -m tests.e2e_real_host

Scenarios: certify on a real nano-vectordb store; LLM change with the data intact; refusal
while the pipeline is really extracting; a real crash-at-startup followed by a rollback;
queries still answered at the end.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import plistlib
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

import httpx

from odyrag.config import Settings
from odyrag.controller import Busy, ChangeError, Controller
from odyrag.engine import EngineClient
from odyrag.launchd import Launchd
from odyrag.lightrag import LightRagClient
from odyrag.registry import (
    Corpus, EmbedderConfig, EmbedderIdentity, IndexedWith, Instance, Registry, RegistryModel,
)

LABEL = "eu.odyssai.odyrag-e2e"


def need(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"set {name}")
    return v


ENGINE = need("E2E_ENGINE_URL")
EMBED = need("E2E_EMBED_URL")
EMBED_MODEL = need("E2E_EMBED_MODEL")
EMBED_DIM = need("E2E_EMBED_DIM")
LLM_A, LLM_B = need("E2E_LLM_A"), need("E2E_LLM_B")
PORT = int(need("E2E_PORT"))
LR_PYTHON = need("E2E_LIGHTRAG_PYTHON")

DOCS = [
    "OdyRAG control plane test corpus, document one. The Zephyr project is led by Marta Okonkwo, "
    "who reports to the Halvorsen Institute in Oslo. Zephyr studies wind-driven desalination.",
    "OdyRAG control plane test corpus, document two. The Halvorsen Institute funds the Zephyr project "
    "and the Birch project. Birch is led by Tomas Reyes and studies kelp farming in cold water.",
    "OdyRAG control plane test corpus, document three. Marta Okonkwo and Tomas Reyes co-authored a "
    "paper on coastal energy. The paper was presented at the Oslo Ocean Conference.",
]
LONG_DOC = " ".join(
    f"Section {i}. The Halvorsen Institute hosts meeting number {i} where Marta Okonkwo and Tomas Reyes "
    f"discuss budget line {i} for the Zephyr and Birch projects, and review the Oslo Ocean Conference agenda."
    for i in range(1, 60)
)

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)


def write_plist(path: Path, workdir: Path, llm: str) -> None:
    data = {
        "Label": LABEL, "KeepAlive": True, "RunAtLoad": True, "WorkingDirectory": str(workdir),
        "ProgramArguments": [LR_PYTHON, "-m", "lightrag.api.lightrag_server", "--host", "127.0.0.1",
                             "--port", str(PORT), "--llm-binding", "openai", "--embedding-binding", "openai"],
        "StandardOutPath": str(workdir / "lightrag.log"), "StandardErrorPath": str(workdir / "lightrag.log"),
        "EnvironmentVariables": {
            "WORKING_DIR": str(workdir), "LLM_BINDING_HOST": f"{ENGINE}/v1", "LLM_BINDING_API_KEY": "none",
            "LLM_MODEL": llm, "EMBEDDING_BINDING_HOST": EMBED, "EMBEDDING_BINDING_API_KEY": "none",
            "EMBEDDING_MODEL": EMBED_MODEL, "EMBEDDING_DIM": EMBED_DIM, "EMBEDDING_MAX_TOKEN_SIZE": "512",
            "MAX_GLEANING": "0",
        },
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)


async def until(pred, timeout: float, every: float = 2.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if await pred():
            return True
        await asyncio.sleep(every)
    return False


async def run(name: str, scenario) -> bool:
    """Run one scenario. Any exception is recorded as a failure of that scenario only, so the
    remaining scenarios still run and one early error never hides the rest of the report."""
    try:
        ok, detail = await scenario()
    except Exception as e:  # noqa: BLE001 - the harness reports every failure, it never hides one
        ok, detail = False, f"{type(e).__name__}: {e}"
    record(name, ok, detail)
    return ok


async def main() -> int:
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            sys.exit(f"port {PORT} is already in use: refusing to run next to a live service")

    root = Path(tempfile.mkdtemp(prefix="odyrag-e2e-"))
    workdir = root / "lightrag"
    workdir.mkdir()
    plist_path = root / f"{LABEL}.plist"
    write_plist(plist_path, workdir, LLM_A)
    base = f"http://127.0.0.1:{PORT}"
    settings = Settings(state_dir=root / "state", engine_url=ENGINE, admin_token="e2e", launchd_uid=os.getuid(),
                        wait_down_s=90, wait_up_s=180, http_timeout_s=8)
    launchd = Launchd(settings.launchd_uid)
    http = httpx.AsyncClient()
    lr = LightRagClient(http, base, 8)
    steps: list[tuple] = []
    log = lambda step, ok=True, detail="": steps.append((step, ok, detail))
    try:
        # ── setup: nothing below means anything if this fails ─────────────────
        async def boot():
            rc, msg = launchd.bootstrap(str(plist_path))
            if rc != 0:
                return False, f"bootstrap failed: {msg}"
            return await until(lambda: _healthy(lr), 120), base
        if not await run("throwaway instance boots", boot):
            return _report()

        async def ingest():
            r = await http.post(f"{base}/documents/texts", json={"texts": DOCS}, timeout=30)
            if r.status_code != 200:
                return False, r.text
            async def processed():
                c = await lr.counts() or {}
                return c.get("processed", 0) >= len(DOCS) or c.get("failed", 0) > 0
            await until(processed, 600, 5)
            counts = await lr.counts() or {}
            return counts.get("failed", 0) == 0 and counts.get("processed") == len(DOCS), str(counts)
        if not await run("real extraction + embedding of 3 docs", ingest):
            return _report()

        registry = Registry(settings.registry_path)
        registry.save(RegistryModel(corpora={"e2e": Corpus(
            label="E2E", instance=Instance(url=base, launchd_label=LABEL, plist=str(plist_path), workdir=str(workdir)),
            llm=LLM_A, embedder=EmbedderConfig(url=EMBED, model=EMBED_MODEL, dim=int(EMBED_DIM)),
            indexed_with=IndexedWith(embedder=EmbedderIdentity(dim=int(EMBED_DIM))))}))
        ctl = Controller(settings, registry, launchd, http, EngineClient(http, ENGINE, 8))

        # ── 1. certification against a real nano-vectordb store and the real embedder ──
        async def certify():
            res = await ctl.certify("e2e", log, samples=3)  # raises ChangeError when the space differs
            return True, f"min cosine {res['min_cos']}, median {res['median_cos']}, weights {res['weights']}"
        await run("certify on a real nano-vectordb store", certify)

        # ── 2. LLM change with the data intact ────────────────────────────────
        async def change_llm():
            steps.clear()
            res = await ctl.apply_llm("e2e", LLM_B, log)
            live = await lr.health()
            counts = await lr.counts()
            era_ok = registry.corpus("e2e").indexed_with.llm_eras[-1].model == LLM_B
            ok = (live["configuration"]["llm_model"] == LLM_B and counts.get("processed") == len(DOCS)
                  and registry.corpus("e2e").llm == LLM_B and era_ok)
            return ok, f"took {res['took_s']} s, counts {counts}"
        await run("LLM change: restarted, new model live, data intact", change_llm)

        # ── 3. refusal while the pipeline is really extracting ────────────────
        async def refusal():
            r = await http.post(f"{base}/documents/text", json={"text": LONG_DOC}, timeout=30)
            if r.status_code != 200:
                return False, r.text
            async def busy():
                return (await lr.busy()) is True
            if not await until(busy, 90, 1):
                return False, "the pipeline never reported busy"
            try:
                await ctl.apply_llm("e2e", LLM_A, log)
                return False, "the change was NOT refused"
            except Busy:
                return (await lr.health()) is not None, "refused, instance untouched and still serving"
            finally:
                async def idle():
                    return (await lr.busy()) is False
                await until(idle, 600, 5)
        await run("refusal while extracting", refusal)

        # ── 4. a real failure: a dimension that does not match the stored vectors crashes the
        #    instance at startup; the controller must roll back to a serving instance. ──
        async def crash_rollback():
            small = Controller(dataclasses.replace(settings, wait_up_s=45), registry, launchd, http,
                               EngineClient(http, ENGINE, 8))
            steps.clear()
            before = plistlib.loads(plist_path.read_bytes())["EnvironmentVariables"]
            try:
                await small._apply("e2e", {"EMBEDDING_DIM": "512"}, check=lambda cfg: True,
                                   describe_live=lambda cfg: "", commit=lambda c, n: None, log=log)
                return False, "no error raised"
            except ChangeError as e:
                after = plistlib.loads(plist_path.read_bytes())["EnvironmentVariables"]
                serving = (await lr.health()) is not None
                counts = await lr.counts() or {}
                ok = e.rolled_back is True and after == before and serving and counts.get("processed", 0) >= len(DOCS)
                return ok, f"rolled_back={e.rolled_back}, serving={serving}, plist restored={after == before}"
        await run("real startup crash is rolled back", crash_rollback)

        # ── 5. the instance still answers queries after all that ──────────────
        async def query():
            r = await http.post(f"{base}/query", json={"query": "Who leads the Zephyr project?", "mode": "naive",
                                                      "only_need_context": True}, timeout=120)
            return r.status_code == 200 and "Okonkwo" in r.text, f"HTTP {r.status_code}"
        await run("queries still answered (embedding path intact)", query)
    finally:
        launchd.bootout(LABEL)
        await http.aclose()
        failed_any = any(not ok for _, ok, _ in results)
        log_tail = ""
        lf = workdir / "lightrag.log"
        if failed_any and lf.exists():
            log_tail = lf.read_text(errors="replace")[-1500:]
        if failed_any and steps:
            print("--- last controller steps ---")
            for step, ok, detail in steps[-12:]:
                print(f"  {'ok ' if ok else 'ERR'} {step}: {detail}")
        shutil.rmtree(root, ignore_errors=True)
        if log_tail:
            print("--- throwaway instance log (tail) ---\n" + log_tail)
    return _report()


def _report() -> int:
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} scenarios passed")
    return 1 if failed else 0


async def _healthy(lr: LightRagClient) -> bool:
    return (await lr.health()) is not None


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
