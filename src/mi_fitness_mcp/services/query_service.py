"""Query service: reads the local database and builds the analyses exposed by MCP, REST and CLI."""

import json
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from mi_fitness_mcp.storage import Database, validate_date
from mi_fitness_mcp.storage.schema import TABLES

SERIES_METRICS: dict[str, tuple[str, str, str]] = {
    # metric -> (data_type, column, how the same-day rows combine)
    "steps": ("daily_activity", "steps", "sum"),
    "distance_m": ("daily_activity", "distance_m", "sum"),
    "active_kcal": ("daily_activity", "active_kcal", "sum"),
    "sleep_minutes": ("sleep", "time_asleep_minutes", "sum"),
    "workout_minutes": ("workouts", "duration_minutes", "sum"),
    "workout_kcal": ("workouts", "calories_kcal", "sum"),
    "resting_heart_rate": ("heart_rate", "bpm", "min"),
    "avg_heart_rate": ("heart_rate", "bpm", "avg"),
    "max_heart_rate": ("heart_rate", "bpm", "max"),
    "spo2": ("spo2", "spo2_pct", "avg"),
    "stress": ("stress", "stress_score", "avg"),
    "weight_kg": ("body_measurements", "weight_kg", "latest"),
    "bmi": ("body_measurements", "bmi", "latest"),
    "body_fat_pct": ("body_measurements", "body_fat_pct", "latest"),
}
GRANULARITIES = ("day", "week", "month")
AGGREGATIONS = ("sum", "avg", "min", "max", "latest")
HEART_RATE_ZONES = (
    ("rest", 0, 60),
    ("light", 60, 100),
    ("moderate", 100, 140),
    ("vigorous", 140, 1000),
)
OPTIONAL_BODY_FIELDS = (
    "bmi",
    "body_fat_pct",
    "muscle_mass_kg",
    "water_pct",
    "bone_mass_kg",
    "visceral_fat_score",
    "basal_metabolism_kcal",
    "metabolic_age",
)


def _date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def _day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _days_between(start: str, end: str) -> list[str]:
    cursor, stop = _date(start), _date(end)
    return [_day(cursor + timedelta(days=i)) for i in range((stop - cursor).days + 1)]


def _combine(values: list[float], how: str) -> float:
    if how == "sum":
        return sum(values)
    if how == "avg":
        return round(sum(values) / len(values), 2)
    if how == "min":
        return min(values)
    if how == "max":
        return max(values)
    if how == "latest":
        return values[-1]
    raise ValueError(f"aggregation 必须是 {AGGREGATIONS} 之一")


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or not previous:
        return None
    return round((current - previous) / previous * 100, 1)


