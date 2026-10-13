"""Helpers for running agent sessions as a separate, unprivileged uid."""

import asyncio
from pathlib import Path

import claude_agent_sdk


def bundled_cli_path() -> Path:
    """The Claude Code CLI shipped inside the claude-agent-sdk wheel."""
    return Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"


async def kill_agent_processes(user: str) -> None:
    """Kill everything still running as `user` (background shells a session left behind)."""
    process = await asyncio.create_subprocess_exec(
        "sudo", "-n", "-u", user, "--", "/bin/kill", "-KILL", "-1",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    await process.wait()
