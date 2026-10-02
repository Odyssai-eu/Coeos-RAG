"""Safe edits of a LightRAG launchd plist.

Only an allow-list of environment keys can be changed, values are validated, the file is
backed up before any write and replaced atomically.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import tempfile
import time
from pathlib import Path
from typing import Mapping, Optional

# The only keys the control plane may change in an instance plist.
MUTABLE_ENV_KEYS = frozenset(
    {"LLM_MODEL", "EMBEDDING_BINDING_HOST", "EMBEDDING_MODEL", "EMBEDDING_DIM"}
)


class PlistError(ValueError):
    pass


def read_env(path: Path) -> dict[str, str]:
    with open(path, "rb") as f:
        data = plistlib.load(f)
    return dict(data.get("EnvironmentVariables") or {})


def _check_value(key: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PlistError(f"{key}: empty value")
    if any(c in value for c in ("\n", "\r", "\x00")):
        raise PlistError(f"{key}: control character in value")
    if len(value) > 512:
        raise PlistError(f"{key}: value too long")


def backup(path: Path, *, now: Optional[float] = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    dest = path.with_name(f"{path.name}.bak-odyrag-{stamp}")
    n = 0
    while dest.exists():  # two changes within one second must not overwrite a backup
        n += 1
        dest = path.with_name(f"{path.name}.bak-odyrag-{stamp}-{n}")
    shutil.copy2(path, dest)
    return dest


def apply_env(path: Path, updates: Mapping[str, str], *, now: Optional[float] = None) -> Path:
    """Backup `path`, then write `updates` into its EnvironmentVariables. Returns the backup."""
    if not updates:
        raise PlistError("no update requested")
    for key, value in updates.items():
        if key not in MUTABLE_ENV_KEYS:
            raise PlistError(f"{key}: not a key the control plane may change")
        _check_value(key, value)
    with open(path, "rb") as f:
        data = plistlib.load(f)
    env = dict(data.get("EnvironmentVariables") or {})
    env.update(updates)
    data["EnvironmentVariables"] = env
    bak = backup(path, now=now)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            plistlib.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return bak


def restore(backup_path: Path, path: Path) -> None:
    """Put a backup back in place (atomically)."""
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    os.close(fd)
    try:
        shutil.copy2(backup_path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
