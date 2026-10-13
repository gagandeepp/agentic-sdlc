"""Agent artifacts (requirements, design, ...) with their markdown, versioned per run and kind.

People edit the markdown at gates, and downstream prompts are built from the markdown, so an
edit reaches every later agent. `data` keeps the agent's structured output for reference.
"""

import secrets
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.events.bus import EventBus
from app.events.models import ArtifactKind, ArtifactPayload, Phase


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str
    run_id: str
    kind: ArtifactKind
    version: int
    title: str
    data: dict[str, Any]
    markdown: str
    edited_by_user: bool = False
    created_at: datetime


def _title(kind: ArtifactKind) -> str:
    return kind.value.replace("_", " ").capitalize()


def _now_ms() -> datetime:
    # Mongo stores milliseconds; trim so a record equals itself after a round trip.
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


class ArtifactStore:
    def __init__(self, db: AsyncDatabase[dict[str, Any]], bus: EventBus) -> None:
        self._artifacts = db["artifacts"]
        self._bus = bus

    async def ensure_indexes(self) -> None:
        await self._artifacts.create_index(
            [("run_id", ASCENDING), ("kind", ASCENDING), ("version", ASCENDING)], unique=True
        )
        await self._artifacts.create_index([("run_id", ASCENDING), ("ordinal", ASCENDING)])

    async def save(
        self,
        run_id: str,
        kind: ArtifactKind,
        data: dict[str, Any],
        markdown: str,
        *,
        phase: Phase | None,
        agent_id: str | None,
        edited_by_user: bool = False,
    ) -> ArtifactRecord:
        latest = await self.latest(run_id, kind)
        record = ArtifactRecord(
            id=f"art_{secrets.token_hex(6)}",
            run_id=run_id,
            kind=kind,
            version=1 if latest is None else latest.version + 1,
            title=_title(kind),
            data=data,
            markdown=markdown,
            edited_by_user=edited_by_user,
            created_at=_now_ms(),
        )
        ordinal = await self._artifacts.count_documents({"run_id": run_id})
        doc = record.model_dump(mode="python", exclude={"id"})
        await self._artifacts.insert_one({"_id": record.id, "ordinal": ordinal, **doc})
        payload = ArtifactPayload(
            artifact_id=record.id, kind=kind, version=record.version, title=record.title
        )
        event_type = "artifact.created" if record.version == 1 else "artifact.revised"
        await self._bus.emit(run_id, event_type, payload, phase=phase, agent_id=agent_id)
        return record

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        doc = await self._artifacts.find_one({"_id": artifact_id})
        return _from_doc(doc) if doc else None

    async def latest(self, run_id: str, kind: ArtifactKind) -> ArtifactRecord | None:
        doc = await self._artifacts.find_one(
            {"run_id": run_id, "kind": kind.value}, sort=[("version", DESCENDING)]
        )
        return _from_doc(doc) if doc else None

    async def list_for_run(self, run_id: str) -> list[ArtifactRecord]:
        cursor = self._artifacts.find({"run_id": run_id}).sort("ordinal", ASCENDING)
        return [_from_doc(doc) async for doc in cursor]


def _from_doc(doc: dict[str, Any]) -> ArtifactRecord:
    doc.pop("ordinal", None)
    return ArtifactRecord(id=doc.pop("_id"), **doc)
