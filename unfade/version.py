"""Which correction code made a copy, and a snapshot of that code for the archive.

The code id is a digest of the files that decide a copy's pixels and format, so it names the
code itself, whether or not it was committed: an archive snapshot is accepted only when its
files hash to the same id. The git commit (and whether the tree had uncommitted changes) is
recorded beside it as a pointer back into the repository.
"""
from __future__ import annotations

import functools
import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_FILES = (
    "unfade/__init__.py", "unfade/color_methods.py", "unfade/fade_model.py", "unfade/encode.py",
    "unfade/fingerprint.py", "unfade/render.py", "unfade/recompute.py", "unfade/version.py",
    "pyproject.toml", "uv.lock",
)


def digest(root: Path) -> str:
    """The code id of the CODE_FILES under `root` (an archive snapshot or the repository)."""
    total = hashlib.sha256()
    for name in CODE_FILES:
        path = root / name
        total.update(name.encode() + b"\0")
        total.update(path.read_bytes() if path.is_file() else b"<missing>")
        total.update(b"\0")
    return total.hexdigest()[:16]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@functools.lru_cache(maxsize=1)
def code_version() -> dict:
    """{"id", "commit", "dirty"} of the running code; commit is "" outside a git checkout."""
    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--", *CODE_FILES)) if commit else False
    return {"id": digest(ROOT), "commit": commit, "dirty": dirty}


def snapshot(code: dict, target: Path) -> bool:
    """Copy the code with this id into `target`; False when it is no longer available.

    The running code is used when its id matches; otherwise the recorded commit is read from
    git. Either way the copy must hash to the id, so a snapshot is never a near miss.
    """
    if (target / "CODE_ID").is_file() and (target / "CODE_ID").read_text().strip() == code["id"]:
        return True
    staging = target.with_name(target.name + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    if digest(ROOT) == code["id"]:
        for name in CODE_FILES:
            if (ROOT / name).is_file():
                (staging / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, staging / name)
    elif code.get("commit"):
        for name in CODE_FILES:
            content = subprocess.run(["git", "-C", str(ROOT), "show", f"{code['commit']}:{name}"], capture_output=True)
            if content.returncode == 0:
                (staging / name).parent.mkdir(parents=True, exist_ok=True)
                (staging / name).write_bytes(content.stdout)
    if digest(staging) != code["id"]:
        shutil.rmtree(staging, ignore_errors=True)
        return False
    (staging / "CODE_ID").write_text(code["id"] + "\n")
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return True
