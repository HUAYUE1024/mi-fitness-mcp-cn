"""Sync service: pulls records from an adapter and upserts them in per-chunk transactions."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from mi_fitness_mcp.adapters.base import DataAdapter
from mi_fitness_mcp.storage import Database
from mi_fitness_mcp.storage.schema import TABLES

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]

ADAPTER_ITERATORS = {
    "daily_activity": "iter_daily_activity",
    "sleep": "iter_sleep_sessions",
    "workouts": "iter_workouts",
    "body_measurements": "iter_body_measurements",
    "heart_rate": "iter_heart_rate",
    "spo2": "iter_spo2",
    "stress": "iter_stress",
    "abnormal_heart_beat": "iter_abnormal_heart_beat",
}


class SyncService:
    """Synchronizes adapter data into the local database, one date chunk at a time."""

    def __init__(
        self,
        adapter: DataAdapter,
        db: Database,
        default_lookback_days: int = 30,
        chunk_days: int = 7,
    ):
        self.adapter = adapter
        self.db = db
        self.default_lookback_days = default_lookback_days
        self.chunk_days = chunk_days
        self._sync_lock = asyncio.Lock()
        self._sync_active = False

    @property
    def sync_in_progress(self) -> bool:
        return self._sync_active or self._sync_lock.locked()

    @property
    def user_id(self) -> str:
        return self.adapter.get_user_id() or "unknown"

    async def sync_data_type(
        self,
        data_type: str,
        start_date: str | None = None,
        end_date: str | None = None,
        force_full: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> dict:
        """Synchronize one data type; overlapping syncs are rejected, never queued."""
        # Check and reserve without awaiting, making this atomic for tasks on this loop.
        if self._sync_active or self._sync_lock.locked():
            raise RuntimeError("Another synchronization is already in progress")
        self._sync_active = True
        try:
            async with self._sync_lock:
                return await self._sync_data_type_unlocked(
                    data_type, start_date, end_date, force_full, on_progress
                )
        finally:
            self._sync_active = False

    def _resolve_range(
        self, data_type: str, start_date: str | None, end_date: str | None, force_full: bool
    ) -> tuple[datetime, datetime]:
        try:
            end_dt = datetime.strptime(end_date or datetime.now().strftime("%Y-%m-%d"), "%Y-%m-%d")
            start_dt = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        except ValueError as exc:
            raise ValueError("Dates must use YYYY-MM-DD format") from exc

        if start_dt is None:
            state = None if force_full else self.db.get_sync_state(self.user_id, data_type)
            cursor = state and state.get("last_record_timestamp")
            if cursor:
                # 从游标所在那一天重拉：当天后续还可能有新数据，UPSERT 幂等所以不怕重复。
                last = datetime.fromisoformat(cursor)
                start_dt = datetime(last.year, last.month, last.day)
            else:
                start_dt = end_dt - timedelta(days=self.default_lookback_days - 1)
            start_dt = min(start_dt, end_dt)
        if start_dt > end_dt:
            raise ValueError("start_date must not be after end_date")
        return start_dt, end_dt

    async def _sync_data_type_unlocked(
        self,
        data_type: str,
        start_date: str | None,
        end_date: str | None,
        force_full: bool,
        on_progress: ProgressCallback | None,
    ) -> dict:
        if data_type not in TABLES:
            raise ValueError(f"Unknown data type: {data_type}")
        if not self.adapter.is_connected():
            raise RuntimeError("Adapter not connected")

        start_dt, end_dt = self._resolve_range(data_type, start_date, end_date, force_full)
        start_date, end_date = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")

        totals = {"added": 0, "updated": 0, "skipped": 0}
        chunks: list[dict[str, Any]] = []
        cursor = start_dt
        while cursor <= end_dt:
            chunk_end = min(cursor + timedelta(days=self.chunk_days - 1), end_dt)
            chunk = {
                "start_date": cursor.strftime("%Y-%m-%d"),
                "end_date": chunk_end.strftime("%Y-%m-%d"),
            }
            try:
                result = await self._sync_range(data_type, chunk["start_date"], chunk["end_date"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                chunks.append(
                    {**chunk, "status": "error", "error_code": type(exc).__name__, "error": str(exc)}
                )
                if on_progress:
                    on_progress({"data_type": data_type, **chunks[-1]})
                return {
                    "status": "partial" if any(c["status"] == "ok" for c in chunks) else "error",
                    "data_type": data_type,
                    **totals,
                    "start_date": start_date,
                    "end_date": end_date,
                    "chunks": chunks,
                    "error_code": type(exc).__name__,
                    "error": str(exc),
                }
            for key in totals:
                totals[key] += result[key]
            chunks.append({**chunk, "status": "ok", **{k: result[k] for k in totals}})
            if on_progress:
                on_progress({"data_type": data_type, **chunks[-1]})
            cursor = chunk_end + timedelta(days=1)

        return {
            "status": "ok",
            "data_type": data_type,
            **totals,
            "start_date": start_date,
            "end_date": end_date,
            "chunks": chunks,
        }

    async def _sync_range(self, data_type: str, start_date: str, end_date: str) -> dict:
        table = TABLES[data_type]
        iterator = getattr(self.adapter, ADAPTER_ITERATORS[data_type], None)
        if iterator is None:
            raise ValueError(f"Adapter does not support {data_type}")

        records_iter = iterator(start_date, end_date)
        if hasattr(records_iter, "__aiter__"):
            records = [record async for record in records_iter]
        else:
            records = list(records_iter)

        stats = self.db.upsert(data_type, records)
        times = [t for r in records if (t := getattr(r, table.cursor_attr, None)) is not None]
        if times:
            # 一个分块整体入库成功后才推进游标，中途失败不会跳过未入库的数据
            self.db.update_sync_state(self.user_id, data_type, max(times))

        logger.info("Synced %s %s..%s: %s", data_type, start_date, end_date, stats)
        return {"data_type": data_type, **stats, "start_date": start_date, "end_date": end_date}

    async def sync_many(
        self,
        data_types: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        force_full: bool = False,
        timeout_seconds: float | None = None,
        sync_id: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict:
        """依次同步多种数据，单类型失败/超时不影响其它类型，返回统一汇总。"""
        supported = list(self.adapter.get_available_data_types())
        data_types = data_types or supported
        unknown = sorted(set(data_types) - set(supported))
        if unknown:
            raise ValueError(f"Unsupported data types: {', '.join(unknown)}")

        started_at = datetime.now(UTC)
        totals = {"added": 0, "updated": 0, "skipped": 0}
        details: list[dict[str, Any]] = []
        for data_type in data_types:
            began = datetime.now(UTC)
            try:
                result = await asyncio.wait_for(
                    self.sync_data_type(
                        data_type, start_date, end_date, force_full, on_progress=on_progress
                    ),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                details.append(
                    {
                        "data_type": data_type,
                        "status": "error",
                        "error_code": "timeout",
                        "error": "Data type synchronization timed out",
                    }
                )
                continue
            except Exception as exc:
                logger.exception("Failed to sync %s", data_type)
                details.append(
                    {
                        "data_type": data_type,
                        "status": "error",
                        "error_code": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            for key in totals:
                totals[key] += result.get(key, 0)
            details.append(
                {**result, "duration_seconds": (datetime.now(UTC) - began).total_seconds()}
            )

        succeeded = [d["data_type"] for d in details if d["status"] == "ok"]
        if len(succeeded) == len(details):
            status = "ok"
        elif succeeded or any(d["status"] == "partial" for d in details):
            status = "partial"
        else:
            status = "error"
        return {
            "status": status,
            "sync_id": sync_id or str(uuid.uuid4()),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "records_added": totals["added"],
            "records_updated": totals["updated"],
            "records_skipped": totals["skipped"],
            "data_types_synced": succeeded,
            "results": details,
        }
