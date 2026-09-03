"""HTTP API for Mi Fitness data (FastAPI).

把 MCP Server 的同步/查询能力以 REST 形式暴露，供其他程序直接调用。

启动：mi-fitness-mcp api [--host 127.0.0.1] [--port 8321]

鉴权模型（类大模型平台）：
- 默认凭据：来自本地配置（config.json + keyring），不带头直接访问即可用（仅建议本机）。
- 静态 Key：设置环境变量 MI_FITNESS_API_KEY 后，所有请求必须带 X-API-Key。
- 发放 Key：管理员调 POST /api/auth/keys，用 userId+passToken 换取 mif_sk_* 形式的
  API Key；调用方之后只带 Key，不接触小米凭据。Key 记录在主库 api_keys 表。
- 管理端点（发放/列表/吊销/扫码登录）需要 X-Admin-Key 匹配环境变量
  MI_FITNESS_ADMIN_KEY；未设置该变量时仅允许 127.0.0.1 本机调用。

扫码登录（逆向自 account.xiaomi.com 通用 QR 流程，参考 python-miio cloud_qr.py）：
POST /api/auth/qr/start 生成二维码 → 用户用小米系 App 扫码或在浏览器打开
login_url 确认 → GET /api/auth/qr/poll 轮询到 confirmed 并自动发放 API Key。
"""

import asyncio
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
from fastapi.responses import Response
from pydantic import BaseModel

from mi_fitness_mcp.adapters.mi_fitness_cloud import MiFitnessCloudAdapter
from mi_fitness_mcp.auth import load_mi_fitness_token
from mi_fitness_mcp.config import load_config
from mi_fitness_mcp.security import (
    cors_origins_from_env,
    default_allowed_hosts,
    delete_api_key_secret,
    host_allowed,
    load_api_key_secret,
    store_api_key_secret,
)
from mi_fitness_mcp.services.query_service import QueryService
from mi_fitness_mcp.services.sync_service import SyncService
from mi_fitness_mcp.storage import Database

DATA_TYPES = [
    "daily_activity",
    "heart_rate",
    "body_measurements",
    "sleep",
    "workouts",
    "spo2",
    "stress",
    "abnormal_heart_beat",
]

METRICS = ["steps", "distance_m", "active_kcal"]
GRANULARITIES = ["day", "week", "month"]
AGGREGATIONS = ["sum", "avg", "min", "max", "latest"]
STRESS_LEVELS = ["low", "medium", "high"]

LOGIN_URL_ENDPOINT = "https://account.xiaomi.com/longPolling/loginUrl"
LOGIN_PREFIX = "&&&START&&&"

# ---------------------------------------------------------------- 用户上下文


@dataclass
class UserContext:
    """一套小米凭据对应的运行时上下文（适配器/服务/同步状态），按凭据缓存。"""

    user_id: str
    pass_token: str
    region: str
    adapter: MiFitnessCloudAdapter | None = None
    sync_service: SyncService | None = None
    query_service: QueryService | None = None
    sync_running: bool = False


