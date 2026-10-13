import os
import stat
import subprocess
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.gitops.local import create_local_remote
from app.gitops.repo import GitError, GitIdentity, PushRefused, RunRepo

IDENTITY = GitIdentity("agentic-sdlc", "agentic-sdlc@users.noreply.github.com")
CREDENTIAL_QUERY = b"protocol=https\nhost=github.com\n\n"


@pytest.fixture
def remote(tmp_path: Path) -> str:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Demo\n\nA demo repo.\n")
    (source / "app.py").write_text("print('hi')\n")
    return create_local_remote(source, tmp_path / "remote.git")


async def _clone(tmp_path: Path, url: str, branch: str = "main", name: str = "a") -> RunRepo:
    return await RunRepo.clone(
        url,
        branch,
        worktree=tmp_path / name / "repo",
        git_dir=tmp_path / "git" / name,
        identity=IDENTITY,
    )


def _remote_head(remote_dir: Path, branch: str) -> str | None:
    out = subprocess.run(
        ["git", "--git-dir", str(remote_dir), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return out or None


async def test_clone_branch_commit_and_push_with_a_lease(tmp_path: Path, remote: str) -> None:
    repo = await _clone(tmp_path, remote)
    base = await repo.head()
    await repo.start_branch("sdlc/run_1", base)
    assert await repo.commit_all("nothing yet") is None
    (repo.worktree / "feature.py").write_text("x = 1\n")
    sha = await repo.commit_all("feat: feature")
    assert sha is not None and sha != base
    assert await repo.changed_files(base) == ["feature.py"]
    assert "+x = 1" in await repo.diff_against(base, 10_000)
    assert "[diff truncated" in await repo.diff_against(base, 10)
    assert await repo.push("sdlc/run_1", base=base, expected=None) == sha
    assert _remote_head(tmp_path / "remote.git", "sdlc/run_1") == sha

    (repo.worktree / "feature.py").write_text("x = 2\n")
    second = await repo.commit_all("fix: feature")
    with pytest.raises(GitError):  # the branch exists now, so "expected absent" is a stale lease
        await repo.push("sdlc/run_1", base=base, expected=None)
    assert await repo.push("sdlc/run_1", base=base, expected=sha) == second


async def test_git_dir_is_outside_the_tree_and_group_read_only(tmp_path: Path, remote: str) -> None:
    repo = await _clone(tmp_path, remote)
    await repo.start_branch("sdlc/run_1", await repo.head())
    (repo.worktree / "f.txt").write_text("x\n")
    await repo.commit_all("c")
    assert (repo.worktree / ".git").is_file()
    assert not repo.git_dir.is_relative_to(repo.worktree)
    assert stat.S_IMODE(repo.git_dir.parent.stat().st_mode) == 0o2750
    for path in [repo.git_dir, *repo.git_dir.rglob("*")]:
        mode = path.lstat().st_mode
        assert not mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IROTH), path
        if path.is_dir():
            assert stat.S_IMODE(mode) == 0o2750, path
    assert (repo.git_dir / "info" / "exclude").read_text().startswith("node_modules/")
    # The directory holding the tree is API-owned too, so agents can't swap the tree itself.
    assert stat.S_IMODE(repo.worktree.parent.stat().st_mode) == 0o2750


async def test_a_worktree_replaced_by_a_symlink_is_refused(tmp_path: Path, remote: str) -> None:
    repo = await _clone(tmp_path, remote)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "keys.env").write_text("AGENT_GITHUB_TOKEN=secret-value\n")
    moved = repo.worktree.with_name("moved")
    repo.worktree.rename(moved)
    repo.worktree.symlink_to(secrets_dir)
    with pytest.raises(GitError, match="symlink"):
        await repo.commit_all("steal")


async def test_planted_hooks_and_fsmonitor_never_run(tmp_path: Path, remote: str) -> None:
    repo = await _clone(tmp_path, remote)
    await repo.start_branch("sdlc/run_1", await repo.head())
    marker = tmp_path / "pwned"
    script = f"#!/bin/sh\ntouch {marker}\n"
    # An agent replaces the worktree's .git file with a directory of hostile config and hooks.
    (repo.worktree / ".git").unlink()
    fake = repo.worktree / ".git"
    (fake / "hooks").mkdir(parents=True)
    (fake / "config").write_text(f"[core]\n\tfsmonitor = touch {marker}\n\thooksPath = hooks\n")
    for hook in ("pre-commit", "post-commit"):
        (fake / "hooks" / hook).write_text(script)
        (fake / "hooks" / hook).chmod(0o755)
    # Even a hook inside the real git dir is ignored.
    (repo.git_dir / "hooks").mkdir(exist_ok=True)
    (repo.git_dir / "hooks" / "pre-commit").write_text(script)
    (repo.git_dir / "hooks" / "pre-commit").chmod(0o755)
    (repo.worktree / "f.txt").write_text("x\n")
    sha = await repo.commit_all("c")
    assert sha is not None and await repo.head() == sha
    assert not marker.exists()


