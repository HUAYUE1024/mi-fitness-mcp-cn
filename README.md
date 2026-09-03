# Mi Fitness MCP CN

小米运动健康（Mi Fitness）数据的本地 MCP Server + REST API + 现代化可视化 Web 仪表盘。把你自己账号里的健康数据同步到本地 SQLite，供 AI 客户端（MCP）、其他程序（HTTP API）或直接在现代化 Web 控制台查询分析。

基于 [kubulashvili/mi-fitness-mcp](https://github.com/kubulashvili/mi-fitness-mcp) 修改并深度重构，新增：

- 🇨🇳 **中国区云端适配**（`--region cn`）
- 💤 **全量健康指标**：睡眠分期（深睡/浅睡/REM/小睡）、运动记录、静息心率、血氧（SpO₂）、压力、异常心跳
- 🌐 **REST API 服务**（FastAPI）：全部数据端点 + 多维指标聚合，供任意程序调用
- 🖥️ **现代化 Web 仪表盘与控制台**（Python Flask）：高质感深浅主题、图表走势、实时响应检视、cURL 一键生成
- 🔑 **API Key 体系**：用小米凭据换取 `mif_sk_*` Key（类大模型平台风格），支持多账号、可吊销
- 📱 **扫码登录**：逆向小米官方扫码流程，手机 App 扫码即可授权，告别浏览器 F12 抓 Cookie

> ⚠️ 非小米官方项目。全部云端接口为逆向所得，仅用于读取和分析**你自己的**健康数据，请遵守小米用户协议。接口随时可能因小米改版失效。

## 支持的数据

| 类型 | 内容 | CLI `--type` | API 端点 |
|---|---|---|---|
| 每日活动 | 步数、距离、活动卡路里 | `daily_activity` | `/api/summary` |
| 心率 | 逐分钟采样 + 静息心率 | `heart_rate` | `/api/heart-rate` |
| 睡眠 | 时长、入睡/清醒、分段（深睡/浅睡/REM） | `sleep` | `/api/sleep` |
| 运动 | 类型、时长、距离、卡路里、心率、配速 | `workouts` | `/api/workouts` |
| 身体成分 | 体重、BMI、体脂、肌肉量等（需体脂秤） | `body_measurements` | `/api/body-measurements` |
| 血氧 | SpO₂ 采样 | `spo2` | `/api/spo2` |
| 压力 | 压力分数 + 等级 | `stress` | `/api/stress` |
| 异常心跳 | 事件起止与时长 | `abnormal_heart_beat` | `/api/abnormal-heart-beat` |

## 快速开始

### 安装

```bash
git clone https://github.com/<your-name>/mi-fitness-mcp-cn.git
cd mi-fitness-mcp-cn
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e '.[all,dev]'    # 安装所有依赖（含 FastAPI 与 Flask Web UI）
```

要求 Python ≥ 3.11。

### 获取凭据与可视化控制台

**方式 A：通过 Web 仪表盘扫码登录（最简单，推荐）**

```bash
# 1. 启动后端 API 服务 (端口 8321)
mi-fitness-mcp api

# 2. 另开终端启动 Web 仪表盘 (端口 8322)
mi-fitness-mcp web

# 3. 浏览器打开 http://127.0.0.1:8322/ 点击「扫码登录」
# 用小米账号 / 米家 App 扫码确认，自动换取并注入 API Key！
```

**方式 B：手动配置凭据**

浏览器登录 [account.xiaomi.com](https://account.xiaomi.com)，从 Cookie 获取 `userId` 与 `passToken`：

```bash
mi-fitness-mcp setup --mode mi_fitness_cloud --user-id "<userId>" --pass-token "<passToken>" --region cn
mi-fitness-mcp doctor        # 验证连通性
```

### 数据同步

```bash
mi-fitness-mcp sync --start-date 2026-01-01 --end-date 2026-06-30   # 全量数据同步
mi-fitness-mcp sync --type sleep --start-date 2026-06-01 --end-date 2026-06-30 # 按类型同步
```

### 作为 MCP Server 接入 AI 客户端

在 Claude Desktop 或其它 MCP 客户端中配置：

```json
{
  "mcpServers": {
    "mi-fitness": {
      "command": "mi-fitness-mcp",
      "args": ["serve"]
    }
  }
}
```

支持的 MCP 工具列表：
- `get_connection_status`, `sync_data`, `get_sync_status`, `get_profile`, `get_daily_summary`
- `query_metric_series`, `query_heart_rate`, `query_body_measurements`, `query_sleep`, `query_workouts`
- `query_spo2`, `query_stress`, `query_abnormal_heart_beat`, `get_data_coverage`

## REST API 与鉴权

启动 API 服务：

```bash
mi-fitness-mcp api [--host 127.0.0.1] [--port 8321]
```

- 交互式 Swagger 文档：`http://127.0.0.1:8321/docs`
- 鉴权机制：支持静态环境变量 `MI_FITNESS_API_KEY`、管理密钥 `MI_FITNESS_ADMIN_KEY` 以及通过 `/api/auth/keys` 动态发放的 `mif_sk_*` 用户级 Key。
- 扫码登录实现细节见 [docs/QR_LOGIN.md](docs/QR_LOGIN.md)，完整架构与协议文档见 [docs/PROJECT_DOCUMENTATION.md](docs/PROJECT_DOCUMENTATION.md)。

```bash
# 携带 API Key 查询健康数据示例
curl "http://127.0.0.1:8321/api/summary?start_date=2026-08-01&end_date=2026-08-31" \
  -H "X-API-Key: mif_sk_..."
```

## 项目结构

```text
src/mi_fitness_mcp/
├── main.py               # CLI 统一入口: serve / setup / doctor / sync / api / web
├── server.py             # MCP Server（14 个工具 + 异步同步引擎）
├── api.py                # REST API 服务（FastAPI: 数据端点 + Key 体系 + 扫码登录）
├── web.py                # 可视化仪表盘反向代理服务（Flask）
├── web_assets/           # 现代化前端界面（HTML / CSS / JS）
├── adapters/             # 小米云协议：登录、RC4加解密、签名、分页、解析
├── services/             # 同步引擎（分块/增量）与查询聚合
├── storage/              # SQLite 数据库引擎（8 张数据表 + sync_state）
├── models/               # Pydantic 数据模型
├── config.py             # 系统配置管理（platformdirs + JSON）
└── auth/                 # keyring 安全凭据存储
testpage/                 # 可选：独立运行的 Web 测试控制台（免安装，直接 python app.py）
docs/                     # 架构文档与扫码登录逆向原理解析
tests/                    # 单元测试与端到端测试套件
```

## 本地开发与测试

```bash
# 安装开发与测试依赖
pip install -e '.[all,dev]'

# 代码规范检查
ruff check src tests

# 运行完整测试套件
pytest -v
```

## 安全说明

- `passToken` 等同小米账号登录态，**切勿提交或泄露**；若泄露，请在小米账号安全中心退出设备并重新登录。
- 本地数据库与敏感配置文件已在 `.gitignore` 中默认排除。
- 服务默认仅绑定 `127.0.0.1` 本地回环地址。如需向局域网或公网开放，请务必设置 `MI_FITNESS_API_KEY` 与 `MI_FITNESS_ADMIN_KEY`。

## 免责声明

本项目与小米公司无关。接口为社区逆向成果，不保证持续可用。请仅用于读取和分析您自己的个人健康数据。

## License

[MIT](LICENSE)
