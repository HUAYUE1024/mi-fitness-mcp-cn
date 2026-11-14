"""CLI for Mi Fitness MCP."""

import argparse
import asyncio
import contextlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from mi_fitness_mcp import __version__
from mi_fitness_mcp.adapters.mi_fitness_cloud import MiFitnessCloudAdapter
from mi_fitness_mcp.auth import load_mi_fitness_token, save_mi_fitness_token
from mi_fitness_mcp.config import Config, get_config_path, load_config, save_config
from mi_fitness_mcp.models import DATA_TYPES
from mi_fitness_mcp.services.analytics import AnalyticsService
from mi_fitness_mcp.services.sync_service import SyncService
from mi_fitness_mcp.storage import Database

PROGRAM_NAME = "mi-fitness-mcp"
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _fail(message: str, hint: str | None = None) -> None:
    print(f"❌ {message}")
    if hint:
        print(f"   {hint}")
    sys.exit(1)


# ------------------------------------------------------------------ context


def _open_service(args) -> tuple[AnalyticsService, str]:
    """返回分析服务与数据来源说明。--demo 用合成数据，否则读本机库中当前账号的数据。"""
    if getattr(args, "demo", False):
        from mi_fitness_mcp.demo import DEMO_USER_ID, demo_database

        return AnalyticsService(demo_database(), DEMO_USER_ID), "演示数据"
    config = load_config()
    user_id, _ = load_mi_fitness_token()
    if not user_id:
        _fail("未找到账号凭据", f"先运行 `{PROGRAM_NAME} setup`，或加 --demo 看看演示数据")
    return AnalyticsService(Database(config.database_path), user_id), f"账号 {user_id}"


# ------------------------------------------------------------------ setup / doctor / sync


async def _check_adapter_health(adapter) -> tuple[bool, list[str], str | None]:
    try:
        connected = await adapter.connect()
        data_types = adapter.get_available_data_types() if connected else []
        return connected, data_types, getattr(adapter, "region", None)
    finally:
        await adapter.close()


def cmd_setup(args):
    if args.mode == "mi_fitness_cloud" and args.user_id and args.pass_token:
        user_id, pass_token, region = args.user_id, args.pass_token, args.region or "cn"
    else:
        print(f"{PROGRAM_NAME} - 配置向导\n{'=' * 50}\n")
        user_id = input("Mi Fitness user_id： ").strip()
        # getpass 不回显，passToken 不会留在终端滚动缓冲区里
        import getpass

        pass_token = getpass.getpass("Mi Fitness passToken（输入不可见）： ").strip()
        region = input("区域 [cn]： ").strip() or "cn"
    if not user_id or not pass_token:
        _fail("必须提供 user_id 和 passToken")
    save_mi_fitness_token(user_id, pass_token)
    save_config(Config(mode="mi_fitness_cloud", region=region))
    print(f"✅ 配置已保存（User ID: {user_id}，区域: {region}）")
    print(f"   下一步：{PROGRAM_NAME} doctor  验证连通性")


def cmd_doctor(args):
    print(f"{PROGRAM_NAME} {__version__} - 诊断\n{'=' * 50}\n")
    config_path = get_config_path()
    print(f"配置文件： {config_path}")
    if not config_path.exists():
        _fail("未找到配置文件", f"请运行： {PROGRAM_NAME} setup")
    try:
        config = load_config()
        print(f"✅ 配置已加载（模式 {config.mode}，区域 {config.region}）")
        user_id, pass_token = load_mi_fitness_token()
        if user_id and pass_token:
            print(f"✅ 已找到 Mi Fitness 凭据（User ID: {user_id}）")
            adapter = MiFitnessCloudAdapter(user_id=user_id, pass_token=pass_token, region=config.region)
            connected, data_types, _ = asyncio.run(_check_adapter_health(adapter))
            print(f"   连接状态： {'✅ 已连接' if connected else '❌ 连接失败 — ' + str(adapter.last_error)}")
            if connected:
                print(f"   可同步的数据类型： {', '.join(data_types)}")
        else:
            print(f"❌ 未找到 Mi Fitness 凭据，请运行： {PROGRAM_NAME} setup")
        print(f"\n数据库： {config.database_path}")
        if config.database_path.exists():
            Database(config.database_path)
            print("✅ 数据库可用")
        else:
            print("ℹ️  数据库将在首次同步时创建")
    except Exception as e:
        _fail(f"诊断失败： {e}")


