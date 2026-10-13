"""Git for one run: an agent-writable working tree and an API-owned git dir outside it.

Agents (a separate uid in the `sdlc` group) can write the working tree, so they could plant
`.git/hooks/*` or config such as `core.fsmonitor` that would run as the API user on our next
git command. Therefore:

- the git dir lives outside the tree, in a directory agents can read but not write;
- every command names GIT_DIR and GIT_WORK_TREE, ignores system and global config, and turns
  off hooks and fsmonitor;
- network commands get the token from a one-shot credential helper in the git process's
  environment (never .git/config), and refuse to run while any agent process is alive;
- the API reads and writes files in the tree without following symlinks.
"""

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

GIT_DIR_MODE = 0o2750
GIT_FILE_MODE = 0o640
# Workflows and composite actions under .github/ run in CI with the repository's secrets.
# Matched by pathspec, case-insensitively, on raw (-z) names: no path quoting to get around.
CI_PATHSPEC = ":(icase).github"
SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", "dist", "build"}
)
EXCLUDES = "".join(
    f"{pattern}\n"
    for pattern in (
        "node_modules/",
        ".venv/",
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        "dist/",
        "build/",
        "coverage/",
    )
)
README_NAMES = ("README.md", "README.rst", "README.txt", "README")
_HARDENING = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=false",
    "-c", "safe.directory=*",
    "-c", "core.sharedRepository=0640",
    "-c", "commit.gpgSign=false",
)  # fmt: skip
# `credential.helper=` clears helpers from any other config; the next one reads the token
# from the git process's own environment.
_CREDENTIALS = (
    "-c", "credential.helper=",
    "-c",
    "credential.helper=!f() { test \"$1\" = get && "
    "printf 'username=x-access-token\\npassword=%s\\n' \"$AGENTIC_GIT_TOKEN\"; }; f",
)  # fmt: skip


class GitError(RuntimeError):
    pass


class PushRefused(GitError):
    pass


@dataclass(frozen=True)
class GitIdentity:
    name: str
    email: str


@dataclass(frozen=True)
class GitResult:
    code: int
    out: str
    err: str


async def agent_processes_alive(user: str | None) -> bool:
    """True if any process runs as the agent uid (pgrep exits 0 when it finds one)."""
    if user is None:
        return False
    process = await asyncio.create_subprocess_exec(
        "pgrep", "-u", user,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    return await process.wait() == 0


def lock_down(git_dir: Path) -> None:
    """Agents may read the git dir (they run `git diff`); only the API user may write it."""
    os.chmod(git_dir, GIT_DIR_MODE)
    for root, dirs, files in os.walk(git_dir):
        for name, mode in [
            *((d, GIT_DIR_MODE) for d in dirs),
            *((f, GIT_FILE_MODE) for f in files),
        ]:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                os.chmod(path, mode)


def read_inside(root: Path, relative: str, limit: int) -> str | None:
    """Up to `limit` bytes of a regular file under `root`, or None; never through a symlink."""
    path = root / relative
    if path.is_symlink() or not path.is_file():
        return None
    if not Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root)):
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        return handle.read(limit).decode("utf-8", errors="replace")


def write_inside(root: Path, relative: str, text: str) -> None:
    """Create or replace a file under `root`, replacing (not following) symlinks on the way."""
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"not a path inside the repository: {relative!r}")
    directory = root
    for part in path.parts[:-1]:
        directory = directory / part
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            directory.unlink()
        directory.mkdir(exist_ok=True)
    target = directory / path.parts[-1]
    if target.is_symlink():
        target.unlink()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o664)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


