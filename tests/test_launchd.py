import subprocess

from coeos_rag.launchd import Launchd


def fake_runner(rc=0, out="", err=""):
    calls = []

    def run(argv):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, rc, out, err)

    return run, calls


def test_commands_target_the_gui_domain():
    run, calls = fake_runner()
    ld = Launchd(501, run)
    assert ld.domain == "gui/501"
    ld.bootout("eu.x")
    ld.bootstrap("/p/a.plist")
    assert calls == [
        ["launchctl", "bootout", "gui/501/eu.x"],
        ["launchctl", "bootstrap", "gui/501", "/p/a.plist"],
    ]


def test_is_loaded_follows_return_code():
    run, _ = fake_runner(rc=0)
    assert Launchd(501, run).is_loaded("eu.x") is True
    run, _ = fake_runner(rc=113)
    assert Launchd(501, run).is_loaded("eu.x") is False


def test_error_text_is_returned():
    run, _ = fake_runner(rc=5, err="Bootstrap failed")
    assert Launchd(501, run).bootstrap("/p") == (5, "Bootstrap failed")
