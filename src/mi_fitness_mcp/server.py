"""MCP server implementation for Mi Fitness."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mi_fitness_mcp.adapters.mi_fitness_cloud import MiFitnessCloudAdapter
from mi_fitness_mcp.auth import load_mi_fitness_token
from mi_fitness_mcp.config import load_config
from mi_fitness_mcp.models import ConnectionStatus, QueryResponse
from mi_fitness_mcp.services.query_service import QueryService
from mi_fitness_mcp.services.sync_service import SyncService
from mi_fitness_mcp.storage import Database

logger = logging.getLogger(__name__)

app = Server("mi-fitness-mcp")

config = None
db = None
adapter = None
sync_service = None
query_service = None
sync_tasks: dict[str, dict[str, Any]] = {}
sync_active = False
MAX_SYNC_TASKS = 100


def _prune_sync_tasks() -> None:
    completed = [
        sync_id
        for sync_id, state in sync_tasks.items()
        if state.get("status") not in {"queued", "running"}
    ]
    excess = max(0, len(sync_tasks) - MAX_SYNC_TASKS + 1)
    for sync_id in completed[:excess]:
        sync_tasks.pop(sync_id, None)


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_connection_status",
            description="Check connection status",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sync_data",
            description="Synchronize Mi Fitness data",
            inputSchema={
                "type": "object",
                "properties": {
                    "data_types": {"type": "array", "items": {"type": "string"}},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "force_full_sync": {"type": "boolean"},
                    "background": {"type": "boolean", "default": False},
                },
            },
        ),
        Tool(
            name="get_sync_status",
            description="Get background synchronization status",
            inputSchema={
                "type": "object",
                "properties": {"sync_id": {"type": "string"}},
                "required": ["sync_id"],
            },
        ),
        Tool(
            name="get_profile",
            description="Get user profile information",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_daily_summary",
            description="Get daily activity summary",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                },
            },
        ),
        Tool(
            name="query_metric_series",
            description="Query metric series",
            inputSchema={
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": ["steps", "distance_m", "active_kcal", "weight_kg"],
                    },
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "granularity": {"type": "string", "enum": ["day", "week", "month"]},
                    "aggregation": {
                        "type": "string",
                        "enum": ["sum", "avg", "min", "max", "latest"],
                    },
                },
                "required": ["metric", "start_date", "end_date"],
            },
        ),
        Tool(
            name="query_heart_rate",
            description="Query heart rate samples",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "sample_type": {
                        "type": "string",
                        "enum": ["resting", "active", "passive", "workout"],
                    },
                    "limit": {"type": "integer"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="query_body_measurements",
            description="Query body measurements",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "metrics": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "weight_kg",
                                "bmi",
                                "body_fat_pct",
                                "muscle_mass_kg",
                                "water_pct",
                            ],
                        },
                    },
                    "latest_only": {"type": "boolean"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="query_sleep",
            description="Query sleep sessions",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "include_naps": {"type": "boolean"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="query_workouts",
            description="Query workouts",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "activity_types": {"type": "array", "items": {"type": "string"}},
                    "min_duration": {"type": "integer"},
                    "min_distance_km": {"type": "number"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="query_spo2",
            description="Query blood oxygen saturation samples",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="query_stress",
            description="Query stress samples",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "level": {"type": "string", "enum": ["low", "medium", "high"]},
                    "limit": {"type": "integer"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="query_abnormal_heart_beat",
            description="Query abnormal heart beat events",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="get_data_coverage",
            description="Get data coverage",
            inputSchema={
                "type": "object",
                "properties": {"data_types": {"type": "array", "items": {"type": "string"}}},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "get_connection_status":
            result = await _handle_get_connection_status()
        elif name == "sync_data":
            result = await _handle_sync_data(arguments)
        elif name == "get_sync_status":
            result = _handle_get_sync_status(arguments)
        elif name == "get_profile":
            result = await _handle_get_profile()
        elif name == "get_daily_summary":
            result = await _handle_get_daily_summary(arguments)
        elif name == "query_metric_series":
            result = await _handle_query_metric_series(arguments)
        elif name == "query_heart_rate":
            result = await _handle_query_heart_rate(arguments)
        elif name == "query_body_measurements":
            result = await _handle_query_body_measurements(arguments)
        elif name == "query_sleep":
            result = await _handle_query_sleep(arguments)
        elif name == "query_workouts":
            result = await _handle_query_workouts(arguments)
        elif name == "query_spo2":
            result = await _handle_query_spo2(arguments)
        elif name == "query_stress":
            result = await _handle_query_stress(arguments)
        elif name == "query_abnormal_heart_beat":
            result = await _handle_query_abnormal_heart_beat(arguments)
        elif name == "get_data_coverage":
            result = await _handle_get_data_coverage(arguments)
        else:
            result = {"status": "error", "error": f"Unknown tool: {name}"}
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except Exception as e:
        logger.exception("Mi Fitness tool error")
        return [TextContent(type="text", text=json.dumps({"status": "error", "error": str(e)}))]


async def _handle_get_connection_status() -> dict:
    global adapter, config
    if not config or config.mode == "not_configured":
        return ConnectionStatus(
            mode="not_configured", connected=False, message="Server not configured."
        ).model_dump()

    connected = False
    if adapter is not None:
        if sync_service and sync_service.sync_in_progress:
            connected = adapter.is_connected()
        else:
            try:
                connected = await asyncio.wait_for(
                    adapter.health_check(), timeout=config.health_check_timeout_seconds
                )
            except Exception as exc:
                logger.warning("Connection health check failed: %s", exc)
                connected = False
    last_sync = None
    available_types = []
    if db:
        for data_type in [
            "daily_activity",
            "heart_rate",
            "body_measurements",
            "sleep",
            "workouts",
            "spo2",
            "stress",
            "abnormal_heart_beat",
        ]:
            state = db.get_sync_state(data_type)
            if state and state.get("last_sync_at"):
                available_types.append(data_type)
                sync_time = datetime.fromisoformat(state["last_sync_at"])
                if last_sync is None or sync_time > last_sync:
                    last_sync = sync_time
    result = ConnectionStatus(
        mode=config.mode,
        connected=connected,
        last_sync_at=last_sync,
        available_data_types=(adapter.get_available_data_types() if connected else available_types),
        message=getattr(adapter, "last_error", None) if not connected else None,
    ).model_dump()
    result.update(
        {
            "connection_state": "connected" if connected else "disconnected",
            "region": config.region,
            "last_health_check_at": getattr(adapter, "last_health_check_at", None),
            "last_connection_error": getattr(adapter, "last_error", None),
            "sync_in_progress": bool(sync_service and sync_service.sync_in_progress),
        }
    )
    return result


async def _background_sync(sync_id: str, arguments: dict) -> None:
    global sync_active
    try:
        sync_tasks[sync_id].update(
            status="running", started_at=datetime.now(UTC).isoformat()
        )
        sync_tasks[sync_id] = await _run_sync_data(arguments, sync_id)
    except asyncio.CancelledError:
        sync_tasks[sync_id] = {"sync_id": sync_id, "status": "cancelled"}
        raise
    except Exception as exc:
        logger.exception("Background synchronization failed")
        sync_tasks[sync_id] = {
            "sync_id": sync_id,
            "status": "error",
            "error_code": type(exc).__name__,
            "error": str(exc),
        }
    finally:
        sync_active = False


async def _handle_sync_data(arguments: dict) -> dict:
    global sync_active
    # No await between check and assignment: foreground/background reservation is atomic.
    if sync_active:
        return {"status": "error", "error": "Another synchronization is in progress"}
    sync_active = True

    if arguments.get("background"):
        try:
            _prune_sync_tasks()
            sync_id = str(uuid.uuid4())
            sync_tasks[sync_id] = {
                "sync_id": sync_id,
                "status": "queued",
                "created_at": datetime.now(UTC).isoformat(),
            }
            task = asyncio.create_task(
                _background_sync(sync_id, {**arguments, "background": False})
            )
            sync_tasks[sync_id]["task"] = task
            return {"status": "accepted", "sync_id": sync_id}
        except Exception:
            sync_active = False
            raise

    try:
        return await _run_sync_data(arguments)
    finally:
        sync_active = False


def _handle_get_sync_status(arguments: dict) -> dict:
    state = sync_tasks.get(arguments.get("sync_id"))
    if state is None:
        return {"status": "error", "error": "Unknown sync_id"}
    return {key: value for key, value in state.items() if key != "task"}


async def _run_sync_data(arguments: dict, sync_id: str | None = None) -> dict:

    if not sync_service:
        return {"status": "error", "error": "Sync service not initialized"}
    if (not adapter or not adapter.is_connected()) and (not adapter or not await adapter.connect()):
        return {"status": "error", "error": getattr(adapter, "last_error", "Not connected")}
    data_types_arg = arguments.get("data_types")
    if data_types_arg == []:
        return {"status": "error", "error": "data_types must not be empty"}
    data_types = data_types_arg or sync_service.adapter.get_available_data_types()
    supported = set(sync_service.adapter.get_available_data_types())
    unknown = sorted(set(data_types) - supported)
    if unknown:
        return {"status": "error", "error": f"Unsupported data types: {', '.join(unknown)}"}
    sync_id = sync_id or str(uuid.uuid4())
    started_at = datetime.now(UTC)
    totals = {"added": 0, "updated": 0, "skipped": 0}
    details = []
    for data_type in data_types:
        type_started = datetime.now(UTC)
        try:
            result = await asyncio.wait_for(
                sync_service.sync_data_type(
                    data_type=data_type,
                    start_date=arguments.get("start_date"),
                    end_date=arguments.get("end_date"),
                    force_full=arguments.get("force_full_sync", False),
                ),
                timeout=config.sync_type_timeout_seconds,
            )
            result_status = result.get("status", "ok")
            for key in totals:
                totals[key] += result.get(key, 0)
            details.append(
                {
                    "data_type": data_type,
                    "status": result_status,
                    **result,
                    "duration_seconds": (datetime.now(UTC) - type_started).total_seconds(),
                }
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
    succeeded = [item["data_type"] for item in details if item["status"] == "ok"]
    has_partial = any(item["status"] == "partial" for item in details)
    status = (
        "ok"
        if len(succeeded) == len(details)
        else "partial"
        if succeeded or has_partial
        else "error"
    )
    return {
        "status": status,
        "sync_id": sync_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "records_added": totals["added"],
        "records_updated": totals["updated"],
        "records_skipped": totals["skipped"],
        "data_types_synced": succeeded,
        "results": details,
    }


async def _handle_get_profile() -> dict:
    if not adapter or not adapter.is_connected():
        return {"status": "error", "error": "Not connected to data source"}
    return QueryResponse(
        status="ok",
        source=config.mode if config else "unknown",
        data={
            "profile": {
                "user_id": adapter.get_user_id() or "unknown",
                "timezone": config.timezone if config else "UTC",
                "devices": [],
            }
        },
    ).model_dump()


async def _handle_get_daily_summary(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    start_date = arguments.get("date") or arguments.get("start_date")
    end_date = arguments.get("date") or arguments.get("end_date")
    if not start_date or not end_date:
        return {"status": "error", "error": "date or start_date/end_date required"}
    summaries = query_service.get_daily_summaries(start_date, end_date)
    return QueryResponse(status="ok", source="cache", data={"summaries": summaries}).model_dump()


async def _handle_query_metric_series(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    series = query_service.get_metric_series(
        metric=arguments["metric"],
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        granularity=arguments.get("granularity", "day"),
        aggregation=arguments.get("aggregation", "sum"),
    )
    return QueryResponse(
        status="ok", source="cache", data={"metric": arguments["metric"], "series": series}
    ).model_dump()


async def _handle_query_heart_rate(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    samples = query_service.get_heart_rate_samples(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        sample_type=arguments.get("sample_type"),
        limit=arguments.get("limit"),
    )
    return QueryResponse(
        status="ok", source="cache", data={"samples": samples, "count": len(samples)}
    ).model_dump()


async def _handle_query_body_measurements(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    measurements = query_service.get_body_measurements(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        metrics=arguments.get("metrics"),
    )
    if arguments.get("latest_only") and measurements:
        measurements = [measurements[-1]]
    return QueryResponse(
        status="ok", source="cache", data={"measurements": measurements, "count": len(measurements)}
    ).model_dump()


async def _handle_query_sleep(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    sessions = query_service.get_sleep_sessions(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        include_naps=arguments.get("include_naps", True),
    )
    return QueryResponse(
        status="ok", source="cache", data={"sessions": sessions, "count": len(sessions)}
    ).model_dump()


async def _handle_query_workouts(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    workouts = query_service.get_workouts(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        activity_types=arguments.get("activity_types"),
        min_duration=arguments.get("min_duration"),
        min_distance_km=arguments.get("min_distance_km"),
    )
    return QueryResponse(
        status="ok", source="cache", data={"workouts": workouts, "count": len(workouts)}
    ).model_dump()


async def _handle_query_spo2(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    samples = query_service.get_spo2_samples(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        limit=arguments.get("limit"),
    )
    return QueryResponse(
        status="ok", source="cache", data={"samples": samples, "count": len(samples)}
    ).model_dump()


async def _handle_query_stress(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    samples = query_service.get_stress_samples(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        level=arguments.get("level"),
        limit=arguments.get("limit"),
    )
    return QueryResponse(
        status="ok", source="cache", data={"samples": samples, "count": len(samples)}
    ).model_dump()


async def _handle_query_abnormal_heart_beat(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    events = query_service.get_abnormal_heart_beat_events(
        start_date=arguments["start_date"],
        end_date=arguments["end_date"],
        limit=arguments.get("limit"),
    )
    return QueryResponse(
        status="ok", source="cache", data={"events": events, "count": len(events)}
    ).model_dump()


async def _handle_get_data_coverage(arguments: dict) -> dict:
    if not query_service:
        return {"status": "error", "error": "Query service not initialized"}
    coverage = query_service.get_data_coverage(arguments.get("data_types"))
    return QueryResponse(status="ok", source="cache", data={"coverage": coverage}).model_dump()


async def main():
    global config, db, adapter, sync_service, query_service
    config = load_config()
    db = Database(config.database_path)
    if config.mode == "mi_fitness_cloud":
        user_id, pass_token = load_mi_fitness_token()
        if user_id and pass_token:
            adapter = MiFitnessCloudAdapter(
                user_id=user_id, pass_token=pass_token, region=config.region
            )
            adapter.http_timeout = config.http_timeout_seconds
            adapter.request_retries = config.request_retries
            adapter.max_pages = config.max_pages
            # Do not connect here: MCP stdio must become available even when Xiaomi
            # authentication or networking is slow. Status/sync tools connect on demand.
    if adapter:
        sync_service = SyncService(
            adapter, db, config.default_lookback_days, config.sync_chunk_days
        )
        query_service = QueryService(db, adapter.get_user_id() or "unknown")
    else:
        query_service = QueryService(db, "unknown")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        pending = [
            state.get("task") for state in sync_tasks.values() if state.get("task") is not None
        ]
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if adapter:
            await adapter.close()