def _build_context(db: Database, config, user_id: str, pass_token: str, region: str) -> UserContext:
    adapter = MiFitnessCloudAdapter(user_id=user_id, pass_token=pass_token, region=region)
    adapter.http_timeout = config.http_timeout_seconds
    adapter.request_retries = config.request_retries
    adapter.max_pages = config.max_pages
    return UserContext(
        user_id=user_id,
        pass_token=pass_token,
        region=region,
        adapter=adapter,
        sync_service=SyncService(
            adapter, db, config.default_lookback_days, config.sync_chunk_days
        ),
        query_service=QueryService(db, user_id),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    db = Database(config.database_path)
    with db._get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                label TEXT,
                user_id TEXT NOT NULL,
                pass_token TEXT NOT NULL,
                region TEXT NOT NULL DEFAULT 'cn',
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # 迁移：历史版本把 passToken 明文存在该表，启动时移入系统 keyring 并清空列
        legacy = conn.execute(
            "SELECT key, pass_token FROM api_keys WHERE pass_token != ''"
        ).fetchall()
        for row in legacy:
            if store_api_key_secret(row["key"], row["pass_token"]):
                conn.execute("UPDATE api_keys SET pass_token = '' WHERE key = ?", (row["key"],))
        # 已吊销 Key 的 keyring 密钥一并清理（历史版本吊销时尚无 keyring 概念）
        revoked_keys = [
            r["key"] for r in conn.execute("SELECT key FROM api_keys WHERE revoked = 1").fetchall()
        ]
        conn.commit()
        for k in revoked_keys:
            delete_api_key_secret(k)

    default_context = None
    if config.mode == "mi_fitness_cloud":
        user_id, pass_token = load_mi_fitness_token()
        if user_id and pass_token:
            default_context = _build_context(db, config, user_id, pass_token, config.region)

    app.state.config = config
    app.state.db = db
    app.state.default_context = default_context
    app.state.contexts = {}
    app.state.sync_tasks = {}
    app.state.qr_sessions = {}
    try:
        yield
    finally:
        tasks = [entry.get("task") for entry in app.state.sync_tasks.values() if entry.get("task")]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        contexts = list(app.state.contexts.values())
        if app.state.default_context:
            contexts.append(app.state.default_context)
        for ctx in contexts:
            if ctx.adapter:
                await ctx.adapter.close()


async def _gate(x_api_key: str | None = Header(default=None)) -> None:
    """入口闸门：环境静态 Key 设置时强制带头；发了 Key 则校验有效性。"""
    env_key = os.environ.get("MI_FITNESS_API_KEY")
    if x_api_key is None:
        if env_key:
            raise HTTPException(status_code=401, detail="Missing X-API-Key header")
        return
    if env_key and x_api_key == env_key:
        return
    db: Database = app.state.db
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT revoked FROM api_keys WHERE key = ?", (x_api_key,)
        ).fetchone()
    if row is None or row["revoked"]:
        raise HTTPException(status_code=401, detail="Invalid API key")


app = FastAPI(
    title="Mi Fitness API",
    version="0.2.1",
    description="小米运动健康数据 REST API（多 Key 鉴权 + 扫码登录 + 本地 SQLite 缓存）",
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
            host = ""
            for name, value in scope.get("headers", []):
                if name == b"host":
                    host = value.decode("latin-1")
                    break
            if not host_allowed(host, self.allowed_hosts):
                body = b'{"detail": "Host not allowed"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


app.add_middleware(HostAllowlistMiddleware)


def _resolve_context(request: Request) -> UserContext:
    """根据 X-API-Key 解析出对应用户上下文；静态 Key/无头 → 默认凭据。"""
    state = request.app.state
    header = request.headers.get("x-api-key")
    ctx = state.default_context
    if header and header != os.environ.get("MI_FITNESS_API_KEY"):
        db: Database = state.db
        with db._get_connection() as conn:
            row = conn.execute(
                "SELECT user_id, pass_token, region FROM api_keys WHERE key = ? AND revoked = 0",
                (header,),
            ).fetchone()
        if row is not None:
            # passToken 优先从系统 keyring 读取；为空则回退 DB 列（无 keyring 环境的降级存储）
            pass_token = load_api_key_secret(header) or row["pass_token"]
            if not pass_token:
                raise HTTPException(status_code=401, detail="API key credentials unavailable")
            cache_key = (row["user_id"], pass_token, row["region"])
            ctx = state.contexts.get(cache_key)
            if ctx is None:
                ctx = _build_context(state.db, state.config, *cache_key)
                state.contexts[cache_key] = ctx
            with db._get_connection() as conn:
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
    provided = request.headers.get("x-admin-key")
    if env_key:
        if provided != env_key:
            raise HTTPException(status_code=401, detail="Invalid X-Admin-Key")
        return
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(
            status_code=403,
            detail="非本机调用管理端点需要先设置环境变量 MI_FITNESS_ADMIN_KEY",
        )


def _validate_date(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} 必须为 YYYY-MM-DD 格式") from exc
    return value


def _validate_limit(limit: int | None) -> int | None:
    if limit is not None and limit < 0:
        raise HTTPException(status_code=400, detail="limit 不能为负数")
    return limit


def _query_service(request: Request) -> QueryService:
    return _resolve_context(request).query_service


# ---------------------------------------------------------------- Key 管理


class CreateKeyRequest(BaseModel):
    user_id: str
    pass_token: str
    region: str = "cn"
    label: str | None = None


def _create_key_record(db: Database, user_id: str, pass_token: str, region: str, label: str | None) -> str:
    key = "mif_sk_" + secrets.token_hex(20)
    # passToken 存系统 keyring（加密）；keyring 不可用才降级写 DB 明文列
    stored = store_api_key_secret(key, pass_token)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO api_keys (key, label, user_id, pass_token, region, created_at) VALUES (?,?,?,?,?,?)",
            (key, label, user_id, "" if stored else pass_token, region, datetime.now(UTC).isoformat()),
        )
        conn.commit()
    return key


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
        raise HTTPException(
            status_code=400,
            detail=f"凭据验证失败：{error or 'cannot login'}",
        )
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
    db: Database = request.app.state.db
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT key, label, user_id, region, created_at, last_used_at, revoked FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
    items = []
    for r in rows:
        k = r["key"]
        items.append(
            {
                "key_masked": k[:12] + "…" + k[-4:],
                "label": r["label"],
                "user_id": r["user_id"],
                "region": r["region"],
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "revoked": bool(r["revoked"]),
            }
        )
    return {"status": "ok", "count": len(items), "data": items}


