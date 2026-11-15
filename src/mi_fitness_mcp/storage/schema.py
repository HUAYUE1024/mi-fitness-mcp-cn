"""Declarative table specs: one spec drives DDL, upsert, filtering and coverage queries."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

COMMON_COLUMNS = (
    "id",
    "provider",
    "source_type",
    "source_record_id",
    "user_id",
    "device_id",
    "timezone",
    "collected_at",
)
_META_DDL = (
    "id TEXT NOT NULL, provider TEXT NOT NULL, source_type TEXT NOT NULL, "
    "source_record_id TEXT, user_id TEXT NOT NULL, device_id TEXT, "
    "timezone TEXT DEFAULT 'UTC', collected_at TIMESTAMP, "
    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
    "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
)


@dataclass(frozen=True)
class Table:
    data_type: str
    name: str
    fields: tuple[tuple[str, str], ...]
    time_col: str  # column the date-range filter and coverage use
    cursor_attr: str  # model attribute holding the record's own time (sync cursor)

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.fields)

    @property
    def insert_columns(self) -> tuple[str, ...]:
        return COMMON_COLUMNS + self.field_names

    def ddl(self, name: str | None = None) -> str:
        cols = ", ".join(f"{n} {t}" for n, t in self.fields)
        return (
            f"CREATE TABLE IF NOT EXISTS {name or self.name} "
            f"({_META_DDL}, {cols}, PRIMARY KEY (user_id, id))"
        )

    def index_ddl(self) -> str:
        return (
            f"CREATE INDEX IF NOT EXISTS idx_{self.name}_user_{self.time_col} "
            f"ON {self.name}(user_id, {self.time_col})"
        )

    def encode(self, record: BaseModel) -> tuple[Any, ...]:
        return tuple(_encode(getattr(record, col)) for col in self.insert_columns)


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return json.dumps([v.model_dump() if isinstance(v, BaseModel) else v for v in value])
    return value


def _table(data_type: str, name: str, fields: str, time_col: str, cursor_attr: str) -> Table:
    pairs = tuple(tuple(f.split(" ", 1)) for f in fields.split(", "))
    return Table(data_type, name, pairs, time_col, cursor_attr)  # type: ignore[arg-type]


_TABLES = (
    _table(
        "daily_activity",
        "daily_activity",
        "date TEXT NOT NULL, steps INTEGER NOT NULL DEFAULT 0, "
        "distance_m REAL NOT NULL DEFAULT 0, active_kcal REAL NOT NULL DEFAULT 0, "
        "total_kcal REAL, floors INTEGER, active_minutes INTEGER",
        "date",
        "collected_at",
    ),
    _table(
        "sleep",
        "sleep_sessions",
        "sleep_id TEXT NOT NULL, start_at TIMESTAMP NOT NULL, end_at TIMESTAMP NOT NULL, "
        "duration_minutes INTEGER NOT NULL, time_asleep_minutes INTEGER NOT NULL, "
        "time_awake_minutes INTEGER NOT NULL, sleep_score INTEGER, "
        "is_nap BOOLEAN DEFAULT FALSE, stages TEXT",
        "end_at",  # 睡眠归属起床当天：0:08 入睡和 23:21 入睡的两晚不会挤进同一天、也不会漏掉另一天
        "start_at",
    ),
    _table(
        "workouts",
        "workouts",
        "workout_id TEXT NOT NULL, activity_type TEXT NOT NULL, "
        "start_at TIMESTAMP NOT NULL, end_at TIMESTAMP NOT NULL, "
        "duration_minutes INTEGER NOT NULL, distance_m REAL, calories_kcal REAL, "
        "avg_heart_rate_bpm INTEGER, max_heart_rate_bpm INTEGER, "
        "avg_pace_sec_per_km REAL, max_pace_sec_per_km REAL, total_steps INTEGER",
        "start_at",
        "start_at",
    ),
    _table(
        "body_measurements",
        "body_measurements",
        "timestamp TIMESTAMP NOT NULL, weight_kg REAL NOT NULL, bmi REAL, body_fat_pct REAL, "
        "muscle_mass_kg REAL, water_pct REAL, bone_mass_kg REAL, visceral_fat_score INTEGER, "
        "basal_metabolism_kcal INTEGER, metabolic_age INTEGER",
        "timestamp",
        "timestamp",
    ),
    _table(
        "heart_rate",
        "heart_rate_samples",
        "timestamp TIMESTAMP NOT NULL, bpm INTEGER NOT NULL, sample_type TEXT NOT NULL",
        "timestamp",
        "timestamp",
    ),
    _table(
        "spo2",
        "spo2_samples",
        "timestamp TIMESTAMP NOT NULL, spo2_pct INTEGER NOT NULL",
        "timestamp",
        "timestamp",
    ),
    _table(
        "stress",
        "stress_samples",
        "timestamp TIMESTAMP NOT NULL, stress_score INTEGER NOT NULL, level TEXT NOT NULL",
        "timestamp",
        "timestamp",
    ),
    _table(
        "abnormal_heart_beat",
        "abnormal_heart_beat_events",
        "event_id TEXT NOT NULL, start_at TIMESTAMP NOT NULL, end_at TIMESTAMP NOT NULL, "
        "duration_seconds INTEGER NOT NULL",
        "start_at",
        "start_at",
    ),
)
TABLES: dict[str, Table] = {t.data_type: t for t in _TABLES}
