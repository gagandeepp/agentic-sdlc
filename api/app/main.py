"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from app.api.runs import router as runs_router
from app.events.bus import EventBus
from app.settings import Settings
from app.store.mongo import MongoStore

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=settings.mongo_timeout_ms,
            tz_aware=True,
        )
        store = MongoStore(client[settings.mongo_db])
        try:
            await store.ensure_indexes()
        except PyMongoError as exc:
            # Start anyway: /api/health reports the outage, requests get 503, and
            # MongoStore.create_run retries the indexes once Mongo is back.
            log.warning("MongoDB unavailable at startup: %s", exc)
        background: set[asyncio.Task[None]] = set()
        app.state.mongo = client
        app.state.store = store
        app.state.bus = EventBus(store, queue_size=settings.subscriber_queue_size)
        app.state.background_tasks = background
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await client.close()

    app = FastAPI(title="agentic-sdlc", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(PyMongoError)
    async def database_unavailable(_: Request, exc: PyMongoError) -> JSONResponse:
        log.warning("database error: %s", exc)
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        try:
            await app.state.mongo.admin.command("ping")
        except PyMongoError:
            return JSONResponse({"status": "degraded", "mongo": "unreachable"}, status_code=503)
        return JSONResponse({"status": "ok", "mongo": "ok"})

    app.include_router(runs_router)
    return app


app = create_app()
