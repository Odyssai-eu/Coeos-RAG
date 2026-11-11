import base64
import hashlib
import json
import struct
import zlib

import httpx
import pytest

from coeos_rag import embedder as E


def fake_vec(text: str, dim: int = 8, salt: str = "") -> list[float]:
    out = []
    for i in range(dim):
        h = hashlib.sha256(f"{salt}{text}{i}".encode()).digest()
        out.append((int.from_bytes(h[:2], "big") - 32768) / 32768)
    return out


def transport(handler):
    return httpx.MockTransport(handler)


def test_urls_follow_the_openai_sdk_convention():
    assert E.embeddings_url("http://h:8765") == "http://h:8765/embeddings"
    assert E.embeddings_url("http://h:8082/v1/") == "http://h:8082/v1/embeddings"
    assert E.health_url("http://h:8765") == "http://h:8765/health"
    assert E.health_url("http://h:8082/v1") == "http://h:8082/health"


@pytest.mark.parametrize("addr,ok", [("127.0.0.1", True), ("10.1.2.3", True), ("192.168.1.9", True),
                                     ("169.254.1.1", True), ("8.8.8.8", False), ("93.184.216.34", False)])
def test_url_validation_private_only(addr, ok):
    resolver = lambda host, port: [(2, 1, 6, "", (addr, port))]
    if ok:
        E.validate_embedder_url("http://emb.local:8765", resolver)
    else:
        with pytest.raises(ValueError, match="public"):
            E.validate_embedder_url("http://emb.local:8765", resolver)


def test_url_validation_rejects_bad_scheme_and_unresolvable():
    with pytest.raises(ValueError):
        E.validate_embedder_url("ftp://x")
    with pytest.raises(ValueError):
        E.validate_embedder_url("http://")
    def boom(host, port):
        raise OSError("nope")
    with pytest.raises(ValueError, match="resolve"):
        E.validate_embedder_url("http://x.invalid", boom)


def test_vector_codecs_and_cosine():
    v = [0.5, -0.25, 0.125, 1.0]
    assert E.decode_vec(E.encode_vec(v)) == v
    stored = base64.b64encode(zlib.compress(struct.pack("<4e", *v))).decode()
    assert E.decode_stored_vector(stored) == v
    assert E.cosine(v, v) == pytest.approx(1.0)
    assert E.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    with pytest.raises(E.EmbedderError):
        E.cosine([1, 0], [1, 0, 0])
    with pytest.raises(E.EmbedderError):
        E.cosine([0, 0], [1, 0])


async def test_embed_success_orders_by_index_and_posts_openai_body():
    seen = {}
    def handler(req: httpx.Request):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]})
    async with httpx.AsyncClient(transport=transport(handler)) as c:
        out = await E.embed(c, "http://h:1/v1", "m", ["a", "b"])
    assert out == [[1.0], [2.0]]
    assert seen["url"] == "http://h:1/v1/embeddings" and seen["body"] == {"model": "m", "input": ["a", "b"]}


async def test_embed_errors_are_embedder_errors():
    async with httpx.AsyncClient(transport=transport(lambda r: httpx.Response(500, text="oops"))) as c:
        with pytest.raises(E.EmbedderError, match="500"):
            await E.embed(c, "http://h", "m", ["a"])
    async with httpx.AsyncClient(transport=transport(lambda r: httpx.Response(200, json={"data": []}))) as c:
        with pytest.raises(E.EmbedderError, match="got 0"):
            await E.embed(c, "http://h", "m", ["a"])
    async with httpx.AsyncClient(transport=transport(lambda r: httpx.Response(200, json={"x": 1}))) as c:
        with pytest.raises(E.EmbedderError, match="payload"):
            await E.embed(c, "http://h", "m", ["a"])
    def refuse(req):
        raise httpx.ConnectError("refused")
    async with httpx.AsyncClient(transport=transport(refuse)) as c:
        with pytest.raises(E.EmbedderError, match="unreachable"):
            await E.embed(c, "http://h", "m", ["a"])


async def test_probe_alive_reads_weights_from_health():
    def handler(req: httpx.Request):
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})
        return httpx.Response(200, json={"embed_model": "mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ"})
    async with httpx.AsyncClient(transport=transport(handler)) as c:
        p = await E.probe(c, "http://h:8765", "emb-model")
    assert p.alive and p.dim == 3 and p.weights.endswith("DWQ") and p.latency_ms >= 0


async def test_probe_dead_and_health_failure_is_not_fatal():
    def refuse(req):
        raise httpx.ConnectError("refused")
    async with httpx.AsyncClient(transport=transport(refuse)) as c:
        p = await E.probe(c, "http://h", "m")
    assert not p.alive and "unreachable" in p.error
    def handler(req):
        if req.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=transport(handler)) as c:
        p = await E.probe(c, "http://h", "m")
    assert p.alive and p.weights is None
