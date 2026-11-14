"""MCP server for Mi Fitness: a tool registry on top of the local database."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from mi_fitness_mcp import __version__
from mi_fitness_mcp.adapters.mi_fitness_cloud import MiFitnessCloudAdapter
from mi_fitness_mcp.auth import load_mi_fitness_token
from mi_fitness_mcp.config import load_config
from mi_fitness_mcp.demo import DEMO_USER_ID, demo_database
from mi_fitness_mcp.models import DATA_TYPES, ConnectionStatus, QueryResponse
from mi_fitness_mcp.services.analytics import AnalyticsService
from mi_fitness_mcp.services.query_service import AGGREGATIONS, GRANULARITIES, SERIES_METRICS
from mi_fitness_mcp.services.sync_service import SyncService
from mi_fitness_mcp.storage import Database

logger = logging.getLogger(__name__)

app = Server("mi-fitness-mcp")

config = None
db = None
adapter = None
sync_service = None
query_service = None
demo_mode = False
sync_tasks: dict[str, dict[str, Any]] = {}
sync_active = False
MAX_SYNC_TASKS = 100


def _prune_sync_tasks() -> None:
    finished = [i for i, s in sync_tasks.items() if s.get("status") not in {"queued", "running"}]
    for sync_id in finished[: max(0, len(sync_tasks) - MAX_SYNC_TASKS + 1)]:
        sync_tasks.pop(sync_id, None)


# ------------------------------------------------------------------ tool registry


@dataclass(frozen=True)
class ToolSpec:
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    handler: Callable[[dict], Awaitable[dict]]

    def tool(self, name: str) -> Tool:
        schema: dict[str, Any] = {"type": "object", "properties": self.properties}
        if self.required:
            schema["required"] = list(self.required)
        return Tool(name=name, description=self.description, inputSchema=schema)


TOOLS: dict[str, ToolSpec] = {}


def tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
):
    def register(fn: Callable[[dict], Awaitable[dict]]):
        TOOLS[name] = ToolSpec(description, properties or {}, required, fn)
        return fn

    return register


DATE = {"type": "string", "description": "YYYY-MM-DD"}
START = {"start_date": {**DATE, "description": "起始日期（含），YYYY-MM-DD"}}
RANGE = {**START, "end_date": {**DATE, "description": "结束日期（含），YYYY-MM-DD"}}
LIMIT = {"limit": {"type": "integer", "minimum": 0, "description": "最多返回条数，按时间从早到晚"}}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [spec.tool(name) for name, spec in TOOLS.items()]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    spec = TOOLS.get(name)
    try:
        result = (
            await spec.handler(arguments or {})
            if spec
            else {"status": "error", "error": f"Unknown tool: {name}"}
        )
    except (ValueError, KeyError) as exc:
        message = f"缺少参数 {exc}" if isinstance(exc, KeyError) else str(exc)
        result = {"status": "error", "error": message}
    except Exception as exc:
        logger.exception("Mi Fitness tool error")
        result = {"status": "error", "error": str(exc)}
    return [TextContent(type="text", text=json.dumps(result, default=str, ensure_ascii=False))]


def _ok(data: dict) -> dict:
    return QueryResponse(status="ok", source="cache", data=data).model_dump()


def _need_query() -> AnalyticsService:
    if query_service is None:
        raise ValueError("Query service not initialized")
    return query_service


# ------------------------------------------------------------------ connection & sync


@tool("get_connection_status", "检查小米云连接状态、区域、上次同步时间以及是否正在同步。先调用它判断数据新不新。")
async def _handle_get_connection_status(_: dict | None = None) -> dict:
    if demo_mode:
        return ConnectionStatus(
            mode="demo", connected=False, available_data_types=list(DATA_TYPES),
            message="演示模式：数据为合成数据，未连接小米云。",
        ).model_dump() | {"connection_state": "demo", "sync_in_progress": False}
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
    last_sync, synced_types = None, []
    if db and adapter:
        for data_type in DATA_TYPES:
            state = db.get_sync_state(adapter.get_user_id() or "unknown", data_type)
            if state and state.get("last_sync_at"):
                synced_types.append(data_type)
                when = datetime.fromisoformat(state["last_sync_at"])
                last_sync = when if last_sync is None or when > last_sync else last_sync
    result = ConnectionStatus(
        mode=config.mode,
        connected=connected,
        last_sync_at=last_sync,
        available_data_types=adapter.get_available_data_types() if connected else synced_types,
        message=getattr(adapter, "last_error", None) if not connected else None,
    ).model_dump()
    result.update(
        connection_state="connected" if connected else "disconnected",
        region=config.region,
        last_health_check_at=getattr(adapter, "last_health_check_at", None),
        last_connection_error=getattr(adapter, "last_error", None),
        sync_in_progress=bool(sync_service and sync_service.sync_in_progress),
    )
    return result


async def _background_sync(sync_id: str, arguments: dict) -> None:
    global sync_active
    try:
        sync_tasks[sync_id].update(status="running", started_at=datetime.now(UTC).isoformat())
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


@tool(
    "sync_data",
    "从小米云同步数据到本地库。不传日期时从上次断点增量续传（首次为最近 30 天）；"
    "大范围同步建议 background=true，再用 get_sync_status 轮询。其它查询工具只读本地库，需先同步。",
    {
        "data_types": {"type": "array", "items": {"type": "string", "enum": list(DATA_TYPES)}},
        **RANGE,
        "force_full_sync": {"type": "boolean", "description": "忽略增量游标，按给定/默认区间重拉"},
        "background": {"type": "boolean", "default": False},
    },
)
async def _handle_sync_data(arguments: dict) -> dict:
    global sync_active
    if demo_mode:
        return {"status": "error", "error": "演示模式不连接小米云，无需同步"}
    if sync_active:  # 检查与占位之间没有 await，前台/后台预占是原子的
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
            sync_tasks[sync_id]["task"] = asyncio.create_task(
                _background_sync(sync_id, {**arguments, "background": False})
            )
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


@tool(
    "get_sync_status",
    "查询后台同步任务进度与结果。",
    {"sync_id": {"type": "string"}},
    ("sync_id",),
)
async def _get_sync_status_tool(arguments: dict) -> dict:
    return _handle_get_sync_status(arguments)


async def _run_sync_data(arguments: dict, sync_id: str | None = None) -> dict:
    if not sync_service:
        return {"status": "error", "error": "Sync service not initialized"}
    if not adapter.is_connected() and not await adapter.connect():
        return {"status": "error", "error": getattr(adapter, "last_error", "Not connected")}
    data_types = arguments.get("data_types")
    if data_types == []:
        return {"status": "error", "error": "data_types must not be empty"}
    try:
        return await sync_service.sync_many(
            data_types or None,
            arguments.get("start_date"),
            arguments.get("end_date"),
            arguments.get("force_full_sync", False),
            timeout_seconds=config.sync_type_timeout_seconds,
            sync_id=sync_id,
        )
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}


@tool("get_profile", "当前账号的基本信息（账号 ID、时区）。")
async def _handle_get_profile(_: dict | None = None) -> dict:
    if demo_mode:
        return _ok({"profile": {"user_id": DEMO_USER_ID, "timezone": "Asia/Shanghai", "devices": []}})
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


# ------------------------------------------------------------------ raw queries


@tool(
    "get_daily_summary",
    "每日活动汇总（步数、距离、活动卡路里）。传 date 查单日，或传 start_date+end_date 查区间。",
    {"date": DATE, **RANGE},
)
async def _handle_get_daily_summary(arguments: dict) -> dict:
    start = arguments.get("date") or arguments.get("start_date")
    end = arguments.get("date") or arguments.get("end_date")
    if not start or not end:
        return {"status": "error", "error": "date or start_date/end_date required"}
    return _ok({"summaries": _need_query().get_daily_summaries(start, end)})


@tool(
    "query_metric_series",
    "任意指标的时间序列，用于画趋势图或做同比分析。aggregation 缺省时按指标自然方式聚合"
    "（步数求和、心率取平均）。可用指标：" + "、".join(SERIES_METRICS) + "。",
    {
        "metric": {"type": "string", "enum": list(SERIES_METRICS)},
        **RANGE,
        "granularity": {"type": "string", "enum": list(GRANULARITIES), "default": "day"},
        "aggregation": {"type": "string", "enum": list(AGGREGATIONS)},
    },
    ("metric", "start_date", "end_date"),
)
async def _handle_query_metric_series(arguments: dict) -> dict:
    series = _need_query().get_metric_series(
        arguments["metric"],
        arguments["start_date"],
        arguments["end_date"],
        arguments.get("granularity", "day"),
        arguments.get("aggregation"),
    )
    return _ok({"metric": arguments["metric"], "series": series, "count": len(series)})


@tool(
    "query_heart_rate",
    "原始心率样本（逐分钟量级，数据量大，务必用 limit；要看趋势请用 get_heart_rate_summary）。",
    {
        **RANGE,
        "sample_type": {"type": "string", "enum": ["resting", "active", "passive", "workout"]},
        **LIMIT,
    },
    ("start_date", "end_date"),
)
async def _handle_query_heart_rate(arguments: dict) -> dict:
    samples = _need_query().get_heart_rate_samples(
        arguments["start_date"], arguments["end_date"],
        arguments.get("sample_type"), arguments.get("limit"),
    )
    return _ok({"samples": samples, "count": len(samples)})


@tool(
    "query_body_measurements",
    "体重/体成分记录（需体脂秤）。latest_only=true 只取区间内最近一次。",
    {
        **RANGE,
        "metrics": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["weight_kg", "bmi", "body_fat_pct", "muscle_mass_kg", "water_pct",
                         "bone_mass_kg", "visceral_fat_score", "basal_metabolism_kcal", "metabolic_age"],
            },
        },
        "latest_only": {"type": "boolean"},
    },
    ("start_date", "end_date"),
)
async def _handle_query_body_measurements(arguments: dict) -> dict:
    items = _need_query().get_body_measurements(
        arguments["start_date"], arguments["end_date"],
        arguments.get("metrics"), bool(arguments.get("latest_only")),
    )
    return _ok({"measurements": items, "count": len(items)})


@tool(
    "query_sleep",
    "睡眠记录（时长、入睡/醒来时间、评分、深/浅/REM 分期）。要看均值与作息规律请用 get_sleep_summary。",
    {**RANGE, "include_naps": {"type": "boolean", "default": True}},
    ("start_date", "end_date"),
)
async def _handle_query_sleep(arguments: dict) -> dict:
    sessions = _need_query().get_sleep_sessions(
        arguments["start_date"], arguments["end_date"], arguments.get("include_naps", True)
    )
    return _ok({"sessions": sessions, "count": len(sessions)})


@tool(
    "query_workouts",
    "运动记录，可按类型/最短时长/最短距离过滤。",
    {
        **RANGE,
        "activity_types": {"type": "array", "items": {"type": "string"}},
        "min_duration": {"type": "integer", "description": "最短时长（分钟）"},
        "min_distance_km": {"type": "number"},
    },
    ("start_date", "end_date"),
)
async def _handle_query_workouts(arguments: dict) -> dict:
    workouts = _need_query().get_workouts(
        arguments["start_date"], arguments["end_date"], arguments.get("activity_types"),
        arguments.get("min_duration"), arguments.get("min_distance_km"),
    )
    return _ok({"workouts": workouts, "count": len(workouts)})


@tool("query_spo2", "血氧（SpO₂）原始样本。", {**RANGE, **LIMIT}, ("start_date", "end_date"))
async def _handle_query_spo2(arguments: dict) -> dict:
    samples = _need_query().get_spo2_samples(
        arguments["start_date"], arguments["end_date"], arguments.get("limit")
    )
    return _ok({"samples": samples, "count": len(samples)})


@tool(
    "query_stress",
    "压力原始样本（0-100 分与低/中/高等级）。",
    {**RANGE, "level": {"type": "string", "enum": ["low", "medium", "high"]}, **LIMIT},
    ("start_date", "end_date"),
)
async def _handle_query_stress(arguments: dict) -> dict:
    samples = _need_query().get_stress_samples(
        arguments["start_date"], arguments["end_date"], arguments.get("level"), arguments.get("limit")
    )
    return _ok({"samples": samples, "count": len(samples)})


@tool("query_abnormal_heart_beat", "异常心跳事件（起止时间与时长）。", {**RANGE, **LIMIT}, ("start_date", "end_date"))
async def _handle_query_abnormal_heart_beat(arguments: dict) -> dict:
    events = _need_query().get_abnormal_heart_beat_events(
        arguments["start_date"], arguments["end_date"], arguments.get("limit")
    )
    return _ok({"events": events, "count": len(events)})


@tool(
    "get_data_coverage",
    "本地库里每类数据的首末日期、有数据天数与记录数。判断「能查多久以前的数据」时用。",
    {"data_types": {"type": "array", "items": {"type": "string", "enum": list(DATA_TYPES)}}},
)
async def _handle_get_data_coverage(arguments: dict) -> dict:
    return _ok({"coverage": _need_query().get_data_coverage(arguments.get("data_types"))})


# ------------------------------------------------------------------ analytics


@tool(
    "get_health_overview",
    "一次拿到某天（默认今天）的健康快照：当日步数、昨晚睡眠、静息心率、最新血氧/压力/体重，"
    "以及近 7 天均值。用户问「我今天/最近怎么样」时优先用它。",
    {"date": {**DATE, "description": "快照日期，默认今天"}},
)
async def _handle_get_health_overview(arguments: dict) -> dict:
    return _ok(_need_query().get_overview(arguments.get("date")))


@tool(
    "get_period_report",
    "周报/月报：指定区间的活动、睡眠、心率、血氧、压力、运动与体重汇总，并与紧邻的等长上一区间对比（change_pct）。",
    RANGE,
    ("start_date", "end_date"),
)
async def _handle_get_period_report(arguments: dict) -> dict:
    return _ok(_need_query().get_period_report(arguments["start_date"], arguments["end_date"]))


@tool(
    "get_sleep_summary",
    "睡眠分析：平均时长/评分、平均入睡与起床时间、入睡时间波动（越小越规律）、深浅 REM 占比、不足 6 小时的夜数。",
    RANGE,
    ("start_date", "end_date"),
)
async def _handle_get_sleep_summary(arguments: dict) -> dict:
    return _ok(_need_query().get_sleep_summary(arguments["start_date"], arguments["end_date"]))


@tool(
    "get_heart_rate_summary",
    "心率分析：逐日静息/最低/平均/最高心率，以及整段时间落在各心率区间（rest/light/moderate/vigorous）的样本数。",
    RANGE,
    ("start_date", "end_date"),
)
async def _handle_get_heart_rate_summary(arguments: dict) -> dict:
    return _ok(_need_query().get_heart_rate_summary(arguments["start_date"], arguments["end_date"]))


@tool(
    "get_personal_records",
    "历史个人纪录：最多步数日、最长/最远/最耗能运动、最佳睡眠评分、最低静息心率、最低体重，以及连续达成步数目标的天数。",
    {"step_goal": {"type": "integer", "default": 8000, "minimum": 1}},
)
async def _handle_get_personal_records(arguments: dict) -> dict:
    return _ok(_need_query().get_personal_records(int(arguments.get("step_goal", 8000))))


@tool(
    "find_data_gaps",
    "找出区间内缺失数据的日期（合并为连续区间）。用来判断是没戴手环、没开监测还是没同步，避免把「没数据」误读为「数值为 0」。",
    {**RANGE, "data_types": {"type": "array", "items": {"type": "string", "enum": list(DATA_TYPES)}}},
    ("start_date", "end_date"),
)
async def _handle_find_data_gaps(arguments: dict) -> dict:
    gaps = _need_query().find_data_gaps(
        arguments["start_date"], arguments["end_date"], arguments.get("data_types")
    )
    return _ok({"gaps": gaps})


# ------------------------------------------------------------------ lifecycle


def configure_demo() -> None:
    global db, query_service, demo_mode
    db = demo_database()
    query_service = AnalyticsService(db, DEMO_USER_ID)
    demo_mode = True


async def main(demo: bool = False):
    global config, db, adapter, sync_service, query_service
    config = load_config()
    if demo:
        configure_demo()
    else:
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
                # 不在这里连接：小米登录慢时 MCP stdio 也必须先可用，状态/同步工具按需连接
        if adapter:
            sync_service = SyncService(adapter, db, config.default_lookback_days, config.sync_chunk_days)
        query_service = AnalyticsService(db, (adapter.get_user_id() if adapter else None) or "unknown")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        pending = [s.get("task") for s in sync_tasks.values() if s.get("task") is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if adapter:
            await adapter.close()


__all__ = ["__version__", "app", "main"]
