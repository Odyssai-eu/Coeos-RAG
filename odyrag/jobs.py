"""Tracking of long-running changes (a restart can take minutes) and the audit log."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class Job:
    id: str
    corpus: str
    kind: str
    status: str = "running"  # running | done | failed
    steps: list[dict] = field(default_factory=list)
    result: Optional[dict] = None
    error: Optional[str] = None
    started: float = 0.0
    ended: Optional[float] = None

    def public(self) -> dict:
        return {
            "id": self.id, "corpus": self.corpus, "kind": self.kind, "status": self.status,
            "steps": list(self.steps), "result": self.result, "error": self.error,
            "started": self.started, "ended": self.ended,
        }


class JobManager:
    def __init__(self, actions_log: Optional[Path] = None, clock: Callable[[], float] = time.time,
                 keep: int = 50) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._log_path = actions_log
        self._clock = clock
        self._keep = keep

    def create(self, corpus: str, kind: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], corpus=corpus, kind=kind, started=self._clock())
        with self._lock:
            self._jobs[job.id] = job
            for old in list(self._jobs)[: -self._keep]:
                del self._jobs[old]
        return job

    def step(self, job: Job, name: str, ok: bool = True, detail: str = "") -> None:
        job.steps.append({"t": round(self._clock(), 1), "step": name, "ok": ok, "detail": detail})

    def finish(self, job: Job, result: dict) -> None:
        job.status, job.result, job.ended = "done", result, self._clock()
        self._audit(job)

    def fail(self, job: Job, error: str) -> None:
        job.status, job.error, job.ended = "failed", error, self._clock()
        self._audit(job)

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def recent(self, n: int = 20) -> list[dict]:
        with self._lock:
            return [j.public() for j in list(self._jobs.values())[-n:]][::-1]

    def _audit(self, job: Job) -> None:
        if self._log_path is None:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(job.ended)),
            "corpus": job.corpus, "kind": job.kind, "status": job.status,
            "error": job.error, "took_s": round((job.ended or 0) - job.started, 1),
            "result": job.result,
        }
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def audit_tail(self, n: int = 30) -> list[dict]:
        if self._log_path is None or not self._log_path.exists():
            return []
        lines = self._log_path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out[::-1]
