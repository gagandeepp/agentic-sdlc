"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from app.settings import Settings

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
        app.state.mongo = client
        try:
            yield
        finally:
            await client.close()

    app = FastAPI(title="agentic-sdlc", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> JSONResponse:
        try:
            await app.state.mongo.admin.command("ping")
        except PyMongoError:
            return JSONResponse({"status": "degraded", "mongo": "unreachable"}, status_code=503)
        return JSONResponse({"status": "ok", "mongo": "ok"})

    return app


app = create_app()
