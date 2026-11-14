"""Analytics on top of the local database: reports, records, gaps, overview."""

import statistics
from datetime import datetime, timedelta
from typing import Any

from mi_fitness_mcp.services.query_service import (
    QueryService,
    _date,
    _day,
    _days_between,
    _pct_change,
    _round,
)
from mi_fitness_mcp.storage import validate_date
from mi_fitness_mcp.storage.schema import TABLES

DEFAULT_GAP_TYPES = ("daily_activity", "heart_rate", "sleep", "spo2", "stress")
ALL_TIME = ("1970-01-01", "2100-01-01")


def _minutes_of_day(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    return dt.hour * 60 + dt.minute


def _clock(minutes: float) -> str:
    total = round(minutes) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


class AnalyticsService(QueryService):
    """QueryService plus derived insights. Same constructor, same data scope."""

    def get_sleep_summary(self, start_date: str, end_date: str) -> dict[str, Any]:
        """睡眠均值、各阶段占比与作息规律（入睡时间的离散程度越小越规律）。"""
        sessions = self.get_sleep_sessions(start_date, end_date, include_naps=False)
        if not sessions:
            return {"nights": 0}
        stage_minutes: dict[str, int] = {}
        for session in sessions:
            for stage in session.get("stages", []):
                stage_minutes[stage["stage"]] = stage_minutes.get(stage["stage"], 0) + stage["minutes"]
        staged = sum(stage_minutes.values())
        # 以 18:00 为起点计分钟，使 23:30 与 00:30 的入睡时间在数轴上相邻
        bedtimes = [(_minutes_of_day(s["start_at"]) - 18 * 60) % 1440 for s in sessions]
        wakes = [_minutes_of_day(s["end_at"]) for s in sessions]
        scores = [s["sleep_score"] for s in sessions if s.get("sleep_score")]
        return {
            "nights": len(sessions),
            "avg_asleep_minutes": round(statistics.fmean(s["time_asleep_minutes"] for s in sessions)),
            "avg_duration_minutes": round(statistics.fmean(s["duration_minutes"] for s in sessions)),
            "avg_score": _round(statistics.fmean(scores)) if scores else None,
            "avg_bedtime": _clock(statistics.fmean(bedtimes) + 18 * 60),
            "avg_wake_time": _clock(statistics.fmean(wakes)),
            "bedtime_variability_minutes": round(statistics.pstdev(bedtimes)),
            "stage_share_pct": (
                {n: round(m / staged * 100, 1) for n, m in stage_minutes.items()} if staged else {}
            ),
            "short_nights_under_6h": sum(1 for s in sessions if s["time_asleep_minutes"] < 360),
        }

    def get_period_report(self, start_date: str, end_date: str) -> dict[str, Any]:
        """某段时间的健康指标，并与紧邻的、等长的上一段对比（周报/月报）。"""
        start = _date(validate_date(start_date, "start_date"))
        end = _date(validate_date(end_date, "end_date"))
        if start > end:
            raise ValueError("start_date must not be after end_date")
        length = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=length - 1)
        current = self._period_metrics(start_date, end_date)
        previous = self._period_metrics(_day(prev_start), _day(prev_end))
        changes = {
            key: _pct_change(value, previous.get(key))
            for key, value in current.items()
            if isinstance(value, (int, float)) and not key.endswith("_change")
        }
        return {
            "period": {"start_date": start_date, "end_date": end_date, "days": length},
            "previous_period": {"start_date": _day(prev_start), "end_date": _day(prev_end)},
            "metrics": current,
            "previous_metrics": previous,
            "change_pct": {k: v for k, v in changes.items() if v is not None},
            "sleep": self.get_sleep_summary(start_date, end_date),
        }

    def _period_metrics(self, start_date: str, end_date: str) -> dict[str, Any]:
        uid = self.user_id
        activity = self.get_daily_summaries(start_date, end_date)
        active_days = [d for d in activity if d["steps"] > 0]
        sleeps = self.get_sleep_sessions(start_date, end_date, include_naps=False)
        workouts = self.get_workouts(start_date, end_date)
        hr = self.db.aggregate("heart_rate", uid, start_date, end_date, "bpm")[0]
        resting = self.db.aggregate(
            "heart_rate", uid, start_date, end_date, "bpm", where={"sample_type": "resting"}
        )[0]
        spo2 = self.db.aggregate("spo2", uid, start_date, end_date, "spo2_pct")[0]
        stress = self.db.aggregate("stress", uid, start_date, end_date, "stress_score")[0]
        weights = self.get_body_measurements(start_date, end_date, ["weight_kg"])
        return {
            "total_steps": sum(d["steps"] for d in activity),
            "avg_daily_steps": (
                round(statistics.fmean(d["steps"] for d in active_days)) if active_days else None
            ),
            "total_distance_km": round(sum(d["distance_m"] for d in activity) / 1000, 2),
            "total_active_kcal": round(sum(d["active_kcal"] for d in activity)),
            "days_with_activity": len(active_days),
            "avg_sleep_minutes": (
                round(statistics.fmean(s["time_asleep_minutes"] for s in sleeps)) if sleeps else None
            ),
            "avg_heart_rate": hr["avg"],
            "avg_resting_heart_rate": resting["avg"],
            "avg_spo2": spo2["avg"],
            "min_spo2": spo2["min"],
            "avg_stress": stress["avg"],
            "workout_count": len(workouts),
            "workout_minutes": sum(w["duration_minutes"] or 0 for w in workouts),
            "workout_kcal": round(sum(w["calories_kcal"] or 0 for w in workouts)),
            "weight_kg_end": weights[-1]["weight_kg"] if weights else None,
            "weight_kg_change": (
                round(weights[-1]["weight_kg"] - weights[0]["weight_kg"], 2)
                if len(weights) > 1
                else None
            ),
        }

    def get_overview(self, as_of: str | None = None) -> dict[str, Any]:
        """某天（默认今天）的健康快照：当日数据 + 近 7 天对比 + 最近一次体重。"""
        day = validate_date(as_of or _day(datetime.now()), "as_of")
        week_start = _day(_date(day) - timedelta(days=6))
        today = next(iter(self.get_daily_summaries(day, day)), None)
        week_steps = [d["steps"] for d in self.get_daily_summaries(week_start, day) if d["steps"] > 0]

        night = next(
            (
                s
                for s in reversed(
                    self.get_sleep_sessions(_day(_date(day) - timedelta(days=1)), day, False)
                )
                if s["end_at"][:10] == day
            ),
            None,
        )

        def latest(data_type: str, column: str, where: dict | None = None) -> Any:
            rows = self.db.query(
                data_type, self.user_id, week_start, day, where=where, limit=1, descending=True
            )
            return rows[0][column] if rows else None

        body = self.db.query(
            "body_measurements", self.user_id, ALL_TIME[0], day, limit=1, descending=True
        )
        return {
            "date": day,
            "today": today,
            "sleep_last_night": night,
            "resting_heart_rate": latest("heart_rate", "bpm", {"sample_type": "resting"}),
            "latest_heart_rate": latest("heart_rate", "bpm"),
            "latest_spo2": latest("spo2", "spo2_pct"),
            "latest_stress": latest("stress", "stress_score"),
            "latest_weight": (
                {k: body[0][k] for k in ("timestamp", "weight_kg", "bmi", "body_fat_pct")}
                if body
                else None
            ),
            "last_7_days": {
                "avg_steps": round(statistics.fmean(week_steps)) if week_steps else None,
                "best_steps": max(week_steps, default=None),
                "days_with_data": len(week_steps),
                "workouts": len(self.get_workouts(week_start, day)),
            },
        }

    def get_personal_records(self, step_goal: int = 8000) -> dict[str, Any]:
        """全部历史中的个人纪录，以及连续达成步数目标的天数。"""
        uid = self.user_id

        def pick(row: dict | None, *keys: str) -> dict | None:
            return {k: row[k] for k in keys} if row else None

        resting = {"sample_type": "resting"}
        by_day = {d["date"]: d["steps"] for d in self.get_daily_summaries(*ALL_TIME)}
        longest_streak = run = 0
        previous: str | None = None
        for day in sorted(by_day):
            met = by_day[day] >= step_goal
            consecutive = previous is not None and (_date(day) - _date(previous)).days == 1
            run = run + 1 if met and consecutive else int(met)
            longest_streak = max(longest_streak, run)
            previous = day
        # 目前仍在进行的连续达标：最后一个达标日必须是今天或昨天
        ongoing = bool(previous) and run > 0 and (datetime.now() - _date(previous)).days <= 1
        return {
            "most_steps_day": pick(self.db.top("daily_activity", uid, "steps"), "date", "steps"),
            "longest_workout": pick(
                self.db.top("workouts", uid, "duration_minutes"),
                "start_at",
                "activity_type",
                "duration_minutes",
            ),
            "farthest_workout": pick(
                self.db.top("workouts", uid, "distance_m"), "start_at", "activity_type", "distance_m"
            ),
            "most_calories_workout": pick(
                self.db.top("workouts", uid, "calories_kcal"),
                "start_at",
                "activity_type",
                "calories_kcal",
            ),
            "best_sleep_score": pick(
                self.db.top("sleep", uid, "sleep_score"),
                "start_at",
                "sleep_score",
                "time_asleep_minutes",
            ),
            "lowest_resting_heart_rate": pick(
                self.db.top("heart_rate", uid, "bpm", descending=False, where=resting),
                "timestamp",
                "bpm",
            ),
            "lowest_weight": pick(
                self.db.top("body_measurements", uid, "weight_kg", descending=False),
                "timestamp",
                "weight_kg",
            ),
            "step_goal": step_goal,
            "longest_goal_streak_days": longest_streak,
            "current_goal_streak_days": run if ongoing else 0,
        }

    def find_data_gaps(
        self, start_date: str, end_date: str, data_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """每类数据在区间内没有任何记录的日期，合并成连续区间，帮助判断是没戴还是没同步。"""
        wanted = data_types or list(DEFAULT_GAP_TYPES)
        unknown = sorted(set(wanted) - set(TABLES))
        if unknown:
            raise ValueError(f"未知数据类型: {', '.join(unknown)}")
        all_days = _days_between(
            validate_date(start_date, "start_date"), validate_date(end_date, "end_date")
        )
        report = []
        for data_type in wanted:
            present = set(self.db.distinct_dates(data_type, self.user_id, start_date, end_date))
            gaps: list[dict[str, Any]] = []
            for day in (d for d in all_days if d not in present):
                if gaps and (_date(day) - _date(gaps[-1]["end_date"])).days == 1:
                    gaps[-1]["end_date"] = day
                    gaps[-1]["days"] += 1
                else:
                    gaps.append({"start_date": day, "end_date": day, "days": 1})
            report.append(
                {
                    "data_type": data_type,
                    "days_in_range": len(all_days),
                    "days_with_data": len(present),
                    "missing_days": len(all_days) - len(present),
                    "gaps": gaps,
                }
            )
        return report
