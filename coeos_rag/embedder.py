"""Embedder probing and identity.

The dimension of a vector is not the identity of the space it lives in: nemo-embed
(Qwen3-Embedding) and bge-m3 are both 1024-d, yet swapping one for the other makes
vector search return noise without any error. Identity is checked by cosine: the same
text embedded by two endpoints must give (almost) the same vector.
"""
from __future__ import annotations

import base64
import ipaddress
import math
import socket
import struct
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Optional, Sequence
from urllib.parse import urlparse

import httpx

# Fixed sentence used to fingerprint a vector space.
PROBE_TEXT = "OdyRAG embedder identity probe: le droit est un graphe, pas une pile de documents."


class EmbedderError(Exception):
    pass


def embeddings_url(binding_host: str) -> str:
    """The URL an OpenAI-style client calls for embeddings given EMBEDDING_BINDING_HOST:
    the SDK appends `/embeddings` to the base URL it is given, nothing else."""
    return binding_host.rstrip("/") + "/embeddings"


def health_url(binding_host: str) -> str:
    base = binding_host.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base + "/health"


def validate_embedder_url(url: str, resolver: Callable = socket.getaddrinfo, what: str = "embedder") -> None:
    """Document text goes through this URL: only http(s) to private/loopback hosts."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"{what} URL must be http(s) with a host")
    try:
        infos = resolver(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as e:
        raise ValueError(f"{what} host does not resolve: {e}") from e
    addrs = {ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos}
    if not addrs:
        raise ValueError(f"{what} host does not resolve")
    for a in addrs:
        if not (a.is_private or a.is_loopback or a.is_link_local):
            raise ValueError(f"{what} host resolves to a public address ({a}); refused")


def encode_vec(vec: Sequence[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vec)}e", *vec)).decode()


def decode_vec(b64: str) -> list[float]:
    raw = base64.b64decode(b64)
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def decode_stored_vector(b64_zlib_f16: str) -> list[float]:
    """Per-item vector as nano-vectordb stores it: base64(zlib(float16 little-endian))."""
    raw = zlib.decompress(base64.b64decode(b64_zlib_f16))
    return list(struct.unpack(f"<{len(raw) // 2}e", raw))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise EmbedderError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        raise EmbedderError("zero vector")
    return dot / (na * nb)


async def embed(
    client: httpx.AsyncClient,
    binding_host: str,
    model: str,
    texts: Sequence[str],
    *,
    timeout: float = 30.0,
) -> list[list[float]]:
    try:
        r = await client.post(
            embeddings_url(binding_host),
            json={"model": model, "input": list(texts)},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise EmbedderError(f"embedder unreachable: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise EmbedderError(f"embedder answered HTTP {r.status_code}: {r.text[:200]}")
    try:
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        vecs = [d["embedding"] for d in data]
    except (KeyError, ValueError, TypeError) as e:
        raise EmbedderError(f"unexpected embeddings payload: {e}") from e
    if len(vecs) != len(texts):
        raise EmbedderError(f"asked {len(texts)} embeddings, got {len(vecs)}")
    return vecs


@dataclass
class Probe:
    alive: bool
    latency_ms: Optional[float] = None
    dim: Optional[int] = None
    weights: Optional[str] = None
    vec: Optional[list[float]] = None
    error: Optional[str] = None


async def probe(
    client: httpx.AsyncClient,
    binding_host: str,
    model: str,
    text: str = PROBE_TEXT,
    *,
    timeout: float = 15.0,
) -> Probe:
    """Real embedding call (liveness is judged on what matters: producing a vector) plus a
    best-effort read of the server's own description of its weights."""
    t0 = time.perf_counter()
    try:
        vec = (await embed(client, binding_host, model, [text], timeout=timeout))[0]
    except EmbedderError as e:
        return Probe(alive=False, error=str(e))
    latency = (time.perf_counter() - t0) * 1000
    weights = None
    try:
        h = await client.get(health_url(binding_host), timeout=timeout)
        if h.status_code == 200:
            body = h.json()
            for key in ("embed_model", "model"):
                if isinstance(body.get(key), str):
                    weights = body[key]
                    break
    except (httpx.HTTPError, ValueError):
        pass
    return Probe(alive=True, latency_ms=round(latency, 1), dim=len(vec), weights=weights, vec=vec)
