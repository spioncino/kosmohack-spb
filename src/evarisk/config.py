from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="EVARISK_", extra="ignore")

    raw_root: Path = Path("data/raw")
    assessments_root: Path = Path("data/assessments")
    http_timeout_s: float = 60.0
    user_agent: str = "evarisk/0.1 (kosmohackathon-2026)"
    spacetrack_user: str | None = Field(default=None, validation_alias="SPACETRACK_USER")
    spacetrack_pass: str | None = Field(default=None, validation_alias="SPACETRACK_PASS")
    nasa_api_key: str | None = Field(default=None, validation_alias="NASA_API_KEY")
    database_url: str = "sqlite:///data/evarisk.db"
    worker_tick_s: float = 60.0
    frontend_dist: Path = Path("frontend/dist")
    tts_url: str | None = None
    mirror_root: Path = Path("data/mirror")
    archive_backfill: bool = True
    tunnel_readonly: bool = True
    # Корень проекта с README.md и docs/: раздел «Документация» страницы читает файлы отсюда.
    manual_root: Path = Path(".")
