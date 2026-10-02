"""Runtime settings, read from the environment.

No address, host or port is hardcoded: every endpoint comes from the environment
(service plist) or from the registry file. Numeric defaults below are timings and
thresholds only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    engine_url: Optional[str]
    admin_token: Optional[str]
    launchd_uid: int
    # How long to wait for an instance to stop / come back after a restart.
    wait_down_s: float = 120.0
    wait_up_s: float = 300.0
    http_timeout_s: float = 8.0
    # Probe vs golden vector (both embedded one text per call, so deterministic): the same
    # embedder gives 1.0000. Two different embedders of the same dimension give ~0 (measured
    # nemo-embed vs bge-m3: -0.05 .. +0.06).
    embed_cos_threshold: float = 0.98
    # Certifying an index compares freshly embedded chunks with the vectors LightRAG stored.
    # Those were often produced in batches, and the same embedder gives batch vectors that
    # differ from per-text ones (measured: median 0.91, worst 0.756). The criteria sit
    # between that noise floor and the different-embedder ceiling (<= 0.06).
    cert_median_min: float = 0.80
    cert_min_floor: float = 0.50

    @property
    def registry_path(self) -> Path:
        return self.state_dir / "registry.json"

    @property
    def actions_log_path(self) -> Path:
        return self.state_dir / "actions.jsonl"

    @property
    def mutations_enabled(self) -> bool:
        """Mutating endpoints stay disabled until an admin token is configured."""
        return bool(self.admin_token)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env
        state = Path(e.get("ODYRAG_STATE_DIR") or (Path.home() / ".odyrag")).expanduser()
        engine = (e.get("ODYRAG_ENGINE_URL") or "").strip().rstrip("/") or None
        token = (e.get("ODYRAG_ADMIN_TOKEN") or "").strip() or None
        uid = int(e.get("ODYRAG_LAUNCHD_UID") or os.getuid())
        return cls(
            state_dir=state,
            engine_url=engine,
            admin_token=token,
            launchd_uid=uid,
            wait_down_s=float(e.get("ODYRAG_WAIT_DOWN_S", 120)),
            wait_up_s=float(e.get("ODYRAG_WAIT_UP_S", 300)),
            http_timeout_s=float(e.get("ODYRAG_HTTP_TIMEOUT_S", 8)),
            embed_cos_threshold=float(e.get("ODYRAG_EMBED_COS_THRESHOLD", 0.98)),
            cert_median_min=float(e.get("ODYRAG_CERT_MEDIAN_MIN", 0.80)),
            cert_min_floor=float(e.get("ODYRAG_CERT_MIN_FLOOR", 0.50)),
        )