@app.delete("/api/auth/keys/{key_prefix}")
async def revoke_key(key_prefix: str, request: Request) -> dict:
    _require_admin(request)
    db: Database = request.app.state.db
    with db._get_connection() as conn:
        rows = conn.execute("SELECT key FROM api_keys").fetchall()
        matches = [r["key"] for r in rows if r["key"] == key_prefix or r["key"].startswith(key_prefix)]
        if not matches:
            raise HTTPException(status_code=404, detail="没有匹配的 Key（可用完整 Key 或唯一前缀）")
        for k in matches:
            conn.execute("UPDATE api_keys SET revoked = 1 WHERE key = ?", (k,))
            delete_api_key_secret(k)
        conn.commit()
    return {"status": "ok", "revoked": matches}


# ---------------------------------------------------------------- 扫码登录


@app.post("/api/auth/qr/start")
async def qr_start(request: Request, region: str = "cn") -> dict:
    """生成小米扫码登录二维码。

    采用小米通用扫码流程（sid=xiaomiio，与米家/小米账号 App 扫码兼容），
    扫码确认后从长轮询结果取账号级 passToken，再校验并发放 API Key。
    """
    _require_admin(request)
    state = request.app.state
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
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"请求小米登录服务失败: {exc}") from exc
        text = resp.text
        if text.startswith(LOGIN_PREFIX):
            text = text[len(LOGIN_PREFIX) :]
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="小米返回了意外的登录响应") from exc
        if "qr" not in data or "lp" not in data:
            raise HTTPException(status_code=502, detail=f"小米未返回二维码: {list(data)[:8]}")
        png_resp = await client.get(data["qr"])
        png = png_resp.content

    token = uuid.uuid4().hex
    state.qr_sessions[token] = {
        "lp": data["lp"],
        "login_url": data.get("loginUrl", ""),
        "region": region or "cn",
        "created_at": time.time(),
        "expires_in": int(data.get("timeout", 300)),
        "status": "waiting",
        "png": png,
    }
    return {
        "qr_token": token,
        "qr_image": f"/api/auth/qr/{token}.png",
        "login_url": data.get("loginUrl", ""),
        "expires_in": int(data.get("timeout", 300)),
        "instruction": "用小米运动健康/小米账号 App 扫码，或在浏览器打开 login_url 确认；然后轮询 /api/auth/qr/poll?token=...",
    }


