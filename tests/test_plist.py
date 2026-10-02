import plistlib
import stat

import pytest

from odyrag import plist as P


def write_plist(path, **env):
    base = {
        "Label": "eu.test.lightrag",
        "KeepAlive": True,
        "RunAtLoad": True,
        "WorkingDirectory": "/w",
        "ProgramArguments": ["/bin/python", "-m", "lightrag", "--port", "9"],
        "EnvironmentVariables": {"LLM_MODEL": "old", "LLM_BINDING_HOST": "http://h/v1", **env},
    }
    with open(path, "wb") as f:
        plistlib.dump(base, f)
    return path


def test_apply_changes_only_requested_keys_and_backs_up(tmp_path):
    p = write_plist(tmp_path / "a.plist")
    bak = P.apply_env(p, {"LLM_MODEL": "new"}, now=1_700_000_000)
    env = P.read_env(p)
    assert env["LLM_MODEL"] == "new" and env["LLM_BINDING_HOST"] == "http://h/v1"
    assert P.read_env(bak)["LLM_MODEL"] == "old"
    data = plistlib.loads(p.read_bytes())
    assert data["KeepAlive"] is True and data["ProgramArguments"][-1] == "9"
    assert ".bak-odyrag-" in bak.name


def test_rejects_unlisted_key_empty_and_control_chars(tmp_path):
    p = write_plist(tmp_path / "a.plist")
    before = p.read_bytes()
    for bad in ({"PATH": "/x"}, {"LLM_MODEL": ""}, {"LLM_MODEL": "a\nb"}, {"LLM_MODEL": "x" * 600}, {}):
        with pytest.raises(P.PlistError):
            P.apply_env(p, bad)
    assert p.read_bytes() == before
    assert [f.name for f in tmp_path.iterdir()] == ["a.plist"]  # no stray backup/tmp


def test_two_changes_in_same_second_keep_both_backups(tmp_path):
    p = write_plist(tmp_path / "a.plist")
    b1 = P.apply_env(p, {"LLM_MODEL": "m1"}, now=1_700_000_000)
    b2 = P.apply_env(p, {"LLM_MODEL": "m2"}, now=1_700_000_000)
    assert b1 != b2 and P.read_env(b1)["LLM_MODEL"] == "old" and P.read_env(b2)["LLM_MODEL"] == "m1"


def test_restore_puts_back_original(tmp_path):
    p = write_plist(tmp_path / "a.plist")
    bak = P.apply_env(p, {"LLM_MODEL": "new"})
    P.restore(bak, p)
    assert P.read_env(p)["LLM_MODEL"] == "old"


def test_file_mode_preserved(tmp_path):
    p = write_plist(tmp_path / "a.plist")
    p.chmod(0o600)
    P.apply_env(p, {"LLM_MODEL": "new"})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
