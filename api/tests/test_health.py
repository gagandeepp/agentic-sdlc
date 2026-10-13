from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.settings import Settings


async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mongo": "ok"}


async def test_health_degraded_when_mongo_unreachable() -> None:
    app = create_app(
        Settings(_env_file=None, mongo_uri="mongodb://127.0.0.1:1", mongo_timeout_ms=200)
    )
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            response = await c.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "mongo": "unreachable"}