@app.get("/api/auth/qr/{token}.png")
async def qr_png(token: str, request: Request) -> Response:
    _require_admin(request)
    sess = request.app.state.qr_sessions.get(token)
    if sess is None or "png" not in sess:
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
        return {"status": "confirmed", "api_key": sess.get("api_key"), "user_id": sess.get("user_id")}
    if time.time() - sess["created_at"] > sess["expires_in"] + 10:
        sess["status"] = "expired"
        return {"status": "expired"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(sess["lp"])
    except httpx.HTTPError:
        return {"status": "waiting"}
    if resp.status_code != 200:
        return {"status": "waiting"}
    text = resp.text
    if text.startswith(LOGIN_PREFIX):
        text = text[len(LOGIN_PREFIX) :]
    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return {"status": "waiting"}
    if not (body.get("code") == 0 and body.get("userId")):
        return {"status": "waiting"}

    user_id = str(body["userId"])
    pass_token = body.get("passToken", "")
    if not pass_token:
        sess["status"] = "confirmed"
        return {
            "status": "confirmed",
            "user_id": user_id,
            "api_key": None,
            "note": "小米未在扫码结果中返回 passToken，请改用 POST /api/auth/keys 手动发放",
        }
    key = _create_key_record(state.db, user_id, pass_token, sess["region"], "qr-login")
    sess["status"] = "confirmed"
    sess["api_key"] = key
    sess["user_id"] = user_id
    return {"status": "confirmed", "api_key": key, "user_id": user_id}


# ---------------------------------------------------------------- 数据接口


@app.get("/")
async def root() -> dict:
    return {
        "service": "mi-fitness-api",
        "docs": "/docs",
        "data_types": DATA_TYPES,
        "sync": "POST /api/sync",
        "status": "GET /api/status",
        "auth": "POST /api/auth/keys (admin) | POST /api/auth/qr/start (admin)",
    }


@app.get("/api/status")
async def get_status(request: Request, deep: bool = True) -> dict:
    """连接状态。deep=true 时真实请求小米云做健康检查。"""
    ctx = _resolve_context(request)
    config = request.app.state.config
    adapter = ctx.adapter
    if adapter is None:
        return {"mode": "not_configured", "connected": False, "message": "Server not configured."}
    if ctx.sync_service and ctx.sync_service.sync_in_progress:
        connected = adapter.is_connected()
    elif deep:
        try:
            connected = await asyncio.wait_for(
                adapter.health_check(), timeout=config.health_check_timeout_seconds
            )
        except Exception:
            connected = False
    else:
        connected = adapter.is_connected()
    return {
        "mode": "mi_fitness_cloud",
        "region": ctx.region,
        "user_id": ctx.user_id,
        "connected": connected,
        "available_data_types": adapter.get_available_data_types() if connected else [],
        "error": None if connected else adapter.last_error,
        "sync_in_progress": bool(ctx.sync_service and ctx.sync_service.sync_in_progress),
    }


class SyncRequest(BaseModel):
    data_types: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    force_full_sync: bool = False
    background: bool = False


async def _run_sync(ctx: UserContext, config, arguments: dict) -> dict:
    sync_service = ctx.sync_service
    adapter = ctx.adapter
    if sync_service is None or adapter is None:
        raise HTTPException(status_code=503, detail="Sync service not initialized")
    if not adapter.is_connected() and not await adapter.connect():
        raise HTTPException(
            status_code=502, detail=adapter.last_error or "Cannot connect to Mi Fitness cloud"
        )
    supported = set(adapter.get_available_data_types())
    data_types = arguments.get("data_types") or sorted(supported)
    unknown = sorted(set(data_types) - supported)
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unsupported data types: {', '.join(unknown)}"
        )
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
        except TimeoutError:
            details.append({"data_type": data_type, "status": "error", "error": "sync timed out"})
            continue
        except Exception as exc:
            details.append({"data_type": data_type, "status": "error", "error": str(exc)})
            continue
        details.append(
            {
                "data_type": data_type,
                "status": result.get("status", "ok"),
                "added": result.get("added", 0),
                "updated": result.get("updated", 0),
                "skipped": result.get("skipped", 0),
                "duration_seconds": (datetime.now(UTC) - type_started).total_seconds(),
            }
        )
        for key in totals:
            totals[key] += result.get(key, 0)
    succeeded = [d["data_type"] for d in details if d["status"] == "ok"]
    has_partial = any(d["status"] == "partial" for d in details)
    status = (
        "ok"
        if len(succeeded) == len(details)
        else "partial"
        if succeeded or has_partial
        else "error"
    )
    return {
        "status": status,
        "sync_id": arguments["sync_id"],
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "records_added": totals["added"],
        "records_updated": totals["updated"],
        "records_skipped": totals["skipped"],
        "data_types_synced": succeeded,
        "results": details,
    }


