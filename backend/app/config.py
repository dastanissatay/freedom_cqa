"""Настройки приложения из .env (корень репозитория)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    dataset_api_url: str = ""
    default_group_id: str = "DEV"

    llm_provider: str = "anthropic"  # anthropic | openai_compat | mock
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    llm_max_tokens: int = 16000
    llm_max_retries: int = 2

    anthropic_api_key: str = ""
    openai_base_url: str = "http://localhost:8001/v1"
    openai_api_key: str = "EMPTY"

    price_input_per_m: Optional[float] = None
    price_output_per_m: Optional[float] = None

    db_path: str = "data/cqa.db"
    batch_concurrency: int = 4
    cors_origins: str = "*"

    @field_validator("price_input_per_m", "price_output_per_m", mode="before")
    @classmethod
    def _empty_none(cls, v):
        return None if v in ("", None) else v

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
