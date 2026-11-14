"""Synthetic but realistic demo data, written through the real storage layer.

Lets anyone try the dashboard / MCP tools without a Xiaomi account. Deterministic for a given seed.
"""

import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from platformdirs import user_data_dir

from mi_fitness_mcp.models import (
    AbnormalHeartBeatEvent,
    BodyMeasurement,
    DailyActivity,
    HeartRateSample,
    SleepSession,
    SleepStage,
    SpO2Sample,
    StressSample,
    Workout,
)
from mi_fitness_mcp.storage import TABLES, Database

DEMO_USER_ID = "demo"
TZ = timezone(timedelta(hours=8))
TZ_NAME = "Asia/Shanghai"
_COMMON = {"provider": "mi_fitness", "source_type": "cloud_session", "user_id": DEMO_USER_ID}
SPORTS = (
    ("running", 35, 9000, 8.0, 5.8),
    ("walking", 50, 4000, 3.2, 8.0),
    ("cycling", 60, 16000, 9.0, 3.5),
    ("strength_training", 45, None, 5.0, None),
)


def _stress_level(score: int) -> str:
    return "low" if score < 30 else "medium" if score < 60 else "high"


def generate_demo_data(
    db: Database, days: int = 90, seed: int = 42, end: date | None = None
) -> dict[str, dict[str, int]]:
    """写入 `days` 天的演示数据，返回每类数据的写入统计。"""
    rng = random.Random(seed)
    end = end or date.today()
    batches: dict[str, list] = {t: [] for t in (
        "daily_activity", "heart_rate", "body_measurements", "sleep",
        "workouts", "spo2", "stress", "abnormal_heart_beat",
    )}
    weight = 74.0 + rng.uniform(-2, 2)

    for offset in range(days - 1, -1, -1):
        day = end - timedelta(days=offset)
        key = day.isoformat()
        weekend = day.weekday() >= 5
        midnight = datetime(day.year, day.month, day.day, tzinfo=TZ)
        # 周末更爱睡懒觉，周中步数多；偶尔整天没戴手环以体现数据缺口
        worn = rng.random() > 0.04

        steps = int(rng.gauss(6200 if weekend else 8300, 2100)) if worn else 0
        steps = max(0, steps)
        if worn:
            batches["daily_activity"].append(
                DailyActivity(
                    id=f"demo_activity_{key}", timezone=TZ_NAME, collected_at=midnight + timedelta(hours=23),
                    date=key, steps=steps, distance_m=round(steps * 0.68, 1),
                    active_kcal=max(0.0, round(steps * 0.042 + rng.uniform(-20, 20), 1)), **_COMMON,
                )
            )

        bed = midnight + timedelta(hours=23 + (1 if weekend else 0), minutes=rng.randint(-40, 50))
        asleep = int(rng.gauss(465 if weekend else 420, 40))
        awake = rng.randint(12, 38)
        wake = bed + timedelta(minutes=asleep + awake)
        deep, rem = int(asleep * rng.uniform(0.15, 0.22)), int(asleep * rng.uniform(0.18, 0.25))
        if worn:
            batches["sleep"].append(
                SleepSession(
                    id=f"demo_sleep_{key}", timezone=TZ_NAME, collected_at=wake, sleep_id=f"demo_{key}",
                    start_at=bed, end_at=wake, duration_minutes=asleep + awake,
                    time_asleep_minutes=asleep, time_awake_minutes=awake,
                    sleep_score=max(40, min(98, int(60 + asleep / 12 + rng.gauss(0, 5)))),
                    stages=[SleepStage(stage="deep", minutes=deep), SleepStage(stage="rem", minutes=rem),
                            SleepStage(stage="light", minutes=asleep - deep - rem),
                            SleepStage(stage="awake", minutes=awake)], **_COMMON,
                )
            )
            batches["heart_rate"].append(
                HeartRateSample(
                    id=f"demo_rhr_{key}", timezone=TZ_NAME, collected_at=midnight, timestamp=midnight + timedelta(hours=6),
                    bpm=int(rng.gauss(58, 2)), sample_type="resting", **_COMMON,
                )
            )

        if worn:
            # 每 10 分钟一个被动心率样本：夜里低、白天随活动起伏
            for minute in range(0, 24 * 60, 10):
                hour = minute / 60
                base = 54 if hour < 6.5 else 70 + 8 * (1 if 9 <= hour <= 20 else 0)
                batches["heart_rate"].append(
                    HeartRateSample(
                        id=f"demo_hr_{key}_{minute}", timezone=TZ_NAME, collected_at=midnight,
                        timestamp=midnight + timedelta(minutes=minute),
                        bpm=max(45, int(rng.gauss(base, 6))), sample_type="passive", **_COMMON,
                    )
                )
            for hour in range(0, 24, 2):
                ts = midnight + timedelta(hours=hour, minutes=rng.randint(0, 59))
                batches["spo2"].append(
                    SpO2Sample(id=f"demo_spo2_{key}_{hour}", timezone=TZ_NAME, collected_at=ts, timestamp=ts,
                               spo2_pct=min(100, max(90, int(rng.gauss(97, 1.2)))), **_COMMON)
                )
            for hour in range(8, 22, 3):
                ts = midnight + timedelta(hours=hour, minutes=rng.randint(0, 59))
                score = max(5, min(95, int(rng.gauss(52 if not weekend else 35, 14))))
                batches["stress"].append(
                    StressSample(id=f"demo_stress_{key}_{hour}", timezone=TZ_NAME, collected_at=ts, timestamp=ts,
                                 stress_score=score, level=_stress_level(score), **_COMMON)
                )

        if worn and rng.random() < 0.5:
            sport, minutes, dist, bpm, pace = rng.choice(SPORTS)
            start = midnight + timedelta(hours=rng.choice((7, 18, 19)), minutes=rng.randint(0, 40))
            duration = int(minutes * rng.uniform(0.7, 1.3))
            distance = round(dist * duration / minutes, 1) if dist else None
            batches["workouts"].append(
                Workout(
                    id=f"demo_workout_{key}", timezone=TZ_NAME, collected_at=start, workout_id=f"demo_w_{key}",
                    activity_type=sport, start_at=start, end_at=start + timedelta(minutes=duration),
                    duration_minutes=duration, distance_m=distance,
                    calories_kcal=round(duration * bpm * rng.uniform(0.9, 1.1), 1),
                    avg_heart_rate_bpm=int(rng.gauss(138, 8)), max_heart_rate_bpm=int(rng.gauss(168, 6)),
                    avg_pace_sec_per_km=round(pace * 60 * rng.uniform(0.95, 1.05), 1) if pace else None,
                    total_steps=int(distance / 0.75) if distance and sport != "cycling" else None, **_COMMON,
                )
            )

        if day.weekday() in (0, 3):  # 每周两次称重，体重缓慢下降 + 噪声
            weight += rng.uniform(-0.25, 0.1)
            ts = midnight + timedelta(hours=7, minutes=rng.randint(0, 30))
            batches["body_measurements"].append(
                BodyMeasurement(
                    id=f"demo_weight_{key}", timezone=TZ_NAME, collected_at=ts, timestamp=ts,
                    weight_kg=round(weight, 2), bmi=round(weight / 1.76**2, 1),
                    body_fat_pct=round(21.5 + (weight - 74) * 0.6 + rng.uniform(-0.3, 0.3), 1),
                    muscle_mass_kg=round(55 + rng.uniform(-0.4, 0.4), 1), water_pct=round(56 + rng.uniform(-0.5, 0.5), 1),
                    **_COMMON,
                )
            )

        if worn and rng.random() < 0.03:
            ts = midnight + timedelta(hours=rng.randint(10, 20), minutes=rng.randint(0, 59))
            batches["abnormal_heart_beat"].append(
                AbnormalHeartBeatEvent(id=f"demo_abn_{key}", timezone=TZ_NAME, collected_at=ts, event_id=f"demo_a_{key}",
                                       start_at=ts, end_at=ts + timedelta(seconds=45), duration_seconds=45, **_COMMON)
            )

    stats = {}
    for data_type, rows in batches.items():
        stats[data_type] = db.upsert(data_type, rows)
        if rows:
            attr = TABLES[data_type].cursor_attr
            db.update_sync_state(DEMO_USER_ID, data_type, max(getattr(r, attr) for r in rows))
    return stats


def demo_database(path: Path | str | None = None, days: int = 90) -> Database:
    """打开（并刷新到今天）演示数据库；写入幂等，重复调用只补新的日期。"""
    db = Database(path or Path(user_data_dir("mi-fitness-mcp")) / "demo.db")
    generate_demo_data(db, days=days)
    return db