async def _background_sync(app: FastAPI, sync_id: str, ctx: UserContext, arguments: dict) -> None:
    entry = app.state.sync_tasks[sync_id]
    entry["status"] = "running"
    try:
        result = await _run_sync(ctx, app.state.config, {**arguments, "sync_id": sync_id})
        entry.update(result)
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
    _validate_date(req.start_date, "start_date")
    _validate_date(req.end_date, "end_date")
    arguments = {
        "data_types": req.data_types,
        "start_date": req.start_date,
        "end_date": req.end_date,
        "force_full_sync": req.force_full_sync,
    }
    if req.background:
        ctx.sync_running = True
        sync_id = str(uuid.uuid4())
        task = asyncio.create_task(_background_sync(request.app, sync_id, ctx, arguments))
        request.app.state.sync_tasks[sync_id] = {"sync_id": sync_id, "status": "queued", "task": task}
        return {"status": "accepted", "sync_id": sync_id}
    ctx.sync_running = True
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


@app.get("/api/summary")
def get_summary(request: Request, start_date: str, end_date: str) -> dict:
    data = _query_service(request).get_daily_summaries(
        _validate_date(start_date, "start_date"), _validate_date(end_date, "end_date")
    )
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/metric-series")
def get_metric_series(
    request: Request,
    metric: str,
    start_date: str,
    end_date: str,
    granularity: str = "day",
    aggregation: str = "sum",
) -> dict:
    if metric not in METRICS:
        raise HTTPException(status_code=400, detail=f"metric 必须是 {METRICS} 之一")
    if granularity not in GRANULARITIES:
        raise HTTPException(status_code=400, detail=f"granularity 必须是 {GRANULARITIES} 之一")
    if aggregation not in AGGREGATIONS:
        raise HTTPException(status_code=400, detail=f"aggregation 必须是 {AGGREGATIONS} 之一")
    data = _query_service(request).get_metric_series(
        metric=metric,
        start_date=_validate_date(start_date, "start_date"),
        end_date=_validate_date(end_date, "end_date"),
        granularity=granularity,
        aggregation=aggregation,
    )
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/heart-rate")
def get_heart_rate(
    request: Request,
    start_date: str,
    end_date: str,
    sample_type: str | None = None,
    limit: int | None = None,
) -> dict:
    data = _query_service(request).get_heart_rate_samples(
        start_date=_validate_date(start_date, "start_date"),
        end_date=_validate_date(end_date, "end_date"),
        sample_type=sample_type,
        limit=_validate_limit(limit),
    )
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/sleep")
def get_sleep(
    request: Request, start_date: str, end_date: str, include_naps: bool = True
) -> dict:
    data = _query_service(request).get_sleep_sessions(
        start_date=_validate_date(start_date, "start_date"),
        end_date=_validate_date(end_date, "end_date"),
        include_naps=include_naps,
    )
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/workouts")
def get_workouts(
    request: Request,
    start_date: str,
    end_date: str,
    activity_types: Annotated[list[str] | None, Query()] = None,
    min_duration: int | None = None,
    min_distance_km: float | None = None,
) -> dict:
    data = _query_service(request).get_workouts(
        start_date=_validate_date(start_date, "start_date"),
        end_date=_validate_date(end_date, "end_date"),
        activity_types=activity_types,
        min_duration=min_duration,
        min_distance_km=min_distance_km,
    )
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/body-measurements")
def get_body_measurements(
    request: Request,
    start_date: str,
    end_date: str,
    metrics: Annotated[list[str] | None, Query()] = None,
    latest_only: bool = False,
) -> dict:
    data = _query_service(request).get_body_measurements(
        start_date=_validate_date(start_date, "start_date"),
        end_date=_validate_date(end_date, "end_date"),
        metrics=metrics,
    )
    if latest_only and data:
        data = [data[-1]]
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/spo2")
def get_spo2(request: Request, start_date: str, end_date: str, limit: int | None = None) -> dict:
    data = _query_service(request).get_spo2_samples(
        start_date=_validate_date(start_date, "start_date"),
        end_date=_validate_date(end_date, "end_date"),
        limit=_validate_limit(limit),
    )
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/stress")
def get_stress(
    request: Request, start_date: str, end_date: str, level: str | None = None, limit: int | None = None
) -> dict:
    if level and level not in STRESS_LEVELS:
        raise HTTPException(status_code=400, detail=f"level 必须是 {STRESS_LEVELS} 之一")
    data = _query_service(request).get_stress_samples(
        start_date=_validate_date(start_date, "start_date"),
        end_date=_validate_date(end_date, "end_date"),
        level=level,
        limit=_validate_limit(limit),
    )
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/abnormal-heart-beat")
def get_abnormal_heart_beat(
    request: Request, start_date: str, end_date: str, limit: int | None = None
) -> dict:
    data = _query_service(request).get_abnormal_heart_beat_events(
        start_date=_validate_date(start_date, "start_date"),
        end_date=_validate_date(end_date, "end_date"),
        limit=_validate_limit(limit),
    )
    return {"status": "ok", "count": len(data), "data": data}