async def test_push_refuses_workflow_changes_other_branches_and_live_agents(
    tmp_path: Path, remote: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = await _clone(tmp_path, remote)
    base = await repo.head()
    await repo.start_branch("sdlc/run_1", base)
    (repo.worktree / ".github" / "workflows").mkdir(parents=True)
    (repo.worktree / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    await repo.commit_all("ci")
    with pytest.raises(PushRefused, match="workflows"):
        await repo.push("sdlc/run_1", base=base, expected=None)
    with pytest.raises(PushRefused, match="not on"):
        await repo.push("sdlc/other", base=base, expected=None)

    async def alive(user: str | None) -> bool:
        return True

    monkeypatch.setattr("app.gitops.repo.agent_processes_alive", alive)
    guarded = RunRepo(repo.worktree, repo.git_dir, identity=IDENTITY, agent_user="agent")
    with pytest.raises(GitError, match="agent processes"):
        await guarded.fetch_commit("0" * 40)
    assert _remote_head(tmp_path / "remote.git", "sdlc/run_1") is None


async def test_the_token_reaches_git_only_through_the_credential_helper(
    tmp_path: Path, remote: str
) -> None:
    repo = await _clone(tmp_path, remote)
    token = "ghp_" + "t" * 36
    authed = RunRepo(repo.worktree, repo.git_dir, identity=IDENTITY, token=SecretStr(token))
    filled = await authed._run("credential", "fill", network=True, stdin=CREDENTIAL_QUERY)
    assert "username=x-access-token" in filled.out and f"password={token}" in filled.out
    plain = await authed._run("credential", "fill", stdin=CREDENTIAL_QUERY, check=False)
    assert token not in plain.out  # no helper outside network commands
    assert token not in (repo.git_dir / "config").read_text()


async def test_a_run_branch_clone_fetches_its_base_for_diffs(tmp_path: Path, remote: str) -> None:
    first = await _clone(tmp_path, remote)
    base = await first.head()
    await first.start_branch("sdlc/run_1", base)
    (first.worktree / "f.txt").write_text("x\n")
    await first.commit_all("c")
    await first.push("sdlc/run_1", base=base, expected=None)

    resumed = await _clone(tmp_path, remote, branch="sdlc/run_1", name="b")
    assert await resumed.current_branch() == "sdlc/run_1"
    assert not await resumed.has_commit(base)
    await resumed.fetch_commit(base)
    assert await resumed.changed_files(base) == ["f.txt"]


async def test_summary_and_writes_never_follow_symlinks(tmp_path: Path, remote: str) -> None:
    repo = await _clone(tmp_path, remote)
    (repo.worktree / "node_modules" / "x").mkdir(parents=True)
    (repo.worktree / "node_modules" / "x" / "index.js").write_text("")
    summary = repo.summary()
    assert "app.py" in summary and "A demo repo." in summary
    assert "node_modules" not in summary

    secret = tmp_path / "keys.env"
    secret.write_text("AGENT_GITHUB_TOKEN=secret-value\n")
    (repo.worktree / "README.md").unlink()
    (repo.worktree / "README.md").symlink_to(secret)
    assert "secret-value" not in repo.summary()

    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.worktree / "docs").symlink_to(outside)
    repo.write_file("docs/sdlc/run_1/requirements.md", "# Requirements\n")
    assert list(outside.iterdir()) == []
    assert not (repo.worktree / "docs").is_symlink()
    written = repo.worktree / "docs" / "sdlc" / "run_1" / "requirements.md"
    assert written.read_text() == "# Requirements\n"
    os.symlink(secret, written.with_name("design.md"))
    repo.write_file("docs/sdlc/run_1/design.md", "# Design\n")
    assert secret.read_text().startswith("AGENT_GITHUB_TOKEN")
    with pytest.raises(ValueError):
        repo.write_file("../escape.md", "x")