class RunRepo:
    def __init__(
        self,
        worktree: Path,
        git_dir: Path,
        *,
        identity: GitIdentity,
        token: SecretStr | None = None,
        agent_user: str | None = None,
    ) -> None:
        self.worktree = worktree
        self.git_dir = git_dir
        self._identity = identity
        self._token = token
        self._agent_user = agent_user

    @classmethod
    async def clone(
        cls,
        url: str,
        branch: str,
        *,
        worktree: Path,
        git_dir: Path,
        identity: GitIdentity,
        token: SecretStr | None = None,
        agent_user: str | None = None,
    ) -> "RunRepo":
        """Shallow-clone `branch` into a fresh worktree whose git dir is `git_dir`."""
        for path in (worktree, git_dir):
            if path.exists():
                shutil.rmtree(path)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        # Agents may write inside the tree, but must not rename or replace the tree or a git
        # dir: the directories that hold them are API-owned and group read-only.
        os.chmod(worktree.parent, GIT_DIR_MODE)
        os.chmod(git_dir.parent, GIT_DIR_MODE)
        repo = cls(worktree, git_dir, identity=identity, token=token, agent_user=agent_user)
        await repo._run(
            "clone", "--quiet", "--depth=1", "--no-tags", f"--branch={branch}",
            f"--separate-git-dir={git_dir}", "--", url, str(worktree),
            network=True, bind=False,
        )  # fmt: skip
        (git_dir / "info").mkdir(exist_ok=True)
        (git_dir / "info" / "exclude").write_text(EXCLUDES)
        lock_down(git_dir)
        return repo

    def _env(self, *, bind: bool, network: bool) -> dict[str, str]:
        name, email = self._identity.name, self._identity.email
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/nonexistent"),
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
        if bind:
            env["GIT_DIR"] = str(self.git_dir)
            env["GIT_WORK_TREE"] = str(self.worktree)
        if network and self._token is not None:
            env["AGENTIC_GIT_TOKEN"] = self._token.get_secret_value()
        return env

    def _redact(self, text: str) -> str:
        if self._token is None:
            return text
        return text.replace(self._token.get_secret_value(), "***")

    async def _run(
        self,
        *args: str,
        network: bool = False,
        bind: bool = True,
        check: bool = True,
        stdin: bytes | None = None,
    ) -> GitResult:
        if network and await agent_processes_alive(self._agent_user):
            raise GitError("refusing a network git command while agent processes are running")
        if bind and self.worktree.is_symlink():
            # `git add` would read whatever the link points at (for example the keys file).
            raise GitError("the working tree was replaced by a symlink; refusing to use it")
        process = await asyncio.create_subprocess_exec(
            "git", *_HARDENING, *(_CREDENTIALS if network else ()), *args,
            cwd=self.worktree if bind else self.worktree.parent,
            env=self._env(bind=bind, network=network),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        try:
            out, err = await process.communicate(stdin)
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        assert process.returncode is not None
        result = GitResult(
            process.returncode,
            out.decode(errors="replace"),
            self._redact(err.decode(errors="replace")),
        )
        if check and result.code != 0:
            raise GitError(f"git {args[0]} failed ({result.code}): {result.err.strip()[:500]}")
        return result

    async def head(self) -> str:
        return (await self._run("rev-parse", "HEAD")).out.strip()

    async def current_branch(self) -> str:
        return (await self._run("symbolic-ref", "--quiet", "--short", "HEAD")).out.strip()

    async def has_commit(self, sha: str) -> bool:
        return (await self._run("cat-file", "-e", f"{sha}^{{commit}}", check=False)).code == 0

    async def fetch_commit(self, sha: str) -> None:
        """Make `sha` available locally (a shallow clone of the run branch lacks its base)."""
        if not await self.has_commit(sha):
            await self._run(
                "fetch", "--quiet", "--depth=1", "--no-tags", "origin", sha, network=True
            )
            lock_down(self.git_dir)

    async def start_branch(self, name: str, at: str) -> None:
        await self._run("checkout", "--quiet", "-B", name, at)
        lock_down(self.git_dir)

    async def commit_all(self, message: str) -> str | None:
        """Commit every change in the tree; None when there is nothing to commit."""
        await self._run("add", "--all")
        if (await self._run("diff", "--cached", "--quiet", check=False)).code == 0:
            return None
        await self._run("commit", "--quiet", "--no-verify", "-m", message)
        lock_down(self.git_dir)
        return await self.head()

    async def changed_files(self, base: str) -> list[str]:
        out = (await self._run("diff", "--name-only", "--no-renames", base, "HEAD")).out
        return [line for line in out.splitlines() if line]

    async def ci_changes(self, base: str) -> list[str]:
        """Changed paths under .github/ (any case) between `base` and HEAD."""
        out = (
            await self._run(
                "diff", "--name-only", "--no-renames", "-z", base, "HEAD", "--", CI_PATHSPEC
            )
        ).out
        return [path for path in out.split("\0") if path]

    async def diff_against(self, base: str, max_bytes: int) -> str:
        out = (
            await self._run("diff", "--no-color", "--no-ext-diff", "--no-textconv", base, "HEAD")
        ).out
        raw = out.encode()
        if len(raw) <= max_bytes:
            return out
        clipped = raw[:max_bytes].decode(errors="ignore")
        return f"{clipped}\n[diff truncated: showing {max_bytes} of {len(raw)} bytes]\n"

    async def push(self, branch: str, *, base: str, expected: str | None) -> str:
        """Push HEAD to `branch` if the remote branch is still at `expected` (None: absent)."""
        if await self.current_branch() != branch:
            raise PushRefused(f"HEAD is not on {branch}")
        ci = await self.ci_changes(base)
        if ci:
            raise PushRefused(
                "the change touches CI configuration (workflows and actions under .github/): "
                + ", ".join(ci)
            )
        lease = f"--force-with-lease=refs/heads/{branch}:{expected or ''}"
        await self._run(
            "push", "--quiet", lease, "origin", f"HEAD:refs/heads/{branch}", network=True
        )
        return await self.head()

    def summary(self, *, max_files: int = 300, readme_bytes: int = 4000) -> str:
        """A capped file list plus the README head: what BA and Architect see first."""
        files: list[str] = []
        for directory, dirnames, filenames in os.walk(self.worktree):
            dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
            relative = Path(directory).relative_to(self.worktree)
            files.extend(
                (relative / name).as_posix() for name in sorted(filenames) if name != ".git"
            )
            if len(files) > max_files:
                break
        listing = "\n".join(files[:max_files])
        if len(files) > max_files:
            listing += f"\n... (more than {max_files} files; list truncated)"
        readme = next(
            (
                text
                for name in README_NAMES
                if (text := read_inside(self.worktree, name, readme_bytes))
            ),
            None,
        )
        return f"Files:\n{listing}\n\nREADME (first {readme_bytes} bytes):\n{readme or '(none)'}\n"

    def write_file(self, relative: str, text: str) -> None:
        write_inside(self.worktree, relative, text)
