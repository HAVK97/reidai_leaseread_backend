"""Server configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_REPO_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REIDAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage
    data_dir: Path = _REPO_ROOT / "data"

    # CORS — comma-separated origins, or "*" for dev
    cors_origins: str = "*"

    # LlamaParse ingest
    llamaparse_tier: str = "agentic"
    llamaparse_version: str = "latest"
    ingest_dpi: int = 160
    ingest_skip_parse: bool = False
    ingest_download_assets: bool = False
    ingest_draw_overlays: bool = False

    # Extraction
    extraction_provider: str | None = None  # override all groups, e.g. "fake"
    extraction_model_profile: str | None = None
    extraction_dry_run: bool = False
    fail_on_validation: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
