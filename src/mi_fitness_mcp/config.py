"""Configuration management for Mi Fitness MCP."""

import json
from pathlib import Path
from typing import Literal

from platformdirs import user_config_dir, user_data_dir, user_log_dir
from pydantic import BaseModel, ConfigDict, Field


def _default_database_path() -> Path:
    return Path(user_data_dir("mi-fitness-mcp")) / "mi_fitness.db"


def _default_logs_path() -> Path:
    return Path(user_log_dir("mi-fitness-mcp")) / "mi_fitness.log"


class Config(BaseModel):
    mode: Literal["mi_fitness_cloud", "not_configured"] = "not_configured"
    region: str = "ru"
    timezone: str = Field(default="UTC")
    database_path: Path = Field(default_factory=_default_database_path)
    logs_path: Path = Field(default_factory=_default_logs_path)
    auto_sync_on_start: bool = True
    stale_after_minutes: int = 60
    store_raw_payloads: bool = True
    default_lookback_days: int = Field(default=30, ge=1, le=3650)
    sync_chunk_days: int = Field(default=7, ge=1, le=90)
    http_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    request_retries: int = Field(default=3, ge=1, le=10)
    health_check_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    sync_type_timeout_seconds: float = Field(default=180.0, gt=0, le=3600)
    max_pages: int = Field(default=200, ge=1, le=5000)

    model_config = ConfigDict(arbitrary_types_allowed=True)


def get_config_dir() -> Path:
    config_dir = Path(user_config_dir("mi-fitness-mcp"))
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def load_config() -> Config:
    config_path = get_config_path()
    if not config_path.exists():
        config = Config()
        save_config(config)
        return config

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    if "database_path" in data and isinstance(data["database_path"], str):
        data["database_path"] = Path(data["database_path"])
    if "logs_path" in data and isinstance(data["logs_path"], str):
        data["logs_path"] = Path(data["logs_path"])

    return Config(**data)


def save_config(config: Config) -> None:
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump()
    data["database_path"] = str(data["database_path"])
    data["logs_path"] = str(data["logs_path"])

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
