import os
import subprocess
import sys

from app.paths import API_ROOT

WRAPPER = API_ROOT / "docker" / "agent-cli"


def test_wrapper_passes_only_the_allowlisted_environment() -> None:
    env = {
        "PATH": os.environ["PATH"],
        "AGENT_CLI_NO_SUDO": "1",
        "CLAUDE_AGENT_REAL_CLI": "/usr/bin/env",  # prints the environment it receives
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787",
        "ANTHROPIC_AUTH_TOKEN": "agt_placeholder",
        "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
        "CLAUDE_CONFIG_DIR": "/work/runs/r/claude/ba",
        "AGENT_ANTHROPIC_KEY": "sk-ant-test-SECRET",
        "ANTHROPIC_API_KEY": "sk-ant-test-SECRET2",
        "GH_TOKEN": "ghp_testsecret",
        "MONGO_URI": "mongodb://mongo:27017",
        "GIT_ASKPASS": "/tmp/x",
    }
    # `env NAME=value` prints NAME=value too, which proves CLI arguments are forwarded.
    output = subprocess.run(
        [sys.executable, str(WRAPPER), "FORWARDED_ARG=1"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    received = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    assert received["FORWARDED_ARG"] == "1"
    assert received["ANTHROPIC_AUTH_TOKEN"] == "agt_placeholder"
    assert received["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"
    assert received["CLAUDE_CONFIG_DIR"] == "/work/runs/r/claude/ba"
    assert received["HOME"] == "/work/agent-home"
    assert received["GIT_CONFIG_KEY_0"] == "safe.directory"
    leaked_names = (
        "AGENT_ANTHROPIC_KEY",
        "ANTHROPIC_API_KEY",
        "GH_TOKEN",
        "MONGO_URI",
        "GIT_ASKPASS",
        "CLAUDE_AGENT_REAL_CLI",
        "AGENT_CLI_NO_SUDO",
    )
    for leaked in leaked_names:
        assert leaked not in received
    assert "SECRET" not in output and "ghp_" not in output
