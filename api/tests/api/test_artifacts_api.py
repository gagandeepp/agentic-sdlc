from fastapi import FastAPI
from httpx import AsyncClient

from app.events.models import ArtifactKind, Phase
from app.store.mongo import NewRun


async def test_list_and_get_artifacts(app: FastAPI, client: AsyncClient) -> None:
    run = await app.state.store.create_run(NewRun(requirement="r", repo="o/r"))
    saved = await app.state.artifacts.save(
        run.id,
        ArtifactKind.REQUIREMENTS,
        {"summary": "s"},
        "# Req\n",
        phase=Phase.BA,
        agent_id="ba",
    )
    listed = (await client.get(f"/api/runs/{run.id}/artifacts")).json()
    assert [a["id"] for a in listed] == [saved.id]
    one = await client.get(f"/api/runs/{run.id}/artifacts/{saved.id}")
    assert one.status_code == 200 and one.json()["markdown"] == "# Req\n"
    assert (await client.get(f"/api/runs/run_other/artifacts/{saved.id}")).status_code == 404
    assert (await client.get("/api/runs/run_missing/artifacts")).status_code == 404
