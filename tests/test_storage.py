import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from mi_fitness_mcp.models import HeartRateSample
from mi_fitness_mcp.storage import Database, validate_date

TZ = timezone(timedelta(hours=8))
BASE = datetime(2025, 4, 1, tzinfo=TZ)


def hr(uid: str, i: int, bpm: int = 60) -> HeartRateSample:
    return HeartRateSample(
        id=f"hr_{i}",
        provider="mi_fitness",
        source_type="cloud_session",
        user_id=uid,
        timestamp=BASE + timedelta(minutes=i),
        bpm=bpm,
        sample_type="passive",
    )


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


def test_upsert_reports_added_updated_skipped(db):
    assert db.upsert("heart_rate", [hr("u1", i) for i in range(100)]) == {
        "added": 100,
        "updated": 0,
        "skipped": 0,
    }
    # 内容完全相同：全部 skipped，不再误报 updated
    assert db.upsert("heart_rate", [hr("u1", i) for i in range(100)])["skipped"] == 100
    edited = [hr("u1", i, 99) for i in range(10)] + [hr("u1", i) for i in range(10, 100)]
    assert db.upsert("heart_rate", edited) == {"added": 0, "updated": 10, "skipped": 90}
    # 批内重复只算一条
    assert db.upsert("heart_rate", [hr("u1", 5, 99)] * 3) == {"added": 0, "updated": 0, "skipped": 3}


def test_upsert_spans_multiple_chunks(db):
    stats = db.upsert("heart_rate", [hr("u1", i) for i in range(1440)])
    assert stats["added"] == 1440
    assert db.count("heart_rate", "u1", "2025-04-01", "2025-04-01") == 1440


def test_accounts_do_not_overwrite_each_other(db):
    db.upsert("heart_rate", [hr("u1", 0, 60)])
    db.upsert("heart_rate", [hr("u2", 0, 90)])
    assert db.query("heart_rate", "u1", "2025-04-01", "2025-04-01")[0]["bpm"] == 60
    assert db.query("heart_rate", "u2", "2025-04-01", "2025-04-01")[0]["bpm"] == 90


def test_query_range_is_inclusive_limit_and_order(db):
    db.upsert("heart_rate", [hr("u1", i) for i in (0, 1440, 2880)])  # 4/1, 4/2, 4/3
    days = [r["timestamp"][:10] for r in db.query("heart_rate", "u1", "2025-04-01", "2025-04-02")]
    assert days == ["2025-04-01", "2025-04-02"]
    newest = db.query("heart_rate", "u1", "2025-04-01", "2025-04-03", limit=1, descending=True)
    assert newest[0]["timestamp"].startswith("2025-04-03")
    assert db.query("heart_rate", "u1", "2025-04-01", "2025-04-03", limit=0) == []
    with pytest.raises(ValueError):
        db.query("heart_rate", "u1", "2025-04-01", "2025-04-03", where={"nope": 1})


def test_cursor_is_per_user_and_never_moves_backwards(db):
    db.update_sync_state("u1", "spo2", datetime(2025, 4, 10, tzinfo=TZ))
    db.update_sync_state("u1", "spo2", datetime(2025, 3, 1, tzinfo=TZ))
    assert db.get_sync_state("u1", "spo2")["last_record_timestamp"].startswith("2025-04-10")
    assert db.get_sync_state("u2", "spo2") is None


def test_coverage_counts_records_and_days(db):
    db.upsert("heart_rate", [hr("u1", i) for i in (0, 1, 1440)])
    (row,) = db.get_data_coverage("u1")
    assert row["data_type"] == "heart_rate"
    assert (row["first_date"], row["last_date"]) == ("2025-04-01", "2025-04-02")
    assert (row["days_with_data"], row["record_count"]) == (2, 3)


def test_legacy_database_is_migrated_without_data_loss(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE spo2_samples (id TEXT PRIMARY KEY, provider TEXT NOT NULL,
        source_type TEXT NOT NULL, source_record_id TEXT, user_id TEXT NOT NULL, device_id TEXT,
        timezone TEXT DEFAULT 'UTC', collected_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        timestamp TIMESTAMP NOT NULL, spo2_pct INTEGER NOT NULL, UNIQUE(user_id, timestamp))"""
    )
    conn.execute(
        "INSERT INTO spo2_samples (id, provider, source_type, user_id, timestamp, spo2_pct) "
        "VALUES ('x', 'mi_fitness', 'cloud_session', 'u9', '2025-04-01T01:00:00+08:00', 97)"
    )
    conn.execute(
        "CREATE TABLE sync_state (id INTEGER PRIMARY KEY AUTOINCREMENT, data_type TEXT NOT NULL "
        "UNIQUE, last_sync_at TIMESTAMP, last_record_timestamp TIMESTAMP, records_count INTEGER)"
    )
    conn.execute(
        "INSERT INTO sync_state (data_type, last_sync_at, last_record_timestamp) "
        "VALUES ('spo2', '2025-04-02 00:00:00', '2025-04-01T01:00:00+08:00')"
    )
    conn.commit()
    conn.close()

    migrated = Database(path)
    assert migrated.query("spo2", "u9", "2025-04-01", "2025-04-01")[0]["spo2_pct"] == 97
    assert migrated.get_sync_state("u9", "spo2")["last_record_timestamp"].startswith("2025-04-01")
    names = {r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")}
    assert "sync_state" not in names
    Database(path)  # 再次打开是幂等的


def test_validate_date():
    assert validate_date("2025-04-01") == "2025-04-01"
    for bad in ("2025-4-1x", "", None, "2025-13-01"):
        with pytest.raises(ValueError):
            validate_date(bad)
