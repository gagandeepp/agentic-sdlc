"""MongoDB persistence: run records (with a live projection) and the append-only event log."""

import secrets
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.events.models import (
    EVENT_ADAPTER,
    TERMINAL_EVENT_TYPES,
    AgentUsageEvent,
    BudgetUpdatedEvent,
    Checkpoint,
    GateOpenedEvent,
    GateResolvedEvent,
    Phase,
    PhaseStartedEvent,
    PrOpenedEvent,
    RunEvent,
    RunStatus,
    RunStatusChangedEvent,
)

_TERMINAL_STATUS = {
    "run.completed": RunStatus.COMPLETED,
    "run.failed": RunStatus.FAILED,
    "run.aborted": RunStatus.ABORTED,
    "run.interrupted": RunStatus.INTERRUPTED,
}


class RunNotFound(Exception):
    pass


RunMode = Literal["live", "scripted", "demo"]


class NewRun(BaseModel):
    requirement: str = Field(min_length=1, max_length=4000)
    repo: str = Field(min_length=1)
    base_branch: str = "main"
    budget_usd: float = Field(default=5.0, gt=0)
    max_parallel_devs: int = Field(default=3, ge=1, le=8)
    mode: RunMode = "live"


_ALWAYS_SERIALIZED = ConfigDict(json_schema_serialization_defaults_required=True)


class TokenTotals(BaseModel):
    model_config = _ALWAYS_SERIALIZED

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class RunRecord(BaseModel):
    model_config = _ALWAYS_SERIALIZED

    id: str
    requirement: str
    repo: str
    base_branch: str
    budget_usd: float
    max_parallel_devs: int
    mode: RunMode = "live"
    status: RunStatus = RunStatus.PENDING
    phase: Phase | None = None
    checkpoint: Checkpoint | None = None
    checkpoint_data: dict[str, Any] = Field(default_factory=dict)
    pr_url: str | None = None
    spent_usd: float = 0.0
    tokens: TokenTotals = Field(default_factory=TokenTotals)
    last_seq: int = 0
    created_at: datetime
    updated_at: datetime


def new_run_id() -> str:
    return f"run_{secrets.token_hex(4)}"


class MongoStore:
    def __init__(self, db: AsyncDatabase[dict[str, Any]]) -> None:
        self._db = db
        self._runs = db["runs"]
        self._events = db["events"]
        self._indexes_ready = False

    async def ensure_indexes(self) -> None:
        await self._events.create_index([("run_id", ASCENDING), ("seq", ASCENDING)], unique=True)
        await self._runs.create_index([("created_at", DESCENDING)])
        self._indexes_ready = True

    async def create_run(self, new: NewRun, *, run_id: str | None = None) -> RunRecord:
        if not self._indexes_ready:
            # Mongo may have been down at startup; every run starts here, so the unique
            # (run_id, seq) index always exists before the first event is written.
            await self.ensure_indexes()
        now = datetime.now(UTC)
        record = RunRecord(
            id=run_id or new_run_id(), created_at=now, updated_at=now, **new.model_dump()
        )
        doc = record.model_dump(exclude={"id"})
        doc["_id"] = record.id
        await self._runs.insert_one(doc)
        return record

    async def get_run(self, run_id: str) -> RunRecord | None:
        doc = await self._runs.find_one({"_id": run_id})
        return _run_from_doc(doc) if doc else None

    async def list_runs(self, limit: int = 50) -> list[RunRecord]:
        cursor = self._runs.find().sort("created_at", DESCENDING).limit(limit)
        return [_run_from_doc(doc) async for doc in cursor]

    @property
    def database(self) -> AsyncDatabase[dict[str, Any]]:
        return self._db

    async def list_runs_in(self, statuses: Iterable[RunStatus]) -> list[RunRecord]:
        cursor = self._runs.find({"status": {"$in": [status.value for status in statuses]}})
        return [_run_from_doc(doc) async for doc in cursor]

    async def set_checkpoint(
        self, run_id: str, checkpoint: Checkpoint, data: dict[str, Any]
    ) -> None:
        """Record a resume point. Unlike the projection this raises: resume depends on it."""
        result = await self._runs.update_one(
            {"_id": run_id},
            {
                "$set": {
                    "checkpoint": checkpoint.value,
                    "checkpoint_data": data,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        if result.matched_count == 0:
            raise RunNotFound(run_id)

    async def next_seq(self, run_id: str) -> int:
        doc = await self._runs.find_one_and_update(
            {"_id": run_id},
            {"$inc": {"last_seq": 1}},
            projection={"last_seq": 1},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            raise RunNotFound(run_id)
        return int(doc["last_seq"])

    async def release_seq(self, run_id: str, seq: int) -> None:
        """Give back a seq whose event was never written (only if it is still the latest)."""
        await self._runs.update_one({"_id": run_id, "last_seq": seq}, {"$inc": {"last_seq": -1}})

    async def insert_event(self, event: RunEvent) -> None:
        await self._events.insert_one(EVENT_ADAPTER.dump_python(event, mode="json"))

    async def has_event(self, run_id: str, seq: int) -> bool:
        return await self._events.find_one({"run_id": run_id, "seq": seq}, {"_id": 1}) is not None

    async def last_event_is_terminal(self, run_id: str) -> bool:
        doc = await self._events.find_one(
            {"run_id": run_id}, {"type": 1}, sort=[("seq", DESCENDING)]
        )
        return doc is not None and doc["type"] in TERMINAL_EVENT_TYPES

    async def list_events_after(self, run_id: str, after_seq: int = 0) -> AsyncIterator[RunEvent]:
        cursor = self._events.find(
            {"run_id": run_id, "seq": {"$gt": after_seq}}, projection={"_id": 0}
        ).sort("seq", ASCENDING)
        async for doc in cursor:
            yield EVENT_ADAPTER.validate_python(doc)

    async def apply_projection(self, event: RunEvent) -> None:
        sets: dict[str, Any] = {"updated_at": event.ts}
        incs: dict[str, Any] = {}
        match event:
            case RunStatusChangedEvent():
                sets["status"] = event.payload.status.value
            case PhaseStartedEvent():
                sets["phase"] = event.phase.value if event.phase else None
            case GateOpenedEvent():
                sets["status"] = RunStatus.WAITING_GATE.value
            case GateResolvedEvent():
                sets["status"] = RunStatus.RUNNING.value
            case BudgetUpdatedEvent():
                sets["spent_usd"] = event.payload.spent_usd
            case AgentUsageEvent():
                usage = event.payload
                incs = {
                    "spent_usd": usage.cost_usd,
                    "tokens.input_tokens": usage.input_tokens,
                    "tokens.output_tokens": usage.output_tokens,
                    "tokens.cache_creation_input_tokens": usage.cache_creation_input_tokens,
                    "tokens.cache_read_input_tokens": usage.cache_read_input_tokens,
                }
            case PrOpenedEvent():
                sets["pr_url"] = event.payload.url
            case _ if event.type in _TERMINAL_STATUS:
                sets["status"] = _TERMINAL_STATUS[event.type].value
        update: dict[str, Any] = {"$set": sets}
        if incs:
            update["$inc"] = incs
        await self._runs.update_one({"_id": event.run_id}, update)


def _run_from_doc(doc: dict[str, Any]) -> RunRecord:
    return RunRecord(id=doc.pop("_id"), **doc)