async def cmd_sync_async(args):
    print(f"{PROGRAM_NAME} - 同步\n{'=' * 50}\n")
    config = load_config()
    if config.mode == "not_configured":
        _fail("服务尚未配置", f"请运行： {PROGRAM_NAME} setup")
    user_id, pass_token = load_mi_fitness_token()
    if not user_id or not pass_token:
        _fail("未找到 Mi Fitness 凭据", f"请运行： {PROGRAM_NAME} setup")

    adapter = MiFitnessCloudAdapter(user_id=user_id, pass_token=pass_token, region=config.region)
    adapter.http_timeout = config.http_timeout_seconds
    adapter.request_retries = config.request_retries
    adapter.max_pages = config.max_pages
    if not await adapter.connect():
        _fail("无法连接到 Mi Fitness API", adapter.last_error)
    service = SyncService(
        adapter, Database(config.database_path), config.default_lookback_days, config.sync_chunk_days
    )

    def progress(event: dict) -> None:
        mark = "✓" if event.get("status") == "ok" else "✗"
        print(f"   {mark} {event['data_type']:<20} {event['start_date']} ~ {event['end_date']}", flush=True)

    try:
        result = await service.sync_many(
            [args.type] if args.type else None,
            args.start_date,
            args.end_date,
            args.full,
            timeout_seconds=config.sync_type_timeout_seconds,
            on_progress=progress if args.verbose else None,
        )
    finally:
        await adapter.close()
    for item in result["results"]:
        if item["status"] == "ok":
            print(f"✅ {item['data_type']}: 新增 {item['added']}，更新 {item['updated']}，未变 {item['skipped']}")
        else:
            print(f"❌ {item['data_type']}: {item.get('error', item['status'])}")
    print(f"\n同步完成：新增 {result['records_added']}，更新 {result['records_updated']}（{result['status']}）")
    if result["status"] != "ok":
        sys.exit(2)


def cmd_sync(args):
    asyncio.run(cmd_sync_async(args))


# ------------------------------------------------------------------ read-only commands


def cmd_status(args):
    """本地库概况：每类数据的记录数与覆盖范围，不联网。"""
    service, source = _open_service(args)
    coverage = {c["data_type"]: c for c in service.get_data_coverage()}
    if args.json:
        return _print_json({"source": source, "coverage": list(coverage.values())})
    print(f"{PROGRAM_NAME} {__version__} - 本地数据（{source}）\n{'=' * 50}")
    if not coverage:
        print("本地库还是空的。运行同步，或加 --demo 查看演示数据：")
        print(f"   {PROGRAM_NAME} sync --start-date 2026-01-01")
        return
    print(f"{'类型':<22}{'记录数':>10}  {'有数据天数':>8}  覆盖范围")
    for data_type in DATA_TYPES:
        if c := coverage.get(data_type):
            print(f"{data_type:<22}{c['record_count']:>10}  {c['days_with_data']:>8}  {c['first_date']} ~ {c['last_date']}")
        else:
            print(f"{data_type:<22}{'—':>10}")


def _fmt(value, suffix="", empty="—"):
    return empty if value is None else f"{value:,}{suffix}" if isinstance(value, int) else f"{value}{suffix}"


def cmd_overview(args):
    service, source = _open_service(args)
    data = service.get_overview(args.date)
    if args.json:
        return _print_json(data)
    today, night, week = data["today"] or {}, data["sleep_last_night"], data["last_7_days"]
    weight = data["latest_weight"]
    print(f"📅 {data['date']} 健康快照（{source}）\n{'=' * 50}")
    print(f"👣 步数      {_fmt(today.get('steps'))}   距离 {_fmt(round(today['distance_m'] / 1000, 2) if today else None, ' km')}   活动消耗 {_fmt(round(today['active_kcal']) if today else None, ' kcal')}")
    if night:
        print(f"😴 昨晚睡眠  {night['time_asleep_minutes'] // 60}h{night['time_asleep_minutes'] % 60:02d}m   评分 {_fmt(night.get('sleep_score'))}")
    else:
        print("😴 昨晚睡眠  —")
    print(f"❤️  心率      静息 {_fmt(data['resting_heart_rate'], ' bpm')}   最近 {_fmt(data['latest_heart_rate'], ' bpm')}")
    print(f"🫁 血氧 {_fmt(data['latest_spo2'], '%')}   🧠 压力 {_fmt(data['latest_stress'])}")
    if weight:
        print(f"⚖️  体重      {weight['weight_kg']} kg   BMI {_fmt(weight['bmi'])}   体脂 {_fmt(weight['body_fat_pct'], '%')}")
    print(f"\n近 7 天：日均 {_fmt(week['avg_steps'])} 步，最佳 {_fmt(week['best_steps'])} 步，{week['days_with_data']} 天有数据，运动 {week['workouts']} 次")


