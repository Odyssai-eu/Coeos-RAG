import json

import pytest

from odyrag.registry import (
    Corpus, EmbedderConfig, EmbedderIdentity, Era, IndexedWith, Instance, Registry,
    RegistryModel, open_era,
)


def make_corpus(llm="m1") -> Corpus:
    return Corpus(
        label="Alpha",
        instance=Instance(url="http://x:1", launchd_label="l", plist="/p", workdir="/w"),
        llm=llm,
        embedder=EmbedderConfig(url="http://e", model="emb", dim=1024),
        indexed_with=IndexedWith(embedder=EmbedderIdentity(dim=1024)),
    )


def test_roundtrip(tmp_path):
    reg = Registry(tmp_path / "state" / "registry.json")
    assert reg.load().corpora == {}
    model = RegistryModel(corpora={"alpha": make_corpus()})
    reg.save(model)
    assert reg.load().corpora["alpha"].llm == "m1"
    assert reg.corpus("alpha").label == "Alpha"
    with pytest.raises(KeyError):
        reg.corpus("nope")


def test_txn_saves_on_clean_exit_and_not_on_error(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.save(RegistryModel(corpora={"alpha": make_corpus("m1")}))
    with reg.txn() as m:
        m.corpora["alpha"].llm = "m2"
    assert reg.load().corpora["alpha"].llm == "m2"
    with pytest.raises(RuntimeError):
        with reg.txn() as m:
            m.corpora["alpha"].llm = "m3"
            raise RuntimeError("boom")
    assert reg.load().corpora["alpha"].llm == "m2"  # unchanged


def test_failed_save_keeps_old_file_and_no_tmp_left(tmp_path, monkeypatch):
    reg = Registry(tmp_path / "registry.json")
    reg.save(RegistryModel(corpora={"alpha": make_corpus("m1")}))
    import odyrag.registry as r
    monkeypatch.setattr(r.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        reg.save(RegistryModel(corpora={"alpha": make_corpus("m2")}))
    monkeypatch.undo()
    assert reg.load().corpora["alpha"].llm == "m1"
    assert [p.name for p in tmp_path.iterdir()] == ["registry.json"]


def test_open_era_closes_previous_and_is_idempotent():
    c = make_corpus()
    c.indexed_with.llm_eras = [Era(model="a", start="2026-06-21", end=None, source="s")]
    assert open_era(c, "a", "2026-07-01", "settings") is False  # same model: no change
    assert len(c.indexed_with.llm_eras) == 1
    assert open_era(c, "b", "2026-07-02", "settings") is True
    eras = c.indexed_with.llm_eras
    assert (eras[0].model, eras[0].end) == ("a", "2026-07-02")
    assert (eras[1].model, eras[1].start, eras[1].end) == ("b", "2026-07-02", None)


def test_open_era_on_empty_list():
    c = make_corpus()
    assert open_era(c, "a", "2026-10-02", "seed") is True
    assert c.indexed_with.llm_eras[0].end is None


def test_file_is_valid_json_with_version(tmp_path):
    reg = Registry(tmp_path / "registry.json")
    reg.save(RegistryModel(corpora={"alpha": make_corpus()}))
    data = json.loads((tmp_path / "registry.json").read_text())
    assert data["version"] == 1 and "alpha" in data["corpora"]
