"""Certify that an embedder matches an index, using the index itself as ground truth.

Take chunks already stored in `vdb_chunks.json`, re-embed their text with the embedder
under test and compare with the stored vectors. If the cosine is ~1 for every sample the
embedder produces the space the index was built in.
"""
from __future__ import annotations

import json
import mmap
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


# Every item of the "data" array starts with this key. Inside a JSON string a quote is
# always escaped, so these bytes can only be the start of an item.
_ITEM_START = b'{"__id__"'
# How far past a picked item to look for a usable one (non-empty text and a stored vector).
_MAX_SKIP = 50


def read_chunk_samples(vdb_path: Path, n: int) -> list[Sample]:
    """Read up to `n` chunk samples evenly spaced over the WHOLE nano-vectordb file.

    The file is one big JSON line (close to a gigabyte on a large corpus): a "data" array
    of items followed by a "matrix" blob. It is memory-mapped, the start of every item is
    located by a byte scan, and only the picked items are decoded. Sampling a prefix would
    certify the oldest chunks only: on the alpha index the first 24 MB hold 7 % of them.
    """
    with open(vdb_path, "rb") as f:
        try:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError as e:  # an empty file cannot be mapped
            raise ValueError(f"{vdb_path}: empty file") from e
        with mm:
            marker = mm.find(b'"data"')
            start = mm.find(b"[", marker) if marker != -1 else -1
            if start == -1:
                raise ValueError(f"{vdb_path}: no data array")
            end = mm.rfind(b'"matrix"')
            if end < start:
                end = len(mm)  # no matrix (or a truncated file): the array runs to the end
            positions: list[int] = []
            idx = mm.find(_ITEM_START, start, end)
            while idx != -1:
                positions.append(idx)
                idx = mm.find(_ITEM_START, idx + 1, end)

            def load(k: int) -> Optional[Sample]:
                stop = positions[k + 1] if k + 1 < len(positions) else end
                try:
                    obj, _ = json.JSONDecoder().raw_decode(mm[positions[k]:stop].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return None  # a file truncated inside this item
                content, vector = obj.get("content"), obj.get("vector")
                if isinstance(content, str) and content.strip() and isinstance(vector, str):
                    return Sample(obj.get("__id__", ""), content, decode_stored_vector(vector))
                return None

            total = len(positions)
            picks = range(total) if total <= n else [int(i * total / n) for i in range(n)]
            samples: list[Sample] = []
            taken: set[int] = set()
            for k in picks:
                for j in range(k, min(k + _MAX_SKIP, total)):
                    if j in taken:
                        continue
                    sample = load(j)
                    if sample is not None:
                        taken.add(j)
                        samples.append(sample)
                        break
    if not samples:
        raise ValueError(f"{vdb_path}: no usable chunk")
    return samples


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
