from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import Settings


def _layout(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text("MONGO_DB=from_root_env\nAGENT_ANTHROPIC_KEY=sk-ant-test-key\n")
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    return api_dir


def test_settings_read_the_repo_root_env_when_run_from_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_layout(tmp_path))
    settings = Settings()
    assert settings.mongo_db == "from_root_env"
    assert settings.agent_anthropic_key is not None
    assert settings.agent_anthropic_key.get_secret_value() == "sk-ant-test-key"
    assert "sk-ant-test-key" not in repr(settings)


def test_tests_can_ignore_env_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(_layout(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.mongo_db == "agentic_sdlc"
    assert settings.agent_anthropic_key is None
    assert settings.proxy_port == 0
    assert settings.agent_user is None


def test_only_one_live_run_at_a_time() -> None:
    # Every agent runs as the one `agent` uid, and cleanup kills all of its processes, so two
    # live runs would kill and tamper with each other's agents.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_concurrent_runs=2)
