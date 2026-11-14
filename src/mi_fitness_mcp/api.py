"""HTTP API for Mi Fitness data (FastAPI).

把同步/查询/分析能力以 REST 形式暴露，供其他程序直接调用。启动：mi-fitness-mcp api

鉴权模型：
- 默认凭据：来自本地配置（config.json + keyring），不带头直接访问即可用（仅建议本机）。
- 静态 Key：设置环境变量 MI_FITNESS_API_KEY 后，所有请求必须带 X-API-Key。
- 发放 Key：管理员调 POST /api/auth/keys，用 userId+passToken 换取 mif_sk_* Key；
  调用方之后只带 Key，不接触小米凭据。Key 记录在主库 api_keys 表，passToken 存系统 keyring。
- 管理端点（发放/列表/吊销/扫码登录）需要 X-Admin-Key 匹配 MI_FITNESS_ADMIN_KEY；
  未设置该变量时仅允许 127.0.0.1 本机调用。

扫码登录（逆向自 account.xiaomi.com 通用 QR 流程，参考 python-miio cloud_qr.py）：
POST /api/auth/qr/start 生成二维码 → 扫码确认 → GET /api/auth/qr/poll 轮询到 confirmed 并自动发放 Key。
"""

import asyncio
import csv
import hmac
import io
import json
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from mi_fitness_mcp import __version__
from mi_fitness_mcp.adapters.mi_fitness_cloud import MiFitnessCloudAdapter
from mi_fitness_mcp.auth import load_mi_fitness_token
from mi_fitness_mcp.config import load_config
from mi_fitness_mcp.demo import DEMO_USER_ID, demo_database
from mi_fitness_mcp.models import DATA_TYPES
from mi_fitness_mcp.security import (
    cors_origins_from_env,
    default_allowed_hosts,
    delete_api_key_secret,
    host_allowed,
    load_api_key_secret,
    store_api_key_secret,
)
from mi_fitness_mcp.services.analytics import AnalyticsService
from mi_fitness_mcp.services.query_service import AGGREGATIONS, GRANULARITIES, SERIES_METRICS
from mi_fitness_mcp.services.sync_service import SyncService
from mi_fitness_mcp.storage import Database, validate_date

STRESS_LEVELS = ["low", "medium", "high"]
LOGIN_URL_ENDPOINT = "https://account.xiaomi.com/longPolling/loginUrl"
LOGIN_PREFIX = "&&&START&&&"
QR_MAX_SESSIONS = 20
QR_FAILURE_PAUSE = 2.0
MAX_SYNC_TASKS = 100

# ---------------------------------------------------------------- 用户上下文


@dataclass
class UserContext:
    """一套小米凭据对应的运行时上下文（适配器/服务/同步状态），按凭据缓存。"""

    user_id: str
    region: str
    adapter: MiFitnessCloudAdapter | None = None
    sync_service: SyncService | None = None
    query_service: AnalyticsService | None = None
    sync_running: bool = False


def _build_context(db: Database, config, user_id: str, pass_token: str, region: str) -> UserContext:
    adapter = MiFitnessCloudAdapter(user_id=user_id, pass_token=pass_token, region=region)
    adapter.http_timeout = config.http_timeout_seconds
    adapter.request_retries = config.request_retries
    adapter.max_pages = config.max_pages
    return UserContext(
        user_id=user_id,
        region=region,
        adapter=adapter,
        sync_service=SyncService(adapter, db, config.default_lookback_days, config.sync_chunk_days),
        query_service=AnalyticsService(db, user_id),
    )


