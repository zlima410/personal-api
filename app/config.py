from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    github_username: str
    github_token: str | None = None
    hardcover_token: str | None = None
    hardcover_user_id: int | None = None
    ingest_api_key: str
    admin_api_key: str
    read_api_key: str
    timezone: str = "America/Chicago"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
