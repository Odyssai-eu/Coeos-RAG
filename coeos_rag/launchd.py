"""Thin wrapper over `launchctl` for the user's GUI domain.

The command runner is injectable so the apply flow is testable without launchd.
"""
from __future__ import annotations

import subprocess
from typing import Callable, Sequence

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=60)


class Launchd:
    def __init__(self, uid: int, run: Runner = _default_runner) -> None:
        self.uid = uid
        self._run = run

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    def bootout(self, label: str) -> tuple[int, str]:
        r = self._run(["launchctl", "bootout", f"{self.domain}/{label}"])
        return r.returncode, (r.stderr or r.stdout or "").strip()

    def bootstrap(self, plist_path: str) -> tuple[int, str]:
        r = self._run(["launchctl", "bootstrap", self.domain, plist_path])
        return r.returncode, (r.stderr or r.stdout or "").strip()

    def is_loaded(self, label: str) -> bool:
        r = self._run(["launchctl", "print", f"{self.domain}/{label}"])
        return r.returncode == 0
