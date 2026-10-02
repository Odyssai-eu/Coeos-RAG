"""Certify that an embedder matches an index, using the index itself as ground truth.

Take chunks already stored in `vdb_chunks.json`, re-embed their text with the embedder
under test and compare with the stored vectors. If the cosine is ~1 for every sample the
embedder produces the space the index was built in.
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional, Sequence

from .embedder import EmbedderError, cosine, decode_stored_vector
from .registry import Certification, Era

EmbedFn = Callable[[Sequence[str]], Awaitable[list[list[float]]]]


@dataclass
class Sample:
    id: str
    content: str
    vec: list[float]


def read_chunk_samples(vdb_path: Path, n: int, *, max_bytes: int = 24_000_000) -> list[Sample]:
    """Read up to `n` evenly spaced chunk samples from the start of a nano-vectordb file.

    The file is one big JSON line (gigabytes on a large corpus), so only a prefix is read
    and items are decoded one by one until the prefix runs out.
    """
    with open(vdb_path, "r", encoding="utf-8") as f:
        text = f.read(max_bytes)
    marker = text.find('"data"')
    start = text.find("[", marker) if marker != -1 else -1
    if start == -1:
        raise ValueError(f"{vdb_path}: no data array in the first {max_bytes} bytes")
    dec = json.JSONDecoder()
    idx = start + 1
    items: list[Sample] = []
    while True:
        while idx < len(text) and text[idx] in " \n\r\t,":
            idx += 1
        if idx >= len(text) or text[idx] == "]":
            break
        try:
            obj, idx = dec.raw_decode(text, idx)
        except json.JSONDecodeError:
            break  # prefix ended inside an item
        content, vector = obj.get("content"), obj.get("vector")
        if isinstance(content, str) and content.strip() and isinstance(vector, str):
            items.append(Sample(obj.get("__id__", ""), content, decode_stored_vector(vector)))
    if not items:
        raise ValueError(f"{vdb_path}: no usable chunk in the first {max_bytes} bytes")
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


async def certify(
    embed_fn: EmbedFn,
    samples: Sequence[Sample],
    *,
    batch: int = 1,
    now: Optional[str] = None,
) -> Certification:
    """Re-embed `samples` and compare with their stored vectors.

    `batch` defaults to 1 on purpose: measured on a real index, one text per call
    reproduces the stored vectors exactly (cosine 1.0000) while batches of 8 of the same
    embedder drift (down to 0.756) because of padding."""
    if not samples:
        raise ValueError("no samples")
    cosines: list[float] = []
    for i in range(0, len(samples), batch):
        chunk = samples[i : i + batch]
        vecs = await embed_fn([s.content for s in chunk])
        for s, v in zip(chunk, vecs):
            cosines.append(cosine(s.vec, v))
    return Certification(
        method="stored-vector-recompute",
        samples=len(cosines),
        min_cos=round(min(cosines), 6),
        median_cos=round(statistics.median(cosines), 6),
        at=now or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )


def era_counts(doc_status_path: Path, eras: Sequence[Era]) -> list[int]:
    """How many documents were ingested during each era (by `created_at`)."""
    with open(doc_status_path, "r", encoding="utf-8") as f:
        status = json.load(f)
    counts = [0] * len(eras)
    for doc in status.values():
        created = doc.get("created_at") if isinstance(doc, dict) else None
        if not isinstance(created, str):
            continue
        for i, era in enumerate(eras):
            if (era.start is None or created >= era.start) and (era.end is None or created < era.end):
                counts[i] += 1
                break
    return counts
