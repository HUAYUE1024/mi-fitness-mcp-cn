"""CLI for Mi Fitness MCP."""

import argparse
import asyncio
import sys

from mi_fitness_mcp.adapters.mi_fitness_cloud import MiFitnessCloudAdapter
from mi_fitness_mcp.auth import load_mi_fitness_token, save_mi_fitness_token
from mi_fitness_mcp.config import Config, get_config_path, load_config, save_config
from mi_fitness_mcp.server import main as server_main
from mi_fitness_mcp.services.sync_service import SyncService
from mi_fitness_mcp.storage import Database

PROGRAM_NAME = "mi-fitness-mcp"


async def _check_adapter_health(adapter) -> tuple[bool, list[str], str | None]:
    connected = await adapter.connect()
    region = getattr(adapter, "region", None)
    data_types = adapter.get_available_data_types() if connected else []
    if hasattr(adapter, "close"):
        await adapter.close()
    return connected, data_types, region


def cmd_setup(args):
    if args.mode == "mi_fitness_cloud" and args.user_id and args.pass_token:
        save_mi_fitness_token(args.user_id, args.pass_token)
        config = Config(mode="mi_fitness_cloud", region=args.region or "cn")
        save_config(config)
        print("✅ 配置已保存（mi_fitness_cloud）")
        print(f"   User ID: {args.user_id}")
        print(f"   Region: {config.region}")
        return

    print(f"{PROGRAM_NAME} - 配置向导")
    print("=" * 50)
    print()
    user_id = input("Mi Fitness user_id： ").strip()
    pass_token = input("Mi Fitness passToken： ").strip()
    region = input("区域 [cn]： ").strip() or "cn"
    if not user_id or not pass_token:
        print("❌ 必须提供 user_id 和 passToken")
        sys.exit(1)
    save_mi_fitness_token(user_id, pass_token)
    config = Config(mode="mi_fitness_cloud", region=region)
    save_config(config)
    print("✅ Mi Fitness 配置已保存！")


def cmd_doctor(args):
    print(f"{PROGRAM_NAME} - 诊断")
    print("=" * 50)
    print()
    config_path = get_config_path()
    print(f"配置文件： {config_path}")

    if not config_path.exists():
        print("❌ 未找到配置文件")
        print(f"   请运行： {PROGRAM_NAME} setup")
        sys.exit(1)

    try:
        config = load_config()
        print("✅ 配置已加载")
        print(f"   模式： {config.mode}")
        user_id, pass_token = load_mi_fitness_token()
        if user_id and pass_token:
            print("✅ 已找到 Mi Fitness 凭据")
            print(f"   User ID: {user_id}")
            print(f"   Region: {config.region}")
            adapter = MiFitnessCloudAdapter(
                user_id=user_id, pass_token=pass_token, region=config.region
            )
            connected, data_types, region = asyncio.run(_check_adapter_health(adapter))
            print(f"   连接状态： {'✅' if connected else '❌'}")
            if connected:
                print(f"   识别到的区域： {region}")
                print(f"   数据类型： {', '.join(data_types)}")
        else:
            print("❌ 未找到 Mi Fitness 凭据")
            print(f"   请运行： {PROGRAM_NAME} setup")

        print()
        print(f"数据库： {config.database_path}")
        if config.database_path.exists():
            Database(config.database_path)
            print("✅ 数据库可用")
        else:
            print("ℹ️  数据库将在首次运行时创建")
    except Exception as e:
        print(f"❌ 加载配置失败： {e}")
        sys.exit(1)


