"""The registry: corpora, how each LightRAG instance is wired, the models configured
for it, and the "indexed with" provenance that LightRAG itself never records.

Stored as one JSON file, written atomically (tmp file + os.replace).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from pydantic import BaseModel, Field


class Era(BaseModel):
    """A period during which one LLM was the extraction model of a corpus."""

    model: str
    start: Optional[str] = None  # ISO date or datetime
    end: Optional[str] = None  # None = still current
    source: str = ""  # where this fact comes from (shard env backups, settings change...)


class Certification(BaseModel):
    """Proof that an embedder produces the vector space the index was built in."""

    method: str
    samples: int
    min_cos: float
    median_cos: float
    at: str


class EmbedderIdentity(BaseModel):
    """Identity of the vector space of an index. The dimension alone is not an identity:
    two different embedders can both be 1024-d."""

    weights: Optional[str] = None
    dim: int
    golden_probe: Optional[str] = None
    golden_vec: Optional[str] = None  # base64 of float16 little-endian
    certified: Optional[Certification] = None


class IndexedWith(BaseModel):
    embedder: EmbedderIdentity
    llm_eras: list[Era] = Field(default_factory=list)


class Instance(BaseModel):
    url: str  # LightRAG HTTP base URL as seen by the control plane
    launchd_label: str
    plist: str
    workdir: str  # directory holding the stores (vdb_*.json, kv_store_*.json)


class EmbedderConfig(BaseModel):
    url: str  # EMBEDDING_BINDING_HOST, the exact string LightRAG is given
    model: str  # EMBEDDING_MODEL
    dim: int


class Corpus(BaseModel):
    label: str
    instance: Instance
    llm: str  # configured LLM_MODEL; the applied value is read live from the instance
    embedder: EmbedderConfig
    indexed_with: IndexedWith


class RegistryModel(BaseModel):
    version: int = 1
    corpora: dict[str, Corpus] = Field(default_factory=dict)


def open_era(corpus: Corpus, model: str, at: str, source: str) -> bool:
    """Record that `model` is the extraction model from `at` on. Closes the open era if
    it is a different model. Returns True when the era list changed."""
    eras = corpus.indexed_with.llm_eras
    current = eras[-1] if eras and eras[-1].end is None else None
    if current is not None:
        if current.model == model:
            return False
        current.end = at
    eras.append(Era(model=model, start=at, end=None, source=source))
    return True


class Registry:
    """Thread-safe load/save of the registry file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> RegistryModel:
        with self._lock:
            if not self.path.exists():
                return RegistryModel()
            return RegistryModel.model_validate_json(self.path.read_text())

    def save(self, model: RegistryModel) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(model.model_dump_json(indent=2))
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    @contextmanager
    def txn(self) -> Iterator[RegistryModel]:
        """Read-modify-write under one lock hold. The registry is saved on clean exit only."""
        with self._lock:
            model = self.load()
            yield model
            self.save(model)

    def corpus(self, corpus_id: str) -> Corpus:
        model = self.load()
        if corpus_id not in model.corpora:
            raise KeyError(corpus_id)
        return model.corpora[corpus_id]


def dumps(model: RegistryModel) -> str:
    return json.dumps(model.model_dump(), indent=2, ensure_ascii=False)