def _arrow(change):
    if change is None:
        return ""
    return f"  ({'▲' if change > 0 else '▼' if change < 0 else '＝'}{abs(change)}%)"


def cmd_report(args):
    service, source = _open_service(args)
    end = args.end_date or _today()
    start = args.start_date or (
        _days_ago(29) if args.period == "month" else (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    )
    report = service.get_period_report(start, end)
    if args.json:
        return _print_json(report)
    m, c, sleep = report["metrics"], report["change_pct"], report["sleep"]
    p = report["period"]
    print(f"📊 {p['start_date']} ~ {p['end_date']}（{p['days']} 天，{source}）\n   对比上一期 {report['previous_period']['start_date']} ~ {report['previous_period']['end_date']}\n{'=' * 50}")
    rows = [
        ("总步数", m["total_steps"], "", "total_steps"),
        ("日均步数", m["avg_daily_steps"], "", "avg_daily_steps"),
        ("总距离", m["total_distance_km"], " km", "total_distance_km"),
        ("活动消耗", m["total_active_kcal"], " kcal", "total_active_kcal"),
        ("平均睡眠", m["avg_sleep_minutes"], " 分钟", "avg_sleep_minutes"),
        ("平均心率", m["avg_heart_rate"], " bpm", "avg_heart_rate"),
        ("平均静息心率", m["avg_resting_heart_rate"], " bpm", "avg_resting_heart_rate"),
        ("平均血氧", m["avg_spo2"], "%", "avg_spo2"),
        ("平均压力", m["avg_stress"], "", "avg_stress"),
        ("运动次数", m["workout_count"], " 次", "workout_count"),
        ("运动时长", m["workout_minutes"], " 分钟", "workout_minutes"),
        ("体重变化", m["weight_kg_change"], " kg", None),
    ]
    for label, value, suffix, key in rows:
        print(f"{label:<10}{_fmt(value, suffix):>14}{_arrow(c.get(key)) if key else ''}")
    if sleep.get("nights"):
        print(f"\n😴 平均 {sleep['avg_bedtime']} 入睡 / {sleep['avg_wake_time']} 起床，入睡时间波动 ±{sleep['bedtime_variability_minutes']} 分钟，{sleep['short_nights_under_6h']} 晚睡不足 6 小时")


def cmd_records(args):
    service, source = _open_service(args)
    data = service.get_personal_records(args.step_goal)
    if args.json:
        return _print_json(data)
    print(f"🏆 个人纪录（{source}）\n{'=' * 50}")
    for label, key, fmt in (
        ("单日最多步数", "most_steps_day", lambda r: f"{r['steps']:,} 步（{r['date']}）"),
        ("最长运动", "longest_workout", lambda r: f"{r['duration_minutes']} 分钟 {r['activity_type']}（{r['start_at'][:10]}）"),
        ("最远运动", "farthest_workout", lambda r: f"{r['distance_m'] / 1000:.2f} km {r['activity_type']}（{r['start_at'][:10]}）"),
        ("最耗能运动", "most_calories_workout", lambda r: f"{r['calories_kcal']:.0f} kcal {r['activity_type']}（{r['start_at'][:10]}）"),
        ("最佳睡眠评分", "best_sleep_score", lambda r: f"{r['sleep_score']} 分（{r['start_at'][:10]}）"),
        ("最低静息心率", "lowest_resting_heart_rate", lambda r: f"{r['bpm']} bpm（{r['timestamp'][:10]}）"),
        ("最低体重", "lowest_weight", lambda r: f"{r['weight_kg']} kg（{r['timestamp'][:10]}）"),
    ):
        print(f"{label:<10}{fmt(data[key]) if data[key] else '—'}")
    print(f"\n步数目标 {data['step_goal']:,}：最长连续 {data['longest_goal_streak_days']} 天，当前连续 {data['current_goal_streak_days']} 天")


def cmd_gaps(args):
    service, source = _open_service(args)
    start, end = args.start_date or _days_ago(29), args.end_date or _today()
    report = service.find_data_gaps(start, end, args.type or None)
    if args.json:
        return _print_json(report)
    print(f"🕳️  数据缺口 {start} ~ {end}（{source}）\n{'=' * 50}")
    for item in report:
        status = "✅ 完整" if not item["missing_days"] else f"缺 {item['missing_days']}/{item['days_in_range']} 天"
        print(f"{item['data_type']:<22}{status}")
        for gap in item["gaps"][:8]:
            span = gap["start_date"] if gap["days"] == 1 else f"{gap['start_date']} ~ {gap['end_date']}"
            print(f"    · {span}（{gap['days']} 天）")
        if len(item["gaps"]) > 8:
            print(f"    … 另有 {len(item['gaps']) - 8} 段")


def cmd_export(args):
    from mi_fitness_mcp.api import EXPORT_SOURCES, _to_csv

    service, _ = _open_service(args)
    start, end = args.start_date or _days_ago(29), args.end_date or _today()
    rows = EXPORT_SOURCES[args.type](service, start, end)
    text = _to_csv(rows) if args.format == "csv" else json.dumps(rows, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="")
        print(f"✅ 已导出 {len(rows)} 条 → {args.output}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)


# ------------------------------------------------------------------ servers


def cmd_serve(args):
    from mi_fitness_mcp.server import main as server_main

    asyncio.run(server_main(demo=args.demo))


def cmd_api(args):
    import uvicorn

    if args.demo:
        os.environ["MI_FITNESS_DEMO"] = "1"
    if args.host not in LOOPBACK:
        # 非回环绑定：Host 白名单放行该地址（默认仅本机域名，见 security.py）
        os.environ.setdefault("MI_FITNESS_ALLOWED_HOSTS", args.host)
        if not os.environ.get("MI_FITNESS_API_KEY"):
            print("⚠️  绑定到非本机地址但未设置 MI_FITNESS_API_KEY，健康数据将暴露给局域网。")
            print("    建议先设置环境变量 MI_FITNESS_API_KEY 再启动。")
    # 关闭访问日志：查询串中的日期等参数不再落入终端/日志
    uvicorn.run("mi_fitness_mcp.api:app", host=args.host, port=args.port, access_log=False)


def cmd_web(args):
    from mi_fitness_mcp.web import run_server

    if args.host not in LOOPBACK:
        os.environ.setdefault("MI_FITNESS_ALLOWED_HOSTS", args.host)
    run_server(host=args.host, port=args.port, backend_url=args.backend_url, debug=args.debug)


def cmd_dashboard(args):
    """一条命令起 API + Web 仪表盘（同一进程），并打开浏览器。"""
    import threading
    import webbrowser

    import uvicorn

    from mi_fitness_mcp.web import create_app

    if args.demo:
        os.environ["MI_FITNESS_DEMO"] = "1"
    api_port = args.port + 1
    config = uvicorn.Config(
        "mi_fitness_mcp.api:app", host="127.0.0.1", port=api_port, access_log=False, log_level="warning"
    )
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    web = create_app(backend_url=f"http://127.0.0.1:{api_port}")
    url = f"http://127.0.0.1:{args.port}/"
    print(f"📊 仪表盘 {url}  （{'演示数据' if args.demo else '本机数据'}，Ctrl+C 退出）")
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    try:
        web.run(host="127.0.0.1", port=args.port)
    finally:
        server.should_exit = True


# ------------------------------------------------------------------ parser


def _common(parser: argparse.ArgumentParser, *, dates: bool = True, demo: bool = True) -> None:
    if dates:
        parser.add_argument("--start-date", help="开始日期（YYYY-MM-DD）")
        parser.add_argument("--end-date", help="结束日期（YYYY-MM-DD）")
    if demo:
        parser.add_argument("--demo", action="store_true", help="使用合成演示数据，无需小米账号")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROGRAM_NAME, description="小米运动健康数据：MCP / REST API / 仪表盘 / 命令行")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", help="可用命令")

    serve = sub.add_parser("serve", help="运行 MCP Server（stdio）")
    serve.add_argument("--demo", action="store_true", help="使用合成演示数据，无需小米账号")
    serve.set_defaults(func=cmd_serve)

    setup = sub.add_parser("setup", help="配置小米账号凭据")
    setup.add_argument("--mode", choices=["mi_fitness_cloud"], help="配置模式")
    setup.add_argument("--user-id", help="Mi Fitness user ID")
    setup.add_argument("--pass-token", help="Mi Fitness passToken")
    setup.add_argument("--region", help="云端区域（cn/de/i2/ru/sg/us）")
    setup.set_defaults(func=cmd_setup)

    sub.add_parser("doctor", help="检查配置并诊断问题").set_defaults(func=cmd_doctor)

    sync = sub.add_parser("sync", help="从小米云同步数据到本地")
    sync.add_argument("--type", choices=list(DATA_TYPES), help="只同步某一类数据")
    sync.add_argument("--start-date", help="开始日期；省略则从上次断点续传")
    sync.add_argument("--end-date", help="结束日期，默认今天")
    sync.add_argument("--full", action="store_true", help="忽略断点，按默认回溯天数重拉")
    sync.add_argument("-v", "--verbose", action="store_true", help="逐个时间块打印进度")
    sync.set_defaults(func=cmd_sync)

    status = sub.add_parser("status", help="查看本地库的数据量与覆盖范围（不联网）")
    _common(status, dates=False)
    status.set_defaults(func=cmd_status)

    overview = sub.add_parser("overview", help="今天（或指定日）的健康快照")
    overview.add_argument("--date", help="日期，默认今天")
    _common(overview, dates=False)
    overview.set_defaults(func=cmd_overview)

    report = sub.add_parser("report", help="周报/月报，并与上一期对比")
    report.add_argument("--period", choices=["week", "month"], default="week", help="未给日期时的默认跨度")
    _common(report)
    report.set_defaults(func=cmd_report)

    records = sub.add_parser("records", help="历史个人纪录与步数目标连续天数")
    records.add_argument("--step-goal", type=int, default=8000, help="每日步数目标（默认 8000）")
    _common(records, dates=False)
    records.set_defaults(func=cmd_records)

    gaps = sub.add_parser("gaps", help="找出缺数据的日期（没戴手环 / 没开监测 / 没同步）")
    gaps.add_argument("--type", action="append", choices=list(DATA_TYPES), help="可重复；默认检查常用 5 类")
    _common(gaps)
    gaps.set_defaults(func=cmd_gaps)

    export = sub.add_parser("export", help="导出本地数据为 CSV / JSON")
    export.add_argument("--type", required=True, choices=list(DATA_TYPES), help="数据类型")
    export.add_argument("--format", choices=["csv", "json"], default="csv")
    export.add_argument("-o", "--output", help="输出文件，省略则打印到标准输出")
    export.add_argument("--start-date", help="开始日期，默认 30 天前")
    export.add_argument("--end-date", help="结束日期，默认今天")
    export.add_argument("--demo", action="store_true", help="使用合成演示数据")
    export.set_defaults(func=cmd_export, json=False)

    api = sub.add_parser("api", help="启动 REST API 服务（供其他程序调用）")
    api.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    api.add_argument("--port", type=int, default=8321, help="监听端口（默认 8321）")
    api.add_argument("--demo", action="store_true", help="使用合成演示数据")
    api.set_defaults(func=cmd_api)

    for name, help_text in (("web", "启动 Web 仪表盘（反向代理到 API）"), ("webui", "web 的别名")):
        web = sub.add_parser(name, help=help_text)
        web.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
        web.add_argument("--port", type=int, default=8322, help="监听端口（默认 8322）")
        web.add_argument("--backend-url", default="http://127.0.0.1:8321", help="API 地址")
        web.add_argument("--debug", action="store_true", help="开启 Flask 调试模式")
        web.set_defaults(func=cmd_web)

    dash = sub.add_parser("dashboard", help="一条命令启动 API + 仪表盘并打开浏览器")
    dash.add_argument("--port", type=int, default=8322, help="仪表盘端口（API 使用 端口+1）")
    dash.add_argument("--demo", action="store_true", help="使用合成演示数据，无需小米账号")
    dash.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    dash.set_defaults(func=cmd_dashboard)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["serve"])  # 兼容旧行为：不带子命令 = 启动 MCP Server
    # Windows 控制台默认 GBK，输出 emoji/中文前先切到 UTF-8，避免 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)
    except ValueError as exc:
        _fail(str(exc))


if __name__ == "__main__":
    main()
