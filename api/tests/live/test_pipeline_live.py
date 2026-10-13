"""A real run against the sandbox repo: RUN_LIVE=1 with both keys in the repo-root .env.

Outside the container there is no agent uid, so this also needs ALLOW_UNISOLATED_BASH=true
(accepting that agents run as you). Spends up to $2 and leaves a ready PR on
gagandeepp/agentic-sdlc-sandbox for inspection.
"""

import os
import uuid
from pathlib import Path

import httpx
import pytest

from app.settings import Settings
from tests.run_helpers import approve, ended, running, wait_for

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1", reason="live test: set RUN_LIVE=1"
)

REQUIREMENT = (
    'Add a GET /health endpoint to the Python service that returns {"status": "ok"}, '
    "with a pytest test."
)
GATE_TIMEOUT_S = 1800.0


async def test_a_live_run_reaches_a_ready_pull_request(mongo_uri: str, tmp_path: Path) -> None:
    settings = Settings(
        mongo_uri=mongo_uri,
        mongo_db=f"live_{uuid.uuid4().hex[:8]}",
        workspace_root=tmp_path,
        enable_dev_routes=True,
        ci_poll_interval_s=10.0,
    )
    if settings.agent_anthropic_key is None or settings.agent_github_token is None:
        pytest.skip("AGENT_ANTHROPIC_KEY and AGENT_GITHUB_TOKEN must be configured")
    async with running(settings) as (app, client):
        response = await client.post(
            "/api/runs", json={"requirement": REQUIREMENT, "budget_usd": 2.0}
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        for kind in ("requirements", "design", "release"):
            await approve(client, app, run_id, kind, limit_s=GATE_TIMEOUT_S)
        events = await wait_for(app, run_id, ended, limit_s=GATE_TIMEOUT_S)
        record = (await client.get(f"/api/runs/{run_id}")).json()
    assert events[-1].type == "run.completed", events[-1].payload
    assert 0 < record["spent_usd"] <= 2.0

    number = int(record["pr_url"].rsplit("/", 1)[1])
    token = settings.agent_github_token.get_secret_value()
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as github:
        api = f"{settings.github_api_url}/repos/{settings.default_repo}/pulls/{number}"
        pull = (await github.get(api)).json()
        files = [f["filename"] for f in (await github.get(f"{api}/files")).json()]
    assert pull["draft"] is False
    assert any(name.startswith("src/") for name in files)
    assert any(name.startswith("tests/") for name in files)
    assert f"docs/sdlc/{run_id}/requirements.md" in files
