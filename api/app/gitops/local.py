"""Bare repositories on disk that stand in for GitHub (tests and the keyless scripted runs)."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_SEED_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "agentic-sdlc seed",
    "GIT_AUTHOR_EMAIL": "seed@agentic-sdlc.invalid",
    "GIT_COMMITTER_NAME": "agentic-sdlc seed",
    "GIT_COMMITTER_EMAIL": "seed@agentic-sdlc.invalid",
}


def _git(cwd: Path, *args: str) -> None:
    env = {**_SEED_ENV, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )


def create_local_remote(source: Path, remote: Path, *, branch: str = "main") -> str:
    """Create a bare repo at `remote` whose `branch` holds a copy of `source`; return its URL."""
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch) / "seed"
        shutil.copytree(source, work, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        _git(work, "init", "--quiet", f"--initial-branch={branch}")
        _git(work, "add", "--all")
        _git(work, "commit", "--quiet", "-m", "Initial commit")
        remote.parent.mkdir(parents=True, exist_ok=True)
        _git(work, "clone", "--quiet", "--bare", str(work), str(remote))
    return remote.as_uri()
