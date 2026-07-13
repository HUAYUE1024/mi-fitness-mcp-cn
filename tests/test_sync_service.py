import pytest

from mi_fitness_mcp.services.sync_service import SyncService


class FakeAdapter:
    def is_connected(self):
        return True

    async def iter_daily_activity(self, start_date, end_date):
        if False:
            yield None


class FakeDatabase:
    def get_sync_state(self, data_type):
        return None

    def update_sync_state(self, data_type, timestamp):
        raise AssertionError("empty sync must not advance cursor")


@pytest.mark.asyncio
async def test_default_lookback_and_date_validation():
    service = SyncService(FakeAdapter(), FakeDatabase(), default_lookback_days=7)
    result = await service.sync_data_type("daily_activity", end_date="2026-07-12")
    assert result["start_date"] == "2026-07-06"
    assert result["end_date"] == "2026-07-12"

    with pytest.raises(ValueError, match="start_date"):
        await service.sync_data_type(
            "daily_activity", start_date="2026-07-13", end_date="2026-07-12"
        )


@pytest.mark.asyncio
async def test_sync_lock_rejects_overlap():
    service = SyncService(FakeAdapter(), FakeDatabase())
    await service._sync_lock.acquire()
    try:
        assert service.sync_in_progress is True
        with pytest.raises(RuntimeError, match="already in progress"):
            await service.sync_data_type(
                "daily_activity", start_date="2026-07-12", end_date="2026-07-12"
            )
    finally:
        service._sync_lock.release()
    assert service.sync_in_progress is False


@pytest.mark.asyncio
async def test_sync_splits_date_range_into_chunks(monkeypatch):
    service = SyncService(FakeAdapter(), FakeDatabase(), chunk_days=3)
    calls = []

    async def fake_range(data_type, start_date, end_date):
        calls.append((start_date, end_date))
        return {
            "data_type": data_type,
            "added": 1,
            "updated": 0,
            "skipped": 0,
            "start_date": start_date,
            "end_date": end_date,
        }

    monkeypatch.setattr(service, "_sync_range", fake_range)
    result = await service.sync_data_type(
        "daily_activity", start_date="2026-07-06", end_date="2026-07-12"
    )
    assert calls == [
        ("2026-07-06", "2026-07-08"),
        ("2026-07-09", "2026-07-11"),
        ("2026-07-12", "2026-07-12"),
    ]
    assert result["added"] == 3
    assert len(result["chunks"]) == 3


@pytest.mark.asyncio
async def test_chunk_failure_returns_partial_statistics(monkeypatch):
    service = SyncService(FakeAdapter(), FakeDatabase(), chunk_days=2)
    calls = 0

    async def fake_range(data_type, start_date, end_date):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cloud unavailable")
        return {
            "data_type": data_type,
            "added": 2,
            "updated": 0,
            "skipped": 0,
            "start_date": start_date,
            "end_date": end_date,
        }

    monkeypatch.setattr(service, "_sync_range", fake_range)
    result = await service.sync_data_type(
        "daily_activity", start_date="2026-07-06", end_date="2026-07-09"
    )
    assert result["status"] == "partial"
    assert result["added"] == 2
    assert result["chunks"][0]["status"] == "ok"
    assert result["chunks"][1]["status"] == "error"


@pytest.mark.asyncio
async def test_concurrent_sync_is_rejected_instead_of_queued(monkeypatch):
    service = SyncService(FakeAdapter(), FakeDatabase())
    entered = __import__("asyncio").Event()
    release = __import__("asyncio").Event()

    async def blocked_range(data_type, start_date, end_date):
        entered.set()
        await release.wait()
        return {"data_type": data_type, "added": 0, "updated": 0, "skipped": 0}

    monkeypatch.setattr(service, "_sync_range", blocked_range)
    first = __import__("asyncio").create_task(
        service.sync_data_type("daily_activity", "2026-07-12", "2026-07-12")
    )
    await entered.wait()
    with pytest.raises(RuntimeError, match="already in progress"):
        await service.sync_data_type(
            "daily_activity", "2026-07-12", "2026-07-12"
        )
    release.set()
    await first
