from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-based configuration (a local `.env` file is also read)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Default matches docker-compose.yml (local development only).
    database_url: str = "postgresql+psycopg://trader:trader@localhost:5432/trades"


@lru_cache
def get_settings() -> Settings:
    return Settings()
