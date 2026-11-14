"""End-to-end checks for the sync → storage → analytics path, run on deterministic demo data."""

from datetime import date, timedelta

import pytest

from mi_fitness_mcp.demo import DEMO_USER_ID, generate_demo_data
from mi_fitness_mcp.models import DATA_TYPES
from mi_fitness_mcp.services.analytics import AnalyticsService
from mi_fitness_mcp.services.query_service import SERIES_METRICS
from mi_fitness_mcp.storage import Database

END = date(2025, 6, 30)
START = (END - timedelta(days=89)).isoformat()
LAST = END.isoformat()


@pytest.fixture(scope="module")
def svc(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("demo") / "demo.db")
    generate_demo_data(db, days=90, seed=7, end=END)
    return AnalyticsService(db, DEMO_USER_ID)


def test_demo_is_idempotent_and_deterministic(tmp_path):
    db = Database(tmp_path / "d.db")
    first = generate_demo_data(db, days=14, seed=1, end=END)
    again = generate_demo_data(db, days=14, seed=1, end=END)
    assert all(first[t]["added"] > 0 for t in ("daily_activity", "heart_rate", "sleep"))
    assert all(s["added"] == 0 and s["updated"] == 0 for s in again.values())


def test_every_data_type_has_demo_coverage(svc):
    covered = {c["data_type"] for c in svc.get_data_coverage()}
    assert covered >= set(DATA_TYPES) - {"abnormal_heart_beat"}  # 异常心跳是低概率事件


def test_every_series_metric_returns_points(svc):
    for metric in SERIES_METRICS:
        series = svc.get_metric_series(metric, START, LAST)
        assert series, f"{metric} 没有数据点"
        assert all(isinstance(p["value"], (int, float)) for p in series)


def test_weekly_and_monthly_series_bucket_correctly(svc):
    weekly = svc.get_metric_series("steps", START, LAST, "week")
    assert all(date.fromisoformat(p["date"]).weekday() == 0 for p in weekly)  # 周一起算
    monthly = svc.get_metric_series("steps", START, LAST, "month")
    assert [p["date"][8:] for p in monthly] == ["01"] * len(monthly)
    assert sum(p["value"] for p in monthly) == sum(
        p["value"] for p in svc.get_metric_series("steps", START, LAST)
    )


def test_latest_aggregation_is_no_longer_silently_sum(svc):
    latest = svc.get_metric_series("weight_kg", START, LAST, "month", "latest")
    summed = svc.get_metric_series("weight_kg", START, LAST, "month", "sum")
    assert latest[0]["value"] < 100 < summed[0]["value"]  # 求和会得到上百公斤


def test_invalid_series_arguments_raise(svc):
    with pytest.raises(ValueError):
        svc.get_metric_series("nope", START, LAST)
    with pytest.raises(ValueError):
        svc.get_metric_series("steps", START, LAST, "year")
    with pytest.raises(ValueError):
        svc.get_metric_series("steps", START, LAST, "day", "median")


def test_period_report_compares_with_previous_equal_window(svc):
    week_start = (END - timedelta(days=6)).isoformat()
    report = svc.get_period_report(week_start, LAST)
    assert report["period"]["days"] == 7
    assert report["previous_period"]["end_date"] == (END - timedelta(days=7)).isoformat()
    assert report["metrics"]["total_steps"] > 0
    assert "total_steps" in report["change_pct"]
    with pytest.raises(ValueError):
        svc.get_period_report(LAST, week_start)


def test_sleep_summary_handles_midnight_bedtimes(svc):
    summary = svc.get_sleep_summary(START, LAST)
    assert summary["nights"] > 50
    assert 5 * 60 < summary["avg_asleep_minutes"] < 9 * 60
    # 演示入睡时间在 22:20~00:50 之间；若直接对钟点取平均，跨零点会算出 ~12:00
    hour = int(summary["avg_bedtime"][:2])
    assert hour >= 22 or hour <= 1
    assert abs(sum(summary["stage_share_pct"].values()) - 100) < 1


def test_heart_rate_summary_zones_cover_all_passive_samples(svc):
    result = svc.get_heart_rate_summary(START, LAST)
    passive = svc.db.count("heart_rate", DEMO_USER_ID, START, LAST)
    assert sum(result["zone_samples"].values()) <= passive
    assert result["days"] and result["days"][0]["resting_bpm"]


def test_overview_and_records(svc):
    overview = svc.get_overview(LAST)
    assert overview["date"] == LAST and overview["last_7_days"]["days_with_data"] > 0
    records = svc.get_personal_records(step_goal=1)
    assert records["most_steps_day"]["steps"] >= records["step_goal"]
    assert records["longest_goal_streak_days"] >= 1


def test_find_data_gaps_merges_consecutive_days(tmp_path):
    db = Database(tmp_path / "g.db")
    generate_demo_data(db, days=10, seed=3, end=END)
    svc = AnalyticsService(db, DEMO_USER_ID)
    (report,) = svc.find_data_gaps("2025-06-01", "2025-06-30", ["sleep"])
    # 只生成了最近 10 天，6/1~6/20 整段应合并成一个缺口区间
    assert report["gaps"][0] == {"start_date": "2025-06-01", "end_date": "2025-06-20", "days": 20}
    with pytest.raises(ValueError):
        svc.find_data_gaps("2025-06-01", "2025-06-30", ["bogus"])


def test_multi_device_steps_are_not_double_counted(tmp_path):
    from mi_fitness_mcp.models import DailyActivity

    db = Database(tmp_path / "m.db")
    base = {"provider": "mi_fitness", "source_type": "cloud_session", "user_id": "u", "date": "2025-04-01"}
    db.upsert(
        "daily_activity",
        [
            DailyActivity(id="a", device_id="band", steps=1000, distance_m=700, active_kcal=40, **base),
            DailyActivity(id="b", device_id="watch", steps=1200, distance_m=800, active_kcal=45, **base),
        ],
    )
    (day,) = AnalyticsService(db, "u").get_daily_summaries("2025-04-01", "2025-04-01")
    assert day["steps"] == 1200


@pytest.mark.parametrize("seed", range(40))
def test_demo_generation_never_violates_model_constraints(tmp_path, seed):
    """回归：随机值（如活动卡路里）曾在步数很少的日子里算出负数，导致 API 启动即崩溃。"""
    generate_demo_data(Database(tmp_path / f"{seed}.db"), days=120, seed=seed, end=END)
