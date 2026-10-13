"""Runtime configuration from environment variables and env files."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # In the container the entrypoint moves keys into /run/agentic/keys.env (readable by the
    # API user only) and drops them from the process env. Locally we run from api/, so the
    # repo-root .env is "../.env".
    model_config = SettingsConfigDict(
        env_file=("/run/agentic/keys.env", "../.env", ".env"), extra="ignore"
    )

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "agentic_sdlc"
    mongo_timeout_ms: int = 3000
    enable_dev_routes: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]
    sse_ping_seconds: float = 15.0
    subscriber_queue_size: int = 1000

    agent_anthropic_key: SecretStr | None = None
    agent_github_token: SecretStr | None = None
    workspace_root: Path = Path("/tmp/agentic-sdlc")
    proxy_port: int = 0
    llm_upstream: str = "https://api.anthropic.com"
    agent_user: str | None = None
    agent_cli_path: Path = Path("/usr/local/bin/agent-cli")
    # Local development only: let Bash-capable roles run as the API user (no uid boundary).
    allow_unisolated_bash: bool = False
    # Orchestrator (Plan 3)
    git_author_name: str = "agentic-sdlc"
    git_author_email: str = "agentic-sdlc@users.noreply.github.com"
    run_timeout_s: float = 3000.0  # active time; gate and CI waits don't count
    ci_poll_interval_s: float = 15.0
    ci_poll_timeout_s: float = 600.0
    max_diff_bytes: int = 60_000
    default_repo: str = "gagandeepp/agentic-sdlc-sandbox"
    github_api_url: str = "https://api.github.com"
    github_clone_base: str = "https://github.com"
    max_concurrent_runs: int = 1
    scripted_agent_delay_s: float = 1.0  # keyless demo runs: time for the GUI to show each agent
