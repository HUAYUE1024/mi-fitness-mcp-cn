import asyncio

import pytest

from mi_fitness_mcp import server


@pytest.mark.asyncio
async def test_background_sync_can_be_polled(monkeypatch):
    server.sync_tasks.clear()

    async def fake_run(arguments, sync_id=None):
        await asyncio.sleep(0)
        return {"status": "ok", "sync_id": sync_id, "records_added": 1}

    monkeypatch.setattr(server, "_run_sync_data", fake_run)
    accepted = await server._handle_sync_data({"background": True})
    assert accepted["status"] == "accepted"
    await asyncio.sleep(0.01)
    state = server._handle_get_sync_status({"sync_id": accepted["sync_id"]})
    assert state["status"] == "ok"
    assert state["records_added"] == 1
    assert "task" not in state


def test_unknown_background_sync_id():
    assert server._handle_get_sync_status({"sync_id": "missing"})["status"] == "error"


@pytest.mark.asyncio
async def test_second_background_sync_is_rejected_while_queued(monkeypatch):
    server.sync_tasks.clear()
    gate = asyncio.Event()

    async def fake_run(arguments, sync_id=None):
        await gate.wait()
        return {"status": "ok", "sync_id": sync_id}

    monkeypatch.setattr(server, "_run_sync_data", fake_run)
    first = await server._handle_sync_data({"background": True})
    task = server.sync_tasks[first["sync_id"]]["task"]
    second = await server._handle_sync_data({"background": True})
    assert first["status"] == "accepted"
    assert second["status"] == "error"
    gate.set()
    await task


@pytest.mark.asyncio
async def test_connection_status_skips_health_check_during_sync(monkeypatch):
    class Adapter:
        last_error = None
        last_health_check_at = None

        def is_connected(self):
            return True

        def get_available_data_types(self):
            return []

        async def health_check(self):
            raise AssertionError("health check must not run while syncing")

    class Service:
        sync_in_progress = True

    class Config:
        mode = "mi_fitness_cloud"
        region = "cn"
        health_check_timeout_seconds = 1

    monkeypatch.setattr(server, "adapter", Adapter())
    monkeypatch.setattr(server, "sync_service", Service())
    monkeypatch.setattr(server, "config", Config())
    monkeypatch.setattr(server, "db", None)
    result = await server._handle_get_connection_status()
    assert result["connected"] is True
