import json
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://localhost:5432/memorycard"
    redis_url: str = "redis://localhost:6379/0"

    # Cache TTLs (seconds)
    game_state_ttl: int = 3600
    leaderboard_ttl: int = 30

    # Game tuning
    max_rounds: int = 10
    points_per_item: int = 10

    # Voice providers
    deepgram_api_key: str = ""
    deepgram_stt_model: str = "nova-3"
    deepgram_tts_voice: str = "aura-2-thalia-en"

    # LLM (via LiteLLM). Gemini on Vertex by default.
    llm_model: str = "vertex_ai/gemini-2.5-flash"
    llm_temperature: float = 0.2
    llm_timeout: float = 30
    llm_num_retries: int = 2
    llm_streaming: bool = True
    llm_extra_params: Annotated[dict[str, Any], NoDecode] = Field(default_factory=dict)
    # Contents of a Google credentials JSON (e.g. an "authorized_user" file),
    # or a path to one. Passed to LiteLLM as `vertex_credentials`.
    vertex_credentials: str | None = None
    # Used instead of Vertex when LLM_MODEL is a `gemini/...` (AI Studio) model.
    gemini_api_key: str | None = None

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, value: str) -> str:
        # Render hands out `postgres://` / `postgresql://` URLs; SQLAlchemy needs the async driver.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value.removeprefix(prefix)
        return value

    @field_validator("llm_extra_params", mode="before")
    @classmethod
    def _parse_json(cls, value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value) if value.strip() else {}
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