def _migrate_key_secrets(db: Database) -> None:
    """历史版本把 passToken 明文存在 api_keys 表：移入系统 keyring 并清空列，已吊销的一并清理。"""
    with db._get_connection() as conn:
        for row in conn.execute("SELECT key, pass_token FROM api_keys WHERE pass_token != ''"):
            if store_api_key_secret(row["key"], row["pass_token"]):
                conn.execute("UPDATE api_keys SET pass_token = '' WHERE key = ?", (row["key"],))
        revoked = [r["key"] for r in conn.execute("SELECT key FROM api_keys WHERE revoked = 1")]
        conn.commit()
    for key in revoked:
        delete_api_key_secret(key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    demo = os.environ.get("MI_FITNESS_DEMO") == "1"
    db = demo_database() if demo else Database(config.database_path)
    _migrate_key_secrets(db)

    default_context = None
    if demo:
        default_context = UserContext(DEMO_USER_ID, "cn", query_service=AnalyticsService(db, DEMO_USER_ID))
    elif config.mode == "mi_fitness_cloud":
        user_id, pass_token = load_mi_fitness_token()
        if user_id and pass_token:
            default_context = _build_context(db, config, user_id, pass_token, config.region)

    app.state.config = config
    app.state.db = db
    app.state.demo = demo
    app.state.default_context = default_context
    app.state.contexts = {}
    app.state.sync_tasks = {}
    app.state.qr_sessions = {}
    app.state.qr_failures = 0.0
    try:
        yield
    finally:
        tasks = [e.get("task") for e in app.state.sync_tasks.values() if e.get("task")]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        contexts = [*app.state.contexts.values(), app.state.default_context]
        for ctx in filter(None, contexts):
            if ctx.adapter:
                await ctx.adapter.close()


def _secret_equals(provided: str | None, expected: str | None) -> bool:
    return bool(provided and expected) and hmac.compare_digest(provided.encode(), expected.encode())


async def _gate(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """入口闸门：环境静态 Key 设置时强制带头；发了 Key 则校验有效性。"""
    env_key = os.environ.get("MI_FITNESS_API_KEY")
    if x_api_key is None:
        if env_key:
            raise HTTPException(status_code=401, detail="Missing X-API-Key header")
        return
    if _secret_equals(x_api_key, env_key):
        return
    row = request.app.state.db.api_key_row(x_api_key)
    if row is None or row["revoked"]:
        raise HTTPException(status_code=401, detail="Invalid API key")


app = FastAPI(
    title="Mi Fitness API",
    version=__version__,
    description="小米运动健康数据 REST API（多 Key 鉴权 + 扫码登录 + 本地 SQLite 缓存 + 健康分析）",
    lifespan=lifespan,
    dependencies=[Depends(_gate)],
)
app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_headers=["*"],
    **cors_origins_from_env(default_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"),
)


class HostAllowlistMiddleware:
    """Host 头白名单（防 DNS 重绑定）：拒绝非本机域名直连本地端口读取数据。"""

    def __init__(self, app, allowed_hosts: set[str] | None = None):
        self.app = app
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else default_allowed_hosts()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            host = next(
                (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), ""
            )
            if not host_allowed(host, self.allowed_hosts):
                await JSONResponse({"detail": "Host not allowed"}, status_code=403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


app.add_middleware(HostAllowlistMiddleware)


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


def _resolve_context(request: Request) -> UserContext:
    """根据 X-API-Key 解析出对应用户上下文；静态 Key/无头 → 默认凭据。"""
    state = request.app.state
    header = request.headers.get("x-api-key")
    ctx = state.default_context
    if header and not _secret_equals(header, os.environ.get("MI_FITNESS_API_KEY")):
        row = state.db.api_key_row(header)
        if row is not None and not row["revoked"]:
            # passToken 优先读系统 keyring；为空则回退 DB 列（无 keyring 环境的降级存储）
            pass_token = load_api_key_secret(header) or row["pass_token"]
            if not pass_token:
                raise HTTPException(status_code=401, detail="API key credentials unavailable")
            cache_key = (row["user_id"], pass_token, row["region"])
            ctx = state.contexts.get(cache_key)
            if ctx is None:
                ctx = _build_context(state.db, state.config, *cache_key)
                state.contexts[cache_key] = ctx
            with state.db._get_connection() as conn:
                conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE key = ?",
                    (datetime.now(UTC).isoformat(), header),
                )
                conn.commit()
    if ctx is None or ctx.query_service is None:
        raise HTTPException(status_code=503, detail="Server not configured")
    return ctx


def _require_admin(request: Request) -> None:
    env_key = os.environ.get("MI_FITNESS_ADMIN_KEY")
    if env_key:
        if not _secret_equals(request.headers.get("x-admin-key"), env_key):
            raise HTTPException(status_code=401, detail="Invalid X-Admin-Key")
        return
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(
            status_code=403, detail="非本机调用管理端点需要先设置环境变量 MI_FITNESS_ADMIN_KEY"
        )


def _qs(request: Request) -> AnalyticsService:
    return _resolve_context(request).query_service


def _listing(data: list) -> dict:
    return {"status": "ok", "count": len(data), "data": data}


def _check_limit(limit: int | None) -> int | None:
    if limit is not None and limit < 0:
        raise HTTPException(status_code=400, detail="limit 不能为负数")
    return limit


# ---------------------------------------------------------------- Key 管理


class CreateKeyRequest(BaseModel):
    user_id: str
    pass_token: str
    region: str = "cn"
    label: str | None = None


def _create_key_record(
    db: Database, user_id: str, pass_token: str, region: str, label: str | None
) -> str:
    key = "mif_sk_" + secrets.token_hex(20)
    stored = store_api_key_secret(key, pass_token)  # keyring 不可用才降级写 DB 明文列
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO api_keys (key, label, user_id, pass_token, region, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (key, label, user_id, "" if stored else pass_token, region, datetime.now(UTC).isoformat()),
        )
        conn.commit()
    return key


def _mask(key: str) -> str:
    return key[:12] + "…" + key[-4:]


@app.post("/api/auth/keys")
async def create_key(body: CreateKeyRequest, request: Request) -> dict:
    """用小米凭据换取 API Key。会真实登录一次验证凭据有效性。"""
    _require_admin(request)
    state = request.app.state
    adapter = MiFitnessCloudAdapter(
        user_id=body.user_id, pass_token=body.pass_token, region=body.region or "cn"
    )
    adapter.http_timeout = state.config.http_timeout_seconds
    adapter.request_retries = state.config.request_retries
    connected = await adapter.connect()
    error = adapter.last_error
    await adapter.close()
    if not connected:
        raise HTTPException(status_code=400, detail=f"凭据验证失败：{error or 'cannot login'}")
    key = _create_key_record(state.db, body.user_id, body.pass_token, body.region or "cn", body.label)
    return {
        "api_key": key,
        "user_id": body.user_id,
        "region": body.region or "cn",
        "label": body.label,
        "note": "完整 Key 只显示这一次，请妥善保存",
    }


@app.get("/api/auth/keys")
async def list_keys(request: Request) -> dict:
    _require_admin(request)
    with request.app.state.db._get_connection() as conn:
        rows = conn.execute(
            "SELECT key, label, user_id, region, created_at, last_used_at, revoked "
            "FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
    return _listing(
        [
            {
                "key_masked": _mask(r["key"]),
                "prefix": r["key"][:16],
                "label": r["label"],
                "user_id": r["user_id"],
                "region": r["region"],
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "revoked": bool(r["revoked"]),
            }
            for r in rows
        ]
    )


@app.delete("/api/auth/keys/{key_prefix}")
async def revoke_key(key_prefix: str, request: Request) -> dict:
    """按前缀吊销。前缀至少 12 位，且必须唯一匹配，避免 `mif_` 一次吊销全部 Key。"""
    _require_admin(request)
    if len(key_prefix) < 12:
        raise HTTPException(status_code=400, detail="前缀至少 12 位（mif_sk_ 之后再取几位）")
    with request.app.state.db._get_connection() as conn:
        matches = [
            r["key"] for r in conn.execute("SELECT key FROM api_keys WHERE revoked = 0").fetchall()
            if r["key"].startswith(key_prefix)
        ]
        if not matches:
            raise HTTPException(status_code=404, detail="没有匹配的 Key")
        if len(matches) > 1:
            raise HTTPException(status_code=409, detail="前缀匹配了多个 Key，请提供更长的前缀")
        conn.execute("UPDATE api_keys SET revoked = 1 WHERE key = ?", (matches[0],))
        conn.commit()
    delete_api_key_secret(matches[0])
    return {"status": "ok", "revoked": [_mask(matches[0])]}


# ---------------------------------------------------------------- 扫码登录


def _parse_login_payload(text: str) -> dict:
    if text.startswith(LOGIN_PREFIX):
        text = text[len(LOGIN_PREFIX) :]
    return json.loads(text)


def _prune_qr_sessions(sessions: dict[str, dict]) -> None:
    now = time.time()
    for token in [t for t, s in sessions.items() if now - s["created_at"] > s["expires_in"] + 60]:
        sessions.pop(token, None)
    for token in sorted(sessions, key=lambda t: sessions[t]["created_at"])[:-QR_MAX_SESSIONS]:
        sessions.pop(token, None)


@app.post("/api/auth/qr/start")
async def qr_start(request: Request, region: str = "cn") -> dict:
    """生成小米扫码登录二维码（sid=xiaomiio，与米家/小米账号 App 扫码兼容）。"""
    _require_admin(request)
    state = request.app.state
    _prune_qr_sessions(state.qr_sessions)
    params = {
        "_qrsize": "480",
        "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
        "callback": "https://sts.api.io.mi.com/sts",
        "_hasLogo": "false",
        "sid": "xiaomiio",
        "serviceParam": "",
        "_locale": "zh_CN",
        "_dc": str(int(time.time() * 1000)),
    }
    async with httpx.AsyncClient(follow_redirects=False, timeout=20) as client:
        try:
            resp = await client.get(LOGIN_URL_ENDPOINT, params=params)
            data = _parse_login_payload(resp.text)
            if "qr" not in data or "lp" not in data:
                raise HTTPException(status_code=502, detail=f"小米未返回二维码: {list(data)[:8]}")
            png = (await client.get(data["qr"])).content
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"请求小米登录服务失败: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="小米返回了意外的登录响应") from exc

    token = uuid.uuid4().hex
    expires_in = int(data.get("timeout", 300))
    state.qr_sessions[token] = {
        "lp": data["lp"],
        "region": region or "cn",
        "created_at": time.time(),
        "expires_in": expires_in,
        "status": "waiting",
        "png": png,
    }
    return {
        "qr_token": token,
        "qr_image": f"/api/auth/qr/{token}.png",
        "login_url": data.get("loginUrl", ""),
        "expires_in": expires_in,
        "instruction": "用小米运动健康/小米账号 App 扫码，或在浏览器打开 login_url 确认；然后轮询 /api/auth/qr/poll?token=...",
    }


@app.get("/api/auth/qr/{token}.png")
async def qr_png(token: str, request: Request) -> Response:
    _require_admin(request)
    sess = request.app.state.qr_sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=404, detail="二维码不存在或已过期")
    return Response(content=sess["png"], media_type="image/png")


@app.get("/api/auth/qr/poll")
async def qr_poll(token: str, request: Request) -> dict:
    _require_admin(request)
    state = request.app.state
    sess = state.qr_sessions.get(token)
    if sess is None:
        raise HTTPException(status_code=404, detail="Unknown qr_token")
    if sess["status"] == "confirmed":
        # Key 只在确认那一次返回，之后再轮询不会重复泄露
        return {"status": "confirmed", "user_id": sess.get("user_id"), "api_key": None}
    if time.time() - sess["created_at"] > sess["expires_in"] + 10:
        sess["status"] = "expired"
        return {"status": "expired"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(sess["lp"])
        body = _parse_login_payload(resp.text) if resp.status_code == 200 else {}
    except (httpx.HTTPError, json.JSONDecodeError):
        return {"status": "waiting"}
    if not (body.get("code") == 0 and body.get("userId")):
        return {"status": "waiting"}

    user_id, pass_token = str(body["userId"]), body.get("passToken", "")
    sess.update(status="confirmed", user_id=user_id)
    if not pass_token:
        return {
            "status": "confirmed",
            "user_id": user_id,
            "api_key": None,
            "note": "小米未在扫码结果中返回 passToken，请改用 POST /api/auth/keys 手动发放",
        }
    key = _create_key_record(state.db, user_id, pass_token, sess["region"], "qr-login")
    sess.pop("png", None)
    return {"status": "confirmed", "api_key": key, "user_id": user_id}


# ---------------------------------------------------------------- 状态与同步


@app.get("/")
async def root() -> dict:
    return {
        "service": "mi-fitness-api",
        "version": __version__,
        "docs": "/docs",
        "data_types": list(DATA_TYPES),
        "metrics": list(SERIES_METRICS),
    }


@app.get("/api/status")
async def get_status(request: Request, deep: bool = True) -> dict:
    """连接状态。deep=true 时真实请求小米云做健康检查。"""
    ctx = _resolve_context(request)
    config = request.app.state.config
    if request.app.state.demo:
        return {"mode": "demo", "connected": False, "user_id": DEMO_USER_ID,
                "message": "演示模式：合成数据，未连接小米云", "sync_in_progress": False}
    adapter = ctx.adapter
    if adapter is None:
        return {"mode": "not_configured", "connected": False, "message": "Server not configured."}
    syncing = bool(ctx.sync_service and ctx.sync_service.sync_in_progress)
    if syncing or not deep:
        connected = adapter.is_connected()
    else:
        try:
            connected = await asyncio.wait_for(
                adapter.health_check(), timeout=config.health_check_timeout_seconds
            )
        except Exception:
            connected = False
    return {
        "mode": "mi_fitness_cloud",
        "region": ctx.region,
        "user_id": ctx.user_id,
        "connected": connected,
        "available_data_types": adapter.get_available_data_types() if connected else [],
        "error": None if connected else adapter.last_error,
        "sync_in_progress": syncing,
    }


class SyncRequest(BaseModel):
    data_types: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    force_full_sync: bool = False
    background: bool = False


async def _run_sync(ctx: UserContext, config, arguments: dict) -> dict:
    if ctx.sync_service is None or ctx.adapter is None:
        raise HTTPException(status_code=503, detail="Sync service not initialized")
    if not ctx.adapter.is_connected() and not await ctx.adapter.connect():
        raise HTTPException(
            status_code=502, detail=ctx.adapter.last_error or "Cannot connect to Mi Fitness cloud"
        )
    return await ctx.sync_service.sync_many(
        arguments.get("data_types"),
        arguments.get("start_date"),
        arguments.get("end_date"),
        arguments.get("force_full_sync", False),
        timeout_seconds=config.sync_type_timeout_seconds,
        sync_id=arguments["sync_id"],
    )


async def _background_sync(app: FastAPI, sync_id: str, ctx: UserContext, arguments: dict) -> None:
    entry = app.state.sync_tasks[sync_id]
    entry["status"] = "running"
    try:
        entry.update(await _run_sync(ctx, app.state.config, {**arguments, "sync_id": sync_id}))
    except HTTPException as exc:
        entry.update({"status": "error", "error": exc.detail})
    except asyncio.CancelledError:
        entry["status"] = "cancelled"
        raise
    except Exception as exc:
        entry.update({"status": "error", "error": str(exc)})
    finally:
        ctx.sync_running = False
        entry.pop("task", None)


@app.post("/api/sync")
async def post_sync(req: SyncRequest, request: Request) -> dict:
    """触发一次同步。不传日期则按增量游标/最近30天回溯；长任务建议 background=true。"""
    ctx = _resolve_context(request)
    if ctx.sync_service is None:
        raise HTTPException(status_code=503, detail="Sync service not initialized")
    if ctx.sync_running:
        raise HTTPException(status_code=409, detail="Another synchronization is in progress")
    for value, name in ((req.start_date, "start_date"), (req.end_date, "end_date")):
        if value is not None:
            validate_date(value, name)
    arguments = req.model_dump(exclude={"background"})
    ctx.sync_running = True
    if req.background:
        tasks = request.app.state.sync_tasks
        for old in [i for i, e in tasks.items() if "task" not in e][: max(0, len(tasks) - MAX_SYNC_TASKS + 1)]:
            tasks.pop(old, None)
        sync_id = str(uuid.uuid4())
        task = asyncio.create_task(_background_sync(request.app, sync_id, ctx, arguments))
        tasks[sync_id] = {"sync_id": sync_id, "status": "queued", "task": task}
        return {"status": "accepted", "sync_id": sync_id}
    try:
        return await _run_sync(ctx, request.app.state.config, {**arguments, "sync_id": str(uuid.uuid4())})
    finally:
        ctx.sync_running = False


@app.get("/api/sync/{sync_id}")
async def get_sync_status(sync_id: str, request: Request) -> dict:
    entry = request.app.state.sync_tasks.get(sync_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown sync_id")
    return {key: value for key, value in entry.items() if key != "task"}


# ---------------------------------------------------------------- 数据接口

Date = Annotated[str, Query(description="YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")]


@app.get("/api/summary")
def get_summary(request: Request, start_date: Date, end_date: Date) -> dict:
    return _listing(_qs(request).get_daily_summaries(start_date, end_date))


@app.get("/api/metric-series")
def get_metric_series(
    request: Request,
    metric: str,
    start_date: Date,
    end_date: Date,
    granularity: str = "day",
    aggregation: str | None = None,
) -> dict:
    if metric not in SERIES_METRICS:
        raise HTTPException(status_code=400, detail=f"metric 必须是 {list(SERIES_METRICS)} 之一")
    if granularity not in GRANULARITIES:
        raise HTTPException(status_code=400, detail=f"granularity 必须是 {list(GRANULARITIES)} 之一")
    if aggregation is not None and aggregation not in AGGREGATIONS:
        raise HTTPException(status_code=400, detail=f"aggregation 必须是 {list(AGGREGATIONS)} 之一")
    return _listing(_qs(request).get_metric_series(metric, start_date, end_date, granularity, aggregation))


@app.get("/api/heart-rate")
def get_heart_rate(
    request: Request, start_date: Date, end_date: Date, sample_type: str | None = None, limit: int | None = None
) -> dict:
    return _listing(_qs(request).get_heart_rate_samples(start_date, end_date, sample_type, _check_limit(limit)))


@app.get("/api/sleep")
def get_sleep(request: Request, start_date: Date, end_date: Date, include_naps: bool = True) -> dict:
    return _listing(_qs(request).get_sleep_sessions(start_date, end_date, include_naps))


@app.get("/api/workouts")
def get_workouts(
    request: Request,
    start_date: Date,
    end_date: Date,
    activity_types: Annotated[list[str] | None, Query()] = None,
    min_duration: int | None = None,
    min_distance_km: float | None = None,
) -> dict:
    return _listing(
        _qs(request).get_workouts(start_date, end_date, activity_types, min_duration, min_distance_km)
    )


@app.get("/api/body-measurements")
def get_body_measurements(
    request: Request,
    start_date: Date,
    end_date: Date,
    metrics: Annotated[list[str] | None, Query()] = None,
    latest_only: bool = False,
) -> dict:
    return _listing(_qs(request).get_body_measurements(start_date, end_date, metrics, latest_only))


@app.get("/api/spo2")
def get_spo2(request: Request, start_date: Date, end_date: Date, limit: int | None = None) -> dict:
    return _listing(_qs(request).get_spo2_samples(start_date, end_date, _check_limit(limit)))


@app.get("/api/stress")
def get_stress(
    request: Request, start_date: Date, end_date: Date, level: str | None = None, limit: int | None = None
) -> dict:
    if level and level not in STRESS_LEVELS:
        raise HTTPException(status_code=400, detail=f"level 必须是 {STRESS_LEVELS} 之一")
    return _listing(_qs(request).get_stress_samples(start_date, end_date, level, _check_limit(limit)))


@app.get("/api/abnormal-heart-beat")
def get_abnormal_heart_beat(
    request: Request, start_date: Date, end_date: Date, limit: int | None = None
) -> dict:
    return _listing(_qs(request).get_abnormal_heart_beat_events(start_date, end_date, _check_limit(limit)))


@app.get("/api/coverage")
def get_coverage(request: Request, data_types: Annotated[list[str] | None, Query()] = None) -> dict:
    return _listing(_qs(request).get_data_coverage(data_types))


# ---------------------------------------------------------------- 分析接口


@app.get("/api/overview")
def get_overview(request: Request, date: str | None = None) -> dict:
    """某天（默认今天）的健康快照：当日数据、昨晚睡眠、最新心率/血氧/压力/体重与近 7 天均值。"""
    return {"status": "ok", "data": _qs(request).get_overview(date)}


@app.get("/api/report")
def get_report(request: Request, start_date: Date, end_date: Date) -> dict:
    """周报/月报：区间指标 + 与上一等长区间的对比。"""
    return {"status": "ok", "data": _qs(request).get_period_report(start_date, end_date)}


@app.get("/api/sleep-summary")
def get_sleep_summary(request: Request, start_date: Date, end_date: Date) -> dict:
    return {"status": "ok", "data": _qs(request).get_sleep_summary(start_date, end_date)}


@app.get("/api/heart-rate-summary")
def get_heart_rate_summary(request: Request, start_date: Date, end_date: Date) -> dict:
    return {"status": "ok", "data": _qs(request).get_heart_rate_summary(start_date, end_date)}


@app.get("/api/records")
def get_records(request: Request, step_goal: int = Query(8000, ge=1)) -> dict:
    """历史个人纪录与步数目标连续达成天数。"""
    return {"status": "ok", "data": _qs(request).get_personal_records(step_goal)}


@app.get("/api/gaps")
def get_gaps(
    request: Request,
    start_date: Date,
    end_date: Date,
    data_types: Annotated[list[str] | None, Query()] = None,
) -> dict:
    """区间内缺数据的日期（合并为连续区间）。"""
    return _listing(_qs(request).find_data_gaps(start_date, end_date, data_types))


# ---------------------------------------------------------------- 导出


def _to_csv(rows: list[dict[str, Any]]) -> str:
    """把查询结果扁平化为 CSV（嵌套结构转 JSON 字符串，加 BOM 便于 Excel 打开中文）。"""
    fieldnames: list[str] = []
    for row in rows:
        fieldnames.extend(name for name in row if name not in fieldnames)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
                for k, v in row.items()
            }
        )
    return "\ufeff" + buf.getvalue()


EXPORT_SOURCES = {
    "daily_activity": AnalyticsService.get_daily_summaries,
    "heart_rate": AnalyticsService.get_heart_rate_samples,
    "sleep": AnalyticsService.get_sleep_sessions,
    "workouts": AnalyticsService.get_workouts,
    "body_measurements": AnalyticsService.get_body_measurements,
    "spo2": AnalyticsService.get_spo2_samples,
    "stress": AnalyticsService.get_stress_samples,
    "abnormal_heart_beat": AnalyticsService.get_abnormal_heart_beat_events,
}


@app.get("/api/export", response_model=None)
def export_data(
    request: Request, data_type: str, start_date: Date, end_date: Date, format: str = "json"
) -> Response | dict:
    """导出本地数据（JSON 或 CSV）。仅读本地 SQLite，不触发任何网络请求。"""
    if data_type not in EXPORT_SOURCES:
        raise HTTPException(status_code=400, detail=f"data_type 必须是 {sorted(EXPORT_SOURCES)} 之一")
    if format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="format 必须是 json 或 csv")
    data = EXPORT_SOURCES[data_type](_qs(request), start_date, end_date)
    if format == "json":
        return _listing(data)
    return Response(
        content=_to_csv(data),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="mi_fitness_{data_type}_{start_date}_{end_date}.csv"'
        },
    )
