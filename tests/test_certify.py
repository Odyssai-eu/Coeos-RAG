import base64
import json
import struct
import zlib

import pytest

from odyrag import certify as C
from odyrag.registry import Era
from tests.test_embedder import fake_vec


def stored(vec):
    return base64.b64encode(zlib.compress(struct.pack(f"<{len(vec)}e", *vec))).decode()


def write_vdb(path, n=20, salt=""):
    items = [{"__id__": f"chunk-{i}", "content": f"chunk text number {i}",
              "vector": stored(fake_vec(f"chunk text number {i}", salt=salt))} for i in range(n)]
    path.write_text(json.dumps({"embedding_dim": 8, "data": items}))
    return path


async def embed_ok(texts):
    return [fake_vec(t) for t in texts]


async def embed_other_space(texts):
    return [fake_vec(t, salt="other") for t in texts]


def test_read_samples_even_spacing_and_all_when_few(tmp_path):
    p = write_vdb(tmp_path / "v.json", n=20)
    s = C.read_chunk_samples(p, 5)
    assert len(s) == 5 and s[0].id == "chunk-0" and len({x.id for x in s}) == 5
    assert len(C.read_chunk_samples(p, 50)) == 20


def test_read_samples_cover_the_whole_file_not_its_start(tmp_path):
    p = write_vdb(tmp_path / "v.json", n=1000)
    ids = [int(x.id.split("-")[1]) for x in C.read_chunk_samples(p, 10)]
    assert ids == [0, 100, 200, 300, 400, 500, 600, 700, 800, 900]


def test_read_samples_stop_at_the_matrix_and_ignore_item_like_text(tmp_path):
    items = [{"__id__": f"chunk-{i}", "content": 'quoted {"__id__": "fake"} and "matrix" in the text',
              "vector": stored(fake_vec(f"t{i}"))} for i in range(6)]
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"embedding_dim": 8, "data": items, "matrix": "QUJD" * 5000}))
    s = C.read_chunk_samples(p, 50)
    assert [x.id for x in s] == [f"chunk-{i}" for i in range(6)]
    assert all(x.content.startswith("quoted {") for x in s)


def test_read_samples_skip_unusable_items(tmp_path):
    items = [{"__id__": f"chunk-{i}", "content": "" if i % 2 == 0 else f"text {i}",
              "vector": stored(fake_vec(f"text {i}"))} for i in range(40)]
    p = tmp_path / "v.json"
    p.write_text(json.dumps({"embedding_dim": 8, "data": items}))
    s = C.read_chunk_samples(p, 4)
    assert [x.id for x in s] == ["chunk-1", "chunk-11", "chunk-21", "chunk-31"]


def test_read_samples_survives_a_truncated_file(tmp_path):
    p = write_vdb(tmp_path / "v.json", n=200)
    raw = p.read_bytes()
    p.write_bytes(raw[: len(raw) // 2])  # cut inside an item, as a non-atomic flush can do
    s = C.read_chunk_samples(p, 10)
    assert 0 < len(s) <= 10 and len({x.id for x in s}) == len(s)


def test_read_samples_rejects_garbage(tmp_path):
    p = tmp_path / "v.json"
    p.write_text("not a vdb")
    with pytest.raises(ValueError):
        C.read_chunk_samples(p, 3)
    p.write_text("")
    with pytest.raises(ValueError, match="empty"):
        C.read_chunk_samples(p, 3)
    p.write_text(json.dumps({"embedding_dim": 8, "data": [{"__id__": "x", "content": "", "vector": "AA=="}]}))
    with pytest.raises(ValueError):
        C.read_chunk_samples(p, 3)


async def test_certify_same_space_is_near_one(tmp_path):
    samples = C.read_chunk_samples(write_vdb(tmp_path / "v.json"), 12)
    cert = await C.certify(embed_ok, samples, now="2026-10-02T00:00:00")
    assert cert.samples == 12 and cert.min_cos > 0.999 and cert.at == "2026-10-02T00:00:00"


async def test_certify_other_space_is_low(tmp_path):
    samples = C.read_chunk_samples(write_vdb(tmp_path / "v.json"), 12)
    cert = await C.certify(embed_other_space, samples)
    assert cert.min_cos < 0.9


async def test_certify_embeds_one_text_per_call_by_default(tmp_path):
    """Measured on a real index: batches change the vectors (padding), one text per call does not."""
    samples = C.read_chunk_samples(write_vdb(tmp_path / "v.json"), 12)
    seen = []
    async def spy(texts):
        seen.append(len(texts))
        return [fake_vec(t) for t in texts]
    await C.certify(spy, samples)
    assert seen == [1] * 12
    seen.clear()
    await C.certify(spy, samples, batch=4)
    assert seen == [4, 4, 4]


async def test_certify_dimension_mismatch_raises(tmp_path):
    samples = C.read_chunk_samples(write_vdb(tmp_path / "v.json"), 4)
    async def wrong_dim(texts):
        return [[0.1, 0.2] for _ in texts]
    from odyrag.embedder import EmbedderError
    with pytest.raises(EmbedderError):
        await C.certify(wrong_dim, samples)


def test_era_counts(tmp_path):
    status = {
        "d1": {"created_at": "2026-06-21T14:00:00+00:00"},
        "d2": {"created_at": "2026-06-26T23:59:59+00:00"},
        "d3": {"created_at": "2026-06-27T00:00:01+00:00"},
        "d4": {"created_at": "2026-09-25T09:26:32+00:00"},
        "bad": {"created_at": None},
    }
    p = tmp_path / "kv_store_doc_status.json"
    p.write_text(json.dumps(status))
    eras = [Era(model="a", start="2026-06-21", end="2026-06-27"),
            Era(model="b", start="2026-06-27", end="2026-09-25"),
            Era(model="c", start="2026-09-25", end=None)]
    assert C.era_counts(p, eras) == [2, 1, 1]
