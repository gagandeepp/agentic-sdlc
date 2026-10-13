"""Runtime configuration, read from environment variables (and `.env` when present)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "agentic_sdlc"
    mongo_timeout_ms: int = 3000
    enable_dev_routes: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]
    sse_ping_seconds: float = 15.0
    subscriber_queue_size: int = 1000
