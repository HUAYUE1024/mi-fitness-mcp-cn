"""Sync-service behaviour that used to be wrong: cursor semantics and same-day incremental syncs."""

from datetime import datetime, timedelta, timezone

import pytest

from mi_fitness_mcp.models import HeartRateSample
from mi_fitness_mcp.services.sync_service import SyncService
from mi_fitness_mcp.storage import Database

TZ = timezone(timedelta(hours=8))


class CloudStub:
    def __init__(self, samples):
        self.samples = samples
        self.calls = []

    def is_connected(self):
        return True

    def get_user_id(self):
        return "u1"

    def get_available_data_types(self):
        return ["heart_rate", "spo2"]

    async def iter_heart_rate(self, start_date, end_date):
        self.calls.append((start_date, end_date))
        for sample in self.samples:
            if start_date <= sample.timestamp.strftime("%Y-%m-%d") <= end_date:
                yield sample


def sample(ts: datetime, bpm: int = 70) -> HeartRateSample:
    return HeartRateSample(
        id=f"hr_{int(ts.timestamp())}", provider="mi_fitness", source_type="cloud_session",
        user_id="u1", timestamp=ts, bpm=bpm, sample_type="passive",
    )


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "s.db")


@pytest.mark.asyncio
async def test_incremental_sync_after_same_day_record_does_not_crash(db):
    """回归：游标落在今天时，旧实现 start=游标时刻 > end=今天 00:00，抛 start_date > end_date。"""
    now = datetime.now(TZ).replace(hour=13, minute=45, second=0, microsecond=0)
    stub = CloudStub([sample(now)])
    service = SyncService(stub, db)

    first = await service.sync_data_type("heart_rate")
    assert first["status"] == "ok" and first["added"] == 1

    later = sample(now + timedelta(minutes=30))
    stub.samples.append(later)
    second = await service.sync_data_type("heart_rate")
    assert second["status"] == "ok"
    assert second["added"] == 1 and second["skipped"] == 1  # 当天被重拉，旧样本只跳过不重复


@pytest.mark.asyncio
async def test_incremental_sync_starts_from_cursor_day(db):
    now = datetime.now(TZ)
    day = now - timedelta(days=3)
    stub = CloudStub([sample(day)])
    service = SyncService(stub, db, default_lookback_days=30, chunk_days=30)
    await service.sync_data_type("heart_rate", start_date=(now - timedelta(days=10)).strftime("%Y-%m-%d"))
    stub.calls.clear()
    await service.sync_data_type("heart_rate")
    assert stub.calls[0][0] == day.strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_failed_chunk_does_not_advance_cursor_past_unsaved_data(db):
    class Flaky(CloudStub):
        async def iter_heart_rate(self, start_date, end_date):
            if start_date >= "2025-04-08":
                raise RuntimeError("cloud down")
            async for s in super().iter_heart_rate(start_date, end_date):
                yield s

    ok = sample(datetime(2025, 4, 2, 9, tzinfo=TZ))
    service = SyncService(Flaky([ok]), db, chunk_days=7)
    result = await service.sync_data_type("heart_rate", "2025-04-01", "2025-04-14")
    assert result["status"] == "partial"
    cursor = db.get_sync_state("u1", "heart_rate")["last_record_timestamp"]
    assert cursor.startswith("2025-04-02")


@pytest.mark.asyncio
async def test_sync_many_isolates_failures_and_validates_types(db):
    class Mixed(CloudStub):
        async def iter_spo2(self, start_date, end_date):
            raise RuntimeError("spo2 endpoint broke")
            yield

    service = SyncService(Mixed([]), db)
    result = await service.sync_many(["heart_rate", "spo2"], "2025-04-01", "2025-04-01")
    assert result["status"] == "partial"
    assert result["data_types_synced"] == ["heart_rate"]
    assert any(d["data_type"] == "spo2" and d["status"] != "ok" for d in result["results"])
    with pytest.raises(ValueError, match="Unsupported"):
        await service.sync_many(["steps"], "2025-04-01", "2025-04-01")