@app.get("/api/coverage")
def get_coverage(
    request: Request, data_types: Annotated[list[str] | None, Query()] = None
) -> dict:
    data = _query_service(request).get_data_coverage(data_types)
    return {"status": "ok", "count": len(data), "data": data}


def _to_csv(rows: list[dict[str, Any]]) -> str:
    """把查询结果扁平化为 CSV（嵌套结构转 JSON 字符串，加 BOM 便于 Excel 打开中文）。"""
    import csv
    import io

    fieldnames: list[str] = []
    for row in rows:
        for name in row:
            if name not in fieldnames:
                fieldnames.append(name)
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
    "daily_activity": lambda qs, s, e: qs.get_daily_summaries(s, e),
    "heart_rate": lambda qs, s, e: qs.get_heart_rate_samples(s, e),
    "sleep": lambda qs, s, e: qs.get_sleep_sessions(s, e),
    "workouts": lambda qs, s, e: qs.get_workouts(s, e),
    "body_measurements": lambda qs, s, e: qs.get_body_measurements(s, e),
    "spo2": lambda qs, s, e: qs.get_spo2_samples(s, e),
    "stress": lambda qs, s, e: qs.get_stress_samples(s, e),
    "abnormal_heart_beat": lambda qs, s, e: qs.get_abnormal_heart_beat_events(s, e),
}


@app.get("/api/export", response_model=None)
def export_data(
    request: Request,
    data_type: str,
    start_date: str,
    end_date: str,
    format: str = "json",
) -> Response | dict:
    """导出本地数据（JSON 或 CSV）。仅读本地 SQLite，不触发任何网络请求。"""
    if data_type not in EXPORT_SOURCES:
        raise HTTPException(status_code=400, detail=f"data_type 必须是 {sorted(EXPORT_SOURCES)} 之一")
    if format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="format 必须是 json 或 csv")
    data = EXPORT_SOURCES[data_type](
        _query_service(request),
        _validate_date(start_date, "start_date"),
        _validate_date(end_date, "end_date"),
    )
    if format == "csv":
        return Response(
            content=_to_csv(data),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="mi_fitness_{data_type}_{start_date}_{end_date}.csv"'
                )
            },
        )
    return {"status": "ok", "count": len(data), "data": data}
