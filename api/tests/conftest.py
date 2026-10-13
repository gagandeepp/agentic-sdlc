import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient, MongoClient
from testcontainers.community.mongodb import MongoDbContainer

from app.main import create_app
from app.settings import Settings


@pytest.fixture(scope="session")
def mongo_uri() -> Iterator[str]:
    with MongoDbContainer("mongo:8.0") as mongo:
        url = mongo.get_connection_url()
        # The image logs "waiting for connections" from a temporary init mongod before
        # restarting with auth, which is when testcontainers reports ready; wait for a real ping.
        with MongoClient(url, serverSelectionTimeoutMS=30_000) as probe:
            probe.admin.command("ping")
        yield url


@pytest.fixture
def settings(mongo_uri: str) -> Settings:
    return Settings(
        mongo_uri=mongo_uri,
        mongo_db=f"test_{uuid.uuid4().hex[:8]}",
        enable_dev_routes=True,
        sse_ping_seconds=0.2,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with LifespanManager(application):
        yield application
    cleanup: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(settings.mongo_uri)
    await cleanup.drop_database(settings.mongo_db)
    await cleanup.close()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
