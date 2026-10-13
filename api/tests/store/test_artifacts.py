from app.events.bus import EventBus
from app.events.models import ArtifactKind, Phase
from app.store.artifacts import ArtifactStore
from app.store.mongo import MongoStore, NewRun


async def test_save_versions_per_kind_and_emits_created_then_revised(store: MongoStore) -> None:
    bus = EventBus(store)
    artifacts = ArtifactStore(store.database, bus)
    await artifacts.ensure_indexes()
    run = await store.create_run(NewRun(requirement="r", repo="o/r"))
    first = await artifacts.save(
        run.id, ArtifactKind.REQUIREMENTS, {"summary": "a"}, "# A\n", phase=Phase.BA, agent_id="ba"
    )
    edited = await artifacts.save(
        run.id, ArtifactKind.REQUIREMENTS, {"summary": "a"}, "# B\n",
        phase=Phase.BA, agent_id=None, edited_by_user=True,
    )  # fmt: skip
    design = await artifacts.save(
        run.id, ArtifactKind.DESIGN, {}, "# D\n", phase=Phase.ARCHITECT, agent_id="architect"
    )
    assert (first.version, edited.version, design.version) == (1, 2, 1)
    assert edited.title == "Requirements" and edited.edited_by_user
    assert await artifacts.latest(run.id, ArtifactKind.REQUIREMENTS) == edited
    assert await artifacts.get(first.id) == first
    assert await artifacts.get("art_missing") is None
    assert [a.id for a in await artifacts.list_for_run(run.id)] == [first.id, edited.id, design.id]

    events = [e async for e in store.list_events_after(run.id)]
    assert [e.type for e in events] == ["artifact.created", "artifact.revised", "artifact.created"]
    assert events[1].payload.artifact_id == edited.id  # type: ignore[union-attr]
    assert events[1].agent_id is None and events[0].agent_id == "ba"