async def cmd_sync_async(args):
    print(f"{PROGRAM_NAME} - 同步")
    print("=" * 50)
    print()

    try:
        config = load_config()
    except Exception as e:
        print(f"❌ 加载配置失败： {e}")
        sys.exit(1)

    if config.mode == "not_configured":
        print("❌ 服务尚未配置")
        print(f"   请运行： {PROGRAM_NAME} setup")
        sys.exit(1)

    user_id, pass_token = load_mi_fitness_token()
    if not user_id or not pass_token:
        print("❌ 未找到 Mi Fitness 凭据")
        print(f"   请运行： {PROGRAM_NAME} setup")
        sys.exit(1)

    db = Database(config.database_path)
    adapter = MiFitnessCloudAdapter(user_id=user_id, pass_token=pass_token, region=config.region)
    if not await adapter.connect():
        print("❌ 无法连接到 Mi Fitness API")
        sys.exit(1)

    sync_service = SyncService(adapter, db)
    data_types = [args.type] if args.type else adapter.get_available_data_types()
    print(f"正在同步 {len(data_types)} 种数据类型...")
    print()
    for data_type in data_types:
        try:
            result = await sync_service.sync_data_type(
                data_type=data_type,
                start_date=args.start_date,
                end_date=args.end_date,
            )
            print(f"✅ {data_type}: 新增 {result['added']} 条，更新 {result['updated']} 条")
        except Exception as e:
            print(f"❌ {data_type}: {e}")

    print()
    print("同步完成！")


def cmd_sync(args):
    asyncio.run(cmd_sync_async(args))


def cmd_api(args):
    import os

    import uvicorn

    if args.host not in ("127.0.0.1", "localhost") and not os.environ.get("MI_FITNESS_API_KEY"):
        print("⚠️  绑定到非本机地址但未设置 MI_FITNESS_API_KEY，健康数据将暴露给局域网。")
        print("    建议先设置环境变量 MI_FITNESS_API_KEY 再启动。")
    uvicorn.run("mi_fitness_mcp.api:app", host=args.host, port=args.port)


def cmd_web(args):
    from mi_fitness_mcp.web import run_server

    run_server(
        host=args.host,
        port=args.port,
        backend_url=args.backend_url,
        debug=args.debug,
    )


def main():
    parser = argparse.ArgumentParser(prog=PROGRAM_NAME, description="小米运动健康数据 MCP Server")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    subparsers.add_parser("serve", help="运行 MCP Server")

    setup_parser = subparsers.add_parser("setup", help="配置服务")
    setup_parser.add_argument("--mode", choices=["mi_fitness_cloud"], help="配置模式")
    setup_parser.add_argument("--user-id", help="Mi Fitness user ID")
    setup_parser.add_argument("--pass-token", help="Mi Fitness passToken")
    setup_parser.add_argument("--region", help="云端区域")

    subparsers.add_parser("doctor", help="检查配置并诊断问题")

    sync_parser = subparsers.add_parser("sync", help="从数据源同步数据")
    sync_parser.add_argument(
        "--type",
        choices=[
            "daily_activity",
            "body_measurements",
            "heart_rate",
            "sleep",
            "workouts",
            "spo2",
            "stress",
            "abnormal_heart_beat",
        ],
        help="要同步的数据类型",
    )
    sync_parser.add_argument("--start-date", help="开始日期（YYYY-MM-DD）")
    sync_parser.add_argument("--end-date", help="结束日期（YYYY-MM-DD）")

    api_parser = subparsers.add_parser("api", help="启动 HTTP API 服务（供其他程序调用）")
    api_parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    api_parser.add_argument("--port", type=int, default=8321, help="监听端口（默认 8321）")

    web_parser = subparsers.add_parser("web", help="启动可视化 Web 仪表盘与测试控制台（Flask）")
    web_parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    web_parser.add_argument("--port", type=int, default=8322, help="监听端口（默认 8322）")
    web_parser.add_argument(
        "--backend-url",
        default="http://127.0.0.1:8321",
        help="FastAPI 后端服务地址（默认 http://127.0.0.1:8321）",
    )
    web_parser.add_argument("--debug", action="store_true", help="开启 Flask 调试模式")

    # Alias for webui
    webui_parser = subparsers.add_parser("webui", help="启动可视化 Web 仪表盘（web 命令别名）")
    webui_parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    webui_parser.add_argument("--port", type=int, default=8322, help="监听端口（默认 8322）")
    webui_parser.add_argument(
        "--backend-url",
        default="http://127.0.0.1:8321",
        help="FastAPI 后端服务地址（默认 http://127.0.0.1:8321）",
    )
    webui_parser.add_argument("--debug", action="store_true", help="开启 Flask 调试模式")

    args = parser.parse_args()
    if args.command == "serve" or args.command is None:
        asyncio.run(server_main())
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "api":
        cmd_api(args)
    elif args.command in ("web", "webui"):
        cmd_web(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