class QueryService:
    """Reads fitness data for one account from the local database."""

    def __init__(self, db: Database, user_id: str):
        self.db = db
        self.user_id = user_id

    # ----------------------------------------------------------- plain readers

    def get_daily_summaries(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """每日活动。多台设备同日记录取各列最大值，避免手环+手表的步数重复累加。"""
        days: dict[str, dict[str, Any]] = {}
        for record in self.db.query("daily_activity", self.user_id, start_date, end_date):
            day = days.setdefault(
                record["date"],
                {"date": record["date"], "steps": 0, "distance_m": 0.0, "active_kcal": 0.0},
            )
            for key in ("steps", "distance_m", "active_kcal"):
                day[key] = max(day[key], record[key] or 0)
            for key in ("total_kcal", "floors", "active_minutes"):
                if record.get(key) is not None:
                    day[key] = max(day.get(key) or 0, record[key])
        return list(days.values())

    def get_sleep_sessions(
        self, start_date: str, end_date: str, include_naps: bool = True
    ) -> list[dict[str, Any]]:
        sessions = []
        for record in self.db.query("sleep", self.user_id, start_date, end_date):
            if not include_naps and record.get("is_nap"):
                continue
            session = {
                key: record.get(key)
                for key in (
                    "sleep_id",
                    "start_at",
                    "end_at",
                    "duration_minutes",
                    "time_asleep_minutes",
                    "time_awake_minutes",
                    "sleep_score",
                )
            }
            session["is_nap"] = bool(record.get("is_nap"))
            if record.get("stages"):
                with suppress(json.JSONDecodeError):
                    session["stages"] = json.loads(record["stages"])
            sessions.append(session)
        return sessions

    def get_workouts(
        self,
        start_date: str,
        end_date: str,
        activity_types: list[str] | None = None,
        min_duration: int | None = None,
        min_distance_km: float | None = None,
    ) -> list[dict[str, Any]]:
        wanted = {t.lower() for t in activity_types or []}
        keep = (
            "workout_id",
            "activity_type",
            "start_at",
            "end_at",
            "duration_minutes",
            "distance_m",
            "calories_kcal",
            "avg_heart_rate_bpm",
            "max_heart_rate_bpm",
            "avg_pace_sec_per_km",
            "max_pace_sec_per_km",
            "total_steps",
        )
        workouts = []
        for record in self.db.query("workouts", self.user_id, start_date, end_date):
            if wanted and record["activity_type"].lower() not in wanted:
                continue
            if min_duration and (record.get("duration_minutes") or 0) < min_duration:
                continue
            if min_distance_km and (record.get("distance_m") or 0) / 1000 < min_distance_km:
                continue
            workouts.append({key: record.get(key) for key in keep})
        return workouts

    def get_body_measurements(
        self,
        start_date: str,
        end_date: str,
        metrics: list[str] | None = None,
        latest_only: bool = False,
    ) -> list[dict[str, Any]]:
        measurements = []
        for record in self.db.query("body_measurements", self.user_id, start_date, end_date):
            item = {"timestamp": record["timestamp"], "weight_kg": record["weight_kg"]}
            item.update({k: record[k] for k in OPTIONAL_BODY_FIELDS if record.get(k) is not None})
            if metrics:
                item = {k: v for k, v in item.items() if k == "timestamp" or k in metrics}
            measurements.append(item)
        return measurements[-1:] if latest_only else measurements

    def get_heart_rate_samples(
        self,
        start_date: str,
        end_date: str,
        sample_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where = {"sample_type": sample_type} if sample_type else None
        rows = self.db.query(
            "heart_rate", self.user_id, start_date, end_date, where=where, limit=limit
        )
        return [
            {"timestamp": r["timestamp"], "bpm": r["bpm"], "sample_type": r["sample_type"]}
            for r in rows
        ]

    def get_spo2_samples(
        self, start_date: str, end_date: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        rows = self.db.query("spo2", self.user_id, start_date, end_date, limit=limit)
        return [{"timestamp": r["timestamp"], "spo2_pct": r["spo2_pct"]} for r in rows]

    def get_stress_samples(
        self,
        start_date: str,
        end_date: str,
        level: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where = {"level": level} if level else None
        rows = self.db.query("stress", self.user_id, start_date, end_date, where=where, limit=limit)
        return [
            {"timestamp": r["timestamp"], "stress_score": r["stress_score"], "level": r["level"]}
            for r in rows
        ]

    def get_abnormal_heart_beat_events(
        self, start_date: str, end_date: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        rows = self.db.query("abnormal_heart_beat", self.user_id, start_date, end_date, limit=limit)
        return [
            {key: r[key] for key in ("event_id", "start_at", "end_at", "duration_seconds")}
            for r in rows
        ]

    def get_data_coverage(self, data_types: list[str] | None = None) -> list[dict[str, Any]]:
        coverage = self.db.get_data_coverage(self.user_id)
        if data_types:
            coverage = [c for c in coverage if c["data_type"] in data_types]
        return coverage

    # ------------------------------------------------------------ time series

    def get_metric_series(
        self,
        metric: str,
        start_date: str,
        end_date: str,
        granularity: str = "day",
        aggregation: str | None = None,
    ) -> list[dict[str, Any]]:
        """任意指标的时间序列。aggregation 缺省时用指标自然的方式（步数求和、心率取平均…）。"""
        if metric not in SERIES_METRICS:
            raise ValueError(f"metric 必须是 {sorted(SERIES_METRICS)} 之一")
        if granularity not in GRANULARITIES:
            raise ValueError(f"granularity 必须是 {GRANULARITIES} 之一")
        if aggregation is not None and aggregation not in AGGREGATIONS:
            raise ValueError(f"aggregation 必须是 {AGGREGATIONS} 之一")
        data_type, column, natural = SERIES_METRICS[metric]
        daily = self._daily_values(data_type, column, natural, start_date, end_date)
        if granularity == "day":
            return [{"date": d, "value": _round(v, 2)} for d, v in daily]

        buckets: dict[str, list[float]] = {}
        for day, value in daily:
            dt = _date(day)
            key = _day(dt - timedelta(days=dt.weekday())) if granularity == "week" else day[:7] + "-01"
            buckets.setdefault(key, []).append(value)
        how = aggregation or ("sum" if natural == "sum" else natural)
        return [{"date": k, "value": _round(_combine(v, how), 2)} for k, v in sorted(buckets.items())]

    def _daily_values(
        self, data_type: str, column: str, how: str, start_date: str, end_date: str
    ) -> list[tuple[str, float]]:
        if data_type == "daily_activity":
            return [
                (s["date"], s[column]) for s in self.get_daily_summaries(start_date, end_date)
            ]
        where = {"sample_type": "resting"} if column == "bpm" and how == "min" else None
        if how == "latest":
            rows = self.db.query(data_type, self.user_id, start_date, end_date)
            last: dict[str, float] = {}
            for row in rows:
                if row.get(column) is not None:
                    last[row[TABLES[data_type].time_col][:10]] = row[column]
            return sorted(last.items())
        rows = self.db.aggregate(
            data_type, self.user_id, start_date, end_date, column, where=where, group="day"
        )
        if column == "bpm" and how == "min" and not rows:  # 没有静息心率样本时退化为当日最低值
            rows = self.db.aggregate(
                data_type, self.user_id, start_date, end_date, column, group="day"
            )
        key = {"sum": "sum", "avg": "avg", "min": "min", "max": "max"}[how]
        return [(r["bucket"], r[key]) for r in rows if r["count"]]

    # ------------------------------------------------------------- analytics

    def get_heart_rate_summary(self, start_date: str, end_date: str) -> dict[str, Any]:
        """逐日静息/平均/最高心率，以及整段时间在各心率区间的分钟占比。"""
        validate_date(start_date, "start_date")
        validate_date(end_date, "end_date")
        daily = self.db.aggregate(
            "heart_rate", self.user_id, start_date, end_date, "bpm", group="day"
        )
        resting = {
            r["bucket"]: r["min"]
            for r in self.db.aggregate(
                "heart_rate",
                self.user_id,
                start_date,
                end_date,
                "bpm",
                where={"sample_type": "resting"},
                group="day",
            )
        }
        rows = [
            {
                "date": r["bucket"],
                "resting_bpm": resting.get(r["bucket"]),
                "min_bpm": r["min"],
                "avg_bpm": r["avg"],
                "max_bpm": r["max"],
                "samples": r["count"],
            }
            for r in daily
        ]
        zone_samples = self.db.bucket_counts(
            "heart_rate",
            self.user_id,
            start_date,
            end_date,
            "bpm",
            HEART_RATE_ZONES,
            where={"sample_type": "passive"},
        )
        overall = self.db.aggregate("heart_rate", self.user_id, start_date, end_date, "bpm")[0]
        return {"days": rows, "overall": overall, "zone_samples": zone_samples}
